"""Scenario 38 — Scan dialog path-field validation + output Browse button.

Required source: none — driver starts from an empty source list and
exercises the path-field validation surface directly.

Two related dialog-edge-case bugs share this scenario:

#144 — typed path that doesn't exist must surface inline:
  1. Open scan dialog, confirm empty source list.
  2. Type a path that doesn't exist on disk, click ``+ Add``.
  3. Assert: source list unchanged AND an inline error label is visible
     in the dialog naming the offending path.
  4. Type a valid existing path, click ``+ Add``.
  5. Assert: error indicator clears AND the source list grew by one row.

  Why this scenario exists: the bug (#144) was that step 2 silently
  no-op'd — the user had no signal that ``+ Add`` did anything. Layer-1
  tests pin the QLabel state behind the dialog widget, but only a UIA
  read of the live dialog tree catches "the label exists but the text
  never reached the accessibility surface" / "label was added but never
  shown" regressions.

#216 — output Browse button must open a real save-file dialog:
  6. Click ``Browse…`` next to the output path field.
  7. Assert: a top-level window titled "Save Manifest As" appears in
     the app's process.
  8. Press Escape to dismiss.
  9. Assert: the output field value is unchanged (cancel never clobbers).

  Why this matters at layer 3: the #216 bug was that passing the bare
  relative string ``"migration_manifest.sqlite"`` as ``start`` confused
  Qt on Windows — getSaveFileName opened against the process CWD and
  could render a folder-picker-flavoured dialog instead of the standard
  save-file UI. The unit test in tests/test_scan_dialog.py pins the
  ``start`` argument value; this driver pins that what Qt does with
  that argument is still "open the polished Save Manifest As dialog".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
SOURCE_VALID = REPO / "qa" / "sandbox" / "unique"
# A path that is guaranteed not to exist — used as the failure-case
# input. Keep it under the repo so the assertion stays portable; the
# component name is descriptive enough that an actual fixture by this
# name would be obvious.
BAD_PATH = REPO / "qa" / "sandbox" / "nonexistent_xyz123_s38"


def _read_path_error(dlg) -> str:
    """Return the visible text of the inline path-error QLabel, or ``""``.

    The label is a child of ``_FolderTreePanel``. Its style + setText
    are what surface to UIA as a Text control whose ``window_text``
    starts with the localised ``scan_dialog.path_not_found`` prefix.
    Other Text controls in the dialog (group titles, slider labels,
    notice text) don't share that prefix, so a substring filter on
    "not found" / "找不到" is enough to disambiguate.
    """
    needles = ("not found", "找不到")
    for text in dlg.descendants(control_type="Text"):
        try:
            raw = (text.window_text() or "").strip()
        except Exception:
            continue
        low = raw.lower()
        if any(n in low for n in needles):
            return raw
    return ""


def main() -> int:
    print("scenario: s38_scan_dialog_invalid_path")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # Belt-and-braces: the bad path MUST genuinely not exist; if some
    # prior scenario created the directory, the failure case stops
    # being a failure case.
    if BAD_PATH.exists():
        print(f"FAIL: precondition broken — {BAD_PATH} unexpectedly exists")
        return 1

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: assert_empty_baseline")
    initial_paths = _uia.read_source_paths(dlg)
    print(f"  initial_paths={initial_paths!r}")
    if initial_paths:
        print(f"FAIL: expected empty source list, got {initial_paths!r}")
        return 1

    # ── 1. Bad path → inline error, source list unchanged ───────────────
    print("step: add_invalid_path")
    _uia.add_source_via_path_field(dlg, str(BAD_PATH))

    paths_after_bad = _uia.read_source_paths(dlg)
    print(f"  paths_after_bad={paths_after_bad!r}")
    if paths_after_bad:
        print(
            f"FAIL: source list grew after typing a non-existent path "
            f"({paths_after_bad!r}); validation regressed (#144)"
        )
        return 1

    err_text = _read_path_error(dlg)
    print(f"  error_label={err_text!r}")
    if not err_text:
        print(
            "FAIL: no inline error label visible after typing a non-existent "
            "path — this is the #144 regression (silent no-op). Expected a "
            "Text descendant containing the localised 'not found' / '找不到' "
            "marker."
        )
        return 1
    if str(BAD_PATH) not in err_text:
        print(
            f"FAIL: error label visible but does not mention the offending "
            f"path. Got: {err_text!r}; expected substring {str(BAD_PATH)!r}"
        )
        return 1

    # ── 2. Valid path → error clears, source list grows by one ──────────
    print("step: add_valid_path")
    _uia.add_source_via_path_field(dlg, str(SOURCE_VALID.resolve()))

    paths_after_good = _uia.read_source_paths(dlg)
    print(f"  paths_after_good={[Path(p).name for p in paths_after_good]!r}")
    if len(paths_after_good) != 1:
        print(
            f"FAIL: expected exactly one source row after the valid add, "
            f"got {len(paths_after_good)}: {paths_after_good!r}"
        )
        return 1

    residual = _read_path_error(dlg)
    print(f"  error_label_after_good={residual!r}")
    if residual:
        print(
            f"FAIL: inline error survived a successful add. Got {residual!r}; "
            f"expected an empty / hidden label so the dialog doesn't claim "
            f"a problem the user resolved."
        )
        return 1

    # ── 3. #216 — Browse… opens the Save Manifest As dialog ────────────
    # Capture the output field before clicking; cancelling must not clobber.
    output_edit = dlg.child_window(
        auto_id=_uia.SCAN_AID_OUTPUT_PATH, control_type="Edit"
    )
    output_before = (output_edit.window_text() or "")
    print(f"  output_before_browse={output_before!r}")

    print("step: click_browse_output")
    browse_btn = next(
        (
            b
            for b in dlg.descendants(control_type="Button")
            if (b.window_text() or "").strip() == _uia.SCAN_BTN_BROWSE
        ),
        None,
    )
    if browse_btn is None:
        print(
            f"FAIL: {_uia.SCAN_BTN_BROWSE!r} button not found in ScanDialog — "
            f"layout drift?"
        )
        return 1
    try:
        browse_btn.invoke()
    except Exception:
        browse_btn.click_input()

    # The fix for #216: with an empty output field, getSaveFileName must
    # receive ``""`` (not the bare relative ``"migration_manifest.sqlite"``).
    # Qt's response to ``""`` is to open its remembered last-visited dir
    # inside the standard Save Manifest As dialog. The regression mode is
    # that Qt opens a different dialog flavour (or no dialog at all);
    # waiting for the exact window title catches both.
    try:
        save_hwnd = _uia.wait_for_dialog(pid, "Save Manifest As", timeout=5.0)
    except TimeoutError:
        titles_now = sorted(
            {t for _, _, t in _uia.list_process_windows(pid) if t}
        )
        print(
            f"FAIL: 'Save Manifest As' dialog did not appear within 5s after "
            f"clicking Browse… — #216 regression. Visible top-level windows: "
            f"{titles_now!r}"
        )
        return 1
    print(f"  save_dialog_hwnd={save_hwnd}")

    print("step: dismiss_save_dialog_via_escape")
    save_dlg = _uia.connect_by_handle(save_hwnd)
    _uia._focus(save_dlg)
    time.sleep(0.2)
    import pywinauto.keyboard as kb
    kb.send_keys("{ESC}")

    deadline = time.time() + 3.0
    dismissed = False
    while time.time() < deadline:
        titles_now = {t for _, _, t in _uia.list_process_windows(pid)}
        if "Save Manifest As" not in titles_now:
            dismissed = True
            break
        time.sleep(0.1)
    if not dismissed:
        print(
            "FAIL: 'Save Manifest As' dialog did not dismiss within 3s of "
            "Escape — focus drift may have eaten the key"
        )
        return 1

    output_after = (output_edit.window_text() or "")
    print(f"  output_after_browse={output_after!r}")
    if output_after != output_before:
        print(
            f"FAIL: Cancelling the Browse dialog clobbered the output field. "
            f"before={output_before!r} after={output_after!r} — cancel must "
            f"leave the field untouched."
        )
        return 1

    print("step: close_dialog")
    _uia.close_scan_dialog_via_close_button(dlg)

    print("scenario: s38_scan_dialog_invalid_path DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
