"""Generate README.md from the latest benchmark results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"
RESULTS_DIR = PROJECT_ROOT / "results"
LATEST_RESULTS = RESULTS_DIR / "latest.json"


def main() -> None:
    """Render README.md from results/latest.json."""
    results = load_latest_results()
    README_PATH.write_text(render_readme(results), encoding="utf-8")
    print(f"Wrote {README_PATH}")


def load_latest_results() -> dict[str, Any]:
    """Load the latest benchmark result JSON."""
    if not LATEST_RESULTS.exists():
        raise FileNotFoundError(f"{LATEST_RESULTS} does not exist")

    with LATEST_RESULTS.open(encoding="utf-8-sig") as input_file:
        return json.load(input_file)


def render_readme(results: dict[str, Any]) -> str:
    """Render the README markdown document."""
    platforms = list(results.get("platforms", {}).keys())
    lines = [
        "# Graph DB Benchmark",
        "",
        "Benchmarking CognoDB Cloud against managed graph database platforms.",
        "",
        "## Results",
        "",
        "### Traversal Latency",
        "",
        render_traversal_table(results, platforms),
        "",
        "![Traversal latency](results/charts/traversal_latency.png)",
        "",
        "### Lookup Latency",
        "",
        render_lookup_table(results, platforms),
        "",
        "### Aggregation Latency",
        "",
        render_aggregation_table(results, platforms),
        "",
        "### Ingest Throughput",
        "",
        render_ingest_table(results, platforms),
        "",
        "![Ingest throughput](results/charts/ingest_throughput.png)",
        "",
        "### Mixed Read/Write Throughput",
        "",
        render_mixed_table(results, platforms),
        "",
        "![Mixed workload QPS](results/charts/mixed_workload_qps.png)",
        "",
        "## Analysis",
        "",
        "[FILL IN: interpret the numbers above]",
        "",
        "## Methodology",
        "",
        "- Dataset: SNAP soc-Pokec relationships sampled to roughly 200,000 relationships.",
        "- Sampling: BFS-connected subgraph from a reproducible random seed.",
        "- Read iterations: "
        f"{nested_get(results, 'config', 'iterations') or '[FILL IN: read iterations]'} "
        "after warm-up.",
        "- Warm-up iterations: "
        f"{nested_get(results, 'config', 'warmup') or '[FILL IN: warm-up iterations]'}.",
        "- Start nodes: shared set saved at "
        f"`{nested_get(results, 'config', 'start_nodes_path') or 'results/start_nodes.json'}`.",
        "- CognoDB Cloud resource tier: [FILL IN: instance size, limits, region].",
        "- Neo4j AuraDB Free resource tier: [FILL IN: instance size, limits, region].",
        "- Memgraph Cloud resource tier: [FILL IN: instance size, limits, region].",
        "- ArangoDB Oasis resource tier: [FILL IN: instance size, limits, region].",
        "- TigerGraph Cloud resource tier: [FILL IN: instance size, limits, region].",
        "- Footprint measurement: [FILL IN: how storage/memory footprint was observed].",
        "",
        "## Reproduce It Yourself",
        "",
        "```bash",
        "python -m venv .venv",
        "source .venv/bin/activate",
        "pip install -r requirements.txt",
        "cp .env.example .env",
        "# Fill in .env with cloud connection details.",
        "python data/prepare_dataset.py --target-edges 200000 --seed 42",
        "bash scripts/run_all.sh",
        "python -m harness.generate_readme",
        "```",
        "",
        "On Windows PowerShell, activate the virtual environment with:",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\Activate.ps1",
        "```",
        "",
        "To rerun timings against already-loaded databases:",
        "",
        "```bash",
        "python -m harness.runner --skip-load",
        "python -m harness.make_charts",
        "python -m harness.generate_readme",
        "```",
        "",
        "## Caveats",
        "",
        render_caveats(results),
        "",
    ]
    return "\n".join(lines)


def render_traversal_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render traversal p50/p95 rows by hop depth."""
    rows = []
    for hop_label in ("1_hop", "2_hop", "3_hop"):
        for percentile in ("p50", "p95"):
            rows.append(
                [
                    f"{hop_label.replace('_', '-')} {percentile} ms",
                    *[
                        format_value(
                            nested_get(
                                results,
                                "platforms",
                                platform,
                                "workloads",
                                "traversals",
                                hop_label,
                                "percentiles_ms",
                                percentile,
                            )
                        )
                        for platform in platforms
                    ],
                ]
            )
    return markdown_table(["metric", *platforms], rows)


