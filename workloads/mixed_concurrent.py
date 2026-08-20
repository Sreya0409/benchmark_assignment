"""Concurrent mixed read/write throughput workloads."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


SessionFactory = Callable[[], object]
QueryFn = Callable[..., object]


def default_cypher_read_query(session) -> object:
    """Run the default mixed-workload read query."""
    return session.run("MATCH (n:User) RETURN count(n)").consume()


def default_cypher_write_query(session, node_id: int) -> object:
    """Run the default mixed-workload single-node write query."""
    return session.run(
        """
        CREATE (:User {
            id: $id,
            user_id_original: $id,
            region: 'mixed'
        })
        """,
        id=node_id,
    ).consume()


def run_mixed_workload(
    session_factory: SessionFactory,
    duration_seconds: float,
    concurrency: int,
    read_write_ratio: float = 0.8,
    read_query_fn: QueryFn | None = None,
    write_query_fn: QueryFn | None = None,
) -> dict[str, float | int]:
    """Run concurrent mixed read/write workload and return measured throughput."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if read_write_ratio < 0 or read_write_ratio > 1:
        raise ValueError("read_write_ratio must be between 0 and 1")

    read_query = read_query_fn or default_cypher_read_query
    write_query = write_query_fn or default_cypher_write_query
    deadline = time.perf_counter() + duration_seconds
    id_lock = threading.Lock()
    total_ops = 0
    next_write_id = -time.time_ns()

    def worker(worker_id: int) -> int:
        nonlocal next_write_id

        rng = random.Random(worker_id)
        session = session_factory()
        worker_ops = 0

        try:
            while time.perf_counter() < deadline:
                if rng.random() < read_write_ratio:
                    read_query(session)
                else:
                    with id_lock:
                        node_id = next_write_id
                        next_write_id -= 1
                    write_query(session, node_id)

                worker_ops += 1
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                close()

        return worker_ops

    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, worker_id) for worker_id in range(concurrency)]
        for future in futures:
            total_ops += future.result()

    elapsed_seconds = time.perf_counter() - started_at
    return {
        "total_ops": total_ops,
        "ops_per_second": total_ops / elapsed_seconds if elapsed_seconds > 0 else 0.0,
    }
