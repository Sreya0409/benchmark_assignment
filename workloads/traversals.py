"""Traversal latency workloads for one-, two-, and three-hop queries."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from harness.stats import Timer


QueryFn = Callable[..., object]


def default_cypher_traversal_query(session, start_id: int, hops: int) -> object:
    """Run the default Cypher traversal workload."""
    query = f"MATCH (n {{id: $id}})-[*1..{hops}]-(m) RETURN count(m)"
    return session.run(query, id=start_id).consume()


def run_traversals(
    session,
    start_ids: list,
    hops: int,
    iterations: int,
    warmup: int,
    query_fn: QueryFn | None = None,
) -> list[float]:
    """Run traversal latency measurements and return raw elapsed ms values."""
    if hops < 1:
        raise ValueError("hops must be at least 1")

    query = query_fn or default_cypher_traversal_query
    ids = _require_ids(start_ids)

    for iteration in range(warmup):
        query(session, ids[iteration % len(ids)], hops)

    latencies_ms: list[float] = []
    for iteration in range(iterations):
        with Timer() as timer:
            query(session, ids[iteration % len(ids)], hops)
        latencies_ms.append(timer.elapsed_ms)

    return latencies_ms


def _require_ids(ids: Sequence) -> Sequence:
    if not ids:
        raise ValueError("start_ids must contain at least one id")
    return ids
