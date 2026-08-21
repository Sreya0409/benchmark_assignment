# How to Run the Reduced Graph Database Benchmark

This repository contains the final reduced benchmark: the same deterministic SNAP soc-Pokec sample of **814 nodes and 1,000 relationships** is used for CognoDB, Memgraph, FalkorDB, ArangoDB, and TigerGraph.

## Prerequisites

- Python 3.11 or later and `pip`.
- A virtual environment is recommended.
- Active services/accounts for CognoDB, Memgraph, FalkorDB, ArangoDB, and TigerGraph.
- Docker is **not required** by this repository. ArangoDB is configured through `ARANGO_URL`, so it can be an existing local Docker service or a managed endpoint.

## Clone and install

```bash
git clone <repository-url>
cd <repository-folder>
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment configuration

Copy the template, then fill in only your own service values. Never commit `.env`.

```bash
cp .env.example .env
```

Required variables for this reduced run:

| Platform | Variables |
| --- | --- |
| CognoDB Cloud | `COGNODB_URI`, `COGNODB_PASSWORD`, `COGNODB_DATABASE` |
| Memgraph Cloud | `MEMGRAPH_URI`, `MEMGRAPH_USER`, `MEMGRAPH_PASSWORD`, `MEMGRAPH_DATABASE` |
| FalkorDB | `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`, `FALKORDB_GRAPH` |
| ArangoDB | `ARANGO_URL`, `ARANGO_USER`, `ARANGO_PASSWORD`, `ARANGO_DB` |
| TigerGraph | `TG_HOST`, `TG_USERNAME`, `TG_PASSWORD`, `TG_GRAPHNAME` |

`ARANGO_GRAPH`, `ARANGO_VERTEX_COLLECTION`, and `ARANGO_EDGE_COLLECTION` remain template placeholders for the general ArangoDB loader. The isolated runner uses its own fresh names: `benchmark_graph_1k`, `users_bench_1k`, and `follows_bench_1k`.

For a local ArangoDB service, set `ARANGO_URL` to its HTTP endpoint and use its database/user/password placeholders in `.env`. The code does not require a particular Docker image or Docker command.

## TigerGraph requirements

Set `TG_GRAPHNAME=Transaction_Fraud`. The administrator-provisioned schema must contain:

- Vertex type `UserBench1K` with `id STRING` as the primary key, `user_id_original UINT`, and `region STRING`.
- Directed edge type `FOLLOWS_BENCH_1K` from `UserBench1K` to `UserBench1K`.

The benchmark user needs graph-scoped `querywriter`/data permissions on `Transaction_Fraud`, including schema read access plus permission to read, create, update, and delete benchmark data. The runner does not create or alter this schema.

## Verify connections

These independent tests are supported by `test_connections.py`:

```bash
python test_connections.py --only cognodb
python test_connections.py --only memgraph
python test_connections.py --only falkordb
python test_connections.py --only arango
```

TigerGraph connectivity is checked by the isolated runner's preflight. The final reduced run intentionally excludes Aura and PuppyGraph.

## Prepare the dataset

The committed final artifacts use a deterministic reduced dataset of 814 nodes and 1,000 relationships. If the local generated CSVs are absent, prepare the reduced input with:

```bash
python data/prepare_dataset.py --target-edges 1000 --seed 42
```

Verify that the generated CSVs contain exactly 814 node rows and 1,000 relationship rows before running the final benchmark.

## Run the benchmark

```bash
python scripts/run_isolated_1k_benchmark.py
```

The runner uses isolated benchmark namespaces. Already-loaded platforms are verified and reused, and already-completed measurements may be reused. This prevents unrelated graph data from contributing to benchmark counts or queries. TigerGraph synthetic vertices created by the mixed workload are cleaned after a run.

The checked-in `results/latest.json` contains the submission measurements. The normal command is deliberately resumable and can reuse those completed values; do not treat a resumed execution as a newly measured run. To collect a distinct experiment, use a separate copy of the repository/results and document it separately.

Do not use ingestion-remeasurement options for the final submission. CognoDB fresh-ingestion throughput remains unavailable because its isolated reset could not be safely completed after a relationship-delete/commit inconsistency.

## Outputs and dashboard

The run writes:

- `results/latest.json`
- `results/charts/`
- `README.md`
- `frontend/`

Serve the repository root:

```bash
python -m http.server 8765
```

Then open <http://localhost:8765/frontend/>.

## Troubleshooting

- **CognoDB ingestion:** do not estimate missing ingestion throughput. The final report records it as unavailable because a safe fresh reset was not possible.
- **TigerGraph:** confirm the exact graph/type names above and refresh the graph-scoped token after granting the benchmark user its data/querywriter permissions.
- **Port 8765 is busy:** use another port, for example `python -m http.server 8766`, then open the corresponding URL.
- **Connection failure:** verify the required variable names are set in `.env`, rerun the platform-specific test above where available, and check that the managed service is running and reachable.

## Security

- Never commit `.env`, passwords, tokens, or credential-bearing connection strings.
- Keep real values only in local `.env`.
- Keep `.env.example` limited to blank placeholders.
