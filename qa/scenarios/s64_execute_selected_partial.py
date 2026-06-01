"""Scenario 64 — Execute Action "Execute selected" partial-execute (#483).

Required source: qa/sandbox/_disposable/s64_source/ — regenerated each run.
Two independent near-duplicate clusters (3 + 3 JPEGs) so the Execute Action
dialog renders TWO groups, each with multiple delete-decision rows. A SUBSET
of one group's rows is highlighted and partially executed via the dedicated
"Execute selected" button (Improvement 1 in the partial-execute bundle).

⚠ HEADS-UP: every run sends a subset of the 6 fixture files to the operator's
real Windows recycle bin (same trade-off as s13 / s36 / s44). The fixture is
regenerated next run. The DESTRUCTIVE-COVERAGE GUARD is satisfied because the
scenario operates ONLY on FIXTURE_DIR, an isolated disposable sandbox dir it
builds itself — never a real or shared path (asserted at startup).

Why s64 exists
--------------
Layer 1 (``tests/test_execute_action_dialog.py::TestOnExecutePartialFilter``)
mocks send2trash and the tree selection, so it pins the ``paths_filter``
plumbing but not the live wiring: that highlighting real tree rows enables the
"Execute selected" button, that clicking it deletes ONLY the highlighted rows
on disk, that un-highlighted rows keep their decisions, that the dialog STAYS
OPEN (partial execute is not a full execute), and that a subsequent full
"Execute" finishes the remaining decided rows and closes the dialog.

Flow:
  regen 2-cluster fixture (6 JPEGs) → scan → close & load →
  mark all rows delete via Action → Set Action by Field… (regex .+) →
  open Execute Action dialog → highlight 2 rows of group 1 →
  click "Execute selected" →
    assert: dialog stays OPEN; the 2 highlighted files gone from disk;
            the other 4 files still present; their manifest decisions
            still 'delete' (un-highlighted decisions intact) →
  click "Execute" (full) →
    assert: remaining decided rows execute; dialog closes; all 6 gone.

Tree-content assertions use direct sqlite reads (s14/s32/s35 pattern), not
``read_result_rows`` (its y_min filter drops rows on CI's smaller render).
Row click coords are read live from UIA ``TreeItem.rectangle()`` (#229),
mirroring s44's ``_file_row_centers_in_tree``.
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
from pathlib import Path

import imagehash
import numpy as np
import pywinauto.mouse
from PIL import Image

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s64_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# Two clusters of 3 near-duplicates each. Distinct base seeds keep the
# clusters in separate dedup groups; within a cluster the q-quality variants
# pHash-collapse into one REVIEW_DUPLICATE group (same mechanism as s13).
QUALITIES = [95, 80, 65]
_SCANNER_THRESHOLD = 10           # scanner/dedup.py default — see s13
_REGEN_MAX_ATTEMPTS = 5
EXECUTE_BTN = "Execute"
EXECUTE_SELECTED_BTN = "Execute selected"


def _build_base(rng: np.random.Generator) -> Image.Image:
    base_color = rng.integers(0, 256, size=(3,))
    fx = float(rng.uniform(0.5, 4.0))
    fy = float(rng.uniform(0.5, 4.0))
    h, w = 480, 640
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        arr[..., c] = (
            base_color[c]
            + 60 * np.sin(2 * np.pi * fx * xx / w + c)
            + 60 * np.cos(2 * np.pi * fy * yy / h + c * 0.7)
        )
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _max_pairwise_phash(base: Image.Image, qualities: list[int]) -> int:
    saved: list[Image.Image] = []
    for q in qualities:
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=q)
        buf.seek(0)
        saved.append(Image.open(buf).copy())
    hashes = [imagehash.phash(im) for im in saved]
    return max(
        hashes[i] - hashes[j]
        for i in range(len(hashes))
        for j in range(i + 1, len(hashes))
    )


def _build_cluster() -> Image.Image:
    """Return one base image whose q-variants reliably cluster (#148)."""
    last_worst: int | None = None
    for _ in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_base(np.random.default_rng())
        worst = _max_pairwise_phash(candidate, QUALITIES)
        if worst <= _SCANNER_THRESHOLD:
            return candidate
        last_worst = worst
    raise RuntimeError(
        f"Could not generate a clustering near-duplicate base after "
        f"{_REGEN_MAX_ATTEMPTS} attempts (last worst pHash distance "
        f"{last_worst})."
    )


def _regen_fixture() -> list[Path]:
    """Wipe FIXTURE_DIR and write 2 clusters × 3 JPEGs = 6 files."""
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for cluster in (1, 2):
        base = _build_cluster()
        for i, q in enumerate(QUALITIES):
            exif = base.getexif()
            exif[36867] = f"2024:0{cluster}:01 1{i}:00:00"
            out = FIXTURE_DIR / f"s64_c{cluster}_{i:02d}_q{q}.jpg"
            base.save(str(out), "JPEG", quality=q, exif=exif.tobytes())
            paths.append(out)
    return paths


def _read_state() -> dict[str, tuple[str, int]]:
    """Return {basename: (user_decision, executed)} for fixture rows."""
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, COALESCE(user_decision, ''), executed "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (d, int(e or 0)) for p, d, e in rows}


def _file_rows_in_tree(tree) -> list[tuple[str, int, int]]:
    """Return ``[(basename, cx, cy)]`` for file rows in a QTreeView.

    Mirrors s44's ``_file_row_centers_in_tree`` but also carries the File
    Name cell text so the scenario can pick specific basenames to highlight.
    Group-header rows (first cell starts with "Group ") are excluded.
    """
    by_y: dict[int, list] = {}
    for it in tree.descendants(control_type="TreeItem"):
        try:
            r = it.rectangle()
        except Exception:
            continue
        by_y.setdefault(r.top, []).append(it)
    out: list[tuple[str, int, int]] = []
    for y_top in sorted(by_y):
        cells = by_y[y_top]
        if not cells:
            continue
        leftmost = min(cells, key=lambda c: c.rectangle().left)
        rightmost = max(cells, key=lambda c: c.rectangle().right)
        leftmost_text = (leftmost.window_text() or "").strip()
        if leftmost_text.lower().startswith("group "):
            continue
        # File Name is the basename-shaped cell on the row.
        basename = ""
        for c in cells:
            t = (c.window_text() or "").strip()
            if t.lower().endswith(".jpg"):
                basename = t
                break
        lr = leftmost.rectangle()
        rr = rightmost.rectangle()
        cx = (lr.left + rr.right) // 2
        cy = (lr.top + lr.bottom) // 2
        out.append((basename, cx, cy))
    return out


def main() -> int:
    print("scenario: s64_execute_selected_partial")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # DESTRUCTIVE-COVERAGE GUARD — verify the fixture root is an isolated
    # disposable sandbox dir before any delete fires. Refuse to proceed if
    # it isn't under qa/sandbox/_disposable (a real/shared path would risk
    # deleting the operator's files).
    print("step: assert_isolated_fixture_root")
    disposable_root = (REPO / "qa" / "sandbox" / "_disposable").resolve()
    if disposable_root not in FIXTURE_DIR.resolve().parents:
        print(
            f"FAIL: refusing to run — FIXTURE_DIR {FIXTURE_DIR} is not under "
            f"the isolated disposable sandbox {disposable_root}"
        )
        return 1
    print(f"  fixture_root_isolated=True ({FIXTURE_DIR})")

    print("step: regen_fixture")
    fixture_paths = _regen_fixture()
    print(f"  fixture_count={len(fixture_paths)}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: mark_all_delete_via_main_menu_regex")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=r"s64_", action_label="delete"
    )
    _, win = _uia.connect_main()
    time.sleep(0.3)

    pre = _read_state()
    decided = [n for n, (d, _e) in pre.items() if d == "delete"]
    print(f"  rows={len(pre)} decided_delete={len(decided)}")
    if len(decided) != len(fixture_paths):
        print(
            f"FAIL: expected all {len(fixture_paths)} rows decided=delete, "
            f"got {len(decided)}: {sorted(decided)}"
        )
        return 1

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: read_dialog_file_rows")
    tree = exec_dlg.descendants(control_type="Tree")[0]
    rows = _file_rows_in_tree(tree)
    print(f"  dialog_file_rows={[bn for bn, _x, _y in rows]}")
    if len(rows) != len(fixture_paths):
        print(
            f"FAIL: Execute dialog should show all {len(fixture_paths)} file "
            f"rows across 2 groups, got {len(rows)}"
        )
        return 1

    # Highlight a SUBSET — the two cluster-1 rows (basenames begin "s64_c1_").
    subset = [(bn, cx, cy) for bn, cx, cy in rows if bn.startswith("s64_c1_")]
    if len(subset) < 2:
        print(
            f"FAIL: need ≥2 cluster-1 rows to highlight a partial subset, "
            f"found {[bn for bn, _x, _y in subset]}"
        )
        return 1
    highlight = subset[:2]
    highlight_names = [bn for bn, _x, _y in highlight]
    print(f"step: highlight_subset {highlight_names}")
    _uia._focus(exec_dlg)
    (bn0, cx0, cy0) = highlight[0]
    (bn1, cx1, cy1) = highlight[1]
    pywinauto.mouse.click(button="left", coords=(cx0, cy0))
    time.sleep(0.2)
    _uia._key_down(_uia._VK_CONTROL)
    try:
        pywinauto.mouse.click(button="left", coords=(cx1, cy1))
    finally:
        _uia._key_up(_uia._VK_CONTROL)
    time.sleep(0.4)

    print("step: click_execute_selected")
    # The "Execute selected" button should be enabled now (≥1 highlighted
    # row carries a decision). Partial execute fires the complete-group
    # confirm if the highlighted subset completes a delete-group; with 2 of
    # 3 cluster-1 rows highlighted the group is NOT complete, so no confirm
    # should appear. Poll for the dialog staying open afterward.
    sel_btn = _uia._find_dialog_button(exec_dlg, EXECUTE_SELECTED_BTN)
    if not sel_btn.is_enabled():
        print(
            "FAIL: 'Execute selected' button is disabled despite a "
            "decided highlighted row — _refresh_execute_selected_state "
            "wiring may have regressed"
        )
        return 1
    sel_btn.click_input()
    time.sleep(0.3)
    # Dismiss a complete-group confirm if one appears (shouldn't for a
    # 2-of-3 partial, but stay robust to clustering that yields a 2-row
    # group). Short timeout → no-op when absent.
    confirm_seen = False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        titles = [t for _, _, t in _uia.list_process_windows(pid)]
        if _uia.EXECUTE_CONFIRM_TITLE in titles:
            confirm_seen = True
            cdlg = _uia.connect_by_handle(
                _uia.wait_for_dialog(pid, _uia.EXECUTE_CONFIRM_TITLE, timeout=2)
            )
            try:
                cdlg.child_window(title="Yes", control_type="Button").click_input()
            except Exception:
                pass
            time.sleep(0.3)
            break
        time.sleep(0.2)
    print(f"  complete_group_confirm_seen={confirm_seen}")
    time.sleep(1.0)

    print("step: assert_dialog_stays_open")
    titles = [t for _, _, t in _uia.list_process_windows(pid)]
    if _uia.EXECUTE_DIALOG_TITLE not in titles:
        print(
            "FAIL: Execute Action dialog CLOSED after 'Execute selected' — "
            "partial execute must keep the dialog open for the remaining rows"
        )
        return 1
    print("  dialog_open_after_partial=True")

    print("step: assert_disk_state_after_partial")
    highlight_paths = [FIXTURE_DIR / bn for bn in highlight_names]
    other_paths = [p for p in fixture_paths if p.name not in highlight_names]
    deleted_highlight = [p for p in highlight_paths if not p.exists()]
    present_others = [p for p in other_paths if p.exists()]
    print(f"  highlighted_deleted={[p.name for p in deleted_highlight]}")
    print(f"  others_still_present={len(present_others)}/{len(other_paths)}")
    if len(deleted_highlight) != len(highlight_paths):
        print(
            f"FAIL: highlighted files not all deleted — "
            f"expected {highlight_names} gone, still present: "
            f"{[p.name for p in highlight_paths if p.exists()]}"
        )
        return 1
    if len(present_others) != len(other_paths):
        print(
            f"FAIL: a non-highlighted file was deleted by partial execute — "
            f"missing: {[p.name for p in other_paths if not p.exists()]}"
        )
        return 1

    print("step: assert_unhighlighted_decisions_intact")
    mid = _read_state()
    for p in other_paths:
        d, executed = mid.get(p.name, ("", 0))
        if d != "delete":
            print(
                f"FAIL: un-highlighted row {p.name} lost its decision "
                f"(decision={d!r}); partial execute should not touch "
                f"out-of-scope decisions"
            )
            return 1
    print("  unhighlighted_decisions_all_delete=True")

    print("step: full_execute_remaining")
    # The remaining 4 rows are still decided=delete. Full Execute now
    # finishes them and closes the dialog. Reuse execute_and_confirm,
    # which clicks Execute, dismisses the all-delete confirm, and waits
    # for the dialog to close.
    exec_dlg = _uia.connect_by_handle(
        _uia.wait_for_dialog(pid, _uia.EXECUTE_DIALOG_TITLE, timeout=5)
    )
    _uia._focus(exec_dlg)
    _uia.execute_and_confirm(exec_dlg)
    print("  execute_dialog_closed=True")

    print("step: assert_final_state")
    remaining = [p for p in fixture_paths if p.exists()]
    if remaining:
        print(
            f"FAIL: {len(remaining)} files still on disk after full Execute: "
            f"{[p.name for p in remaining]}"
        )
        return 1
    post = _read_state()
    not_executed = [n for n, (_d, e) in post.items() if e != 1]
    if not_executed:
        print(f"FAIL: rows still executed=0 after full Execute: {not_executed}")
        return 1
    print(f"  all_{len(fixture_paths)}_files_removed_and_executed=True")

    print("scenario: s64_execute_selected_partial DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
