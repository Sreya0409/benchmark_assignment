"""CognoDB Cloud loader for Bolt-compatible Cypher ingest."""

from __future__ import annotations

import argparse

from loaders.base_loader import BoltLoaderMixin, GraphLoader


class CognoDBLoader(BoltLoaderMixin, GraphLoader):
    """Load benchmark data into CognoDB Cloud using the Neo4j Bolt driver."""

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
