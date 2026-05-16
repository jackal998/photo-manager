"""Scenario 51 — Execute Action dialog embeds a PreviewPane (#165).

Required source: qa/sandbox/near-duplicates (5 files, basenames
neardup_NN_qXX.jpg).

The Option-A landing for #165 wraps the Execute Action dialog's review
tree in a horizontal QSplitter alongside an embedded ``PreviewPane`` so
users can see what each row looks like before confirming a destructive
operation. Selection-change wiring + ``show_single`` / ``clear`` calls
are pinned at layer 1 in
``tests/test_execute_action_dialog.py::TestExecuteDialogPreviewPane``
where MagicMock runners satisfy the contract; this scenario is the
layer-3 confirmation that in a real running app:

  * threading ``task_runner`` from MainWindow → FileOperationsHandler
    → ExecuteActionDialog actually reaches the dialog,
  * the preview-enabled branch of ``_build_ui`` actually fires (not
    silently skipped by a None runner from a refactor regression), and
  * the dialog's UIA tree exposes the preview pane so a user with a
    screen reader can perceive it.

Non-destructive — opens the dialog with one row marked 'delete' so the
Execute button enables and the dialog passes its has-decisions gate,
then dismisses via Close without clicking Execute.

Sister to s30 / s33 / s34 (Execute Action dialog drivers); same
fixture, same regex partition machinery.
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

# Mark every row 'delete' so the dialog tree has rows the user can click
# and the Execute button enables. The actual Execute click never fires
# in this scenario (we close via Cancel/Close before that).
DELETE_REGEX = r"neardup_"
FIELD = "File Name"

# Preview header label text. Lives at ``preview.header`` in translations/en.yml.
# A drift here (rename in the YAML) would surface as a clear FAIL line —
# preferable to a silent miss because the marker IS the only thing the
# scenario can grip onto without inspecting Qt internals.
PREVIEW_HEADER_TEXT = "Preview"


def _read_decisions() -> dict[str, str]:
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (d or "") for p, d in rows}


def _find_preview_header(exec_dlg) -> object | None:
    """Return the PreviewPane's header QLabel wrapper, or None.

    The header is the only Text/Static descendant whose window text is
    exactly the preview header constant. Matching by exact equality
    (not substring) avoids accidentally grabbing a status-bar or
    summary label that happened to contain the word "Preview".
    """
    for ct in ("Text", "Static"):
        for d in exec_dlg.descendants(control_type=ct):
            try:
                txt = (d.window_text() or "").strip()
            except Exception:
                continue
            if txt == PREVIEW_HEADER_TEXT:
                return d
    return None


def _find_splitter(exec_dlg) -> object | None:
    """Return the first QSplitter descendant of ``exec_dlg``, or None.

    Pre-#165 the dialog had a flat QVBoxLayout with no splitter; the
    presence of any QSplitter under the Execute Action dialog is a
    strong positive signal that the preview-enabled layout actually
    fired. Matches by class_name='QSplitter' so a wrapper-widget
    insertion around the splitter doesn't hide it.
    """
    for ct in ("Pane", "Group", "Custom"):
        for d in exec_dlg.descendants(control_type=ct):
            try:
                cls = (d.element_info.class_name or "")
            except Exception:
                cls = ""
            if cls == "QSplitter":
                return d
    return None


def main() -> int:
    print("scenario: s51_execute_dialog_preview")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_initial")
    initial = _read_decisions()
    if not initial:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    print(f"  rows={len(initial)}")

    print(f"step: bulk_delete_via_regex regex={DELETE_REGEX!r}")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=DELETE_REGEX, action_label="delete"
    )
    after = _read_decisions()
    not_deleted = [n for n, d in after.items() if d != "delete"]
    if not_deleted:
        print(
            f"FAIL: bulk delete did not cover every row; missed {not_deleted}"
        )
        return 1
    print(f"  all {len(after)} rows now have user_decision='delete'")

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    # ── Assert the splitter mounted. Without preview-enabled wiring,
    # ExecuteActionDialog._build_ui falls back to a flat layout and no
    # QSplitter would appear among the descendants. ─────────────────────
    print("step: locate_splitter")
    splitter = _find_splitter(exec_dlg)
    if splitter is None:
        print(
            "FAIL: no QSplitter descendant in ExecuteActionDialog — "
            "preview-enabled layout did not fire (task_runner=None?)"
        )
        return 1
    print(f"  splitter_found class={splitter.element_info.class_name!r}")

    # ── Assert the PreviewPane's header label is present in the dialog's
    # UIA tree. This is what a screen reader would announce when the
    # user tabs into the preview region. ─────────────────────────────────
    print("step: locate_preview_header")
    header = _find_preview_header(exec_dlg)
    if header is None:
        print(
            "FAIL: PreviewPane header label "
            f"(text={PREVIEW_HEADER_TEXT!r}) not found among dialog "
            "descendants — preview pane was not mounted"
        )
        return 1
    try:
        rect = header.rectangle()
        print(f"  preview_header_rect={rect}")
    except Exception as exc:
        print(f"  (could not read header rect: {exc})")

    # ── Click the first file row in the dialog tree to drive
    # ``_on_selection_changed`` → ``preview.show_single``. We don't try
    # to assert the rendered image (Qt fills the QLabel pixmap async via
    # the runner pool); the layer-1 tests already pin that show_single
    # is called with the right arguments. Here we just verify nothing
    # crashes between selection and preview update. ─────────────────────
    print("step: click_first_file_row")
    file_rows = [
        d for d in exec_dlg.descendants(control_type="TreeItem")
    ]
    if not file_rows:
        print("FAIL: no TreeItem descendants in Execute Action dialog")
        return 1
    # The first descendant TreeItem is the group header; the second is
    # the first file row. Some Qt versions surface only file rows as
    # TreeItem and put group headers under "DataItem"; if so, fall
    # through to whatever the first clickable row is.
    target_row = file_rows[1] if len(file_rows) > 1 else file_rows[0]
    print(f"  target_row_text={target_row.window_text()!r}")
    try:
        target_row.click_input()
    except Exception as exc:
        print(f"FAIL: clicking tree row raised: {exc!r}")
        return 1
    # Give the preview pipeline a beat to dispatch the show_single call.
    # No assertion on rendered pixmap — just confirm the app didn't
    # crash between click and the next UIA call.
    time.sleep(0.3)

    # Re-locate the dialog and header — if the click crashed the
    # window, this would raise. Successful re-locate is the assertion.
    print("step: post_click_uia_still_responsive")
    header_after = _find_preview_header(exec_dlg)
    if header_after is None:
        print(
            "FAIL: preview header disappeared after row click — "
            "dialog likely crashed or detached"
        )
        return 1

    print("step: close_execute_action_dialog")
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()
    time.sleep(0.3)

    # ── Manifest must still hold the decisions we set — Close does NOT
    # execute. A regression where Close accidentally fired Execute
    # would zero out the deleted_paths and may even hit the recycle
    # bin; pinning the post-Close decision state guards against that.
    post_close = _read_decisions()
    if post_close != after:
        print(
            "FAIL: manifest decisions changed after Close (should be "
            f"a no-op); pre={after} post={post_close}"
        )
        return 1

    print("scenario: s51_execute_dialog_preview DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
