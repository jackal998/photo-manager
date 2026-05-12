"""Scenario 40 — Results tree row double-click (#143).

Required source: qa/sandbox/near-duplicates (5 .jpg fixtures).

Drives the doubleClicked dispatcher in
``TreeController.setup_double_click``:
  scan → close & load →
  observe group header row is expanded by default →
  double-click "Group 1" header row →
  verify the group is now collapsed (is_expanded() flips False) →
  double-click again →
  verify the group toggles back to expanded.

Catches drift in:
  - The signal wiring in ``main_window._connect_signals`` —
    ``setup_double_click(open_file_in_default_viewer)``.
  - The row-type dispatch in
    ``TreeController._on_double_click`` (group branch: toggle expand).
  - ``setup_tree_properties`` leaving ``setExpandsOnDoubleClick`` off —
    if Qt's default ever sneaks back in, the toggle races our
    ``setExpanded`` call and the group appears to no-op visibly.

Distinct from:
  - The s40 scenario does NOT exercise the file-row branch
    (``open_file_in_default_viewer``). That branch is layer-1 tested in
    ``tests/test_tree_controller_double_click.py::TestFileRowRouting``
    and ``tests/test_file_opener.py``. Asserting a real OS-spawned
    viewer here would be flaky (no deterministic close-trigger across
    image apps, no offscreen rendering) — see the brief on this issue
    for the env-var stub alternative if file-row coverage is ever
    promoted to layer 3.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

GROUP_ROW_LABEL = "Group 1"


def main() -> int:
    print("scenario: s40_results_tree_double_click")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Set up a manifest so the result tree has group/file rows ─────────
    print("step: scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win)
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    _, win = _uia.connect_main()

    # ── Verify initial expand state (refresh_model calls expandAll) ──────
    print(f"step: probe_initial_state row={GROUP_ROW_LABEL!r}")
    group_item = _uia.find_tree_item(win, GROUP_ROW_LABEL)
    try:
        initial_expanded = group_item.is_expanded()
    except Exception as e:
        print(f"FAIL: could not read expand state of {GROUP_ROW_LABEL!r}: {e}")
        return 1
    print(f"  initial_expanded={initial_expanded}")
    if not initial_expanded:
        print(
            f"FAIL: expected {GROUP_ROW_LABEL!r} to be expanded initially "
            f"(TreeController.refresh_model calls expandAll())"
        )
        return 1

    # ── Double-click the group header → should collapse ──────────────────
    print(f"step: double_click row={GROUP_ROW_LABEL!r} (expecting collapse)")
    _uia.double_click_tree_row(win, GROUP_ROW_LABEL)
    # Allow Qt to process the slot + repaint
    time.sleep(0.3)

    group_item = _uia.find_tree_item(win, GROUP_ROW_LABEL)
    after_first = group_item.is_expanded()
    print(f"  expanded_after_first_double_click={after_first}")
    if after_first:
        print(
            f"FAIL: {GROUP_ROW_LABEL!r} is still expanded after double-click — "
            f"regression of #143 dispatcher (group branch did not toggle, "
            f"OR Qt's default setExpandsOnDoubleClick is back on and racing)"
        )
        return 1

    # ── Double-click again → should re-expand ────────────────────────────
    print(f"step: double_click row={GROUP_ROW_LABEL!r} (expecting re-expand)")
    _uia.double_click_tree_row(win, GROUP_ROW_LABEL)
    time.sleep(0.3)

    group_item = _uia.find_tree_item(win, GROUP_ROW_LABEL)
    after_second = group_item.is_expanded()
    print(f"  expanded_after_second_double_click={after_second}")
    if not after_second:
        print(
            f"FAIL: {GROUP_ROW_LABEL!r} did not re-expand on second "
            f"double-click — toggle is one-shot rather than symmetric"
        )
        return 1

    print("scenario: s40_results_tree_double_click DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
