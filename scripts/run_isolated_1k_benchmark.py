"""Run the reduced benchmark in fresh namespaces without deleting old data."""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
import re
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from harness.runner import print_summary_table, summarize_latencies, write_results
from loaders.arangodb_loader import ArangoDBLoader
from loaders.cognodb_loader import CognoDBLoader
from loaders.falkordb_loader import FalkorDBLoader
from loaders.memgraph_loader import MemgraphLoader
from loaders.neo4j_loader import Neo4jLoader
from loaders.tigergraph_loader import TigerGraphLoader
from workloads.aggregations import run_aggregation
from workloads.lookups import run_indexed_lookup, run_point_lookup
from workloads.mixed_concurrent import run_mixed_workload
from workloads.traversals import run_traversals


NODES_CSV = ROOT / "data" / "nodes.csv"
EDGES_CSV = ROOT / "data" / "edges.csv"
NODE_LABEL = "UserBench1K"
RUN_LABEL = "UserBench1KRun2"
NODE_PATTERN = f"{NODE_LABEL}:{RUN_LABEL}"
REL_TYPE = "FOLLOWS_BENCH_1K"
FALKOR_GRAPH = "benchmark_1k"
ARANGO_GRAPH = "benchmark_graph_1k"
ARANGO_USERS = "users_bench_1k"
ARANGO_EDGES = "follows_bench_1k"
TIGERGRAPH_VERTEX = "UserBench1K"
TIGERGRAPH_EDGE = "FOLLOWS_BENCH_1K"
# `run_mixed_workload()` produces negative nanosecond-based IDs.  TigerGraph's
# UINT attribute requires their absolute value, so synthetic IDs are far beyond
# the dense shared-dataset IDs (0..813).  Keep this guard deliberately large.
TIGERGRAPH_SYNTHETIC_ID_MIN = 1_000_000_000_000_000
READ_ITERATIONS = 100
WARMUP = 10
MIXED_DURATION_SECONDS = 10
CONCURRENCY_LEVELS = (1, 10, 40)
MIXED_READ_WRITE_RATIO = 0.8


class BenchCypherMixin:
    NODE_LOAD_QUERY = f"""
    UNWIND $batch AS row
    MERGE (user:{NODE_PATTERN} {{id: row.id}})
    SET user.user_id_original = row.user_id_original, user.region = row.region
    """
    EDGE_LOAD_QUERY = f"""
    UNWIND $batch AS row
    MATCH (source:{NODE_PATTERN} {{id: row.source}})
    MATCH (target:{NODE_PATTERN} {{id: row.target}})
    MERGE (source)-[:{REL_TYPE}]->(target)
    """
    USER_ORIGINAL_ID_INDEX_QUERY = (
        f"CREATE INDEX user_bench_1k_original IF NOT EXISTS "
        f"FOR (u:{RUN_LABEL}) ON (u.user_id_original)"
    )
    USER_ID_INDEX_QUERY = (
        f"CREATE INDEX user_bench_1k_id IF NOT EXISTS "
        f"FOR (u:{RUN_LABEL}) ON (u.id)"
    )


class BenchCognoDBLoader(BenchCypherMixin, CognoDBLoader):
    NODE_BATCH_SIZE = 50


class BenchAuraLoader(BenchCypherMixin, Neo4jLoader):
    pass


class BenchMemgraphLoader(BenchCypherMixin, MemgraphLoader):
    USER_ORIGINAL_ID_INDEX_QUERY = f"CREATE INDEX ON :{RUN_LABEL}(user_id_original)"
    USER_ID_INDEX_QUERY = f"CREATE INDEX ON :{RUN_LABEL}(id)"


class BenchFalkorDBLoader(FalkorDBLoader):
    def _insert_node_batch(self, batch):
        prepared = [
            {"id": int(row["id"]), "user_id_original": int(row["user_id_original"]), "region": row["region"]}
            for row in batch
        ]
        self.graph.query(
            f"UNWIND $batch AS row MERGE (user:{NODE_PATTERN} {{id: row.id}}) "
            "SET user.user_id_original = row.user_id_original, user.region = row.region",
            {"batch": prepared},
        )

    def _insert_edge_batch(self, batch):
        prepared = [{"source": int(row["source"]), "target": int(row["target"])} for row in batch]
        self.graph.query(
            f"UNWIND $batch AS row MATCH (source:{NODE_PATTERN} {{id: row.source}}) "
            f"MATCH (target:{NODE_PATTERN} {{id: row.target}}) "
            f"MERGE (source)-[:{REL_TYPE}]->(target)",
            {"batch": prepared},
        )

    def create_indexes(self):
        for query in (
            f"CREATE INDEX ON :{RUN_LABEL}(user_id_original)",
            f"CREATE INDEX ON :{RUN_LABEL}(id)",
        ):
            try:
                self.graph.query(query)
            except Exception as error:
                if "already" not in str(error).lower() and "exist" not in str(error).lower():
                    raise


