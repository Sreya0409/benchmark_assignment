"""TigerGraph Cloud loader for GSQL ingest."""

from __future__ import annotations

import argparse

from loaders.base_loader import GraphLoader


class TigerGraphLoader(GraphLoader):
    """Load benchmark data into TigerGraph Cloud using pyTigerGraph."""

    VERTEX_TYPE = "User"
    EDGE_TYPE = "FOLLOWS"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(
            {
                "platform": "tigergraph",
                "env": {
                    "host": "TG_HOST",
                    "user": "TG_USERNAME",
                    "password": "TG_PASSWORD",
                    "graph": "TG_GRAPHNAME",
                },
                **(config or {}),
            }
        )

    def connect(self) -> None:
        """Connect to TigerGraph and create the benchmark schema if possible."""
        import pyTigerGraph as tg

        self.conn = tg.TigerGraphConnection(
            host=self._required_connection_value("host"),
            graphname=self._required_connection_value("graph"),
            username=self._required_connection_value("user"),
            password=self._required_connection_value("password"),
        )
        self.conn.getToken()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # TigerGraph is schema-first: unlike Bolt/AQL stores, vertex and edge
        # types generally need to exist before REST++ upserts can load data.
        graph_name = self._required_connection_value("graph")
        statements = [
            (
                "CREATE VERTEX User ("
                "PRIMARY_ID id UINT, "
                "user_id_original UINT, "
                "region STRING"
                ') WITH primary_id_as_attribute="true"'
            ),
            "CREATE DIRECTED EDGE FOLLOWS (FROM User, TO User)",
            f"CREATE GRAPH {graph_name} (User, FOLLOWS)",
        ]

        for statement in statements:
            try:
                self.conn.gsql(statement)
            except Exception as exc:
                if "already" not in str(exc).lower() and "exist" not in str(exc).lower():
                    raise

    def _insert_node_batch(self, batch: list[dict[str, str]]) -> None:
        vertices = [
            (
                int(row["id"]),
                {
                    "user_id_original": int(row["user_id_original"]),
                    "region": row["region"],
                },
            )
            for row in batch
        ]
        self.conn.upsertVertices(self.VERTEX_TYPE, vertices)

    def _insert_edge_batch(self, batch: list[dict[str, str]]) -> None:
        edges = [
            (int(row["source"]), int(row["target"]), {})
            for row in batch
        ]
        self.conn.upsertEdges(
            self.VERTEX_TYPE,
            self.EDGE_TYPE,
            self.VERTEX_TYPE,
            edges,
        )

    def create_indexes(self) -> None:
        """Create the secondary index used by lookup workloads."""
        # TigerGraph index DDL is GSQL schema DDL rather than an online Cypher/AQL
        # property-index command, so this may require schema-change privileges.
        try:
            self.conn.gsql(
                "CREATE INDEX user_user_id_original "
                "ON VERTEX User(user_id_original)"
            )
        except Exception as exc:
            if "already" not in str(exc).lower() and "exist" not in str(exc).lower():
                raise

    def close(self) -> None:
        """Close TigerGraph resources."""
        self.conn = None


def main() -> None:
    """Run TigerGraph loading from the command line."""
    parser = argparse.ArgumentParser(
        description="Load CSV benchmark data into TigerGraph Cloud."
    )
    parser.add_argument("nodes_csv_path")
    parser.add_argument("edges_csv_path")
    args = parser.parse_args()

    loader = TigerGraphLoader()
    try:
        loader.connect()
        loader.create_indexes()
        print(loader.load_nodes(args.nodes_csv_path))
        print(loader.load_edges(args.edges_csv_path))
    finally:
        loader.close()


if __name__ == "__main__":
    main()
