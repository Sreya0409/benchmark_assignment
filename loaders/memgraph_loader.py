"""Memgraph Cloud loader for Cypher ingest."""

from __future__ import annotations

import argparse
import os

from loaders.base_loader import BoltLoaderMixin, GraphLoader


class MemgraphLoader(BoltLoaderMixin, GraphLoader):
    """Load benchmark data into Memgraph Cloud using the Neo4j Bolt driver."""

    USER_ORIGINAL_ID_INDEX_QUERY = "CREATE INDEX ON :User(user_id_original)"
    USER_ID_INDEX_QUERY = "CREATE INDEX ON :User(id)"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(
            {
                "platform": "memgraph",
                "env": {
                    "uri": "MEMGRAPH_URI",
                    "password": "MEMGRAPH_PASSWORD",
                    "database": "MEMGRAPH_DATABASE",
                },
                **(config or {}),
            }
        )
        self.connection["user"] = os.getenv("MEMGRAPH_USER", "")

    def create_indexes(self) -> None:
        """Create the Memgraph property index used by lookup workloads."""
        # Memgraph's Cypher index DDL differs from Neo4j/Aura's range-index
        # syntax, even though the same Bolt driver and ingest queries work.
        try:
            super().create_indexes()
        except Exception as exc:
            if "already" not in str(exc).lower() and "exist" not in str(exc).lower():
                raise


def main() -> None:
    """Run Memgraph loading from the command line."""
    parser = argparse.ArgumentParser(
        description="Load CSV benchmark data into Memgraph Cloud."
    )
    parser.add_argument("nodes_csv_path")
    parser.add_argument("edges_csv_path")
    args = parser.parse_args()

    loader = MemgraphLoader()
    try:
        loader.connect()
        loader.create_indexes()
        print(loader.load_nodes(args.nodes_csv_path))
        print(loader.load_edges(args.edges_csv_path))
    finally:
        loader.close()


if __name__ == "__main__":
    main()
