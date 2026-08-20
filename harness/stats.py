"""Benchmark statistics helpers."""

from __future__ import annotations

import time
from collections.abc import Sequence


def percentiles(latencies_ms: list[float], ps: Sequence[int] = (50, 95)) -> dict:
    """Return percentile values for latency measurements in milliseconds."""
    if not latencies_ms:
        raise ValueError("latencies_ms must contain at least one value")

    sorted_latencies = sorted(latencies_ms)
    last_index = len(sorted_latencies) - 1
    values = {}

    for percentile in ps:
        if percentile < 0 or percentile > 100:
            raise ValueError("percentiles must be between 0 and 100")

        rank = (percentile / 100) * last_index
        lower_index = int(rank)
        upper_index = min(lower_index + 1, last_index)
        weight = rank - lower_index
        lower_value = sorted_latencies[lower_index]
        upper_value = sorted_latencies[upper_index]

        values[f"p{percentile}"] = lower_value + (upper_value - lower_value) * weight

    return values


class Timer:
    """Context manager that records elapsed wall-clock time in milliseconds."""

    def __init__(self) -> None:
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.elapsed_ms: float | None = None

    def __enter__(self) -> "Timer":
        self.started_at = time.perf_counter()
        self.ended_at = None
        self.elapsed_ms = None
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.started_at is None:
            raise RuntimeError("Timer exited before it was started")

        self.ended_at = time.perf_counter()
        self.elapsed_ms = (self.ended_at - self.started_at) * 1000
