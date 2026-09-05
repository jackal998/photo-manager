"""Web scenario s37 — Status-bar baseline: idle at startup, summary after load.

Ported from qa/scenarios/s37_status_bar_baseline.py (Qt UIA).

Qt intent (#138, #140):
  #138 — The startup ``Ready`` status must persist beyond a 3 s window
          (originally implemented as a timed ``showMessage``).
  #140 — After a manifest load, opening + dismissing the File menu must
          NOT wipe the load-summary text from the status bar.

Web-observable assertions:
  1. On initial page load the status bar shows a stable idle/ready text
     (``assert_status_idle``).
  2. After a scan completes the status bar shows the ``"N groups · M files"``
     manifest-loaded text (``wait_manifest_loaded``).

Qt divergences:
  - Qt probes for the 'Ready' string 3.5 s after startup to confirm the
    old 3 s timeout bug (#138) is not present.  The web status bar is
    always backed by React state (not a timed Qt showMessage), so no
    3 s sleep is needed — the idle check is instantaneous.
  - Qt opens and dismisses the File menu to trigger Qt action-hover
    ``showMessage(statusTip)`` events (#140).  The web UI has no native
    File menu producing those events; the status-bar React state is not
    overwritten on menu hover.  This probe has no faithful web equivalent
    and is omitted; the web port asserts only the idle → scan-done
    transition.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    assert_status_idle,
    wait_manifest_loaded,
    run_scan,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def run(*, base_url: str) -> None:
    """Assert idle status at startup and manifest-loaded status after scan."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Step 1 — idle baseline.
            # On initial load (no manifest open) the status bar must show a
            # stable ready/idle text.  This is the web equivalent of Qt's #138
            # startup-baseline probe (no 3 s sleep needed — React state is
            # always persistent).
            assert_status_idle(page, timeout=10_000)

            # Step 2 — run a scan so we transition to the loaded state.
            status_text = run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # Step 3 — post-load summary.
            # The status bar must now show the "N groups · M files" pattern.
            # wait_manifest_loaded already returns this text; the assertion is
            # in the match itself.  We additionally validate the return value
            # is non-empty and mentions both "groups" and "files".
            assert "groups" in status_text.lower() and "files" in status_text.lower(), (
                f"Post-scan status bar must contain 'groups' and 'files', "
                f"got: {status_text!r}"
            )
            assert status_text.strip(), (
                "Status bar text must not be empty after manifest load"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
