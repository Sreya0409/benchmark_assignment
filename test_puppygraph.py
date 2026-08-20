"""Run only the safe PuppyGraph connectivity check."""

from __future__ import annotations

import sys

from test_connections import main


if __name__ == "__main__":
    sys.argv[1:] = ["--only", "puppygraph"]
    main()
