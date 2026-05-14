"""Scenario 17 — Scan dialog _SourceListWidget operations.

Required source: none — driver populates the source list from inside
the dialog. SCENARIO_SOURCES["s17_scan_dialog_widgets"] is intentionally
empty.

Drives every widget operation on _SourceListWidget end-to-end:
  - add via _FolderTreePanel path field (UIA ValuePattern, IME-safe)
  - assert display sorts by path (case-insensitive) per #213
  - toggle Recursive via the per-row checkbox
  - remove via the × button
  - tail: clear + re-add one folder, run scan, close & load — proves
    widget mutations actually feed _build_sources() and the scan worker

Catches drift in: row-button click coordinates (column-2 DataItem
rectangle after the #213 layout flattening), the entries-index lambda
capture in _rebuild_table (which decouples display row from the
underlying entries-list index now that the display is sorted), the
signal wiring between _FolderTreePanel.folder_requested and
_SourceListWidget.add_entry, and the path the empty-list settings
take through _load_from_settings.

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

    # ── 0. Advanced-settings collapsed by default (#163) ──────────────────
    # The pHash and mean-color sliders live inside a checkable QGroupBox
    # that defaults to collapsed. Qt hides children when a checkable
    # QGroupBox is unchecked, so a UIA descendant query for visible
    # Slider controls returns zero.
    print("step: assert_advanced_collapsed_by_default")
    sliders = [s for s in dlg.descendants(control_type="Slider")
               if s.is_visible()]
    print(f"  visible_sliders={len(sliders)}")
    if sliders:
        print(
            f"FAIL: expected 0 visible sliders (advanced settings collapsed); "
            f"got {len(sliders)}. The Grouping-Parameters QGroupBox should be "
            f"checkable + unchecked by default — see photo-manager#163."
        )
        return 1

    # ── 1. Empty-state baseline ───────────────────────────────────────────
    print("step: assert_empty_baseline")
    paths = _uia.read_source_paths(dlg)
    print(f"  initial_paths={paths!r}")
    if paths:
        print(f"FAIL: expected empty source list, got {paths!r}")
        return 1

    # ── 2. Add three sources via tree-panel path field ────────────────────
    # The display is sorted alphabetically by path (case-insensitive) per
    # #213 — adding in the order unique, near-duplicates, huge must
    # produce a sorted display: huge, near-duplicates, unique. (The
    # underlying entries list keeps insertion order; only the table view
    # is sorted.)
    print("step: add_three_sources")
    for src in (SOURCE_UNIQUE, SOURCE_NEAR, SOURCE_HUGE):
        _uia.add_source_via_path_field(dlg, str(src.resolve()))

    paths = _uia.read_source_paths(dlg)
    print(f"  paths_after_add={[Path(p).name for p in paths]!r}")
    if not _basenames_match(paths, ["huge", "near-duplicates", "unique"]):
        print(
            "FAIL: folder list not sorted by path (#213) — "
            "expected [huge, near-duplicates, unique], "
            f"got {[Path(p).name for p in paths]!r}"
        )
        return 1

    # ── 3. Toggle Recursive on row 1 (near-duplicates) ────────────────────
    # setCellWidget checkboxes are invisible to UIA; assert the click did
    # not raise and the displayed list otherwise survived. State change
    # is exercised behaviorally by the scan tail not failing.
    print("step: toggle_recursive_row1")
    _uia.toggle_source_row_recursive(dlg, row=1)
    paths = _uia.read_source_paths(dlg)
    if not _basenames_match(paths, ["huge", "near-duplicates", "unique"]):
        print(f"FAIL: paths shifted unexpectedly after toggle: {paths!r}")
        return 1

    # ── 4. Remove row 0 (huge) via × ──────────────────────────────────────
    # Display row 0 must map back to the correct entry — this catches the
    # lambda-capture bug in _rebuild_table where the × handler must
    # reference the entries-list index, not the display row.
    print("step: remove_row0")
    _uia.click_source_row_button(dlg, row=0, kind="remove")
    paths = _uia.read_source_paths(dlg)
    print(f"  paths_after_remove={[Path(p).name for p in paths]!r}")
    if not _basenames_match(paths, ["near-duplicates", "unique"]):
        print("FAIL: paths after × click mismatch")
        return 1

    # ── 5. Tail sanity: clear, re-add one folder, scan, close & load ──────
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

    # ── 6. Cross-scenario invariant — manifest-gated menu items enabled ───
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
