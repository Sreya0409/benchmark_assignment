"""Point and indexed lookup latency workloads."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from harness.stats import Timer


QueryFn = Callable[..., object]


def default_cypher_point_lookup_query(session, value) -> object:
    """Run the default Cypher lookup on the unindexed dense id property."""
    return session.run("MATCH (n {id: $value}) RETURN n LIMIT 1", value=value).consume()


def default_cypher_indexed_lookup_query(session, value) -> object:
    """Run the default Cypher lookup on indexed user_id_original."""
    return session.run(
        "MATCH (n:User {user_id_original: $value}) RETURN n LIMIT 1",
        value=value,
    ).consume()


def run_point_lookup(
    session,
    lookup_values: list,
    iterations: int,
    warmup: int,
    query_fn: QueryFn | None = None,
) -> list[float]:
    """Run unindexed point lookup measurements and return raw elapsed ms values."""
    query = query_fn or default_cypher_point_lookup_query
    return _run_lookup(session, lookup_values, iterations, warmup, query)


def run_indexed_lookup(
    session,
    lookup_values: list,
    iterations: int,
    warmup: int,
    query_fn: QueryFn | None = None,
) -> list[float]:
    """Run indexed lookup measurements and return raw elapsed ms values."""
    query = query_fn or default_cypher_indexed_lookup_query
    return _run_lookup(session, lookup_values, iterations, warmup, query)


def _run_lookup(
    session,
    lookup_values: Sequence,
    iterations: int,
    warmup: int,
    query_fn: QueryFn,
) -> list[float]:
    values = _require_values(lookup_values)

    for iteration in range(warmup):
        query_fn(session, values[iteration % len(values)])

    latencies_ms: list[float] = []
    for iteration in range(iterations):
        with Timer() as timer:
            query_fn(session, values[iteration % len(values)])
        latencies_ms.append(timer.elapsed_ms)

    return latencies_ms


def _require_values(values: Sequence) -> Sequence:
    if not values:
        raise ValueError("lookup_values must contain at least one value")
    return values
