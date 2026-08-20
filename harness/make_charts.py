"""Generate benchmark charts from results/latest.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "results"
LATEST_RESULTS = RESULTS_DIR / "latest.json"
CHARTS_DIR = RESULTS_DIR / "charts"


def main() -> None:
    """Read latest results and write benchmark PNG charts."""
    results = load_latest_results()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    make_traversal_chart(results, CHARTS_DIR / "traversal_latency.png")
    make_ingest_chart(results, CHARTS_DIR / "ingest_throughput.png")
    make_mixed_qps_chart(results, CHARTS_DIR / "mixed_workload_qps.png")

    print(f"Wrote charts to {CHARTS_DIR}")


def load_latest_results() -> dict[str, Any]:
    """Load results/latest.json."""
    if not LATEST_RESULTS.exists():
        raise FileNotFoundError(f"{LATEST_RESULTS} does not exist")

    with LATEST_RESULTS.open(encoding="utf-8-sig") as input_file:
        return json.load(input_file)


def successful_platforms(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only platforms with successful benchmark results."""
    return {
        platform: data
        for platform, data in results.get("platforms", {}).items()
        if data.get("status") == "ok"
    }


def make_traversal_chart(results: dict[str, Any], output_path: Path) -> None:
    """Plot p50/p95 traversal latency per hop depth per platform."""
    platforms = successful_platforms(results)
    labels: list[str] = []
    p50_values: list[float] = []
    p95_values: list[float] = []

    for platform, data in platforms.items():
        traversals = data.get("workloads", {}).get("traversals", {})
        for hop_label in ("1_hop", "2_hop", "3_hop"):
            percentiles = traversals.get(hop_label, {}).get("percentiles_ms", {})
            if "p50" not in percentiles or "p95" not in percentiles:
                continue

            labels.append(f"{platform}\n{hop_label.replace('_', '-')}")
            p50_values.append(percentiles["p50"])
            p95_values.append(percentiles["p95"])

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 0.7), 5))
    x_positions = list(range(len(labels)))
    bar_width = 0.38
    ax.bar(
        [x - bar_width / 2 for x in x_positions],
        p50_values,
        width=bar_width,
        label="p50",
    )
    ax.bar(
        [x + bar_width / 2 for x in x_positions],
        p95_values,
        width=bar_width,
        label="p95",
    )
    ax.set_title("Traversal Latency by Hop Depth")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, output_path)


def make_ingest_chart(results: dict[str, Any], output_path: Path) -> None:
    """Plot node and relationship ingest throughput per platform."""
    platforms = successful_platforms(results)
    labels: list[str] = []
    node_rates: list[float] = []
    rel_rates: list[float] = []

    for platform, data in platforms.items():
        ingest = data.get("ingest", {})
        nodes_per_second = nested_get(ingest, "nodes", "nodes_per_second")
        rels_per_second = nested_get(ingest, "edges", "rels_per_second")
        if nodes_per_second is None and rels_per_second is None:
            continue

        labels.append(platform)
        node_rates.append(nodes_per_second or 0)
        rel_rates.append(rels_per_second or 0)

    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 1.2), 5))
    x_positions = list(range(len(labels)))
    bar_width = 0.38
    ax.bar(
        [x - bar_width / 2 for x in x_positions],
        node_rates,
        width=bar_width,
        label="nodes/s",
    )
    ax.bar(
        [x + bar_width / 2 for x in x_positions],
        rel_rates,
        width=bar_width,
        label="relationships/s",
    )
    ax.set_title("Ingest Throughput")
    ax.set_ylabel("Rows per second")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, output_path)


def make_mixed_qps_chart(results: dict[str, Any], output_path: Path) -> None:
    """Plot mixed-workload QPS per concurrency level per platform."""
    platforms = successful_platforms(results)
    labels = list(platforms.keys())
    concurrency_labels = ["c1", "c10", "c40"]
    x_positions = list(range(len(labels)))
    bar_width = 0.24

    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 1.2), 5))
    for offset, concurrency_label in enumerate(concurrency_labels):
        values = [
            nested_get(data, "mixed_concurrent", concurrency_label, "ops_per_second")
            or 0
            for data in platforms.values()
        ]
        centered_offset = (offset - 1) * bar_width
        ax.bar(
            [x + centered_offset for x in x_positions],
            values,
            width=bar_width,
            label=concurrency_label,
        )

    ax.set_title("Mixed Read/Write Throughput")
    ax.set_ylabel("Operations per second")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels)
    ax.legend(title="Concurrency")
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, output_path)


def nested_get(value: dict[str, Any], *keys: str) -> Any:
    """Return a nested value or None if any key is absent."""
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def save_figure(fig, output_path: Path) -> None:
    """Save a matplotlib figure as a 150dpi PNG."""
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
