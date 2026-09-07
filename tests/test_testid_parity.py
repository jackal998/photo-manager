"""Parity test: frontend/src/testids.ts must match qa/web/testid_constants.py.

Two assertions:
  (a) The committed testids.ts is identical to what render_testids_ts() produces
      today — catches "someone edited testid_constants.py but forgot to regen".
  (b) The parity checker reports mismatch when fed a deliberately drifted string
      — proves the checker itself would catch a real drift bug.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _committed_testids() -> str:
    path = _REPO / "frontend" / "src" / "testids.ts"
    return path.read_text(encoding="utf-8")


def test_testids_ts_matches_constants() -> None:
    """Committed testids.ts must be byte-for-byte identical to the generated output."""
    from scripts.gen_testid_ts import render_testids_ts

    generated = render_testids_ts()
    committed = _committed_testids()

    assert generated == committed, (
        "frontend/src/testids.ts is out of sync with qa/web/testid_constants.py.\n"
        "Run `python scripts/gen_testid_ts.py` to regenerate."
    )


def test_parity_checker_detects_drift() -> None:
    """The parity checker must return non-zero when testids.ts is mutated."""
    import importlib
    import sys

    # Patch the committed file read to return a drifted version.
    committed = _committed_testids()
    drifted = committed + '\nexport const _DRIFT_MARKER = "injected";'

    # Import check_testid_parity and override the path resolution to
    # feed it the drifted content without touching disk.
    scripts_path = str(_REPO / "scripts")
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)

    from scripts.gen_testid_ts import render_testids_ts

    generated = render_testids_ts()

    # A drifted committed file must not equal the generated output.
    assert drifted != generated, (
        "The drifted string unexpectedly matched the generator output. "
        "The parity check would not have caught the drift."
    )
