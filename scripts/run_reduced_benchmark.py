"""Run the approved, shared 814-node / 1,000-edge benchmark safely.

This script deliberately separates preflight and ingest from timing.  It will
not start workloads unless every selected platform has exactly the same
verified benchmark graph.
"""

from __future__ import annotations

import csv
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from loaders.arangodb_loader import ArangoDBLoader
from loaders.cognodb_loader import CognoDBLoader
from loaders.falkordb_loader import FalkorDBLoader
from loaders.memgraph_loader import MemgraphLoader
from loaders.neo4j_loader import Neo4jLoader


NODES_CSV = PROJECT_ROOT / "data" / "nodes.csv"
EDGES_CSV = PROJECT_ROOT / "data" / "edges.csv"
PLATFORMS = {
    "cognodb": ("CognoDB", CognoDBLoader),
    "aura": ("Neo4j Aura", Neo4jLoader),
    "memgraph": ("Memgraph", MemgraphLoader),
    "falkordb": ("FalkorDB", FalkorDBLoader),
    "arango": ("ArangoDB", ArangoDBLoader),
}
COGNODB_CLEANUP_BATCH_SIZE = 50
COGNODB_CLEANUP_TIMEOUT_SECONDS = 45
COGNODB_CLEANUP_MAX_RETRIES = 2


def csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as input_file:
        return sum(1 for _ in csv.DictReader(input_file))


def bolt_counts(loader: Any) -> tuple[int, int]:
    with loader.driver.session(**loader._session_kwargs()) as session:
        nodes = session.run("MATCH (n:User) RETURN count(n) AS count").single()["count"]
        edges = session.run(
            "MATCH (:User)-[r:FOLLOWS]->(:User) RETURN count(r) AS count"
        ).single()["count"]
    return int(nodes), int(edges)


def clear_bolt(loader: Any) -> None:
    with loader.driver.session(**loader._session_kwargs()) as session:
        session.run("MATCH (n:User) DETACH DELETE n").consume()


def _raise_cleanup_timeout(*_args: object) -> None:
    raise TimeoutError("CognoDB cleanup batch timed out")


def _delete_cognodb_batch(loader: CognoDBLoader, query: str) -> tuple[int, int, int]:
    """Run exactly one small, independently committed cleanup batch."""
    retries = 0
    timeouts = 0
    for attempt in range(COGNODB_CLEANUP_MAX_RETRIES + 1):
        previous_handler = signal.signal(signal.SIGALRM, _raise_cleanup_timeout)
        signal.setitimer(signal.ITIMER_REAL, COGNODB_CLEANUP_TIMEOUT_SECONDS)
        try:
            # A new session makes every batch its own auto-commit transaction.
            with loader.driver.session(**loader._session_kwargs()) as session:
                record = session.run(
                    query,
                    batch_size=COGNODB_CLEANUP_BATCH_SIZE,
                    timeout=COGNODB_CLEANUP_TIMEOUT_SECONDS,
                ).single()
            return int(record["deleted"]), retries, timeouts
        except TimeoutError:
            timeouts += 1
            if attempt == COGNODB_CLEANUP_MAX_RETRIES:
                raise
        except Exception:
            if attempt == COGNODB_CLEANUP_MAX_RETRIES:
                raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)

        retries += 1
        loader.close()
        loader.connect()

    raise AssertionError("unreachable")


def clear_cognodb(loader: CognoDBLoader) -> dict[str, int]:
    """Delete only this benchmark's FOLLOWS and User data in bounded batches."""
    relationship_query = """
    MATCH (:User)-[r:FOLLOWS]->(:User)
    WITH r LIMIT $batch_size
    WITH collect(r) AS batch
    FOREACH (relationship IN batch | DELETE relationship)
    RETURN size(batch) AS deleted
    """
    node_query = """
    MATCH (node:User)
    WITH node LIMIT $batch_size
    WITH collect(node) AS batch
    FOREACH (user IN batch | DELETE user)
    RETURN size(batch) AS deleted
    """
    stats = {"relationships": 0, "nodes": 0, "retries": 0, "timeouts": 0}
    for kind, query in (("relationships", relationship_query), ("nodes", node_query)):
        while True:
            deleted, retries, timeouts = _delete_cognodb_batch(loader, query)
            stats["retries"] += retries
            stats["timeouts"] += timeouts
            if deleted == 0:
                break
            stats[kind] += deleted
            print(f"CognoDB      deleted {stats[kind]} {kind}", flush=True)
    return stats


def falkor_counts(loader: FalkorDBLoader) -> tuple[int, int]:
    nodes = loader.graph.query("MATCH (n:User) RETURN count(n)").result_set[0][0]
    edges = loader.graph.query(
        "MATCH (:User)-[r:FOLLOWS]->(:User) RETURN count(r)"
    ).result_set[0][0]
    return int(nodes), int(edges)


def clear_falkor(loader: FalkorDBLoader) -> None:
    loader.graph.query("MATCH (n:User) DETACH DELETE n")


def arango_counts(loader: ArangoDBLoader) -> tuple[int, int]:
    nodes = loader.db.collection(loader.vertex_collection).count()["count"]
    edges = loader.db.collection(loader.edge_collection).count()["count"]
    return int(nodes), int(edges)


def clear_arango(loader: ArangoDBLoader) -> None:
    # Truncate only the two collections belonging to this project's graph.
    loader.db.collection(loader.edge_collection).truncate()
    loader.db.collection(loader.vertex_collection).truncate()


