"""CognoDB Cloud loader for Bolt-compatible Cypher ingest."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from loaders.base_loader import BoltLoaderMixin, GraphLoader


class CognoDBLoader(BoltLoaderMixin, GraphLoader):
    """Load benchmark data into CognoDB Cloud using the Neo4j Bolt driver."""

    # CognoDB edge transactions can take too long at the shared 1,000-row
    # batch size.  Keep node batches unchanged while committing smaller edge
    # batches so interrupted loads can resume promptly.
    EDGE_BATCH_SIZE = 50
    MAX_DEFUNCT_RETRIES = 2
    DRIVER_RECYCLE_BATCHES = 10
    WRITE_TIMEOUT_SECONDS = 45

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(
            {
                "platform": "cognodb",
                "env": {
                    "uri": "COGNODB_URI",
                    "password": "COGNODB_PASSWORD",
                    "database": "COGNODB_DATABASE",
                },
                **(config or {}),
            }
        )
        self.connection["user"] = "cognodb"
        self.edge_reconnects = 0
        self.edge_retries = 0

    def _run_write_batch(self, query: str, batch: list[dict[str, object]]) -> None:
        """Retry an idempotent batch a bounded number of times after a reset."""
        for attempt in range(self.MAX_DEFUNCT_RETRIES + 1):
            try:
                super()._run_write_batch(query, batch)
                return
            except Exception as error:
                if (
                    not self._is_defunct_connection(error)
                    or attempt == self.MAX_DEFUNCT_RETRIES
                ):
                    raise

                # Both node and edge ingest use MERGE, so replaying one batch
                # after an incomplete commit preserves logical graph contents.
                self.close()
                self.connect()

    def load_edges(self, edges_csv_path) -> dict[str, float | int]:
        """Load edges with bounded writes and periodic driver recycling."""
        loaded_count = 0
        started_at = time.perf_counter()
        for batch_number, batch in enumerate(
            self._iter_csv_batches(Path(edges_csv_path), self.EDGE_BATCH_SIZE), start=1
        ):
            if batch_number > 1 and (batch_number - 1) % self.DRIVER_RECYCLE_BATCHES == 0:
                self.close()
                self.connect()
                self.edge_reconnects += 1
            self._write_edge_batch_with_retries(batch)
            loaded_count += len(batch)
            if loaded_count % 500 == 0:
                print(f"CognoDB edges: {loaded_count}", flush=True)
        elapsed = time.perf_counter() - started_at
        return {
            "rels_loaded": loaded_count,
            "wall_clock_seconds": elapsed,
            "rels_per_second": loaded_count / elapsed if elapsed else 0.0,
        }

    def _write_edge_batch_with_retries(self, batch: list[dict[str, str]]) -> None:
        for attempt in range(self.MAX_DEFUNCT_RETRIES + 1):
            try:
                previous_handler = signal.signal(signal.SIGALRM, self._raise_timeout)
                signal.setitimer(signal.ITIMER_REAL, self.WRITE_TIMEOUT_SECONDS)
                try:
                    prepared_batch = [
                        {"source": int(row["source"]), "target": int(row["target"])}
                        for row in batch
                    ]
                    # Bypass this class's generic retry wrapper: this method
                    # owns the watchdog and its single bounded retry policy.
                    super()._run_write_batch(self.EDGE_LOAD_QUERY, prepared_batch)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous_handler)
                return
            except Exception:
                if attempt == self.MAX_DEFUNCT_RETRIES:
                    raise
                self.edge_retries += 1
                self.close()
                self.connect()
                self.edge_reconnects += 1

    @staticmethod
    def _raise_timeout(*_args) -> None:
        raise TimeoutError("CognoDB edge batch timed out")

    @staticmethod
    def _is_defunct_connection(error: Exception) -> bool:
        message = str(error).lower()
        return "defunct connection" in message or "incompletecommit" in message


def main() -> None:
    """Run CognoDB loading from the command line."""
    parser = argparse.ArgumentParser(description="Load CSV benchmark data into CognoDB.")
    parser.add_argument("nodes_csv_path")
    parser.add_argument("edges_csv_path")
    args = parser.parse_args()

    loader = CognoDBLoader()
    try:
        loader.connect()
        loader.create_indexes()
        print(loader.load_nodes(args.nodes_csv_path))
        print(loader.load_edges(args.edges_csv_path))
    finally:
        loader.close()


if __name__ == "__main__":
    main()
