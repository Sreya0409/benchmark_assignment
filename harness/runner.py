"""Benchmark runner orchestration."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.stats import percentiles
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"
NODES_CSV = DATA_DIR / "nodes.csv"
EDGES_CSV = DATA_DIR / "edges.csv"
START_NODES_JSON = RESULTS_DIR / "start_nodes.json"
READ_ITERATIONS = 100
WARMUP_ITERATIONS = 10
MIXED_CONCURRENCY_LEVELS = [1, 10, 40]
MIXED_DURATION_SECONDS = 60
START_NODE_COUNT = 200
START_NODE_SEED = 42

PLATFORMS = {
    "cognodb": CognoDBLoader,
    "aura": Neo4jLoader,
    "memgraph": MemgraphLoader,
    "falkordb": FalkorDBLoader,
    "arango": ArangoDBLoader,
    "tigergraph": TigerGraphLoader,
}


def csv_row_count(path: Path) -> int:
    """Return the number of data rows in a CSV file."""
    with path.open(newline="", encoding="utf-8") as input_file:
        return sum(1 for _ in csv.DictReader(input_file))


def parse_args() -> argparse.Namespace:
    """Parse runner CLI arguments."""
    parser = argparse.ArgumentParser(description="Run the graph database benchmark.")
    parser.add_argument(
        "--platforms",
        default=",".join(PLATFORMS.keys()),
        help="Comma-separated platform names to run. Defaults to all.",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Reuse already-loaded data and run timing workloads only.",
    )
    return parser.parse_args()


def main() -> None:
    """Run benchmark workloads across configured platforms."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    selected_platforms = parse_platforms(args.platforms)
    dataset_nodes = csv_row_count(NODES_CSV)
    dataset_edges = csv_row_count(EDGES_CSV)
    start_nodes = load_or_create_start_nodes(NODES_CSV, START_NODES_JSON)
    dense_ids = [node["id"] for node in start_nodes]
    original_ids = [node["user_id_original"] for node in start_nodes]

    results: dict[str, Any] = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "config": {
            "iterations": READ_ITERATIONS,
            "warmup": WARMUP_ITERATIONS,
            "mixed_duration_seconds": MIXED_DURATION_SECONDS,
            "mixed_concurrency_levels": MIXED_CONCURRENCY_LEVELS,
            "skip_load": args.skip_load,
            "start_nodes_path": str(START_NODES_JSON.relative_to(PROJECT_ROOT)),
            "dataset_nodes": dataset_nodes,
            "dataset_edges": dataset_edges,
        },
        "platforms": {},
    }

    for platform_name in selected_platforms:
        results["platforms"][platform_name] = run_platform(
            platform_name,
            PLATFORMS[platform_name],
            dense_ids,
            original_ids,
            skip_load=args.skip_load,
        )

    results["finished_at_utc"] = datetime.now(UTC).isoformat()
    write_results(results)
    print_summary_table(results)


def parse_platforms(platforms_arg: str) -> list[str]:
    """Return validated platform names from a comma-separated CLI value."""
    platform_names = [
        platform.strip().lower()
        for platform in platforms_arg.split(",")
        if platform.strip()
    ]
    unknown_platforms = sorted(set(platform_names) - set(PLATFORMS))
    if unknown_platforms:
        raise ValueError(
            "Unknown platform(s): "
            f"{', '.join(unknown_platforms)}. "
            f"Known: {', '.join(PLATFORMS)}"
        )
    return platform_names


def run_platform(
    platform_name: str,
    loader_cls,
    dense_ids: list[int],
    original_ids: list[int],
    skip_load: bool,
) -> dict[str, Any]:
    """Run ingest and workload benchmarks for one platform."""
    logging.info("Running platform: %s", platform_name)
    loader = loader_cls()
    platform_results: dict[str, Any] = {"status": "ok"}

    try:
        loader.connect()

        if skip_load:
            platform_results["ingest"] = {"skipped": True}
            loader.create_indexes()
        else:
            # Bolt/FalkorDB node and edge ingest both resolve users by ``id``.
            # Create that index before MERGE/MATCH begins so a large graph is
            # never loaded through repeated full-node scans.
            if platform_name in {"cognodb", "aura", "memgraph", "falkordb"}:
                loader.create_indexes()
            node_ingest = loader.load_nodes(NODES_CSV)
            if platform_name not in {"cognodb", "aura", "memgraph", "falkordb"}:
                loader.create_indexes()
            platform_results["ingest"] = {
                "nodes": node_ingest,
                "edges": loader.load_edges(EDGES_CSV),
            }
        runtime = build_runtime(platform_name, loader)
        platform_results["workloads"] = run_read_workloads(
            runtime,
            dense_ids,
            original_ids,
        )
        platform_results["mixed_concurrent"] = run_mixed_workloads(runtime)
    except Exception as exc:
        logging.exception("Platform %s failed", platform_name)
        platform_results = {
            **platform_results,
            "status": "failed",
            "error": str(exc),
        }
    finally:
        try:
            loader.close()
        except Exception:
            logging.exception("Failed closing platform: %s", platform_name)

    return platform_results


