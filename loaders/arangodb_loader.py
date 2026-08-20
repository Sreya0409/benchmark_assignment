"""ArangoDB Oasis loader for AQL ingest."""

from __future__ import annotations

import argparse

from loaders.base_loader import GraphLoader


class ArangoDBLoader(GraphLoader):
    """Load benchmark data into ArangoDB Oasis using AQL."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(
            {
                "platform": "arango",
                "env": {
                    "url": "ARANGO_URL",
                    "user": "ARANGO_USER",
                    "password": "ARANGO_PASSWORD",
                    "database": "ARANGO_DB",
                    "graph": "ARANGO_GRAPH",
                    "vertex_collection": "ARANGO_VERTEX_COLLECTION",
                    "edge_collection": "ARANGO_EDGE_COLLECTION",
                },
                **(config or {}),
            }
        )
        self.graph_name = self.connection.get("graph") or "benchmark_graph"
        self.vertex_collection = (
            self.connection.get("vertex_collection") or "users"
        )
        self.edge_collection = self.connection.get("edge_collection") or "follows"

    def connect(self) -> None:
        """Connect to ArangoDB and ensure graph collections exist."""
        from arango import ArangoClient

        client = ArangoClient(hosts=self._required_connection_value("url"))
        self.client = client
        self.db = client.db(
            self._required_connection_value("database"),
            username=self._required_connection_value("user"),
            password=self._required_connection_value("password"),
        )
        self._ensure_graph()

    def _ensure_graph(self) -> None:
        if self.db.has_graph(self.graph_name):
            graph = self.db.graph(self.graph_name)
        else:
            graph = self.db.create_graph(self.graph_name)

        if not self.db.has_collection(self.vertex_collection):
            graph.create_vertex_collection(self.vertex_collection)

        if not self.db.has_collection(self.edge_collection):
            graph.create_edge_definition(
                edge_collection=self.edge_collection,
                from_vertex_collections=[self.vertex_collection],
                to_vertex_collections=[self.vertex_collection],
            )

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
        FOR row IN @batch
            INSERT {
                _key: TO_STRING(row.id),
                id: row.id,
                user_id_original: row.user_id_original,
                region: row.region
            } INTO @@users
        """
        self.db.aql.execute(
            query,
            bind_vars={"batch": prepared_batch, "@users": self.vertex_collection},
        )

    def _insert_edge_batch(self, batch: list[dict[str, str]]) -> None:
        prepared_batch = [
            {
                "source": int(row["source"]),
                "target": int(row["target"]),
            }
            for row in batch
        ]
        query = """
        FOR row IN @batch
            INSERT {
                _from: CONCAT(@users, "/", TO_STRING(row.source)),
                _to: CONCAT(@users, "/", TO_STRING(row.target))
            } INTO @@edges
        """
        self.db.aql.execute(
            query,
            bind_vars={
                "batch": prepared_batch,
                "users": self.vertex_collection,
                "@edges": self.edge_collection,
            },
        )

    def create_indexes(self) -> None:
        """Create the persistent index used by lookup workloads."""
        collection = self.db.collection(self.vertex_collection)
        collection.add_persistent_index(
            fields=["user_id_original"],
            name="user_user_id_original",
            unique=False,
        )

    def close(self) -> None:
        """Close ArangoDB resources."""
        self.client = None
        self.db = None


def main() -> None:
    """Run ArangoDB loading from the command line."""
    parser = argparse.ArgumentParser(
        description="Load CSV benchmark data into ArangoDB Oasis."
    )
    parser.add_argument("nodes_csv_path")
    parser.add_argument("edges_csv_path")
    args = parser.parse_args()

    loader = ArangoDBLoader()
    try:
        loader.connect()
        loader.create_indexes()
        print(loader.load_nodes(args.nodes_csv_path))
        print(loader.load_edges(args.edges_csv_path))
    finally:
        loader.close()


if __name__ == "__main__":
    main()