class BenchTigerGraphLoader(TigerGraphLoader):
    """Use the administrator-created TigerGraph benchmark types as-is."""

    VERTEX_TYPE = TIGERGRAPH_VERTEX
    EDGE_TYPE = TIGERGRAPH_EDGE

    def connect(self) -> None:
        """Connect to the existing graph without performing any schema DDL."""
        import pyTigerGraph as tg

        self.conn = tg.TigerGraphConnection(
            host=self._required_connection_value("host"),
            graphname=self._required_connection_value("graph"),
            username=self._required_connection_value("user"),
            password=self._required_connection_value("password"),
        )
        # querywriter was granted on this graph, so request a fresh token
        # scoped to that graph instead of pyTigerGraph's default global token.
        self.conn.getToken(is_global=False)

    def create_indexes(self) -> None:
        """Schema is administrator-managed; do not create or alter indexes."""

    def _insert_node_batch(self, batch: list[dict[str, str]]) -> None:
        vertices = [
            (
                str(row["id"]),
                {
                    "user_id_original": int(row["user_id_original"]),
                    "region": row["region"],
                },
            )
            for row in batch
        ]
        accepted = self.conn.upsertVertices(self.VERTEX_TYPE, vertices)
        print(
            f"TigerGraph   vertex upsert accepted {accepted}/{len(vertices)}",
            flush=True,
        )
        if accepted != len(vertices):
            raise RuntimeError(
                f"TigerGraph REST++ vertex upsert accepted {accepted}/{len(vertices)} records"
            )

    def _insert_edge_batch(self, batch: list[dict[str, str]]) -> None:
        edges = [
            (str(row["source"]), str(row["target"]), {})
            for row in batch
        ]
        accepted = self.conn.upsertEdges(
            self.VERTEX_TYPE,
            self.EDGE_TYPE,
            self.VERTEX_TYPE,
            edges,
        )
        print(
            f"TigerGraph   edge upsert accepted {accepted}/{len(edges)}",
            flush=True,
        )
        if accepted != len(edges):
            raise RuntimeError(
                f"TigerGraph REST++ edge upsert accepted {accepted}/{len(edges)} records"
            )


PLATFORMS = {
    "cognodb": ("CognoDB", BenchCognoDBLoader),
    "memgraph": ("Memgraph", BenchMemgraphLoader),
    "falkordb": ("FalkorDB", BenchFalkorDBLoader),
    "arango": ("ArangoDB", ArangoDBLoader),
    "tigergraph": ("TigerGraph", BenchTigerGraphLoader),
}


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as input_file:
        return list(csv.DictReader(input_file))


def configure_namespace(platform: str, loader: Any) -> None:
    if platform == "falkordb":
        loader.connection["database"] = FALKOR_GRAPH
    elif platform == "arango":
        loader.graph_name = ARANGO_GRAPH
        loader.vertex_collection = ARANGO_USERS
        loader.edge_collection = ARANGO_EDGES
    elif platform == "tigergraph":
        graph_name = os.getenv("TG_GRAPHNAME")
        if not graph_name:
            raise ValueError("Missing TG_GRAPHNAME for TigerGraph")
        loader.connection["graph"] = graph_name


def close_all(loaders: dict[str, Any]) -> None:
    for loader in loaders.values():
        try:
            loader.close()
        except Exception:
            pass


def safe_tigergraph_error(error: Exception) -> str:
    """Return TigerGraph diagnostics without retaining credential material."""
    message = str(error)
    message = re.sub(r"(?i)(token\s*=\s*['\"]?)[^'\"\s,)}]+", r"\1<redacted>", message)
    message = re.sub(r"(?i)(authorization\s*[:=]\s*)\S+", r"\1<redacted>", message)
    return message


def preflight() -> dict[str, Any]:
    loaders: dict[str, Any] = {}
    try:
        for platform, (name, loader_cls) in PLATFORMS.items():
            if platform != "aura":
                loader = loader_cls()
                configure_namespace(platform, loader)
                loader.connect()
                loaders[platform] = loader
                print(f"{name:<12} CONNECTED")
                continue
            for attempt in range(1, 6):
                print(f"Neo4j Aura   attempt {attempt}/5", flush=True)
                loader = loader_cls()
                configure_namespace(platform, loader)
                try:
                    loader.connect()
                except Exception as error:
                    loader.close()
                    print(f"Neo4j Aura   FAILED (attempt {attempt}/5): {type(error).__name__}: {error}")
                    if attempt == 5:
                        raise RuntimeError("Aura did not connect in five attempts") from error
                    time.sleep(5)
                else:
                    loaders[platform] = loader
                    print(f"Neo4j Aura   CONNECTED (attempt {attempt}/5)")
                    break
        return loaders
    except Exception:
        close_all(loaders)
        raise