def run_read_workloads(
    runtime: dict[str, Any],
    dense_ids: list[int],
    original_ids: list[int],
) -> dict[str, Any]:
    """Run traversal, lookup, and aggregation workloads for one platform."""
    results: dict[str, Any] = {}

    with runtime["session_context"]() as session:
        results["traversals"] = {}
        for hops in (1, 2, 3):
            latencies = run_traversals(
                session,
                dense_ids,
                hops,
                READ_ITERATIONS,
                WARMUP_ITERATIONS,
                query_fn=runtime["traversal_query"],
            )
            results["traversals"][f"{hops}_hop"] = summarize_latencies(latencies)

        point_latencies = run_point_lookup(
            session,
            dense_ids,
            READ_ITERATIONS,
            WARMUP_ITERATIONS,
            query_fn=runtime["point_lookup_query"],
        )
        results["point_lookup"] = summarize_latencies(point_latencies)

        indexed_latencies = run_indexed_lookup(
            session,
            original_ids,
            READ_ITERATIONS,
            WARMUP_ITERATIONS,
            query_fn=runtime["indexed_lookup_query"],
        )
        results["indexed_lookup"] = summarize_latencies(indexed_latencies)

        aggregation_latencies = run_aggregation(
            session,
            READ_ITERATIONS,
            WARMUP_ITERATIONS,
            query_fn=runtime["aggregation_query"],
        )
        results["aggregation"] = summarize_latencies(aggregation_latencies)

    return results


def run_mixed_workloads(runtime: dict[str, Any]) -> dict[str, Any]:
    """Run mixed concurrent workloads at the configured concurrency levels."""
    results = {}
    for concurrency in MIXED_CONCURRENCY_LEVELS:
        results[f"c{concurrency}"] = run_mixed_workload(
            runtime["session_factory"],
            MIXED_DURATION_SECONDS,
            concurrency,
            read_query_fn=runtime["mixed_read_query"],
            write_query_fn=runtime["mixed_write_query"],
        )
    return results


def summarize_latencies(latencies_ms: list[float]) -> dict[str, Any]:
    """Return raw latency samples plus percentile aggregates."""
    return {
        "raw_latencies_ms": latencies_ms,
        "percentiles_ms": percentiles(latencies_ms),
    }


def load_or_create_start_nodes(
    nodes_csv: Path,
    output_path: Path,
) -> list[dict[str, int]]:
    """Load saved start nodes or create a reproducible sample from nodes.csv."""
    if output_path.exists():
        with output_path.open(encoding="utf-8") as input_file:
            saved = json.load(input_file)
        if saved and isinstance(saved[0], int):
            return [
                {"id": int(node_id), "user_id_original": int(node_id)}
                for node_id in saved
            ]
        return [
            {
                "id": int(row["id"]),
                "user_id_original": int(row["user_id_original"]),
            }
            for row in saved
        ]

    if not nodes_csv.exists():
        raise FileNotFoundError(
            f"{nodes_csv} does not exist. Run data/prepare_dataset.py first."
        )

    with nodes_csv.open(newline="", encoding="utf-8") as nodes_file:
        rows = [
            {
                "id": int(row["id"]),
                "user_id_original": int(row["user_id_original"]),
            }
            for row in csv.DictReader(nodes_file)
        ]

    if len(rows) < START_NODE_COUNT:
        raise ValueError(
            f"Need at least {START_NODE_COUNT} nodes, found {len(rows)} in {nodes_csv}"
        )

    rng = random.Random(START_NODE_SEED)
    start_nodes = rng.sample(rows, START_NODE_COUNT)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(start_nodes, output_file, indent=2)
        output_file.write("\n")

    return start_nodes


def build_runtime(platform_name: str, loader) -> dict[str, Any]:
    """Build platform-specific session and query callables."""
    if platform_name in {"cognodb", "aura", "memgraph"}:
        return build_bolt_runtime(loader)
    if platform_name == "falkordb":
        return build_falkordb_runtime(loader)
    if platform_name == "arango":
        return build_arango_runtime(loader)
    if platform_name == "tigergraph":
        return build_tigergraph_runtime(loader)
    raise ValueError(f"No runtime builder for {platform_name}")


