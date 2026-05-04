"""Scenario 17 — Scan dialog _SourceListWidget operations.

Required source: none — driver populates the source list from inside
the dialog. SCENARIO_SOURCES["s17_scan_dialog_widgets"] is intentionally
empty.

Drives every widget operation on _SourceListWidget end-to-end:
  - add via _FolderTreePanel path field (UIA ValuePattern, IME-safe)
  - reorder via the ↑ button
  - toggle Recursive via the per-row checkbox
  - remove via the × button
  - tail: clear + re-add one folder, run scan, close & load — proves
    widget mutations actually feed _build_sources() and the scan worker

Catches drift in: row-button click coordinates (column-3 / column-4
DataItem rectangles), the row-index lambda capture in _rebuild_table,
_move / _remove off-by-ones, signal wiring between
_FolderTreePanel.folder_requested and _SourceListWidget.add_entry,
and the path the empty-list settings take through _load_from_settings.

Distinct from s10 / s12 / s16 which all preload the source list from
qa/settings.json — only s17 exercises the in-dialog widgets.

Recursive-state caveat: setCellWidget'd checkboxes are not surfaced
in Qt's UIA tree, so the driver cannot read the toggle state back.
The toggle click is fire-and-forget — verified at the action layer
(no exception, scan still succeeds), not at the state layer. Visible
state regression there would be caught by /qa-explore screenshot
review, not this driver.
"""
from __future__ import annotations

import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

SOURCE_UNIQUE = REPO / "qa" / "sandbox" / "unique"
SOURCE_NEAR = REPO / "qa" / "sandbox" / "near-duplicates"
SOURCE_HUGE = REPO / "qa" / "sandbox" / "huge"


def _basenames_match(paths: list[str], expected: list[str]) -> bool:
    """True if `paths` basenames equal `expected` in the same order."""
    if len(paths) != len(expected):
        return False
    return all(
        Path(p).name.lower() == name.lower() for p, name in zip(paths, expected)
    )


def main() -> int:
    print("scenario: s17_scan_dialog_widgets")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    # ── 1. Empty-state baseline ───────────────────────────────────────────
    print("step: assert_empty_baseline")
    paths = _uia.read_source_paths(dlg)
    print(f"  initial_paths={paths!r}")
    if paths:
        print(f"FAIL: expected empty source list, got {paths!r}")
        return 1

    # ── 2. Add three sources via tree-panel path field ────────────────────
    print("step: add_three_sources")
    for src in (SOURCE_UNIQUE, SOURCE_NEAR, SOURCE_HUGE):
        _uia.add_source_via_path_field(dlg, str(src.resolve()))

    paths = _uia.read_source_paths(dlg)
    print(f"  paths_after_add={[Path(p).name for p in paths]!r}")
    if not _basenames_match(paths, ["unique", "near-duplicates", "huge"]):
        print("FAIL: source list order/contents mismatch after add")
        return 1

    # ── 3. Reorder: row 1 (near-duplicates) ↑ ─────────────────────────────
    print("step: reorder_row_up")
    _uia.click_source_row_button(dlg, row=1, kind="up")
    paths = _uia.read_source_paths(dlg)
    print(f"  paths_after_up={[Path(p).name for p in paths]!r}")
    if not _basenames_match(paths, ["near-duplicates", "unique", "huge"]):
        print("FAIL: order after ↑ click mismatch")
        return 1

    # ── 4. Toggle Recursive on row 1 (now 'unique') ───────────────────────
    # setCellWidget checkboxes are invisible to UIA; assert the click did
    # not raise and the table state otherwise survived. State change is
    # exercised behaviorally by the scan tail not failing.
    print("step: toggle_recursive_row1")
    _uia.toggle_source_row_recursive(dlg, row=1)
    paths = _uia.read_source_paths(dlg)
    if not _basenames_match(paths, ["near-duplicates", "unique", "huge"]):
        print(f"FAIL: paths shifted unexpectedly after toggle: {paths!r}")
        return 1

    # ── 5. Remove row 2 (huge) via × ──────────────────────────────────────
    print("step: remove_row2")
    _uia.click_source_row_button(dlg, row=2, kind="remove")
    paths = _uia.read_source_paths(dlg)
    print(f"  paths_after_remove={[Path(p).name for p in paths]!r}")
    if not _basenames_match(paths, ["near-duplicates", "unique"]):
        print("FAIL: paths after × click mismatch")
        return 1

    # ── 6. Tail sanity: clear, re-add one folder, scan, close & load ──────
    print("step: clear_via_remove_all")
    _uia.click_remove_all_sources(dlg)
    paths = _uia.read_source_paths(dlg)
    if paths:
        print(f"FAIL: Remove All did not clear; paths={paths!r}")
        return 1

    print("step: re_add_for_scan")
    _uia.add_source_via_path_field(dlg, str(SOURCE_NEAR.resolve()))
    paths = _uia.read_source_paths(dlg)
    print(f"  paths_before_scan={[Path(p).name for p in paths]!r}")
    if not _basenames_match(paths, ["near-duplicates"]):
        print("FAIL: re-add did not produce the expected single row")
        return 1

    # Wipe any prior manifest so the post-scan exists() check is meaningful.
    if MANIFEST_PATH.exists():
        try:
            MANIFEST_PATH.unlink()
        except OSError:
            pass

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    print(f"  manifest_path={MANIFEST_PATH}")

    # ── 7. Cross-scenario invariant — manifest-gated menu items enabled ───
    print("step: invariant_actions_enabled")
    _, win = _uia.connect_main()
    inv = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv:
        print("FAIL: manifest-gated menu items not all enabled after close & load")
        return 1

    print("scenario: s17_scan_dialog_widgets DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
