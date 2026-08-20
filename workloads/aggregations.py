"""Aggregation and group-by latency workloads."""

from __future__ import annotations

from collections.abc import Callable

from harness.stats import Timer


QueryFn = Callable[..., object]


def default_cypher_aggregation_query(session) -> object:
    """Run the default Cypher region group-by aggregation."""
    return session.run("MATCH (n) RETURN n.region, count(*)").consume()


def run_aggregation(
    session,
    iterations: int,
    warmup: int,
    query_fn: QueryFn | None = None,
) -> list[float]:
    """Run aggregation measurements and return raw elapsed ms values."""
    query = query_fn or default_cypher_aggregation_query

    for _ in range(warmup):
        query(session)

    latencies_ms: list[float] = []
    for _ in range(iterations):
        with Timer() as timer:
            query(session)
        latencies_ms.append(timer.elapsed_ms)

    return latencies_ms