def build_bolt_runtime(loader) -> dict[str, Any]:
    """Build query callables for Bolt-compatible platforms."""
    def session_factory():
        return loader.driver.session(**loader._session_kwargs())

    return {
        "session_factory": session_factory,
        "session_context": session_factory,
        "traversal_query": None,
        "point_lookup_query": None,
        "indexed_lookup_query": None,
        "aggregation_query": None,
        "mixed_read_query": None,
        "mixed_write_query": None,
    }


def build_arango_runtime(loader) -> dict[str, Any]:
    """Build query callables for ArangoDB AQL."""
    db = loader.db
    users = loader.vertex_collection
    edges = loader.edge_collection

    def session_factory():
        return db

    def traversal_query(session, start_id: int, hops: int) -> object:
        query = """
        LET start = DOCUMENT(@@users, TO_STRING(@id))
        FOR vertex IN 1..@hops ANY start @@edges
            COLLECT WITH COUNT INTO count
            RETURN count
        """
        return list(
            session.aql.execute(
                query,
                bind_vars={
                    "@users": users,
                    "@edges": edges,
                    "id": start_id,
                    "hops": hops,
                },
            )
        )

    def point_lookup_query(session, value: int) -> object:
        query = "FOR n IN @@users FILTER n.id == @value LIMIT 1 RETURN n"
        return list(
            session.aql.execute(
                query,
                bind_vars={"@users": users, "value": value},
            )
        )

    def indexed_lookup_query(session, value: int) -> object:
        query = """
        FOR n IN @@users
            FILTER n.user_id_original == @value
            LIMIT 1
            RETURN n
        """
        return list(
            session.aql.execute(
                query,
                bind_vars={"@users": users, "value": value},
            )
        )

    def aggregation_query(session) -> object:
        query = """
        FOR n IN @@users
            COLLECT region = n.region WITH COUNT INTO count
            RETURN {region: region, count: count}
        """
        return list(session.aql.execute(query, bind_vars={"@users": users}))

    def mixed_read_query(session) -> object:
        query = """
        FOR n IN @@users
            COLLECT WITH COUNT INTO count
            RETURN count
        """
        return list(session.aql.execute(query, bind_vars={"@users": users}))

    def mixed_write_query(session, node_id: int) -> object:
        query = """
        INSERT {
            _key: TO_STRING(@id),
            id: @id,
            user_id_original: @id,
            region: "mixed"
        } INTO @@users
        """
        return list(
            session.aql.execute(
                query,
                bind_vars={"@users": users, "id": node_id},
            )
        )

    return {
        "session_factory": session_factory,
        "session_context": lambda: nullcontext(db),
        "traversal_query": traversal_query,
        "point_lookup_query": point_lookup_query,
        "indexed_lookup_query": indexed_lookup_query,
        "aggregation_query": aggregation_query,
        "mixed_read_query": mixed_read_query,
        "mixed_write_query": mixed_write_query,
    }


def build_falkordb_runtime(loader) -> dict[str, Any]:
    """Build query callables for FalkorDB."""
    graph = loader.graph

    def session_factory():
        return graph

    def traversal_query(session, start_id: int, hops: int) -> object:
        query = f"MATCH (n {{id:$id}})-[*1..{hops}]-(m) RETURN count(m)"
        return session.query(query, {"id": start_id})

    def point_lookup_query(session, value: int) -> object:
        return session.query("MATCH (n {id:$value}) RETURN n LIMIT 1", {"value": value})

    def indexed_lookup_query(session, value: int) -> object:
        return session.query(
            "MATCH (n:User {user_id_original:$value}) RETURN n LIMIT 1",
            {"value": value},
        )

    def aggregation_query(session) -> object:
        return session.query("MATCH (n) RETURN n.region, count(*)")

    def mixed_read_query(session) -> object:
        return session.query("MATCH (n:User) RETURN count(n)")

    def mixed_write_query(session, node_id: int) -> object:
        return session.query(
            """
            CREATE (:User {
                id: $id,
                user_id_original: $id,
                region: 'mixed'
            })
            """,
            {"id": node_id},
        )

    return {
        "session_factory": session_factory,
        "session_context": lambda: nullcontext(graph),
        "traversal_query": traversal_query,
        "point_lookup_query": point_lookup_query,
        "indexed_lookup_query": indexed_lookup_query,
        "aggregation_query": aggregation_query,
        "mixed_read_query": mixed_read_query,
        "mixed_write_query": mixed_write_query,
    }


