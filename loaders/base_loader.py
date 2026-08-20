"""Abstract loader interface for benchmark database adapters."""

from __future__ import annotations

import csv
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path


class GraphLoader(ABC):
    """Base contract for loading graph benchmark data into a database."""

    BATCH_SIZE = 1_000

    def __init__(self, config: dict) -> None:
        """Initialize a loader and resolve platform connection env vars."""
        self.config = config
        self.platform = config.get("platform", self.__class__.__name__)
        self.env_prefix = config.get("env_prefix", self.platform).upper()
        self.connection = self._read_connection_env(config)

    def _read_connection_env(self, config: dict) -> dict[str, str | None]:
        env_mapping = config.get("env", {})
        if env_mapping:
            return {
                config_key: os.getenv(env_var)
                for config_key, env_var in env_mapping.items()
            }

        keys = (
            "URI",
            "URL",
            "HOST",
            "USER",
            "PASSWORD",
            "DATABASE",
            "GRAPH",
            "SECRET",
            "TOKEN",
            "VERTEX_COLLECTION",
            "EDGE_COLLECTION",
        )
        return {
            key.lower(): os.getenv(f"{self.env_prefix}_{key}")
            for key in keys
            if os.getenv(f"{self.env_prefix}_{key}") is not None
        }

    def _required_connection_value(self, key: str) -> str:
        value = self.connection.get(key)
        if value:
            return value

        raise ValueError(
            f"Missing {key!r} connection value for {self.platform}. "
            "Check the required environment variables."
        )

    @abstractmethod
    def connect(self) -> None:
        """Connect to the target graph database."""

    def load_nodes(self, nodes_csv_path) -> dict[str, float | int]:
        """Load nodes from CSV in batches and report ingest throughput."""
        return self._load_csv(
            nodes_csv_path,
            insert_batch=self._insert_node_batch,
            count_key="nodes_loaded",
            rate_key="nodes_per_second",
        )

    def load_edges(self, edges_csv_path) -> dict[str, float | int]:
        """Load edges from CSV in batches and report ingest throughput."""
        return self._load_csv(
            edges_csv_path,
            insert_batch=self._insert_edge_batch,
            count_key="rels_loaded",
            rate_key="rels_per_second",
        )

    def _load_csv(
        self,
        csv_path,
        insert_batch: Callable[[list[dict[str, str]]], None],
        count_key: str,
        rate_key: str,
    ) -> dict[str, float | int]:
        path = Path(csv_path)
        loaded_count = 0
        started_at = time.perf_counter()

        for batch in self._iter_csv_batches(path):
            insert_batch(batch)
            loaded_count += len(batch)

        wall_clock_seconds = time.perf_counter() - started_at
        rows_per_second = (
            loaded_count / wall_clock_seconds if wall_clock_seconds > 0 else 0.0
        )

        return {
            count_key: loaded_count,
            "wall_clock_seconds": wall_clock_seconds,
            rate_key: rows_per_second,
        }

    def _iter_csv_batches(self, csv_path: Path) -> Iterator[list[dict[str, str]]]:
        with csv_path.open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            batch: list[dict[str, str]] = []

            for row in reader:
                batch.append(row)
                if len(batch) == self.BATCH_SIZE:
                    yield batch
                    batch = []

            if batch:
                yield batch

    @abstractmethod
    def _insert_node_batch(self, batch: list[dict[str, str]]) -> None:
        """Insert one batch of node rows into the target graph database."""

    @abstractmethod
    def _insert_edge_batch(self, batch: list[dict[str, str]]) -> None:
        """Insert one batch of edge rows into the target graph database."""

    @abstractmethod
    def create_indexes(self) -> None:
        """Create indexes needed for benchmark lookups."""

    @abstractmethod
    def close(self) -> None:
        """Close open database resources."""


BaseLoader = GraphLoader


class BoltLoaderMixin:
    """Shared Neo4j-driver implementation for Bolt-compatible graph databases."""

    NODE_LOAD_QUERY = """
    UNWIND $batch AS row
    MERGE (user:User {id: row.id})
    SET user.user_id_original = row.user_id_original,
        user.region = row.region
    """
    EDGE_LOAD_QUERY = """
    UNWIND $batch AS row
    MATCH (source:User {id: row.source})
    MATCH (target:User {id: row.target})
    MERGE (source)-[:FOLLOWS]->(target)
    """
    USER_ORIGINAL_ID_INDEX_QUERY = """
    CREATE INDEX user_user_id_original IF NOT EXISTS
    FOR (u:User) ON (u.user_id_original)
    """
    USER_ID_INDEX_QUERY = """
    CREATE INDEX user_id IF NOT EXISTS
    FOR (u:User) ON (u.id)
    """

    def connect(self) -> None:
        """Connect to a Bolt-compatible graph database."""
        import certifi
        from neo4j import GraphDatabase

        uri = self._required_connection_value("uri")
        user = self.connection.get("user")
        if user is None:
            user = self._required_connection_value("user")
        password = self._required_connection_value("password")

        # Python installations on macOS do not always include a usable system
        # CA store.  Point OpenSSL at Certifi's verified CA bundle while still
        # preserving the URI's normal certificate and hostname verification.
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()

    def _session_kwargs(self) -> dict[str, str]:
        database = self.connection.get("database")
        return {"database": database} if database else {}

    def _insert_node_batch(self, batch: list[dict[str, str]]) -> None:
        prepared_batch = [
            {
                "id": int(row["id"]),
                "user_id_original": int(row["user_id_original"]),
                "region": row["region"],
            }
            for row in batch
        ]
        self._run_write_batch(self.NODE_LOAD_QUERY, prepared_batch)

    def _insert_edge_batch(self, batch: list[dict[str, str]]) -> None:
        prepared_batch = [
            {
                "source": int(row["source"]),
                "target": int(row["target"]),
            }
            for row in batch
        ]
        self._run_write_batch(self.EDGE_LOAD_QUERY, prepared_batch)

    def _run_write_batch(self, query: str, batch: list[dict[str, object]]) -> None:
        with self.driver.session(**self._session_kwargs()) as session:
            tx = session.begin_transaction()
            try:
                tx.run(query, batch=batch).consume()
                tx.commit()
            except Exception:
                tx.rollback()
                raise

    def create_indexes(self) -> None:
        """Create the User.user_id_original index used by lookup workloads."""
        with self.driver.session(**self._session_kwargs()) as session:
            tx = session.begin_transaction()
            try:
                tx.run(self.USER_ORIGINAL_ID_INDEX_QUERY).consume()
                tx.run(self.USER_ID_INDEX_QUERY).consume()
                tx.commit()
            except Exception:
                tx.rollback()
                raise

    def close(self) -> None:
        """Close the Neo4j driver."""
        if getattr(self, "driver", None) is not None:
            self.driver.close()
