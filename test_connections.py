"""Run safe, independent connectivity checks for configured graph databases."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable

from dotenv import load_dotenv

from loaders.cognodb_loader import CognoDBLoader
from loaders.memgraph_loader import MemgraphLoader
from loaders.neo4j_loader import Neo4jLoader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check graph database connections.")
    parser.add_argument(
        "--only",
        choices=(
            "cognodb",
            "aura",
            "memgraph",
            "puppygraph",
            "falkordb",
            "arango",
        ),
    )
    return parser.parse_args()


def short_reason(error: Exception) -> str:
    """Return an error category without including URIs or credentials."""
    message = str(error).lower()
    if "incomplete handshake" in message:
        return "Bolt handshake was closed by the endpoint"
    if "authentication" in message or "unauthorized" in message:
        return "authentication was rejected"
    if "routing" in message:
        return "routing could not be resolved"
    if "dns" in message or "resolve" in message:
        return "host could not be resolved"
    if "timed out" in message or "timeout" in message:
        return "connection timed out"
    return type(error).__name__


def check_bolt(loader_cls) -> None:
    loader = loader_cls()
    try:
        loader.connect()
        with loader.driver.session(**loader._session_kwargs()) as session:
            session.run("RETURN 1 AS ok").consume()
    finally:
        loader.close()


def check_aura() -> None:
    """Validate Aura using the loader's configured-database fallback behavior."""
    loader = Neo4jLoader()
    try:
        loader.connect()
        try:
            with loader.driver.session(**loader._session_kwargs()) as session:
                session.run("RETURN 1 AS ok").consume()
        except Exception as error:
            if not loader._is_database_not_found(error):
                raise
            loader.connection["database"] = None
            loader.used_default_database = True
            with loader.driver.session() as session:
                session.run("RETURN 1 AS ok").consume()
    finally:
        loader.close()


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"missing {name}")
    return value


def check_puppygraph() -> None:
    """Check a direct secure Bolt endpoint without routing discovery."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        required_env("PUPPYGRAPH_URL"),
        auth=(required_env("PUPPYGRAPH_USER"), required_env("PUPPYGRAPH_PASSWORD")),
    )
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            session.run("RETURN 1 AS ok").consume()
    finally:
        driver.close()


def check_falkordb() -> None:
    """Check FalkorDB through its native Redis/FalkorDB client."""
    from falkordb import FalkorDB

    client = FalkorDB(
        host=required_env("FALKORDB_HOST"),
        port=int(required_env("FALKORDB_PORT")),
        username=required_env("FALKORDB_USERNAME"),
        password=required_env("FALKORDB_PASSWORD"),
    )
    client.select_graph(required_env("FALKORDB_GRAPH")).query("RETURN 1")


ARANGO_REQUIRED_ENV = ("ARANGO_URL", "ARANGO_USER", "ARANGO_PASSWORD", "ARANGO_DB")


def missing_env(names: tuple[str, ...]) -> list[str]:
    """Return missing configuration keys without reading their values."""
    return [name for name in names if not os.getenv(name)]


def check_arango() -> None:
    """Perform a read-only ArangoDB connection check without creating a graph."""
    from arango import ArangoClient

    client = ArangoClient(hosts=required_env("ARANGO_URL"))
    database = client.db(
        required_env("ARANGO_DB"),
        username=required_env("ARANGO_USER"),
        password=required_env("ARANGO_PASSWORD"),
    )
    database.version()


def run_check(name: str, check: Callable[[], None], verbose_error: bool = False) -> None:
    try:
        check()
    except Exception as error:
        if verbose_error:
            print(f"{name:<12} FAILED")
            print(f"Exception type: {type(error).__name__}")
            print(f"Error: {error}")
        else:
            print(f"{name:<12} FAILED: {short_reason(error)}")
    else:
        print(f"{name:<12} CONNECTED")


def main() -> None:
    args = parse_args()
    load_dotenv()
    checks: list[tuple[str, str, Callable[[], None]]] = [
        ("cognodb", "CognoDB", lambda: check_bolt(CognoDBLoader)),
        ("aura", "Neo4j Aura", check_aura),
        ("memgraph", "Memgraph", lambda: check_bolt(MemgraphLoader)),
        ("puppygraph", "PuppyGraph", check_puppygraph),
        ("falkordb", "FalkorDB", check_falkordb),
        ("arango", "ArangoDB", check_arango),
    ]
    for key, name, check in checks:
        if args.only in (None, key):
            if key == "arango":
                missing = missing_env(ARANGO_REQUIRED_ENV)
                if missing:
                    print(f"{name:<12} SKIPPED: missing required configuration")
                    continue
            run_check(name, check, verbose_error=key == "aura")


if __name__ == "__main__":
    main()
