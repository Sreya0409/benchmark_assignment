"""FalkorDB Cloud loader for Cypher-compatible ingest."""

from __future__ import annotations

import argparse
import os

from loaders.base_loader import GraphLoader


class FalkorDBLoader(GraphLoader):
    """Load benchmark data into FalkorDB using the FalkorDB Python client."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(
            {
                "platform": "falkordb",
                "env": {
                    "graph": "FALKORDB_GRAPH",
                    "host": "FALKORDB_HOST",
                    "instance_id": "FALKORDB_INSTANCE_ID",
                    "port": "FALKORDB_PORT",
                    "user": "FALKORDB_USERNAME",
                    "password": "FALKORDB_PASSWORD",
                },
                **(config or {}),
            }
        )
        self.connection["database"] = self.connection.get("graph") or os.getenv(
            "FALKORDB_DATABASE"
        )
        self.connection["user"] = self.connection.get("user") or os.getenv(
            "FALKORDB_USER"
        )
        if not self.connection.get("password"):
            self.connection["password"] = os.getenv("FALKORDB_PASSWORD") or os.getenv(
                "FALKORDB"
            )

    def connect(self) -> None:
        """Connect to FalkorDB and select the benchmark graph."""
        from falkordb import FalkorDB

        host = self.connection.get("host") or self._required_connection_value(
            "instance_id"
        )
        port = int(self.connection.get("port") or 6379)
        username = self.connection.get("user") or None

        self.client = FalkorDB(
            host=host,
            port=port,
            username=username,
            password=self._required_connection_value("password"),
        )
        self.graph = self.client.select_graph(
            self._required_connection_value("database")
        )
        self.graph.query("RETURN 1")

    def _insert_node_batch(self, batch: list[dict[str, str]]) -> None:
        prepared_batch = [
            {
                "id": int(row["id"]),
                "user_id_original": int(row["user_id_original"]),
                "region": row["region"],
            }
            for row in batch
        ]
        query = """
        UNWIND $batch AS row
        MERGE (user:User {id: row.id})
        SET user.user_id_original = row.user_id_original,
            user.region = row.region
        """
        self.graph.query(query, {"batch": prepared_batch})

    def _insert_edge_batch(self, batch: list[dict[str, str]]) -> None:
        prepared_batch = [
            {
                "source": int(row["source"]),
                "target": int(row["target"]),
            }
            for row in batch
        ]
        query = """
        UNWIND $batch AS row
        MATCH (source:User {id: row.source})
        MATCH (target:User {id: row.target})
        MERGE (source)-[:FOLLOWS]->(target)
        """
        self.graph.query(query, {"batch": prepared_batch})

    def create_indexes(self) -> None:
        """Create the property index used by lookup workloads."""
        try:
            self.graph.query("CREATE INDEX ON :User(user_id_original)")
            self.graph.query("CREATE INDEX ON :User(id)")
        except Exception as exc:
            if "already" not in str(exc).lower() and "exist" not in str(exc).lower():
                raise

    def close(self) -> None:
        """Close FalkorDB resources."""
        self.graph = None
        self.client = None


def main() -> None:
    """Run FalkorDB loading from the command line."""
    parser = argparse.ArgumentParser(
        description="Load CSV benchmark data into FalkorDB Cloud."
    )
    parser.add_argument("nodes_csv_path")
    parser.add_argument("edges_csv_path")
    args = parser.parse_args()

    loader = FalkorDBLoader()
    try:
        loader.connect()
        loader.create_indexes()
        print(loader.load_nodes(args.nodes_csv_path))
        print(loader.load_edges(args.edges_csv_path))
    finally:
        loader.close()


if __name__ == "__main__":
    main()