def counts(platform: str, loader: Any) -> tuple[int, int]:
    if platform in {"cognodb", "aura", "memgraph"}:
        with loader.driver.session(**loader._session_kwargs()) as session:
            nodes = session.run(
                f"MATCH (n:{NODE_PATTERN}) WHERE n.region <> 'mixed' RETURN count(n) AS count"
            ).single()["count"]
            edges = session.run(
                f"MATCH (source:{NODE_PATTERN})-[r:{REL_TYPE}]->(target:{NODE_PATTERN}) "
                "WHERE source.region <> 'mixed' AND target.region <> 'mixed' "
                "RETURN count(r) AS count"
            ).single()["count"]
        return int(nodes), int(edges)
    if platform == "falkordb":
        nodes = loader.graph.query(
            f"MATCH (n:{NODE_PATTERN}) WHERE n.region <> 'mixed' RETURN count(n)"
        ).result_set[0][0]
        edges = loader.graph.query(
            f"MATCH (source:{NODE_PATTERN})-[r:{REL_TYPE}]->(target:{NODE_PATTERN}) "
            "WHERE source.region <> 'mixed' AND target.region <> 'mixed' RETURN count(r)"
        ).result_set[0][0]
        return int(nodes), int(edges)
    if platform == "tigergraph":
        edge_count = loader.conn.getEdgeCount(loader.EDGE_TYPE)
        if isinstance(edge_count, dict):
            edge_count = edge_count.get(loader.EDGE_TYPE, 0)
        return (
            # TigerGraph's default built-in count can lag recent REST++
            # upserts. Realtime counting avoids a false 0/0 load failure.
            int(loader.conn.getVertexCount(loader.VERTEX_TYPE, realtime=True)),
            int(edge_count),
        )
    def collection_count(collection) -> int:
        value = collection.count()
        return int(value["count"] if isinstance(value, dict) else value)

    nodes = list(
        loader.db.aql.execute(
            "FOR n IN @@users FILTER n.region != 'mixed' COLLECT WITH COUNT INTO count RETURN count",
            bind_vars={"@users": ARANGO_USERS},
        )
    )[0]
    return (int(nodes), collection_count(loader.db.collection(ARANGO_EDGES)))


def cleanup_tigergraph_mixed_vertices(loader: BenchTigerGraphLoader) -> int:
    """Delete only mixed-workload vertices left by a prior TigerGraph run.

    The shared input has dense IDs 0..813.  Synthetic writes have a distinct
    timestamp-derived ID and `region == "mixed"`; requiring both properties
    prevents this resume cleanup from touching input vertices or unrelated data.
    """
    response = loader.conn.getVertices(loader.VERTEX_TYPE, select="region")
    # pyTigerGraph returns a list for this REST++ endpoint (older releases may
    # return a vertex-type keyed mapping), so support both documented shapes.
    vertices = (
        response.get(loader.VERTEX_TYPE, [])
        if isinstance(response, dict)
        else response
        if isinstance(response, list)
        else []
    )
    synthetic_ids: list[str] = []
    for vertex in vertices:
        if not isinstance(vertex, dict):
            continue
        vertex_id = str(vertex.get("v_id", ""))
        attributes = vertex.get("attributes", {})
        region = attributes.get("region") if isinstance(attributes, dict) else None
        try:
            is_synthetic_id = int(vertex_id) >= TIGERGRAPH_SYNTHETIC_ID_MIN
        except ValueError:
            is_synthetic_id = False
        if is_synthetic_id and region == "mixed":
            synthetic_ids.append(vertex_id)

    deleted = 0
    for vertex_id in synthetic_ids:
        deleted += int(loader.conn.delVerticesById(loader.VERTEX_TYPE, vertex_id))
    if deleted:
        print(f"TigerGraph   removed {deleted} leftover mixed-workload vertices", flush=True)
    return deleted


def is_memgraph_reset(error: Exception) -> bool:
    message = str(error).lower()
    return "connection reset" in message or "defunct connection" in message


def run_memgraph_operation(loader: Any, operation_name: str, operation):
    """Retry just the interrupted Memgraph operation with a fresh driver."""
    for attempt in range(3):  # initial attempt plus at most two retries
        try:
            return operation()
        except Exception as error:
            if not is_memgraph_reset(error) or attempt == 2:
                raise
            print(f"Memgraph     reset during {operation_name}; reconnecting (retry {attempt + 1}/2)")
            loader.close()
            loader.connect()
    raise AssertionError("unreachable")


