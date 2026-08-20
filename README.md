# Graph DB Benchmark

Benchmarking CognoDB Cloud against managed graph database platforms.

## Results

### Traversal Latency

| metric | cognodb | aura | memgraph |
| --- | --- | --- | --- |
| 1-hop p50 ms | - | - | - |
| 1-hop p95 ms | - | - | - |
| 2-hop p50 ms | - | - | - |
| 2-hop p95 ms | - | - | - |
| 3-hop p50 ms | - | - | - |
| 3-hop p95 ms | - | - | - |

![Traversal latency](results/charts/traversal_latency.png)

### Lookup Latency

| metric | cognodb | aura | memgraph |
| --- | --- | --- | --- |
| point lookup p50 ms | - | - | - |
| point lookup p95 ms | - | - | - |
| indexed lookup p50 ms | - | - | - |
| indexed lookup p95 ms | - | - | - |

### Aggregation Latency

| metric | cognodb | aura | memgraph |
| --- | --- | --- | --- |
| aggregation p50 ms | - | - | - |
| aggregation p95 ms | - | - | - |

### Ingest Throughput

| metric | cognodb | aura | memgraph |
| --- | --- | --- | --- |
| nodes per second | - | - | - |
| relationships per second | - | - | - |

![Ingest throughput](results/charts/ingest_throughput.png)

### Mixed Read/Write Throughput

| metric | cognodb | aura | memgraph |
| --- | --- | --- | --- |
| c1 qps | - | - | - |
| c10 qps | - | - | - |
| c40 qps | - | - | - |

![Mixed workload QPS](results/charts/mixed_workload_qps.png)

## Analysis

[FILL IN: interpret the numbers above]

## Methodology

- Dataset: SNAP soc-Pokec relationships sampled to roughly 200,000 relationships.
- Sampling: BFS-connected subgraph from a reproducible random seed.
- Read iterations: 3 after warm-up.
- Warm-up iterations: 1.
- Start nodes: shared set saved at `results\start_nodes.json`.
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
python data/prepare_dataset.py --target-edges 200000 --seed 42
bash scripts/run_all.sh
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
- cognodb failed: Failed to read from defunct connection IPv4Address(('db-6d40220d.bravo.databases.cognodb.com', 7687)) (ResolvedIPv4Address(('136.70.132.96', 7687)))
- aura failed: {neo4j_code: Neo.ClientError.Security.Unauthorized} {message: The client is unauthorized due to authentication failure.} {gql_status: 42NFF} {gql_status_description: error: syntax error or access rule violation - permission/access denied. Access denied, see the security logs for details.}
- memgraph failed: {neo4j_code: Memgraph.ClientError.Security.Unauthenticated} {message: Authentication failure} {gql_status: 50N42} {gql_status_description: error: general processing exception - unexpected error. Authentication failure}
