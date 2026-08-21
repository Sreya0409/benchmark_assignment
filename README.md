# Graph DB Benchmark

Benchmarking CognoDB Cloud against managed graph database platforms.

## Results

### Traversal Latency

| metric | cognodb | memgraph | falkordb | arango | tigergraph |
| --- | --- | --- | --- | --- | --- |
| 1-hop p50 ms | 359.15 | 256.24 | 67.96 | 2.81 | 322.58 |
| 1-hop p95 ms | 454.32 | 327.69 | 111.62 | 4.24 | 368.89 |
| 2-hop p50 ms | 398.15 | 236.20 | 78.47 | 3.05 | 328.39 |
| 2-hop p95 ms | 496.56 | 312.47 | 182.16 | 4.67 | 411.37 |
| 3-hop p50 ms | 339.85 | 282.86 | 74.46 | 3.63 | 335.24 |
| 3-hop p95 ms | 480.45 | 332.65 | 190.09 | 5.43 | 415.39 |

![Traversal latency](results/charts/traversal_latency.png)

### Lookup Latency

| metric | cognodb | memgraph | falkordb | arango | tigergraph |
| --- | --- | --- | --- | --- | --- |
| point lookup p50 ms | 320.12 | 235.62 | 64.11 | 2.34 | 307.45 |
| point lookup p95 ms | 412.11 | 309.01 | 122.21 | 3.56 | 410.32 |
| indexed lookup p50 ms | 355.78 | 289.35 | 63.97 | 1.16 | 352.90 |
| indexed lookup p95 ms | 488.98 | 409.44 | 86.52 | 2.96 | 493.30 |

### Aggregation Latency

| metric | cognodb | memgraph | falkordb | arango | tigergraph |
| --- | --- | --- | --- | --- | --- |
| aggregation p50 ms | 349.99 | 253.31 | 91.60 | 2.01 | 307.52 |
| aggregation p95 ms | 413.48 | 319.87 | 182.23 | 3.13 | 411.81 |

### Ingest Throughput

| metric | cognodb | memgraph | falkordb | arango | tigergraph |
| --- | --- | --- | --- | --- | --- |
| nodes per second | - | 864.41 | 4435.53 | 35438.75 | - |
| relationships per second | - | 1045.43 | 5515.57 | 33893.23 | - |

![Ingest throughput](results/charts/ingest_throughput.png)

### Mixed Read/Write Throughput

| metric | cognodb | memgraph | falkordb | arango | tigergraph |
| --- | --- | --- | --- | --- | --- |
| c1 qps | 0.49 | 3.98 | 10.42 | 370.10 | 2.93 |
| c10 qps | 4.70 | 38.44 | 73.99 | 2291.46 | 28.99 |
| c40 qps | 16.67 | 166.43 | 135.52 | 2227.62 | 78.00 |

![Mixed workload QPS](results/charts/mixed_workload_qps.png)

## Analysis

[FILL IN: interpret the numbers above]

## Methodology

- Dataset: SNAP soc-Pokec relationships; deterministic shared sample of 814 nodes and 1,000 relationships. This approved reduced benchmark uses 1,000 relationships because CognoDB could not reliably complete sustained ingestion beyond the smaller validated dataset.
- Excluded platforms: Aura was excluded from this reduced run because of intermittent `DatabaseNotFound` availability; PuppyGraph was excluded because its endpoint could not complete a Bolt handshake.
- Sampling: BFS-connected subgraph from a reproducible random seed.
- Read iterations: 100 after warm-up.
- Warm-up iterations: 10.
- Start nodes: shared set saved at `results/start_nodes.json`.
- CognoDB Cloud resource tier: [FILL IN: instance size, limits, region].
- Neo4j AuraDB Free resource tier: [FILL IN: instance size, limits, region].
- Memgraph Cloud resource tier: [FILL IN: instance size, limits, region].
- ArangoDB Oasis resource tier: [FILL IN: instance size, limits, region].
- TigerGraph Cloud resource tier: [FILL IN: instance size, limits, region].
- Footprint measurement: [FILL IN: how storage/memory footprint was observed].

## Reproduce It Yourself

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with cloud connection details.
python data/prepare_dataset.py --target-edges 1000 --seed 42
python scripts/run_isolated_1k_benchmark.py
python -m harness.generate_readme
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

To rerun timings against already-loaded databases:

```bash
python -m harness.runner --skip-load
python -m harness.make_charts
python -m harness.generate_readme
```

## Caveats

- TigerGraph uses schema-first GSQL and REST++ upserts, so setup differs from Bolt/AQL ad hoc inserts.
- Memgraph uses Bolt-compatible ingest queries, but its index DDL differs from Neo4j/Aura.
- [FILL IN: additional benchmark caveats after reviewing the run].
