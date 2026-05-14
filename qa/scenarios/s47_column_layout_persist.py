"""Scenario 47 — Results-tree column layout persists across launches (#214).

The bug class:
  1. Save path — ``QHeaderView.sectionResized`` / ``sectionMoved``
     fires on every user-driven resize/drag and
     ``MainWindow._save_column_state_only`` flushes the new state to
     ``qa/window_state.ini`` immediately. ``closeEvent`` also flushes
     via ``_save_geometry``.
  2. Restore path — on the next launch, ``MainWindow.refresh_tree``
     calls ``TreeController.restore_column_state`` AFTER
     ``refresh_model``'s ``ResizeToContents → Interactive`` cycle
     (otherwise the auto-sized widths would silently overwrite the
     restored ones, the headline trap from the issue).

Why this scenario forges a saveState blob via a sidecar Python
process instead of driving a real header drag:
  Synthetic mouse SendInput is unreliable on GitHub-hosted Windows
  runners for non-foreground windows — Qt's QHeaderView reads the
  live cursor position at mouseMoveEvent time (not the WM_MOUSEMOVE
  lparam), so PostMessage doesn't help either, and SetForegroundWindow
  is rate-limited from background processes. Locally the real-drag
  path works (verified on a dev workstation: 122px → 412px → restored
  at 412px); on CI the drag undershoots silently. Rather than ship a
  flaky scenario, we forge a valid ``QHeaderView.saveState()`` blob
  via a sidecar QApplication and verify the *restore* path
  end-to-end through the real running app's ``refresh_tree`` →
  ``restore_column_state`` chain. The *save* path is pinned at
  layer 1: ``tests/test_tree_controller.py::TestLayoutChangeSignalConnection``
  verifies ``sectionMoved`` / ``sectionResized`` fire the save
  callback (and that ``refresh_model``'s internal resize cycle does
  NOT — the blockSignals guard around it is the biggest regression
  risk of this PR).

Why we ALSO close the window and re-read the INI:
  Layer 1 can't prove that ``closeEvent`` actually invokes
  ``save_column_state`` — that's MainWindow plumbing which we
  intentionally don't unit-test (would cascade-import the whole
  QMainWindow assembly, breaking coverage measurement). This
  scenario verifies the close path keeps the column_header key
  intact (i.e. ``_save_geometry`` doesn't accidentally wipe it).

Lifecycle: single launch (the batch-launched one). The sidecar runs
*before* we trigger a scan, so the in-app ``restore_column_state``
call inside ``refresh_tree`` reads our forged INI. ``window_state.ini``
is cleaned at startup so the assertion is against state THIS scenario
set, not whatever a prior run left behind.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
QSETTINGS_INI_PATH = REPO / "qa" / "window_state.ini"

# Column-name label resolved from ``translations/en.yml::column.file_name``
# — must match what ``app.views.constants.headers()`` returns. We force
# File Name to a known width via the sidecar, then read it back via UIA.
COL_FILE_NAME = "File Name"

# Logical column index of the File Name column. Must match
# ``app.views.constants.COL_NAME`` (currently 4). A drift here would
# fail loudly with a width-mismatch error rather than silently passing.
COL_NAME_IDX = 4

# Total number of columns in the results tree. Must match
# ``app.views.constants.NUM_COLUMNS`` (currently 11). The forged
# QHeaderView state encodes this count, and the in-app section-count
# guard (``TreeController.restore_column_state``) compares against
# ``header.count()`` to detect schema drift.
NUM_COLUMNS = 11

# The width we'll force File Name to via the sidecar. Pick a value
# clearly distinct from any auto-sized default (which depends on
# file-name length in the loaded manifest) and clearly distinct from
# neighbouring columns so the assertion has signal even with ±1 px
# UIA jitter.
TARGET_WIDTH_PX = 411
WIDTH_TOLERANCE_PX = 8

# QSettings key — must match ``MainWindow.QSETTINGS_KEY_COLUMN_STATE``.
# Drifting one without the other is a fast-fail because the scenario's
# forged state writes one key and the app reads the other.
QSETTINGS_KEY = "geometry/column_header"


# ---------------------------------------------------------------------------
# Sidecar — forge a valid QHeaderView state blob and write it to the
# INI before the app reads it.
#
# Runs as its own Python subprocess so QApplication's process-wide
# singleton constraint doesn't clash with anything else the scenario
# does. Receives no arguments; all knobs are baked into the inline
# script via the f-string below.
# ---------------------------------------------------------------------------


def _forge_column_state(target_width_px: int) -> None:
    """Write a forged column-state INI that sets File Name = ``target_width_px``.

    The sidecar:
      1. Creates a QApplication + QTreeView with NUM_COLUMNS sections.
      2. Resizes the COL_NAME_IDX section to ``target_width_px``.
      3. Calls ``QHeaderView.saveState()`` to get a Qt-internal blob
         that, when applied to a header with the same section count,
         produces the same widths.
      4. Writes the blob and the section_count sidecar to
         ``qa/window_state.ini`` via QSettings(IniFormat).

    On the running app, the next ``refresh_tree`` call will invoke
    ``TreeController.restore_column_state`` which reads this INI,
    confirms section_count matches, and applies the blob.
    """
    # DPR scaling — TARGET_WIDTH_PX is expressed in PHYSICAL pixels
    # (matches what UIA ``Rectangle.right - Rectangle.left`` returns).
    # ``QHeaderView.resizeSection`` takes LOGICAL pixels which Qt scales
    # by ``devicePixelRatio`` when rendering. To produce
    # ``target_width_px`` physical pixels post-render, resize to
    # ``target_width_px / dpr`` logical. On a 1:1 display (most CI
    # runners), dpr == 1.0 → no scaling; on a 2:1 HiDPI dev display,
    # dpr == 2.0 → resize to half the target. Same fixture both sides.
    code = (
        "import sys\n"
        "from PySide6.QtCore import QSettings\n"
        "from PySide6.QtWidgets import QApplication, QTreeView\n"
        "from PySide6.QtGui import QStandardItemModel\n"
        "app = QApplication(sys.argv)\n"
        "dpr = app.primaryScreen().devicePixelRatio()\n"
        f"model = QStandardItemModel(0, {NUM_COLUMNS})\n"
        "tv = QTreeView()\n"
        "tv.setModel(model)\n"
        "h = tv.header()\n"
        f"logical = max(1, int(round({target_width_px} / dpr)))\n"
        f"h.resizeSection({COL_NAME_IDX}, logical)\n"
        f"s = QSettings(r'{QSETTINGS_INI_PATH}', QSettings.IniFormat)\n"
        f"s.setValue('{QSETTINGS_KEY}', h.saveState())\n"
        f"s.setValue('{QSETTINGS_KEY}/section_count', {NUM_COLUMNS})\n"
        "s.sync()\n"
        "print(f'SIDECAR_OK dpr={dpr} logical={logical}')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
    )
    if r.returncode != 0 or "SIDECAR_OK" not in r.stdout:
        raise RuntimeError(
            f"sidecar forge failed: rc={r.returncode} "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )
    print(f"  {r.stdout.strip()}")


# ---------------------------------------------------------------------------
# UIA — find the File Name column header section. PySide6's QTreeView
# exposes each section as its own top-level ``Header`` control whose
# ``window_text()`` is the column label. (Not as ``HeaderItem`` children
# of a parent Header — that was the bring-up gotcha; preserved as a
# comment for the next person who looks at this.)
# ---------------------------------------------------------------------------


def _column_width(win, item_name: str) -> int:
    for h in win.descendants(control_type="Header"):
        try:
            if not h.is_visible():
                continue
            if (h.window_text() or "").strip() == item_name:
                r = h.rectangle()
                return r.right - r.left
        except Exception:
            continue
    names = []
    for h in win.descendants(control_type="Header"):
        try:
            names.append((h.window_text() or "").strip())
        except Exception:
            names.append("<err>")
    raise RuntimeError(
        f"Header section {item_name!r} not found; saw: {names!r}"
    )


# ---------------------------------------------------------------------------
# Main scenario
# ---------------------------------------------------------------------------


def main() -> int:
    print("scenario: s47_column_layout_persist")

    # ── Pre-flight: clean stale state so the assertion is against state
    # WE set this run. The configure step already cleared
    # window_state.ini; defence-in-depth here in case configure was
    # bypassed (direct ``python -m qa.scenarios.s47_*`` invocation). ───
    if QSETTINGS_INI_PATH.exists():
        QSETTINGS_INI_PATH.unlink()
        print(f"  cleaned stale qsettings: {QSETTINGS_INI_PATH}")
    if MANIFEST_PATH.exists():
        # A leftover manifest from a previous scenario would let the
        # batch-launched app's auto-load short-circuit our test path.
        # Force the scenario down the explicit scan→load path so
        # refresh_tree (and therefore restore_column_state) is the one
        # we actually exercise.
        MANIFEST_PATH.unlink()
        print(f"  cleaned stale manifest: {MANIFEST_PATH}")

    failures: list[str] = []

    # ── Forge a known column-state INI via the sidecar BEFORE we trigger
    # a scan. The running app will read this INI inside refresh_tree. ─
    print(f"step: forge_column_state file_name_width={TARGET_WIDTH_PX}px")
    _forge_column_state(TARGET_WIDTH_PX)
    if not QSETTINGS_INI_PATH.exists():
        print(f"FAIL: sidecar did not write {QSETTINGS_INI_PATH}")
        return 1
    ini_text_pre = QSETTINGS_INI_PATH.read_text(encoding="utf-8", errors="replace")
    if "column_header" not in ini_text_pre:
        print(f"FAIL: forged INI does not contain 'column_header' key")
        return 1

    # ── Connect to the batch-launched app ─────────────────────────────
    print("step: connect")
    app, win = _uia.connect_main()
    print(f"  pid={win.process_id()}")

    # ── Trigger scan → load manifest → refresh_tree → restore_column_state.
    # The restore must apply our forged 411-px width to the File Name
    # section. ────────────────────────────────────────────────────────
    print("step: scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win)
    _uia.run_scan_and_wait(dlg, timeout=30)
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    # Re-connect (close-and-load can race the UIA cache on hosted
    # runners — same pattern as s40).
    _, win = _uia.connect_main()
    # Settle: refresh_tree → refresh_model has its own resize cycle.
    # Restore runs after that returns; the visible-layout repaint takes
    # a beat to land in the UIA tree.
    time.sleep(0.5)

    # ── Assertion: File Name section width matches our forged value. ─
    restored_width = _column_width(win, COL_FILE_NAME)
    print(f"  File Name width after restore={restored_width}px")
    if abs(restored_width - TARGET_WIDTH_PX) > WIDTH_TOLERANCE_PX:
        failures.append(
            f"File Name width={restored_width}px != forged "
            f"TARGET_WIDTH_PX={TARGET_WIDTH_PX}px (tolerance "
            f"{WIDTH_TOLERANCE_PX}). #214 restore_column_state is "
            f"either not being called from refresh_tree, called in "
            f"the wrong place (BEFORE refresh_model's resize cycle, "
            f"so auto-sizing overwrites), or the section_count "
            f"sentinel guard is rejecting a valid blob."
        )

    # ── Close cleanly so closeEvent → _save_geometry runs, then verify
    # the column_header key SURVIVES the save. (Defends against a
    # future refactor that splits _save_geometry into multiple writes
    # one of which clobbers the others, or where a path raises mid-
    # save and silently leaves the INI in an inconsistent state.) ─────
    print("step: close_and_verify_save")
    win.close()
    # Give Qt's closeEvent + QSettings.sync() time to land. We don't
    # need to wait for full process exit — the INI write happens
    # synchronously inside _save_geometry before super().closeEvent.
    time.sleep(1.5)
    ini_text_post = QSETTINGS_INI_PATH.read_text(encoding="utf-8", errors="replace")
    if "column_header" not in ini_text_post:
        failures.append(
            f"window_state.ini no longer contains 'column_header' key "
            f"after closeEvent — _save_geometry wiped it. Save path "
            f"is silently broken."
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s47_column_layout_persist DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
