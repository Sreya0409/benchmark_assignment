"""Load a 5,000-edge dataset subset into CognoDB without running workloads."""

from __future__ import annotations

import csv
import argparse
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loaders.cognodb_loader import CognoDBLoader


DEFAULT_EDGE_LIMIT = 5_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a CognoDB ingest subset.")
    parser.add_argument("--edges", type=int, default=DEFAULT_EDGE_LIMIT)
    return parser.parse_args()


def write_subset(directory: Path, edge_limit: int) -> tuple[Path, Path, int]:
    """Create temporary CSVs for an existing dataset edge subset."""
    edges_path = directory / "edges.csv"
    nodes_path = directory / "nodes.csv"
    source_edges = PROJECT_ROOT / "data" / "edges.csv"
    source_nodes = PROJECT_ROOT / "data" / "nodes.csv"

    with source_edges.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        edges = [next(reader) for _ in range(edge_limit)]

    node_ids = {row["source"] for row in edges} | {row["target"] for row in edges}
    with source_nodes.open(newline="", encoding="utf-8") as input_file:
        nodes = [row for row in csv.DictReader(input_file) if row["id"] in node_ids]

    with nodes_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("id", "user_id_original", "region"))
        writer.writeheader()
        writer.writerows(nodes)
    with edges_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("source", "target"))
        writer.writeheader()
        writer.writerows(edges)
    return nodes_path, edges_path, len(nodes)


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    with tempfile.TemporaryDirectory(prefix="cognodb-subset-") as temp_dir:
        nodes_path, edges_path, node_count = write_subset(Path(temp_dir), args.edges)
        loader = CognoDBLoader()
        try:
            loader.connect()
            loader.create_indexes()
            started_at = time.perf_counter()
            node_result = loader.load_nodes(nodes_path)
            edge_result = loader.load_edges(edges_path)
            elapsed = time.perf_counter() - started_at
        finally:
            loader.close()

    if edge_result["rels_loaded"] != args.edges:
        raise RuntimeError(f"Expected {args.edges} edges, loaded {edge_result['rels_loaded']}")
    print(f"nodes loaded: {node_result['nodes_loaded']} (expected {node_count})")
    print(f"edges loaded: {edge_result['rels_loaded']} (expected {args.edges})")
    print(f"total loading seconds: {elapsed:.3f}")
    print(f"driver reconnects: {loader.edge_reconnects}")
    print(f"batch retries: {loader.edge_retries}")


if __name__ == "__main__":
    main()
