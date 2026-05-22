"""Probe: post-scan visual selection set ↔ manifest keeper set.

Pins the end-state invariant of the "Auto select after scan" wiring
(#212/#239): after the scan completes and Close & Load lands the
manifest in the main window, the result tree's visual selection MUST
equal — exactly — the set of rows the worker stamped as keepers.

Complements scenario ``s49_scan_auto_select`` (which exercises the
toggle + manifest-write + post-load highlight in a single end-to-end
driver). s49's hard assertion is::

    EXPECTED_KEEPER in read_selected_tree_row_basenames(win)

i.e. **subset** containment — the named keeper is somewhere in the
selection. That passes silently when:

  * Auto-select promotes N keeper rows but the post-load
    ``_select_rows_by_paths`` walks only the first match (selection ⊊
    keepers).
  * Stale selection from a prior manifest panel survives the load and
    co-exists with the auto-select highlight (selection ⊋ keepers).
  * A non-keeper row gets highlighted alongside the keeper because of
    a row-index race during model rebuild.

This probe asserts **set equality** between::

    manifest_keepers = {basename(p) for p in
        SELECT source_path FROM migration_manifest WHERE action = 'KEEP'}
    visual_selection = set(_uia.read_selected_tree_row_basenames(win))

A diff in either direction fails the probe. Same fixture s49 uses
(``qa/sandbox/near-duplicates`` — 5 JPEG variants, one duplicate group)
so the keeper set is the deterministic singleton ``{neardup_00_q95.jpg}``
on green builds; the probe still does the full set diff so a fixture
swap or auto-select multi-group regression surfaces immediately.

Reads the ``action`` column (not ``user_decision``) — the scan worker
writes ``row.action = "KEEP"`` and ``main_window._load_manifest_after_scan``
drives the visual selection from that column via
``extract_keeper_paths``. ``user_decision`` is unset until the user
interacts with the loaded manifest.

Exit code: 0 on PASS, 1 on FAIL or runtime error.

Run:
    .venv/Scripts/python.exe -m qa.probes.post_scan_visual_state
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.probes._runtime import app_with_manifest
from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

FIXTURE_SOURCES = ["qa/sandbox/near-duplicates"]


def _read_keeper_basenames() -> set[str]:
    """Return ``{basename}`` for every row with ``action='KEEP'`` in
    the manifest produced by the scan. The scan worker writes this
    column on top-scored rows when auto-select is enabled (#212).
    """
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path FROM migration_manifest WHERE action = 'KEEP'"
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name for (p,) in rows}


def main() -> int:
    print("probe: post_scan_visual_state")
    with app_with_manifest(FIXTURE_SOURCES, auto_select=True) as win:
        print("step: read_manifest_keepers")
        manifest_keepers = _read_keeper_basenames()
        print(f"  manifest_keepers={sorted(manifest_keepers)!r}")
        if not manifest_keepers:
            # auto_select=True but the worker wrote zero KEEP rows —
            # either the toggle didn't propagate to the dialog (settings
            # load regression) or the auto-select branch is gated off.
            # Without a keeper set there is nothing to compare against;
            # the probe premise is invalid, so surface FAIL rather than
            # vacuously passing on an empty-vs-empty set match.
            print("FAIL: manifest has zero action=KEEP rows — "
                  "auto_select_enabled did not reach the worker")
            print("probe_status: FAIL")
            return 1

        print("step: read_visual_selection")
        visual_selection = set(_uia.read_selected_tree_row_basenames(win))
        print(f"  visual_selection={sorted(visual_selection)!r}")

        missing_from_selection = sorted(manifest_keepers - visual_selection)
        extra_in_selection = sorted(visual_selection - manifest_keepers)

        print(f"  missing_from_selection={missing_from_selection!r}")
        print(f"  extra_in_selection={extra_in_selection!r}")

        if missing_from_selection or extra_in_selection:
            print("probe_status: FAIL")
            return 1

        print("probe_status: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