def render_lookup_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render lookup p50/p95 rows."""
    rows = []
    for workload in ("point_lookup", "indexed_lookup"):
        for percentile in ("p50", "p95"):
            rows.append(
                [
                    f"{workload.replace('_', ' ')} {percentile} ms",
                    *[
                        format_value(
                            nested_get(
                                results,
                                "platforms",
                                platform,
                                "workloads",
                                workload,
                                "percentiles_ms",
                                percentile,
                            )
                        )
                        for platform in platforms
                    ],
                ]
            )
    return markdown_table(["metric", *platforms], rows)


def render_aggregation_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render aggregation p50/p95 rows."""
    rows = []
    for percentile in ("p50", "p95"):
        rows.append(
            [
                f"aggregation {percentile} ms",
                *[
                    format_value(
                        nested_get(
                            results,
                            "platforms",
                            platform,
                            "workloads",
                            "aggregation",
                            "percentiles_ms",
                            percentile,
                        )
                    )
                    for platform in platforms
                ],
            ]
        )
    return markdown_table(["metric", *platforms], rows)


def render_ingest_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render ingest throughput rows."""
    rows = [
        [
            "nodes per second",
            *[
                format_value(
                    nested_get(
                        results,
                        "platforms",
                        platform,
                        "ingest",
                        "nodes",
                        "nodes_per_second",
                    )
                )
                for platform in platforms
            ],
        ],
        [
            "relationships per second",
            *[
                format_value(
                    nested_get(
                        results,
                        "platforms",
                        platform,
                        "ingest",
                        "edges",
                        "rels_per_second",
                    )
                )
                for platform in platforms
            ],
        ],
    ]
    return markdown_table(["metric", *platforms], rows)


def render_mixed_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render mixed workload throughput rows."""
    rows = []
    for concurrency in ("c1", "c10", "c40"):
        rows.append(
            [
                f"{concurrency} qps",
                *[
                    format_value(
                        nested_get(
                            results,
                            "platforms",
                            platform,
                            "mixed_concurrent",
                            concurrency,
                            "ops_per_second",
                        )
                    )
                    for platform in platforms
                ],
            ]
        )
    return markdown_table(["metric", *platforms], rows)


def render_caveats(results: dict[str, Any]) -> str:
    """Render caveats and platform failures found in latest results."""
    caveats = [
        "- TigerGraph uses schema-first GSQL and REST++ upserts, so setup differs from Bolt/AQL ad hoc inserts.",
        "- Memgraph uses Bolt-compatible ingest queries, but its index DDL differs from Neo4j/Aura.",
    ]

    for platform, data in results.get("platforms", {}).items():
        if data.get("status") == "failed":
            caveats.append(f"- {platform} failed: {data.get('error', 'unknown error')}")

    if len(caveats) == 2:
        caveats.append("- [FILL IN: additional benchmark caveats after reviewing the run].")

    return "\n".join(caveats)


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a markdown table."""
    escaped_headers = [escape_cell(header) for header in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def nested_get(value: dict[str, Any], *keys: str) -> Any:
    """Return a nested value or None if any key is absent."""
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def format_value(value: Any) -> str:
    """Format a README table value."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def escape_cell(value: Any) -> str:
    """Escape markdown table cell separators."""
    return str(value).replace("|", "\\|")


if __name__ == "__main__":
    main()
