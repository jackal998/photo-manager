"""Scenario 38 — Scan dialog inline error when typed path doesn't exist.

Required source: none — driver starts from an empty source list and
exercises the path-field validation surface directly.

Drives the #144 fix end-to-end:
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
"""
from __future__ import annotations

import sys
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

    print("step: close_dialog")
    _uia.close_scan_dialog_via_close_button(dlg)

    print("scenario: s38_scan_dialog_invalid_path DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
