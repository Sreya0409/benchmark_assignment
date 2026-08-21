"""Assemble completed resumable 1K platform measurements into one report."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness.runner import PROJECT_ROOT, write_results


RESULTS = PROJECT_ROOT / "results"
FIRST_RUN = RESULTS / "results_20260821T035750Z.json"
MEMGRAPH_RUN = RESULTS / "results_20260821T040253Z.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    first = load(FIRST_RUN)
    memgraph = load(MEMGRAPH_RUN)
    for source in (first, memgraph):
        config = source.get("config", {})
        if (config.get("dataset_nodes"), config.get("dataset_edges")) != (814, 1000):
            raise RuntimeError(f"{source} is not an approved reduced 1K result")

    results = {
        "started_at_utc": first["started_at_utc"],
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "config": {
            **first["config"],
            "assembly_note": (
                "Resumable run: CognoDB/FalkorDB/ArangoDB and Memgraph were "
                "measured in separate successful passes against the same verified "
                "814-node / 1,000-relationship isolated dataset."
            ),
        },
        "platforms": {
            "cognodb": first["platforms"]["cognodb"],
            "memgraph": memgraph["platforms"]["memgraph"],
            "falkordb": first["platforms"]["falkordb"],
            "arango": first["platforms"]["arango"],
            "tigergraph": memgraph["platforms"]["tigergraph"],
        },
    }
    # Keep result artifacts safe to share; the driver exception may echo a
    # masked token fragment even though the root cause is simply REST-10018.
    results["platforms"]["tigergraph"] = {
        "status": "failed",
        "error": "TigerGraph REST-10018: configured token lacks permission for REST data requests.",
    }
    write_results(results)
    print("Wrote final assembled reduced benchmark results")


if __name__ == "__main__":
    main()
