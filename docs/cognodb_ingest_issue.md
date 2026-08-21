# CognoDB sustained-ingestion diagnostic

## Scope

- Dataset: sampled SNAP soc-Pokec graph, 90,679 nodes and 200,000 directed relationships.
- CognoDB tier/specification: not documented in this repository.
- This report contains no credentials, endpoint addresses, usernames, or other secrets.

## Observed behavior

| Isolated relationship write | Result |
| --- | --- |
| 10 edges | completed |
| 100 edges | completed |
| 500 edges | completed |
| 1,000 edges | completed; 20 of 20 commits succeeded |
| 5,000-edge ingest subset | stalled before the first 500-edge progress marker |

The 5,000-edge validation uses the same sampled dataset and temporary local CSVs. It does not run read-latency or concurrency workloads.

## Current CognoDB ingestion strategy

- 50 relationships per independent transaction.
- A fresh Bolt session for each batch.
- Driver recycle every 10 batches (500 relationships).
- Per-batch 45-second watchdog.
- At most two reconnect-and-retry attempts for a failed, idempotent `MERGE` batch.
- Node and relationship writes use `MERGE`, so a replay does not create duplicate logical nodes or relationships.

## Failure mode

Earlier full and reduced loads produced `neo4j.exceptions.IncompleteCommit` / a defunct Bolt connection while committing relationship batches. The latest 5,000-edge validation instead stopped receiving a Bolt response while opening or committing a later relationship transaction; no client exception was emitted before it was manually stopped.

This is not reproducible as a simple edge-count threshold: isolated writes through 1,000 edges complete, while sustained ingestion of 5,000 edges stalls. The evidence indicates a CognoDB server-side sustained-ingestion or Bolt-session limitation rather than invalid data, credentials, or TLS setup.

## Reproduction

```bash
python scripts/test_cognodb_writes.py
python scripts/validate_cognodb_subset.py --edges 5000
```

Do not run the full benchmark until the 5,000-edge validation completes consistently.

## Support request

Provide CognoDB support with this report and ask:

1. Is sustained Bolt ingestion with many small write transactions supported on the c0 free tier, and are there connection, transaction, or rate limits that cause the server to stop responding?
2. Is there a supported bulk or object-storage/CSV import path for a graph of approximately 90,679 nodes and 200,000 relationships?
3. If Bolt is the supported path, what batch size, driver/session lifetime, and retry policy are recommended for the c0 tier?
