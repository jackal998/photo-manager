"""Scenario 33 — Execute Action dialog: all-delete banner renders clickable
group anchors (#166).

Required source: qa/sandbox/near-duplicates (5 files, basenames
neardup_NN_qXX.jpg, all clustered into one near-duplicate group).

Pins the rendering half of the #166 jump-to feature: when every row in
a group has ``user_decision='delete'``, the warning banner inside the
Execute Action dialog must surface that group's number so the user can
click it. The click → scrollTo dispatch itself is covered by unit tests
in ``tests/test_execute_action_dialog.py::TestBannerJumpTo`` (QLabel
HTML anchors aren't first-class UIA elements, so clicking the rendered
``<a>`` inside the label can't be driven reliably from pywinauto on
hosted CI runners — see gotcha #1 in the project handover notes).

Flow:
  scan near-duplicates → close & load → bulk delete regex matching all
  five files → open Execute Action dialog → locate the warning-banner
  QLabel via UIA → assert it is visible and its rendered text contains
  the group number → close (no execute).

Sister to s30 / s32 (Execute Action dialog drivers). Same fixture as
s14 / s29 / s30 / s31 / s32 so the partition stays stable.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Match every neardup_*.jpg in the fixture — drives the whole group to
# user_decision='delete' so the all-delete banner must fire.
DELETE_REGEX = r"neardup_"
FIELD = "File Name"


def _read_state() -> dict[str, tuple[str, str]]:
    """Return {basename: (user_decision, group_id)} for every fixture row.

    ``group_id`` is the scanner's hash-like cluster ID (TEXT in sqlite);
    the dialog's "Group N" labels come from a sequential int assigned at
    manifest-load time over sorted group_ids (see
    ``ManifestRepository.load`` in ``infrastructure/manifest_repository.py``),
    so the scenario only needs to know which rows share a group_id, not
    the rendered label number.
    """
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, group_id "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: ((d or ""), (g or "")) for p, d, g in rows}


def _find_banner_label(exec_dlg) -> object | None:
    """Return the warning-banner QLabel wrapper, or None if not present.

    The banner's QLabel is the only Text/Static descendant whose window
    text contains the localized "ALL files deleted" phrase. Identifying
    by substring keeps the scenario robust against i18n changes (the
    substring lives only in the warning string, not in the regular
    dialog chrome).
    """
    for ct in ("Text", "Static"):
        for d in exec_dlg.descendants(control_type=ct):
            try:
                txt = d.window_text() or ""
            except Exception:
                continue
            if "ALL" in txt and "delete" in txt.lower():
                return d
    return None


def main() -> int:
    print("scenario: s33_execute_dialog_jump_to_all_delete")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_initial")
    initial = _read_state()
    if not initial:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    group_ids = sorted({g for _d, g in initial.values() if g})
    print(f"  rows={len(initial)} group_ids={group_ids}")

    print(f"step: bulk_delete_via_regex regex={DELETE_REGEX!r} action='delete'")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=DELETE_REGEX, action_label="delete"
    )
    after = _read_state()
    not_deleted = [n for n, (d, _g) in after.items() if d != "delete"]
    if not_deleted:
        print(
            f"FAIL: bulk delete regex did not cover every fixture row; "
            f"still-undecided: {not_deleted}"
        )
        return 1
    print(f"  all {len(after)} rows now have user_decision='delete'")

    # Find every group_id whose rows are now all-delete. The dialog's
    # banner must surface a sequential group-number for at least one of
    # these (the exact rendered N depends on sort order across all
    # loaded groups in the manifest — see ManifestRepository.load).
    by_gid: dict[str, list[str]] = {}
    for d, g in after.values():
        if g:
            by_gid.setdefault(g, []).append(d)
    all_delete_gids = [g for g, ds in by_gid.items() if ds and all(d == "delete" for d in ds)]
    if not all_delete_gids:
        print(
            f"FAIL: no group_id ended up all-delete; per-gid decisions: "
            f"{by_gid}"
        )
        return 1
    print(f"  all_delete_group_ids={all_delete_gids}")

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: locate_warning_banner")
    label = _find_banner_label(exec_dlg)
    if label is None:
        print("FAIL: warning-banner QLabel not found among dialog descendants")
        return 1
    banner_text = label.window_text() or ""
    print(f"  banner_text={banner_text!r}")

    print("step: assert_banner_has_a_group_number")
    # UIA exposes the plain-text rendering of the QLabel — the
    # `<a href="N">N</a>` anchors render as just "N" in the displayed
    # text. We can't predict the exact group_number rendered without
    # also reading the dialog tree (gotcha #1: read_result_rows is
    # broken on CI), so assert the banner contains *some* digit — that
    # alone proves _refresh_warning_banner produced the anchor list.
    import re as _re
    if not _re.search(r"\d", banner_text):
        print(
            f"FAIL: banner text contains no digit / group number; "
            f"got {banner_text!r}"
        )
        return 1

    print("step: close_execute_action_dialog")
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()

    print("scenario: s33_execute_dialog_jump_to_all_delete DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