def create_memgraph_indexes(loader: Any) -> None:
    """Memgraph requires index DDL in implicit, not explicit, transactions."""
    with loader.driver.session(**loader._session_kwargs()) as session:
        for query in (loader.USER_ORIGINAL_ID_INDEX_QUERY, loader.USER_ID_INDEX_QUERY):
            try:
                session.run(query).consume()
            except Exception as error:
                message = str(error).lower()
                if "already" not in message and "exist" not in message:
                    raise


def load_one_platform(platform: str, loader: Any, expected_nodes: int, expected_edges: int) -> dict[str, Any]:
    """Load one empty namespace, with a bounded Memgraph-only recovery path."""
    started_at = time.perf_counter()
    if platform == "memgraph":
        run_memgraph_operation(loader, "index setup", lambda: create_memgraph_indexes(loader))
        node_ingest = run_memgraph_operation(
            loader, "node ingestion", lambda: loader.load_nodes(NODES_CSV)
        )
        edge_ingest = run_memgraph_operation(
            loader, "relationship ingestion", lambda: loader.load_edges(EDGES_CSV)
        )
        loaded = run_memgraph_operation(loader, "count verification", lambda: counts(platform, loader))
    else:
        loader.create_indexes()
        node_ingest = loader.load_nodes(NODES_CSV)
        edge_ingest = loader.load_edges(EDGES_CSV)
        loaded = counts(platform, loader)

    if loaded != (expected_nodes, expected_edges):
        raise RuntimeError(
            f"count mismatch: expected {expected_nodes}/{expected_edges}, "
            f"got {loaded[0]}/{loaded[1]}"
        )
    return {
        "nodes": node_ingest,
        "edges": edge_ingest,
        "total_wall_clock_seconds": time.perf_counter() - started_at,
    }


def clear_cognodb_isolated_namespace(loader: BenchCognoDBLoader, batch_size: int = 50) -> None:
    """Clear only this run's labelled nodes and relationships for re-measurement."""
    relationship_query = f"""
    MATCH (source:{NODE_PATTERN})-[r:{REL_TYPE}]->(target:{NODE_PATTERN})
    WITH r LIMIT $batch_size
    DELETE r
    RETURN count(r) AS deleted
    """
    node_query = f"""
    MATCH (node:{NODE_PATTERN})
    WITH node LIMIT $batch_size
    DELETE node
    RETURN count(node) AS deleted
    """
    def delete_batch(query: str) -> int:
        for attempt in range(loader.MAX_DEFUNCT_RETRIES + 1):
            previous_handler = signal.signal(signal.SIGALRM, loader._raise_timeout)
            signal.setitimer(signal.ITIMER_REAL, loader.WRITE_TIMEOUT_SECONDS)
            try:
                with loader.driver.session(**loader._session_kwargs()) as session:
                    return int(session.run(query, batch_size=batch_size).single()["deleted"])
            except Exception:
                if attempt == loader.MAX_DEFUNCT_RETRIES:
                    raise
                loader.close()
                loader.connect()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
        raise AssertionError("unreachable")

    for query, entity in ((relationship_query, "relationships"), (node_query, "nodes")):
        deleted_total = 0
        while True:
            deleted = delete_batch(query)
            if deleted == 0:
                break
            deleted_total += deleted
            if deleted_total % 100 == 0:
                print(f"CognoDB      isolated {entity} cleared: {deleted_total}", flush=True)
        print(f"CognoDB      isolated {entity} cleared: {deleted_total}", flush=True)
    if counts("cognodb", loader) != (0, 0):
        raise RuntimeError("CognoDB isolated namespace did not clear cleanly")


def clear_tigergraph_isolated_namespace(loader: BenchTigerGraphLoader) -> None:
    """Clear only the known dense shared-dataset IDs and their benchmark edges."""
    node_ids = [str(row["id"]) for row in csv_rows(NODES_CSV)]
    edge_sources = sorted({str(row["source"]) for row in csv_rows(EDGES_CSV)})
    for source_id in edge_sources:
        loader.conn.delEdges(loader.VERTEX_TYPE, source_id, loader.EDGE_TYPE)
    deleted = int(loader.conn.delVerticesById(loader.VERTEX_TYPE, node_ids))
    if deleted != len(node_ids):
        raise RuntimeError(
            f"TigerGraph isolated namespace cleanup removed {deleted}/{len(node_ids)} known vertices"
        )
    if counts("tigergraph", loader) != (0, 0):
        raise RuntimeError("TigerGraph isolated namespace did not clear cleanly")
    print("TigerGraph   isolated dataset vertices and edges cleared", flush=True)