def clear_platform(platform: str, loader: Any) -> dict[str, int] | None:
    if platform == "cognodb":
        return clear_cognodb(loader)
    if platform == "falkordb":
        clear_falkor(loader)
    elif platform == "arango":
        clear_arango(loader)
    else:
        clear_bolt(loader)
    return None


def platform_counts(platform: str, loader: Any) -> tuple[int, int]:
    if platform == "falkordb":
        return falkor_counts(loader)
    if platform == "arango":
        return arango_counts(loader)
    return bolt_counts(loader)


def connect_once(platform: str) -> Any:
    _name, loader_cls = PLATFORMS[platform]
    loader = loader_cls()
    loader.connect()
    return loader


def preflight() -> dict[str, Any]:
    """Connect each platform once, except Aura's explicitly bounded retry."""
    connected: dict[str, Any] = {}
    try:
        for platform, (name, _loader_cls) in PLATFORMS.items():
            if platform != "aura":
                try:
                    connected[platform] = connect_once(platform)
                except Exception as error:
                    print(f"{name:<12} FAILED: {type(error).__name__}")
                    raise RuntimeError(f"Preflight failed for {name}") from error
                print(f"{name:<12} CONNECTED")
                continue

            for attempt in range(1, 6):
                print(f"Neo4j Aura   attempt {attempt}/5", flush=True)
                try:
                    connected[platform] = connect_once(platform)
                except Exception as error:
                    print(
                        f"Neo4j Aura   FAILED (attempt {attempt}/5): "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )
                    if attempt == 5:
                        raise RuntimeError("Aura did not connect in five attempts") from error
                    time.sleep(5)
                else:
                    print(f"Neo4j Aura   CONNECTED (attempt {attempt}/5)")
                    break
        return connected
    except Exception:
        close_all(connected)
        raise


def close_all(loaders: dict[str, Any]) -> None:
    for loader in loaders.values():
        try:
            loader.close()
        except Exception:
            pass


def load_and_verify(loaders: dict[str, Any], expected_nodes: int, expected_edges: int) -> None:
    """Clear, verify, ingest, and verify all platforms before any workloads."""
    for platform, (name, _loader_cls) in PLATFORMS.items():
        try:
            cleanup_stats = clear_platform(platform, loaders[platform])
            nodes, edges = platform_counts(platform, loaders[platform])
        except Exception as error:
            print(f"{name:<12} CLEANUP FAILED: {type(error).__name__}")
            raise RuntimeError(f"Cleanup/count verification failed for {name}") from error
        if (nodes, edges) != (0, 0):
            raise RuntimeError(f"{name} benchmark graph was not empty: {nodes} nodes, {edges} edges")
        print(f"{name:<12} CLEANUP OK: 0 nodes, 0 edges")
        if cleanup_stats is not None:
            print(
                "CognoDB      cleanup summary: "
                f"{cleanup_stats['relationships']} relationships, "
                f"{cleanup_stats['nodes']} nodes, batch size "
                f"{COGNODB_CLEANUP_BATCH_SIZE}, retries {cleanup_stats['retries']}, "
                f"timeouts {cleanup_stats['timeouts']}"
            )

    for platform, (name, _loader_cls) in PLATFORMS.items():
        loader = loaders[platform]
        try:
            loader.create_indexes()
            node_result = loader.load_nodes(NODES_CSV)
            edge_result = loader.load_edges(EDGES_CSV)
            nodes, edges = platform_counts(platform, loader)
        except Exception as error:
            print(f"{name:<12} LOAD FAILED: {type(error).__name__}")
            raise RuntimeError(f"Load/count verification failed for {name}") from error
        if (nodes, edges) != (expected_nodes, expected_edges):
            raise RuntimeError(
                f"{name} count mismatch: expected {expected_nodes}/{expected_edges}, "
                f"got {nodes}/{edges}"
            )
        print(
            f"{name:<12} LOADED: {nodes} nodes, {edges} edges "
            f"(ingest {node_result['wall_clock_seconds'] + edge_result['wall_clock_seconds']:.2f}s)"
        )


def run_workloads() -> None:
    command = [
        sys.executable,
        "-m",
        "harness.runner",
        "--platforms",
        ",".join(PLATFORMS),
        "--skip-load",
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    results_path = PROJECT_ROOT / "results" / "latest.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    failed = [
        name
        for name, result in results.get("platforms", {}).items()
        if result.get("status") != "ok"
    ]
    if failed:
        raise RuntimeError(f"Benchmark workloads failed for: {', '.join(failed)}")

    subprocess.run([sys.executable, "-m", "harness.make_charts"], cwd=PROJECT_ROOT, check=True)
    subprocess.run([sys.executable, "-m", "harness.generate_readme"], cwd=PROJECT_ROOT, check=True)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    expected_nodes = csv_row_count(NODES_CSV)
    expected_edges = csv_row_count(EDGES_CSV)
    if (expected_nodes, expected_edges) != (814, 1000):
        raise RuntimeError(
            "Refusing to run: the shared reduced dataset must be exactly "
            f"814 nodes / 1000 edges (found {expected_nodes} / {expected_edges})."
        )
    print(f"Shared dataset: {expected_nodes} nodes, {expected_edges} edges")

    loaders = preflight()
    try:
        load_and_verify(loaders, expected_nodes, expected_edges)
    finally:
        close_all(loaders)

    print("All five platforms contain the identical verified dataset. Starting workloads.")
    run_workloads()
    print("Reduced 1,000-edge benchmark completed; results, charts, and README were generated.")


if __name__ == "__main__":
    main()
