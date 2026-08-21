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
    dataset_nodes = nested_get(results, "config", "dataset_nodes")
    dataset_edges = nested_get(results, "config", "dataset_edges")
    dataset_description = (
        f"SNAP soc-Pokec relationships; deterministic shared sample of "
        f"{dataset_nodes:,} nodes and {dataset_edges:,} relationships."
        if isinstance(dataset_nodes, int) and isinstance(dataset_edges, int)
        else "SNAP soc-Pokec relationships; see the current CSV files for exact size."
    )
    reduced_note = (
        " This approved reduced benchmark uses 1,000 relationships because "
        "CognoDB could not reliably complete sustained ingestion beyond the "
        "smaller validated dataset."
        if dataset_edges == 1000
        else ""
    )
    lines = [
        "# Graph DB Benchmark",
        "",
        "## Overview",
        "",
        "A reproducible, reduced graph-database benchmark of CognoDB Cloud and four comparison platforms using one shared deterministic SNAP soc-Pokec sample.",
        "",
        "For full setup and reproducibility instructions, see [HOW_TO_RUN.md](HOW_TO_RUN.md).",
        "",
        "## Databases Selected",
        "",
        "The final comparison covers CognoDB, Memgraph, FalkorDB, ArangoDB, and TigerGraph. CognoDB is the target platform; the comparison set spans Bolt/Cypher-compatible systems (Memgraph), a Redis-graph implementation (FalkorDB), a multi-model database queried with AQL (ArangoDB), and a schema-first GSQL/REST++ graph system (TigerGraph). This is a technical coverage rationale, not a claim that the managed deployments have equivalent hardware, tier, or region.",
        "",
        "## Results",
        "",
        "## Benchmark Summary",
        "",
        render_summary_table(results, platforms),
        "",
        "CognoDB fresh-ingestion throughput is **Unavailable**: its isolated namespace could not be safely reset after a relationship-delete/commit inconsistency. Missing ingestion values are not treated as zero.",
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
        "### Lookup, Concurrency, and Footprint Metadata",
        "",
        render_platform_metadata_table(results, platforms),
        "",
        "## Database Comparison",
        "",
        render_database_comparison(results, platforms),
        "",
        "## Benchmark Analysis",
        "",
        render_benchmark_analysis(results, platforms),
        "",
        "## Analysis",
        "",
        "These measurements are comparative observations from the same client "
        "and the same reduced dataset, not capacity claims. Network latency, "
        "managed-tier limits, query engines, indexing, and ingestion APIs can all "
        "materially affect the observed values.",
        "",
        "## Methodology",
        "",
        f"- Dataset: {dataset_description}{reduced_note}",
        *(
            [f"- Result assembly: {nested_get(results, 'config', 'assembly_note')}"]
            if nested_get(results, "config", "assembly_note")
            else []
        ),
        "- Excluded platforms: Aura was excluded from this reduced run because of intermittent `DatabaseNotFound` availability; PuppyGraph was excluded because its endpoint could not complete a Bolt handshake.",
        "- Sampling: the dataset is a BFS-connected subgraph from a reproducible random seed; each platform uses the same CSV rows and relationship directions.",
        "- Client: all measurements were issued from the same benchmark client machine.",
        "- Logical workloads: the same traversal, lookup, aggregation, and mixed read/write semantics are used on every platform.",
        "- Read iterations: "
        f"{nested_get(results, 'config', 'iterations') or '[FILL IN: read iterations]'} "
        "after warm-up.",
        "- Warm-up iterations: "
        f"{nested_get(results, 'config', 'warmup') or '[FILL IN: warm-up iterations]'}.",
        "- Latency reporting: p50 and p95 are calculated from the 100 measured post-warm-up samples for each read workload.",
        "- Start nodes: the isolated runner uses the same deterministic first 200 dense node IDs from the shared CSV on every platform.",
        "- Lookup property: `user_id_original` on every platform. The isolated TigerGraph run uses a filtered lookup over this schema attribute; its physical secondary-index state is administrator-managed and was not observable from the client.",
        "- Mixed workload: 80% reads / 20% writes at client concurrencies 1, 10, and 40; each run lasts "
        f"{nested_get(results, 'config', 'mixed_duration_seconds') or '[FILL IN]'} seconds.",
        "- Resource / footprint: **Not observable** from the configured client for every platform (deployment tier, region, vCPU, RAM, storage, memory use, and stored data size were not reported); no estimates are reported.",
        "- Fairness limitations: managed/free-tier allocation, deployment region, and network path may differ. The run controls the dataset, logical workloads, client machine, warm-up, sample count, and client concurrency, but does not claim identical hardware or network conditions.",
        "- Network and query-language caveat: Cypher, AQL, and GSQL/REST++ implementations preserve the same logical workload intent but necessarily use platform-native query APIs; observed differences are not proof of causation.",
        "",
        "## Reproduce It Yourself",
        "",
        "```bash",
        "python -m venv .venv",
        "source .venv/bin/activate",
        "pip install -r requirements.txt",
        "cp .env.example .env",
        "# Fill in .env with cloud connection details.",
        "python data/prepare_dataset.py --target-edges 1000 --seed 42",
        "python scripts/run_isolated_1k_benchmark.py",
        "python -m harness.generate_readme",
        "python -m http.server 8765",
        "```",
        "",
        "Open the dashboard at `http://localhost:8765/frontend/`.",
        "",
        "On Windows PowerShell, activate the virtual environment with:",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\Activate.ps1",
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


def render_summary_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render the concise cross-platform reviewer summary."""
    rows = []
    for platform in platforms:
        data = nested_get(results, "platforms", platform) or {}
        ingest = data.get("ingest", {})
        node_rate = nested_get(ingest, "nodes", "nodes_per_second")
        rel_rate = nested_get(ingest, "edges", "rels_per_second")
        rows.append(
            [
                platform,
                "Unavailable" if node_rate is None else format_value(node_rate),
                "Unavailable" if rel_rate is None else format_value(rel_rate),
                format_value(nested_get(data, "workloads", "traversals", "1_hop", "percentiles_ms", "p95")),
                format_value(nested_get(data, "workloads", "indexed_lookup", "percentiles_ms", "p50")),
                format_value(nested_get(data, "workloads", "aggregation", "percentiles_ms", "p95")),
                format_value(nested_get(data, "mixed_concurrent", "c1", "ops_per_second")),
                format_value(nested_get(data, "mixed_concurrent", "c10", "ops_per_second")),
                format_value(nested_get(data, "mixed_concurrent", "c40", "ops_per_second")),
            ]
        )
    return markdown_table(
        [
            "Database",
            "Ingest nodes/s",
            "Ingest rels/s",
            "1-hop p95 ms",
            "Indexed lookup p50 ms",
            "Aggregation p95 ms",
            "Mixed c1 QPS",
            "Mixed c10 QPS",
            "Mixed c40 QPS",
        ],
        rows,
    )


def render_database_comparison(results: dict[str, Any], platforms: list[str]) -> str:
    """Describe each platform using only its recorded measurements."""
    sections: list[str] = []
    for platform in platforms:
        data = nested_get(results, "platforms", platform) or {}
        ingest = data.get("ingest", {})
        node_rate = nested_get(ingest, "nodes", "nodes_per_second")
        rel_rate = nested_get(ingest, "edges", "rels_per_second")
        sections.extend(
            [
                f"### {platform}",
                "- Traversal: 1-hop p95 "
                f"{format_value(nested_get(data, 'workloads', 'traversals', '1_hop', 'percentiles_ms', 'p95'))} ms; "
                "3-hop p95 "
                f"{format_value(nested_get(data, 'workloads', 'traversals', '3_hop', 'percentiles_ms', 'p95'))} ms.",
                "- Lookup and aggregation: indexed lookup p50 "
                f"{format_value(nested_get(data, 'workloads', 'indexed_lookup', 'percentiles_ms', 'p50'))} ms; "
                "aggregation p95 "
                f"{format_value(nested_get(data, 'workloads', 'aggregation', 'percentiles_ms', 'p95'))} ms.",
                "- Mixed workload: c1/c10/c40 = "
                f"{format_value(nested_get(data, 'mixed_concurrent', 'c1', 'ops_per_second'))}/"
                f"{format_value(nested_get(data, 'mixed_concurrent', 'c10', 'ops_per_second'))}/"
                f"{format_value(nested_get(data, 'mixed_concurrent', 'c40', 'ops_per_second'))} QPS.",
                (
                    "- Ingestion: Unavailable; the required clean reset could not be performed safely after CognoDB's relationship-delete/commit inconsistency."
                    if node_rate is None or rel_rate is None
                    else "- Ingestion: "
                    f"{format_value(node_rate)} nodes/s and {format_value(rel_rate)} relationships/s."
                ),
                "- Operations: lookup property `user_id_original`; footprint and instance specifications were **Not observable** from the configured client.",
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def render_benchmark_analysis(results: dict[str, Any], platforms: list[str]) -> str:
    """Summarize measured leaders while separating observations from inference."""
    def best(path: tuple[str, ...], higher_is_better: bool) -> tuple[str, float] | None:
        values = []
        for platform in platforms:
            value = nested_get(results, "platforms", platform, *path)
            if isinstance(value, (int, float)):
                values.append((platform, float(value)))
        if not values:
            return None
        return (max if higher_is_better else min)(values, key=lambda item: item[1])

    traversal = best(("workloads", "traversals", "1_hop", "percentiles_ms", "p95"), False)
    ingest = best(("ingest", "edges", "rels_per_second"), True)
    mixed = best(("mixed_concurrent", "c10", "ops_per_second"), True)
    cogno = nested_get(results, "platforms", "cognodb") or {}
    tiger = nested_get(results, "platforms", "tigergraph") or {}
    return "\n".join(
        [
            "Measured observations:",
            f"- Lowest recorded 1-hop p95 was {traversal[0]} at {format_value(traversal[1])} ms." if traversal else "- No traversal comparison is available.",
            f"- Strongest measured relationship ingestion rate was {ingest[0]} at {format_value(ingest[1])} relationships/s." if ingest else "- No measured ingestion comparison is available.",
            f"- Highest recorded c10 mixed-workload throughput was {mixed[0]} at {format_value(mixed[1])} QPS." if mixed else "- No mixed-workload comparison is available.",
            "- CognoDB completed the read and mixed workloads, but its recorded 1-hop p95 "
            f"({format_value(nested_get(cogno, 'workloads', 'traversals', '1_hop', 'percentiles_ms', 'p95'))} ms) and c10 throughput "
            f"({format_value(nested_get(cogno, 'mixed_concurrent', 'c10', 'ops_per_second'))} QPS) were not the lowest/highest values in this run. Its fresh-ingestion rate is unavailable, not zero.",
            "- TigerGraph completed the final run with 1-hop p95 "
            f"{format_value(nested_get(tiger, 'workloads', 'traversals', '1_hop', 'percentiles_ms', 'p95'))} ms and c10 throughput "
            f"{format_value(nested_get(tiger, 'mixed_concurrent', 'c10', 'ops_per_second'))} QPS.",
            "Plausible explanations, not causal conclusions: the observed differences may reflect database architecture, query execution models, network and client/database region latency, managed/free-tier limits, indexing, and ingestion API differences. No platform is universally best; results vary by workload and deployment conditions.",
        ]
    )


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
        [
            "total load wall-clock seconds",
            *[
                format_value(
                    nested_get(
                        results,
                        "platforms",
                        platform,
                        "ingest",
                        "total_wall_clock_seconds",
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


def render_platform_metadata_table(results: dict[str, Any], platforms: list[str]) -> str:
    """Render the Section 5.2 per-platform method and footprint fields."""
    rows = []
    for platform in platforms:
        data = nested_get(results, "platforms", platform) or {}
        mixed = data.get("mixed_workload", {})
        rows.append(
            [
                platform,
                lookup_access_path(platform, data.get("indexed_property", "user_id_original")),
                f"{mixed.get('read_percent', 80)}% / {mixed.get('write_percent', 20)}%",
                ", ".join(str(value) for value in mixed.get("concurrency_levels", [1, 10, 40])),
                format_resource_usage(data.get("resource_usage")),
            ]
        )
    return markdown_table(
        ["platform", "lookup property / access path", "read/write mix", "client concurrency", "footprint / instance info"],
        rows,
    )


def lookup_access_path(platform: str, property_name: str) -> str:
    """Describe the recorded lookup property without overstating index state."""
    if platform == "tigergraph":
        return f"{property_name} (filtered; schema index not observed)"
    return f"{property_name} (loader-created index)"


def format_resource_usage(value: Any) -> str:
    """Normalize the honest unavailable-resource label for the report."""
    if not value or str(value).lower().startswith("not observable"):
        return "Not observable from the configured client"
    return str(value)


def render_caveats(results: dict[str, Any]) -> str:
    """Render caveats and platform failures found in latest results."""
    caveats = [
        "- This is a reduced benchmark (814 nodes / 1,000 relationships), not the original 100k–500k target.",
        "- CognoDB's c0 instance sustained isolated writes through 1,000 relationships but stalled or timed out at larger sustained-ingestion validations.",
        "- TigerGraph uses schema-first GSQL and REST++ upserts, so setup differs from Bolt/AQL ad hoc inserts.",
        "- TigerGraph's `user_id_original` lookup is a graph-schema filtered lookup in this run; a physical secondary-index state was not observable from the benchmark client.",
        "- Memgraph uses Bolt-compatible ingest queries, but its index DDL differs from Neo4j/Aura.",
    ]

    for platform, data in results.get("platforms", {}).items():
        if data.get("status") == "failed":
            caveats.append(f"- {platform} failed: {data.get('error', 'unknown error')}")
        ingest = data.get("ingest", {})
        if nested_get(ingest, "nodes", "nodes_per_second") is None:
            caveats.append(
                f"- {platform} ingestion throughput and total load time are not recorded: "
                "the isolated namespace was already loaded and no valid fresh ingestion timing is available."
            )

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