def build_tigergraph_runtime(loader) -> dict[str, Any]:
    """Build query callables for TigerGraph interpreted GSQL."""
    conn = loader.conn
    graph_name = loader.connection["graph"]

    def session_factory():
        return conn

    def traversal_query(session, start_id: int, hops: int) -> object:
        # TigerGraph traversal is expressed as interpreted GSQL instead of a
        # parameterized ad hoc query string, so this is not perfectly symmetric
        # with Cypher/AQL and should be noted in the benchmark caveats.
        query = f"""
        INTERPRET QUERY (UINT start_id) FOR GRAPH {graph_name} {{
            SetAccum<VERTEX<User>> @@seen;
            Start = {{User.*}};
            Current = SELECT s FROM Start:s WHERE s.id == start_id;
            FOREACH i IN RANGE[1, {hops}] DO
                Current = SELECT t FROM Current:s -(FOLLOWS:e)- User:t
                          ACCUM @@seen += t;
            END;
            PRINT @@seen.size();
        }}
        """
        return session.runInterpretedQuery(query, {"start_id": start_id})

    def point_lookup_query(session, value: int) -> object:
        query = f"""
        INTERPRET QUERY (UINT target_id) FOR GRAPH {graph_name} {{
            Start = {{User.*}};
            Result = SELECT s FROM Start:s WHERE s.id == target_id LIMIT 1;
            PRINT Result;
        }}
        """
        return session.runInterpretedQuery(query, {"target_id": value})

    def indexed_lookup_query(session, value: int) -> object:
        query = f"""
        INTERPRET QUERY (UINT original_id) FOR GRAPH {graph_name} {{
            Start = {{User.*}};
            Result = SELECT s FROM Start:s
                     WHERE s.user_id_original == original_id LIMIT 1;
            PRINT Result;
        }}
        """
        return session.runInterpretedQuery(query, {"original_id": value})

    def aggregation_query(session) -> object:
        query = f"""
        INTERPRET QUERY () FOR GRAPH {graph_name} {{
            MapAccum<STRING, SumAccum<UINT>> @@regions;
            Start = {{User.*}};
            Result = SELECT s FROM Start:s ACCUM @@regions += (s.region -> 1);
            PRINT @@regions;
        }}
        """
        return session.runInterpretedQuery(query)

    def mixed_read_query(session) -> object:
        return session.getVertexCount(loader.VERTEX_TYPE)

    def mixed_write_query(session, node_id: int) -> object:
        return session.upsertVertex(
            loader.VERTEX_TYPE,
            node_id,
            {
                "user_id_original": node_id,
                "region": "mixed",
            },
        )

    return {
        "session_factory": session_factory,
        "session_context": lambda: nullcontext(conn),
        "traversal_query": traversal_query,
        "point_lookup_query": point_lookup_query,
        "indexed_lookup_query": indexed_lookup_query,
        "aggregation_query": aggregation_query,
        "mixed_read_query": mixed_read_query,
        "mixed_write_query": mixed_write_query,
    }


def write_results(results: dict[str, Any]) -> None:
    """Write timestamped and latest JSON result files."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    timestamped_path = RESULTS_DIR / f"results_{timestamp}.json"
    latest_path = RESULTS_DIR / "latest.json"

    for path in (timestamped_path, latest_path):
        with path.open("w", encoding="utf-8") as output_file:
            json.dump(results, output_file, indent=2)
            output_file.write("\n")


def print_summary_table(results: dict[str, Any]) -> None:
    """Print a compact platform summary table."""
    rows = []
    for platform, platform_results in results["platforms"].items():
        if platform_results.get("status") != "ok":
            rows.append([platform, "failed", "-", "-", "-", "-"])
            continue

        ingest = platform_results.get("ingest", {})
        node_rate = nested_get(ingest, "nodes", "nodes_per_second")
        rel_rate = nested_get(ingest, "edges", "rels_per_second")
        traversal_p95 = nested_get(
            platform_results,
            "workloads",
            "traversals",
            "1_hop",
            "percentiles_ms",
            "p95",
        )
        mixed_qps = nested_get(
            platform_results,
            "mixed_concurrent",
            "c10",
            "ops_per_second",
        )
        rows.append(
            [
                platform,
                "ok",
                format_number(node_rate),
                format_number(rel_rate),
                format_number(traversal_p95),
                format_number(mixed_qps),
            ]
        )

    headers = ["platform", "status", "nodes/s", "rels/s", "1-hop p95 ms", "c10 qps"]
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    print(format_table_row(headers, widths))
    print(format_table_row(["-" * width for width in widths], widths))
    for row in rows:
        print(format_table_row(row, widths))


def nested_get(value: dict[str, Any], *keys: str) -> Any:
    """Return a nested value or None if any key is absent."""
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def format_number(value: Any) -> str:
    """Format a numeric table value."""
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_table_row(row: list[Any], widths: list[int]) -> str:
    """Format one stdout table row."""
    return " | ".join(str(value).ljust(width) for value, width in zip(row, widths))


if __name__ == "__main__":
    main()
