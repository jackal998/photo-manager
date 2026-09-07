"""Check parity between qa/web/testid_constants.py and frontend/src/testids.ts.

Invariant: the committed frontend/src/testids.ts must be identical to what
``scripts/gen_testid_ts.py`` would generate today.  This script enforces
that invariant locally and in CI (via tests/test_testid_parity.py).

Exit codes
----------
0 — in sync
1 — drift detected (diff printed to stderr)

Usage
-----
python scripts/check_testid_parity.py
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Import the shared render function from the generator.
sys.path.insert(0, str(_REPO))
from scripts.gen_testid_ts import render_testids_ts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    committed_path = _REPO / "frontend" / "src" / "testids.ts"

    if not committed_path.exists():
        print(
            f"ERROR: {committed_path} does not exist. "
            "Run `python scripts/gen_testid_ts.py` to create it.",
            file=sys.stderr,
        )
        return 1

    generated = render_testids_ts()
    committed = committed_path.read_text(encoding="utf-8")

    if generated == committed:
        print("OK   testid parity: frontend/src/testids.ts is in sync with qa/web/testid_constants.py.")
        return 0

    diff = difflib.unified_diff(
        committed.splitlines(keepends=True),
        generated.splitlines(keepends=True),
        fromfile="frontend/src/testids.ts (committed)",
        tofile="frontend/src/testids.ts (generated)",
    )
    print(
        "FAIL testid parity: frontend/src/testids.ts is out of sync.\n"
        "Run `python scripts/gen_testid_ts.py` to regenerate.\n",
        file=sys.stderr,
    )
    sys.stderr.writelines(diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())
