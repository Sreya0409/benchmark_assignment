"""Neo4j AuraDB loader for Cypher ingest."""

from __future__ import annotations

import argparse

from loaders.base_loader import BoltLoaderMixin, GraphLoader


class Neo4jLoader(BoltLoaderMixin, GraphLoader):
    """Load benchmark data into Neo4j AuraDB using the official Neo4j driver."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(
            {
                "platform": "aura",
                "env": {
                    "uri": "AURA_URI",
                    "user": "AURA_USER",
                    "password": "AURA_PASSWORD",
                    "database": "AURA_DATABASE",
                },
                **(config or {}),
            }
        )
        self.used_default_database = False

    def connect(self) -> None:
        """Connect to Aura, falling back to its default database if needed."""
        super().connect()
        configured_database = self.connection.get("database")

        try:
            self._verify_query_database(configured_database)
        except Exception as error:
            if not configured_database or not self._is_database_not_found(error):
                raise

            self.connection["database"] = None
            self.used_default_database = True
            self._verify_query_database(None)

    def _verify_query_database(self, database: str | None) -> None:
        session_kwargs = {"database": database} if database else {}
        with self.driver.session(**session_kwargs) as session:
            session.run("RETURN 1 AS ok").consume()

    @staticmethod
    def _is_database_not_found(error: Exception) -> bool:
        code = "Neo.ClientError.Database.DatabaseNotFound"
        return getattr(error, "code", None) == code or code in str(error)


def main() -> None:
    """Run Neo4j AuraDB loading from the command line."""
    parser = argparse.ArgumentParser(description="Load CSV benchmark data into AuraDB.")
    parser.add_argument("nodes_csv_path")
    parser.add_argument("edges_csv_path")
    args = parser.parse_args()

    loader = Neo4jLoader()
    try:
        loader.connect()
        loader.create_indexes()
        print(loader.load_nodes(args.nodes_csv_path))
        print(loader.load_edges(args.edges_csv_path))
    finally:
        loader.close()


if __name__ == "__main__":
    main()
