"""Scenario 45 — Column-header sort flow + sort preservation across reload (#121).

The bug class:
  1. Header-click handler — ``QHeaderView.sectionClicked`` fires
     ``MainWindow._on_header_clicked`` (``app/views/main_window.py:806``),
     which reads ``tree.header().sortIndicatorOrder()`` and stashes
     ``(logical_index, order)`` on the TreeController via
     ``update_sort_state`` (``tree_controller.py:399``). A regression
     that broke the wire-up (handler not connected, wrong header
     widget, exception swallowed) would silently leave sort working
     visually for the first click but lose the state — invisible at
     layer 1, invisible to existing scenarios.
  2. Sort preservation on refresh — ``TreeController.refresh_model``
     (``tree_controller.py:225``) replays the stashed state via
     ``self.tree.sortByColumn(self._current_sort_column,
     self._current_sort_order)`` on every model rebuild, which is what
     keeps the user's chosen sort across a File → Open Manifest. A
     regression that reset the state, or that called sortByColumn
     before the proxy was attached, would silently revert sort on
     reload. This is the IN-MEMORY persistence surface; the
     across-launch surface (writing the state to ``window_state.ini``
     so a fresh process restores it) is NOT implemented today and is
     out of scope here — a finding for that gap belongs on the issue
     thread, not in this scenario's assertions.

Why this scenario uses ``_uia.read_tree_row_order`` (NEW helper) and
NOT ``_uia.read_result_rows``:
  ``read_result_rows`` filters out tree rows whose
  ``rectangle().top < 600``. On a dev workstation that filter strips
  the header row. On the windows-latest CI runner the same window
  renders smaller (no DPI scaling, default workspace size) and every
  file row's top is also < 600, so ``read_result_rows`` silently
  returns ``[]``. Most scenarios print its output for debug only so
  the breakage is invisible. A sort-assertion scenario CANNOT use
  it — the assertion would pass trivially against ``[] == []``.

Why we re-open the manifest in-process (brief approach (a)) and not
through a relaunch:
  The in-memory state is the one that's actually implemented
  (``TreeController._current_sort_column``). A relaunch would test
  the across-process surface, which isn't wired to QSettings today
  and would predictably FAIL — a useful finding but not the same
  signal. Approach (a) gives us a green scenario that pins the real
  behaviour; the gap is documented separately.

Lifecycle: single launch (the batch-launched one). Scan the
near-duplicates fixture, close & load, drive header clicks, then
trigger File → Open Manifest on the same manifest path to exercise
``refresh_tree`` → ``refresh_model`` → ``sortByColumn(...)`` end-to-end.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Header labels — must match ``app.views.constants.headers()``, which
# resolves them from ``translations/en.yml::column.*``. If en.yml ever
# changes these strings the scenario fails loud (header-not-found) via
# ``click_column_header``'s diagnostic, which is what we want.
COL_FILE_NAME = "File Name"
COL_SIZE = "Size (Bytes)"

# Settle delay after a header click — gives Qt's proxy model a beat to
# reapply the sort and the UIA tree a beat to refresh. Empirically 0.3s
# is plenty on a hosted runner; padded to 0.5s for headroom.
SORT_SETTLE_S = 0.5

# Settle delay after File → Open Manifest completes successfully. The
# helper returns when the status bar shows "Opened manifest: …" but the
# model rebuild + sort replay can lag by a frame on hosted runners.
RELOAD_SETTLE_S = 0.5


def _read_basenames_ordered_by_size_asc() -> list[str]:
    """Return fixture basenames ordered by ``file_size_bytes`` ASC.

    Used as an independent oracle for the Size-column sort assertion:
    after clicking the Size (Bytes) header, the displayed row order
    must match what SQLite reports for the same ORDER BY. Independent
    in the sense that the displayed order comes from Qt's proxy model
    (which sorts by ``SORT_ROLE``) and the SQL order comes from a
    different column entirely — agreement between them rules out a
    proxy-model bug that flipped ordering by some other field.
    """
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path FROM migration_manifest "
            "WHERE source_path LIKE ? "
            "ORDER BY file_size_bytes ASC",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return [Path(r[0]).name for r in rows]


def main() -> int:
    print("scenario: s45_sort_persistence")

    failures: list[str] = []

    # ── Connect to the batch-launched app ─────────────────────────────
    print("step: connect")
    app, win = _uia.connect_main()
    print(f"  pid={win.process_id()}")

    # ── Scan + close & load (pattern from s14, s32, s47). The
    # scenario asserts on displayed sort order, so the manifest must
    # be loaded first and the tree visible. ───────────────────────────
    print("step: scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win)
    _uia.run_scan_and_wait(dlg, timeout=30)
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    # Re-connect after close-and-load (same race-guard as s47 — the
    # post-load model rebuild can lap the UIA cache on hosted runners).
    _, win = _uia.connect_main()
    time.sleep(SORT_SETTLE_S)

    # ── Baseline snapshot. Fixture has 5 files; assert that loud so a
    # future fixture change surfaces immediately. ─────────────────────
    print("step: baseline_snapshot")
    baseline = _uia.read_tree_row_order(win)
    print(f"  baseline_rows={baseline}")
    if len(baseline) != 5:
        print(
            f"FAIL: baseline row count={len(baseline)} != 5 — "
            f"either the fixture changed (qa/sandbox/near-duplicates) "
            f"or read_tree_row_order is missing rows. Got: {baseline!r}"
        )
        return 1

    # ── Click File Name → ascending ───────────────────────────────────
    # Default sort is by similarity / group; the first click on File
    # Name should produce alphabetical-ASC order. Use case-insensitive
    # compare because Qt's default string sort on QStandardItem is
    # locale-aware ASCII for our fixture names but case-folding here
    # keeps the assertion robust against locale drift.
    print(f"step: click_header_{COL_FILE_NAME!r}_ascending")
    _uia.click_column_header(win, COL_FILE_NAME)
    time.sleep(SORT_SETTLE_S)
    ascending = _uia.read_tree_row_order(win)
    expected_asc = sorted(baseline, key=str.lower)
    print(f"  observed={ascending}")
    print(f"  expected={expected_asc}")
    if ascending != expected_asc:
        failures.append(
            f"File Name ASC: observed={ascending!r} != "
            f"expected={expected_asc!r}. Header-click did not produce "
            f"alphabetical-ascending order — either MainWindow."
            f"_on_header_clicked isn't reached, the proxy's SORT_ROLE "
            f"on COL_NAME is wrong, or click_column_header missed the "
            f"section."
        )

    # ── Click File Name again → descending ────────────────────────────
    # The second click on the same column toggles the sortIndicatorOrder
    # from Ascending to Descending. This is what proves the handler
    # actually READ the order from the header (not just guessed).
    print(f"step: click_header_{COL_FILE_NAME!r}_descending")
    _uia.click_column_header(win, COL_FILE_NAME)
    time.sleep(SORT_SETTLE_S)
    descending = _uia.read_tree_row_order(win)
    expected_desc = list(reversed(expected_asc))
    print(f"  observed={descending}")
    print(f"  expected={expected_desc}")
    if descending != expected_desc:
        failures.append(
            f"File Name DESC: observed={descending!r} != "
            f"expected={expected_desc!r}. Second header-click did not "
            f"toggle sort order — _on_header_clicked is probably "
            f"reading a stale indicator or update_sort_state isn't "
            f"being called."
        )

    # ── Click Size (Bytes) → verify against an independent SQL oracle.
    # This catches the case where File Name happens to sort correctly
    # but a different column's SORT_ROLE is broken (the proxy uses
    # SORT_ROLE for non-string columns so size sort is numeric, not
    # lexicographic). ─────────────────────────────────────────────────
    print(f"step: click_header_{COL_SIZE!r}_ascending")
    _uia.click_column_header(win, COL_SIZE)
    time.sleep(SORT_SETTLE_S)
    by_size = _uia.read_tree_row_order(win)
    sql_order = _read_basenames_ordered_by_size_asc()
    print(f"  observed={by_size}")
    print(f"  sql_oracle={sql_order}")
    if by_size != sql_order:
        failures.append(
            f"Size ASC: observed={by_size!r} != "
            f"sql_oracle={sql_order!r}. The proxy's SORT_ROLE on "
            f"COL_SIZE_BYTES is not producing numeric order, or "
            f"click_column_header landed on a different section."
        )

    # ── PERSISTENCE — re-open the same manifest in-process and assert
    # the chosen sort survives. This drives the
    # ``refresh_tree → refresh_model → sortByColumn(...)`` chain that
    # preserves ``_current_sort_column`` / ``_current_sort_order``
    # stashed by update_sort_state. ───────────────────────────────────
    pre_reopen = by_size
    print("step: reopen_manifest_in_process")
    _uia.menu_path(win, _uia.MENU_FILE, _uia.FILE_OPEN_MANIFEST)
    try:
        status = _uia.open_manifest_via_native_dialog(
            win.process_id(), str(MANIFEST_PATH)
        )
        print(f"  status={status!r}")
    except RuntimeError as exc:
        failures.append(
            f"File → Open Manifest failed on the same path that was "
            f"just loaded by Scan: {exc}"
        )
        # No sort assertion to do if the load itself didn't succeed.
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    _, win = _uia.connect_main()
    time.sleep(RELOAD_SETTLE_S)
    post_reopen = _uia.read_tree_row_order(win)
    print(f"  pre_reopen={pre_reopen}")
    print(f"  post_reopen={post_reopen}")
    if post_reopen != pre_reopen:
        # The brief explicitly accepts this as a finding rather than
        # a hard failure if it surfaces — but TreeController is wired
        # to preserve sort across refresh_model, so a mismatch IS a
        # regression, not expected behaviour. File it as a failure so
        # the scenario goes red and the issue thread captures it.
        failures.append(
            f"Sort not preserved across in-process manifest reload: "
            f"pre_reopen={pre_reopen!r} != post_reopen={post_reopen!r}. "
            f"refresh_tree → refresh_model is supposed to replay "
            f"_current_sort_column / _current_sort_order via "
            f"sortByColumn — either the state was reset on reload, "
            f"or sortByColumn fires before the proxy attaches."
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s45_sort_persistence DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