def clear_for_ingest_measurement(platform: str, loader: Any) -> None:
    """Allow fresh timing only for the two platforms missing genuine ingest data."""
    if platform == "cognodb":
        clear_cognodb_isolated_namespace(loader)
    elif platform == "tigergraph":
        clear_tigergraph_isolated_namespace(loader)
    else:
        raise ValueError(f"Fresh ingest measurement is not needed for {platform}")


def ensure_loaded(
    loaders: dict[str, Any], expected_nodes: int, expected_edges: int
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Resume from complete namespaces and load only empty ones."""
    ingest: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for platform, (name, _loader_cls) in PLATFORMS.items():
        try:
            if platform == "tigergraph":
                cleanup_tigergraph_mixed_vertices(loaders[platform])
            existing = counts(platform, loaders[platform])
            if existing == (expected_nodes, expected_edges):
                ingest[platform] = {
                    "skipped": True,
                    "verified_nodes": expected_nodes,
                    "verified_relationships": expected_edges,
                }
                print(f"{name:<12} ALREADY LOADED: {expected_nodes} nodes, {expected_edges} relationships")
                continue
            if existing != (0, 0):
                raise RuntimeError(
                    f"incomplete isolated namespace: {existing[0]} nodes, {existing[1]} edges"
                )
            print(f"{name:<12} fresh namespace empty; loading")
            ingest[platform] = load_one_platform(
                platform, loaders[platform], expected_nodes, expected_edges
            )
            print(f"{name:<12} LOADED: {expected_nodes} nodes, {expected_edges} relationships")
        except Exception as error:
            failures[platform] = f"{type(error).__name__}: {error}"
            if platform == "tigergraph":
                print(
                    f"{name:<12} LOAD FAILED: {type(error).__name__}: "
                    f"{safe_tigergraph_error(error)}"
                )
            else:
                print(f"{name:<12} LOAD FAILED: {type(error).__name__}")
    return ingest, failures


def bolt_runtime(loader: Any) -> dict[str, Any]:
    def sessions():
        return loader.driver.session(**loader._session_kwargs())
    def traversal(session, start_id, hops):
        return session.run(f"MATCH (n:{NODE_PATTERN} {{id:$id}})-[:{REL_TYPE}*1..{hops}]-(m:{NODE_PATTERN}) RETURN count(m)", id=start_id).consume()
    def point(session, value):
        return session.run(f"MATCH (n:{NODE_PATTERN} {{id:$value}}) RETURN n LIMIT 1", value=value).consume()
    def indexed(session, value):
        return session.run(f"MATCH (n:{NODE_PATTERN} {{user_id_original:$value}}) RETURN n LIMIT 1", value=value).consume()
    def aggregate(session):
        return session.run(f"MATCH (n:{NODE_PATTERN}) RETURN n.region, count(*)").consume()
    def mixed_read(session):
        return session.run(f"MATCH (n:{NODE_PATTERN}) RETURN count(n)").consume()
    def mixed_write(session, node_id):
        return session.run(f"CREATE (:{NODE_PATTERN} {{id:$id, user_id_original:$id, region:'mixed'}})", id=node_id).consume()
    return {"session_factory": sessions, "session_context": sessions, "traversal_query": traversal, "point_lookup_query": point, "indexed_lookup_query": indexed, "aggregation_query": aggregate, "mixed_read_query": mixed_read, "mixed_write_query": mixed_write}


def falkor_runtime(loader: Any) -> dict[str, Any]:
    graph = loader.graph
    def traversal(session, start_id, hops): return session.query(f"MATCH (n:{NODE_PATTERN} {{id:$id}})-[:{REL_TYPE}*1..{hops}]-(m:{NODE_PATTERN}) RETURN count(m)", {"id": start_id})
    def point(session, value): return session.query(f"MATCH (n:{NODE_PATTERN} {{id:$value}}) RETURN n LIMIT 1", {"value": value})
    def indexed(session, value): return session.query(f"MATCH (n:{NODE_PATTERN} {{user_id_original:$value}}) RETURN n LIMIT 1", {"value": value})
    def aggregate(session): return session.query(f"MATCH (n:{NODE_PATTERN}) RETURN n.region, count(*)")
    def mixed_read(session): return session.query(f"MATCH (n:{NODE_PATTERN}) RETURN count(n)")
    def mixed_write(session, node_id): return session.query(f"CREATE (:{NODE_PATTERN} {{id:$id, user_id_original:$id, region:'mixed'}})", {"id": node_id})
    return {"session_factory": lambda: graph, "session_context": lambda: nullcontext(graph), "traversal_query": traversal, "point_lookup_query": point, "indexed_lookup_query": indexed, "aggregation_query": aggregate, "mixed_read_query": mixed_read, "mixed_write_query": mixed_write}


def arango_runtime(loader: ArangoDBLoader) -> dict[str, Any]:
    db = loader.db
    def traversal(session, start_id, hops): return list(session.aql.execute("LET start = DOCUMENT(@@users, TO_STRING(@id)) FOR vertex IN 1..@hops ANY start @@edges COLLECT WITH COUNT INTO count RETURN count", bind_vars={"@users": ARANGO_USERS, "@edges": ARANGO_EDGES, "id": start_id, "hops": hops}))
    def point(session, value): return list(session.aql.execute("FOR n IN @@users FILTER n.id == @value LIMIT 1 RETURN n", bind_vars={"@users": ARANGO_USERS, "value": value}))
    def indexed(session, value): return list(session.aql.execute("FOR n IN @@users FILTER n.user_id_original == @value LIMIT 1 RETURN n", bind_vars={"@users": ARANGO_USERS, "value": value}))
    def aggregate(session): return list(session.aql.execute("FOR n IN @@users COLLECT region = n.region WITH COUNT INTO count RETURN {region: region, count: count}", bind_vars={"@users": ARANGO_USERS}))
    def mixed_read(session): return list(session.aql.execute("FOR n IN @@users COLLECT WITH COUNT INTO count RETURN count", bind_vars={"@users": ARANGO_USERS}))
    def mixed_write(session, node_id): return list(session.aql.execute("INSERT {_key: TO_STRING(@id), id: @id, user_id_original: @id, region: 'mixed'} INTO @@users", bind_vars={"@users": ARANGO_USERS, "id": node_id}))
    return {"session_factory": lambda: db, "session_context": lambda: nullcontext(db), "traversal_query": traversal, "point_lookup_query": point, "indexed_lookup_query": indexed, "aggregation_query": aggregate, "mixed_read_query": mixed_read, "mixed_write_query": mixed_write}


def tigergraph_runtime(loader: BenchTigerGraphLoader) -> dict[str, Any]:
    conn, graph, vertex, edge = loader.conn, os.getenv("TG_GRAPHNAME"), loader.VERTEX_TYPE, loader.EDGE_TYPE
    if not graph:
        raise ValueError("Missing TG_GRAPHNAME for TigerGraph")
    def traversal(session, start_id, hops):
        return session.runInterpretedQuery(f"INTERPRET QUERY (STRING start_id) FOR GRAPH {graph} {{ SetAccum<VERTEX<{vertex}>> @@seen; Start = {{{vertex}.*}}; Current = SELECT s FROM Start:s WHERE s.id == start_id; FOREACH i IN RANGE[1, {hops}] DO Current = SELECT t FROM Current:s -({edge}:e)- {vertex}:t ACCUM @@seen += t; END; PRINT @@seen.size(); }}", {"start_id": str(start_id)})
    def point(session, value):
        return session.runInterpretedQuery(f"INTERPRET QUERY (STRING target_id) FOR GRAPH {graph} {{ Start = {{{vertex}.*}}; Result = SELECT s FROM Start:s WHERE s.id == target_id LIMIT 1; PRINT Result; }}", {"target_id": str(value)})
    def indexed(session, value):
        return session.runInterpretedQuery(f"INTERPRET QUERY (UINT original_id) FOR GRAPH {graph} {{ Start = {{{vertex}.*}}; Result = SELECT s FROM Start:s WHERE s.user_id_original == original_id LIMIT 1; PRINT Result; }}", {"original_id": value})
    def aggregate(session):
        return session.runInterpretedQuery(f"INTERPRET QUERY () FOR GRAPH {graph} {{ MapAccum<STRING, SumAccum<UINT>> @@regions; Start = {{{vertex}.*}}; Result = SELECT s FROM Start:s ACCUM @@regions += (s.region -> 1); PRINT @@regions; }}")
    def mixed_read(session): return session.getVertexCount(vertex)
    def mixed_write(session, node_id):
        # The shared mixed workload generates negative synthetic ids. TigerGraph
        # stores user_id_original as UINT, so use the same unique id magnitude.
        synthetic_id = abs(int(node_id))
        return session.upsertVertex(
            vertex,
            str(synthetic_id),
            {"user_id_original": synthetic_id, "region": "mixed"},
        )
    return {"session_factory": lambda: conn, "session_context": lambda: nullcontext(conn), "traversal_query": traversal, "point_lookup_query": point, "indexed_lookup_query": indexed, "aggregation_query": aggregate, "mixed_read_query": mixed_read, "mixed_write_query": mixed_write}


def benchmark(
    runtime: dict[str, Any],
    dense_ids: list[int],
    original_ids: list[int],
    tigergraph_loader: BenchTigerGraphLoader | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with runtime["session_context"]() as session:
        workloads = {"traversals": {}}
        for hops in (1, 2, 3):
            workloads["traversals"][f"{hops}_hop"] = summarize_latencies(run_traversals(session, dense_ids, hops, READ_ITERATIONS, WARMUP, runtime["traversal_query"]))
        workloads["point_lookup"] = summarize_latencies(run_point_lookup(session, dense_ids, READ_ITERATIONS, WARMUP, runtime["point_lookup_query"]))
        workloads["indexed_lookup"] = summarize_latencies(run_indexed_lookup(session, original_ids, READ_ITERATIONS, WARMUP, runtime["indexed_lookup_query"]))
        workloads["aggregation"] = summarize_latencies(run_aggregation(session, READ_ITERATIONS, WARMUP, runtime["aggregation_query"]))
    try:
        mixed = {
            f"c{level}": run_mixed_workload(
                runtime["session_factory"],
                MIXED_DURATION_SECONDS,
                level,
                read_query_fn=runtime["mixed_read_query"],
                write_query_fn=runtime["mixed_write_query"],
            )
            for level in CONCURRENCY_LEVELS
        }
    finally:
        if tigergraph_loader is not None:
            cleanup_tigergraph_mixed_vertices(tigergraph_loader)
            final_counts = counts("tigergraph", tigergraph_loader)
            if final_counts != (814, 1000):
                raise RuntimeError(
                    "TigerGraph mixed-workload cleanup did not restore the "
                    f"shared namespace: got {final_counts[0]}/{final_counts[1]}"
                )
    return workloads, mixed


def load_previous_results() -> dict[str, Any]:
    """Reuse the newest successful result for each platform after a partial run."""
    merged: dict[str, Any] = {"platforms": {}}
    paths = sorted(
        (ROOT / "results").glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict):
            continue
        for platform, result in candidate.get("platforms", {}).items():
            if (
                platform not in merged["platforms"]
                and isinstance(result, dict)
                and result.get("status") == "ok"
            ):
                merged["platforms"][platform] = result
    return merged


def load_saved_ingest_measurement(platform: str) -> dict[str, Any] | None:
    """Load an externally timed, verified ingestion measurement when present."""
    path = ROOT / "results" / f"{platform}_ingest_measurement.json"
    try:
        measurement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if measurement.get("platform") != platform:
        return None
    ingest = measurement.get("ingest")
    return ingest if isinstance(ingest, dict) else None


def normalize_platform_result(platform_result: dict[str, Any]) -> dict[str, Any]:
    """Attach Section 5.2 metadata without changing recorded measurements."""
    normalized = dict(platform_result)
    ingest = dict(normalized.get("ingest", {}))
    node_seconds = ingest.get("nodes", {}).get("wall_clock_seconds")
    edge_seconds = ingest.get("edges", {}).get("wall_clock_seconds")
    if (
        "total_wall_clock_seconds" not in ingest
        and isinstance(node_seconds, (int, float))
        and isinstance(edge_seconds, (int, float))
    ):
        # Historical records timed node and edge loading independently.  Their
        # measured sum is the total load time for that saved run.
        ingest["total_wall_clock_seconds"] = node_seconds + edge_seconds
    normalized["ingest"] = ingest
    normalized.setdefault("indexed_property", "user_id_original")
    normalized.setdefault(
        "mixed_workload",
        {
            "read_write_ratio": MIXED_READ_WRITE_RATIO,
            "read_percent": int(MIXED_READ_WRITE_RATIO * 100),
            "write_percent": round((1 - MIXED_READ_WRITE_RATIO) * 100),
            "concurrency_levels": list(CONCURRENCY_LEVELS),
        },
    )
    normalized.setdefault("resource_usage", "not observable from the configured client")
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remeasure-ingest",
        action="store_true",
        help=(
            "clear and reload only the isolated CognoDB and TigerGraph benchmark "
            "data to collect missing genuine ingestion measurements"
        ),
    )
    parser.add_argument(
        "--remeasure-ingest-platforms",
        nargs="+",
        choices=("cognodb", "tigergraph"),
        metavar="PLATFORM",
        help="freshly measure ingestion only for the named isolated platform(s)",
    )
    args = parser.parse_args()
    # The benchmark's local .env is authoritative; inherited shell variables
    # must not redirect TigerGraph to a different existing graph.
    load_dotenv(ROOT / ".env", override=True)
    node_rows, edge_rows = csv_rows(NODES_CSV), csv_rows(EDGES_CSV)
    if (len(node_rows), len(edge_rows)) != (814, 1000):
        raise RuntimeError("Expected the approved shared dataset: 814 nodes / 1,000 relationships")
    print("Reduced shared dataset: 814 nodes, 1,000 relationships")
    loaders = preflight()
    try:
        ingest_remeasure_platforms = (
            tuple(args.remeasure_ingest_platforms)
            if args.remeasure_ingest_platforms
            else ("cognodb", "tigergraph")
            if args.remeasure_ingest
            else ()
        )
        if ingest_remeasure_platforms:
            for platform in ingest_remeasure_platforms:
                print(f"{PLATFORMS[platform][0]:<12} remeasuring isolated ingestion", flush=True)
                clear_for_ingest_measurement(platform, loaders[platform])
        ingest, load_failures = ensure_loaded(loaders, 814, 1000)
        previous_results = load_previous_results()
        results: dict[str, Any] = {"started_at_utc": datetime.now(UTC).isoformat(), "config": {"dataset_nodes": 814, "dataset_edges": 1000, "excluded_platforms": {"aura": "intermittent DatabaseNotFound availability", "puppygraph": "incompatible Bolt handshake"}, "namespace": {"node_label": NODE_LABEL, "run_label": RUN_LABEL, "relationship_type": REL_TYPE, "falkordb_graph": FALKOR_GRAPH, "arango_graph": ARANGO_GRAPH, "arango_vertex_collection": ARANGO_USERS, "arango_edge_collection": ARANGO_EDGES, "tigergraph_graph": os.getenv("TG_GRAPHNAME"), "tigergraph_vertex_type": TIGERGRAPH_VERTEX, "tigergraph_edge_type": TIGERGRAPH_EDGE}, "iterations": READ_ITERATIONS, "warmup": WARMUP, "mixed_duration_seconds": MIXED_DURATION_SECONDS, "mixed_concurrency_levels": list(CONCURRENCY_LEVELS), "mixed_read_write_ratio": MIXED_READ_WRITE_RATIO, "indexed_property": "user_id_original", "reduced_benchmark_reason": "CognoDB c0 sustained larger ingestion stalled/timed out; isolated tests completed up to 1,000 relationships."}, "platforms": {}}
        dense_ids = [int(row["id"]) for row in node_rows[:200]]
        original_ids = [int(row["user_id_original"]) for row in node_rows[:200]]
        for platform, (name, _loader_cls) in PLATFORMS.items():
            if platform in load_failures:
                results["platforms"][platform] = {
                    "status": "failed",
                    "error": load_failures[platform],
                }
                continue
            previous_platform = previous_results.get("platforms", {}).get(platform, {})
            if previous_platform.get("status") == "ok":
                reused = normalize_platform_result(previous_platform)
                saved_ingest = load_saved_ingest_measurement(platform)
                if ingest.get(platform, {}).get("skipped") and saved_ingest:
                    reused["ingest"] = saved_ingest
                    print(f"{name:<12} BENCHMARK REUSED (verified ingestion measurement recorded)")
                    results["platforms"][platform] = normalize_platform_result(reused)
                    continue
                if not ingest.get(platform, {}).get("skipped"):
                    reused["ingest"] = ingest[platform]
                    print(f"{name:<12} BENCHMARK REUSED (ingestion remeasured)")
                else:
                    print(f"{name:<12} BENCHMARK REUSED")
                results["platforms"][platform] = reused
                continue
            try:
                runtime = bolt_runtime(loaders[platform]) if platform in {"cognodb", "memgraph"} else falkor_runtime(loaders[platform]) if platform == "falkordb" else arango_runtime(loaders[platform]) if platform == "arango" else tigergraph_runtime(loaders[platform])
                workloads, mixed = benchmark(
                    runtime,
                    dense_ids,
                    original_ids,
                    tigergraph_loader=loaders[platform] if platform == "tigergraph" else None,
                )
                results["platforms"][platform] = normalize_platform_result({"status": "ok", "ingest": ingest[platform], "workloads": workloads, "mixed_concurrent": mixed, "resource_usage": "not observable from the configured client"})
                print(f"{name:<12} BENCHMARK OK")
            except Exception as error:
                results["platforms"][platform] = {"status": "failed", "ingest": ingest[platform], "error": f"{type(error).__name__}: {error}"}
                print(f"{name:<12} BENCHMARK FAILED: {type(error).__name__}")
        results["finished_at_utc"] = datetime.now(UTC).isoformat()
        write_results(results)
        print_summary_table(results)
    finally:
        close_all(loaders)
    subprocess.run([sys.executable, "-m", "harness.make_charts"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "harness.generate_readme"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
