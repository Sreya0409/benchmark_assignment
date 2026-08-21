"""Probe small CognoDB edge writes without touching benchmark graph data."""

from __future__ import annotations

import uuid
from pathlib import Path
import sys
import signal
from contextlib import contextmanager

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loaders.cognodb_loader import CognoDBLoader


SMOKE_LABEL = "CognoWriteSmoke"
EDGE_TYPE = "SMOKE_FOLLOWS"
NODE_COUNT = 10
EDGE_BATCH_SIZE = 50
EDGE_COUNTS = (10, 100, 500, 1_000)
TRANSACTION_TIMEOUT_SECONDS = 20


class WriteTimeoutError(TimeoutError):
    """Raised when CognoDB does not acknowledge a smoke-test transaction."""


@contextmanager
def write_timeout():
    """Bound a synchronous smoke write so a defunct server cannot hang tests."""
    def raise_timeout(*_args) -> None:
        raise WriteTimeoutError("transaction did not complete within 20 seconds")

    previous_handler = signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, TRANSACTION_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def run_write(loader: CognoDBLoader, query: str, **parameters: object) -> None:
    """Commit one explicit, independently scoped transaction."""
    with loader.driver.session(**loader._session_kwargs()) as session:
        transaction = session.begin_transaction()
        try:
            with write_timeout():
                transaction.run(
                    query,
                    timeout=TRANSACTION_TIMEOUT_SECONDS,
                    **parameters,
                ).consume()
                transaction.commit()
        except Exception:
            try:
                with write_timeout():
                    transaction.rollback()
            except Exception:
                # A defunct connection can close the transaction before rollback.
                pass
            raise


def cleanup(run_id: str) -> bool:
    """Remove only nodes created by this test, using a fresh connection."""
    loader = CognoDBLoader()
    try:
        loader.connect()
        run_write(
            loader,
            f"MATCH (node:{SMOKE_LABEL} {{run_id: $run_id}}) DETACH DELETE node",
            run_id=run_id,
        )
        return True
    except Exception:
        return False
    finally:
        loader.close()


def connection_is_defunct(error: Exception) -> bool:
    message = str(error).lower()
    return "defunct connection" in message or "incompletecommit" in message


def test_edge_count(edge_count: int) -> tuple[bool, str]:
    """Test one isolated graph with ten nodes and the requested edge count."""
    run_id = f"smoke-{uuid.uuid4()}"
    loader = CognoDBLoader()
    try:
        loader.connect()
        run_write(
            loader,
            f"""
            UNWIND range(0, $node_count - 1) AS id
            CREATE (node:{SMOKE_LABEL} {{run_id: $run_id, id: id}})
            """,
            run_id=run_id,
            node_count=NODE_COUNT,
        )

        edges = [
            {"source": index % NODE_COUNT, "target": (index + 1) % NODE_COUNT}
            for index in range(edge_count)
        ]
        for offset in range(0, edge_count, EDGE_BATCH_SIZE):
            batch_number = offset // EDGE_BATCH_SIZE + 1
            total_batches = (edge_count + EDGE_BATCH_SIZE - 1) // EDGE_BATCH_SIZE
            print(
                f"{edge_count:>4} edges commit {batch_number}/{total_batches}",
                flush=True,
            )
            run_write(
                loader,
                f"""
                UNWIND $batch AS edge
                MATCH (source:{SMOKE_LABEL} {{run_id: $run_id, id: edge.source}})
                MATCH (target:{SMOKE_LABEL} {{run_id: $run_id, id: edge.target}})
                CREATE (source)-[:{EDGE_TYPE}]->(target)
                """,
                run_id=run_id,
                batch=edges[offset : offset + EDGE_BATCH_SIZE],
            )
        return True, "CONNECTED"
    except Exception as error:
        if isinstance(error, WriteTimeoutError):
            return False, "FAILED: transaction timed out"
        if connection_is_defunct(error):
            # Do not retry an ambiguous write.  Reconnect only for cleanup and
            # the next independent scale, so no transaction is retried silently.
            return False, "FAILED: defunct connection during commit"
        return False, f"FAILED: {type(error).__name__}"
    finally:
        loader.close()
        if not cleanup(run_id):
            print(f"{edge_count:>4} edges cleanup failed")


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    for edge_count in EDGE_COUNTS:
        print(f"{edge_count:>4} edges testing", flush=True)
        passed, status = test_edge_count(edge_count)
        print(f"{edge_count:>4} edges {status}", flush=True)
        if not passed:
            # Each following scale reconnects independently; do not hide a
            # server reset by retrying the failed transaction.
            continue


if __name__ == "__main__":
    main()
