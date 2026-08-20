"""Prepare a sampled SNAP soc-Pokec dataset for benchmarking."""

from __future__ import annotations

import argparse
import csv
import gzip
import random
from pathlib import Path
from typing import Iterable

import requests
from tqdm import tqdm


SNAP_RELATIONSHIPS_URL = (
    "https://snap.stanford.edu/data/soc-pokec-relationships.txt.gz"
)
DEFAULT_TARGET_EDGES = 200_000
DEFAULT_SEED = 42
REGIONS = (
    "north",
    "south",
    "east",
    "west",
    "central",
    "metro",
    "coastal",
    "rural",
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for dataset preparation."""
    data_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Download and sample SNAP soc-Pokec relationships."
    )
    parser.add_argument(
        "--target-edges",
        type=int,
        default=DEFAULT_TARGET_EDGES,
        help="Approximate number of relationships to keep in the sampled subgraph.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed used for seed-node selection.",
    )
    parser.add_argument(
        "--seed-node",
        type=int,
        default=None,
        help="Explicit seed node for BFS sampling; overrides random seed selection.",
    )
    parser.add_argument(
        "--relationships-url",
        default=SNAP_RELATIONSHIPS_URL,
        help="Source URL for the gzipped SNAP relationship edge list.",
    )
    parser.add_argument(
        "--relationships-path",
        type=Path,
        default=data_dir / "soc-pokec-relationships.txt.gz",
        help="Local path for the gzipped SNAP relationship edge list.",
    )
    parser.add_argument(
        "--nodes-out",
        type=Path,
        default=data_dir / "nodes.csv",
        help="Output CSV path for sampled nodes.",
    )
    parser.add_argument(
        "--edges-out",
        type=Path,
        default=data_dir / "edges.csv",
        help="Output CSV path for sampled directed edges.",
    )
    return parser.parse_args()


def download_if_missing(url: str, destination: Path) -> None:
    """Download the source archive unless it already exists locally."""
    if destination.exists():
        print(f"Using existing download: {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()

    total_bytes = int(response.headers.get("content-length", 0))
    with destination.open("wb") as output:
        with tqdm(
            total=total_bytes or None,
            unit="B",
            unit_scale=True,
            desc="Downloading soc-Pokec",
        ) as progress:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                progress.update(len(chunk))


def iter_edges(relationships_path: Path) -> Iterable[tuple[int, int]]:
    """Yield directed edges from the gzipped SNAP relationship file."""
    with gzip.open(relationships_path, "rt", encoding="utf-8") as input_file:
        for line in input_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            parts = stripped.split()
            if len(parts) < 2:
                continue

            yield int(parts[0]), int(parts[1])


def choose_seed_node(relationships_path: Path, seed: int) -> int:
    """Select a reproducible random node using reservoir sampling."""
    rng = random.Random(seed)
    chosen_node = None
    seen_edges = 0

    for source, target in iter_edges(relationships_path):
        seen_edges += 1
        candidate = source if rng.randrange(2) == 0 else target
        if rng.randrange(seen_edges) == 0:
            chosen_node = candidate

    if chosen_node is None:
        raise ValueError(f"No edges found in {relationships_path}")

    return chosen_node


def sample_connected_edges(
    relationships_path: Path,
    target_edges: int,
    seed_node: int,
) -> list[tuple[int, int]]:
    """Sample a weakly connected directed subgraph with streaming BFS passes."""
    if target_edges <= 0:
        raise ValueError("--target-edges must be greater than zero")

    visited = {seed_node}
    frontier = {seed_node}
    selected_edges: list[tuple[int, int]] = []
    selected_edge_set: set[tuple[int, int]] = set()
    pass_number = 0

    while frontier and len(selected_edges) < target_edges:
        pass_number += 1
        next_frontier: set[int] = set()

        print(
            "BFS pass "
            f"{pass_number}: frontier={len(frontier)}, "
            f"nodes={len(visited)}, edges={len(selected_edges)}"
        )

        for source, target in iter_edges(relationships_path):
            expands_from_frontier = source in frontier or target in frontier
            already_internal = source in visited and target in visited

            if not expands_from_frontier and not already_internal:
                continue

            if expands_from_frontier:
                if source in frontier and target not in visited:
                    next_frontier.add(target)
                if target in frontier and source not in visited:
                    next_frontier.add(source)

            edge = (source, target)
            if edge in selected_edge_set:
                continue

            selected_edge_set.add(edge)
            selected_edges.append(edge)

            if len(selected_edges) >= target_edges:
                break

        visited.update(next_frontier)
        frontier = next_frontier

    if len(selected_edges) < target_edges:
        print(
            "Warning: exhausted reachable graph before target; "
            f"sample contains {len(selected_edges)} edges."
        )

    return selected_edges


def write_csvs(
    selected_edges: list[tuple[int, int]],
    nodes_out: Path,
    edges_out: Path,
) -> tuple[int, int]:
    """Write dense node and edge CSV files for the sampled subgraph."""
    original_node_ids = sorted({node for edge in selected_edges for node in edge})
    dense_ids = {
        original_id: dense_id for dense_id, original_id in enumerate(original_node_ids)
    }

    nodes_out.parent.mkdir(parents=True, exist_ok=True)
    edges_out.parent.mkdir(parents=True, exist_ok=True)

    with nodes_out.open("w", newline="", encoding="utf-8") as nodes_file:
        writer = csv.writer(nodes_file)
        writer.writerow(("id", "user_id_original", "region"))
        for original_id in original_node_ids:
            writer.writerow(
                (
                    dense_ids[original_id],
                    original_id,
                    REGIONS[original_id % len(REGIONS)],
                )
            )

    with edges_out.open("w", newline="", encoding="utf-8") as edges_file:
        writer = csv.writer(edges_file)
        writer.writerow(("source", "target"))
        for source, target in selected_edges:
            writer.writerow((dense_ids[source], dense_ids[target]))

    return len(original_node_ids), len(selected_edges)


def main() -> None:
    """Run the dataset preparation pipeline."""
    args = parse_args()

    download_if_missing(args.relationships_url, args.relationships_path)
    seed_node = (
        args.seed_node
        if args.seed_node is not None
        else choose_seed_node(args.relationships_path, args.seed)
    )
    print(f"Seed node: {seed_node}")

    selected_edges = sample_connected_edges(
        args.relationships_path,
        args.target_edges,
        seed_node,
    )
    node_count, edge_count = write_csvs(
        selected_edges,
        args.nodes_out,
        args.edges_out,
    )

    print(f"Final node count: {node_count}")
    print(f"Final edge count: {edge_count}")


if __name__ == "__main__":
    main()
