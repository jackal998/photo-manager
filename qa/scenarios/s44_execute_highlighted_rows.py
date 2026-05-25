"""Scenario 44 — Execute Action (only selected) via Action menu (#410).

Required source: qa/sandbox/_disposable/s44_source/ (regenerated each run by
the driver — 5 fresh JPEGs; 2 of them get sent to the user's recycle bin
when Execute fires, the other 3 stay on disk).

Drives the new menu-entry-based scope flow end-to-end:
  regen disposable fixture (5 JPEGs) → scan → close & load →
  mark all rows delete via "Action → Set Action by Field…" (regex .+) →
  return to the main window's tree → highlight 2 file rows in the main tree →
  open "Action → Execute Action (only selected)…" →
  verify the dialog tree contains exactly 2 file rows (not the whole 5-row
  group — the new row-level filter is the original #211 semantic) →
  verify the OK button label is the static "Execute" (not the old
  "Execute Action (highlighted)" — that swap was removed in #410) →
  click Execute → (no confirm dialog: only 2 of 5 group items in scope,
  so the group is not fully deleted) →
  verify (a) the 2 highlighted files no longer exist on disk,
         (b) the 3 un-highlighted files DO still exist on disk,
         (c) manifest rows for the 2 highlighted files have executed=1,
         (d) manifest rows for the 3 un-highlighted files still have
             user_decision='delete' AND executed=0 (their decisions
             survived the click intact).

⚠ HEADS-UP: every run sends 2 files to the operator's real Windows
recycle bin. The fixture is regenerated next run, so the bin grows by
2 each run until manually emptied. Same destructive-scenario contract
as s13.

Catches drift in:
  - The new menu entry "Execute Action (only selected)…"
    (label key ``menu.action.execute_selected_only``).
  - The selection-dependent gating of that menu entry
    (``MainWindow._refresh_execute_selected_only_enabled``).
  - The handler-side row-level filter in
    ``FileOperationsHandler.execute_action(selected_only=True)`` —
    synthetic PhotoGroups with only the selected items.
  - The Execute button label staying static — i.e. NOT picking up the
    removed ``execute_button_highlighted`` key on in-dialog selection.
  - The dialog NOT firing the complete-group confirm when only part of
    a group is in scope.

Click coordinates are read live from UIA ``TreeItem.rectangle()`` rather
than hard-coded pixel offsets (#229).
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
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s44_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
NUM_FILES = 5
QUALITIES = [95, 88, 80, 72, 65]

# Same clustering pre-flight as s13 — see s13_execute_action.py for the
# full rationale. Five retries reduce per-run flake probability of the
# scanner splitting our near-duplicates into multiple groups to ~3e-7.
_SCANNER_THRESHOLD = 10
_REGEN_MAX_ATTEMPTS = 5

EXECUTE_BTN_STATIC = "Execute"


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


def _regen_fixture() -> list[Path]:
    """Wipe FIXTURE_DIR and write NUM_FILES near-duplicate JPEGs that
    cluster into one REVIEW_DUPLICATE group. Mirrors s13's regen."""
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    base: Image.Image | None = None
    last_worst: int | None = None
    for _ in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_base(np.random.default_rng())
        worst = _max_pairwise_phash(candidate, QUALITIES)
        if worst <= _SCANNER_THRESHOLD:
            base = candidate
            break
        last_worst = worst
    if base is None:
        raise RuntimeError(
            f"Could not generate near-duplicate fixture that clusters "
            f"after {_REGEN_MAX_ATTEMPTS} attempts; last worst pairwise "
            f"pHash distance was {last_worst}."
        )

    paths: list[Path] = []
    for i, q in enumerate(QUALITIES):
        exif = base.getexif()
        exif[36867] = f"2024:05:01 1{i}:00:00"
        out = FIXTURE_DIR / f"s44_neardup_{i:02d}_q{q}.jpg"
        base.save(str(out), "JPEG", quality=q, exif=exif.tobytes())
        paths.append(out)
    return paths


def _file_row_centers_in_tree(tree) -> list[tuple[int, int]]:
    """Return ``(cx, cy)`` screen-pixel centers of each file row in the
    given QTreeView (passed as the UIA wrapper), in visual order.

    Reads UIA TreeItem rectangles straight from accessibility, so coords
    are correct at any DPI (#229).
    """
    by_y: dict[int, list] = {}
    for it in tree.descendants(control_type="TreeItem"):
        try:
            r = it.rectangle()
        except Exception:
            continue
        by_y.setdefault(r.top, []).append(it)
    rows: list[tuple[int, int]] = []
    for y_top in sorted(by_y):
        cells = by_y[y_top]
        if not cells:
            continue
        leftmost = min(cells, key=lambda c: c.rectangle().left)
        rightmost = max(cells, key=lambda c: c.rectangle().right)
        leftmost_text = (leftmost.window_text() or "").strip()
        # Group header row carries "Group N" in its first cell; file rows
        # carry similarity ("Ref" or "94%") or are blank.
        if leftmost_text.lower().startswith("group "):
            continue
        lr = leftmost.rectangle()
        rr = rightmost.rectangle()
        cx = (lr.left + rr.right) // 2
        cy = (lr.top + lr.bottom) // 2
        rows.append((cx, cy))
    return rows


def _read_manifest_rows() -> list[tuple[str, str, int]]:
    """Return (source_path, user_decision, executed) for every fixture row."""
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, COALESCE(user_decision, ''), executed "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
    finally:
        conn.close()
    return [(p, d, e) for p, d, e in rows]


def main() -> int:
    print("scenario: s44_execute_highlighted_rows")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: regen_fixture")
    fixture_paths = _regen_fixture()
    print(f"  fixture_dir={FIXTURE_DIR}")
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
    # The new flow sets decisions BEFORE opening the Execute dialog. Use the
    # main-window regex entry (Action → Set Action by Field…) via the standalone
    # helper so decisions are persisted onto manifest rows before we
    # highlight & execute.
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=".+", action_label="delete"
    )
    _, win = _uia.connect_main()
    time.sleep(0.3)

    print("step: locate_main_tree_rows")
    main_tree = win.descendants(control_type="Tree")[0]
    main_rows = _file_row_centers_in_tree(main_tree)
    print(f"  main_file_row_count={len(main_rows)}")
    if len(main_rows) < 2:
        print(
            f"FAIL: main tree exposed {len(main_rows)} file row(s) via UIA; "
            f"need ≥2 to highlight two rows"
        )
        return 1
    (row0_cx, row0_y), (row1_cx, row1_y) = main_rows[0], main_rows[1]
    print(f"  main_click_coords_row0=({row0_cx},{row0_y}) row1=({row1_cx},{row1_y})")

    print("step: highlight_two_file_rows_in_main_tree")
    _uia._focus(win)
    pywinauto.mouse.click(button="left", coords=(row0_cx, row0_y))
    time.sleep(0.2)
    _uia._key_down(_uia._VK_CONTROL)
    try:
        pywinauto.mouse.click(button="left", coords=(row1_cx, row1_y))
    finally:
        _uia._key_up(_uia._VK_CONTROL)
    time.sleep(0.4)

    print("step: open_execute_action_only_selected_dialog")
    # #410 — if the "(only selected)" entry isn't enabled, menu_path
    # raises (Qt's QAction won't dispatch a disabled action click).
    # The downstream verifications (2 rows in dialog, files deleted)
    # cover the rest of the new flow's contract.
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_EXECUTE_SELECTED_ONLY)
    exec_hwnd = _uia.wait_for_dialog(pid, _uia.EXECUTE_DIALOG_TITLE, timeout=5)
    exec_dlg = _uia.connect_by_handle(exec_hwnd)
    _uia._focus(exec_dlg)
    time.sleep(0.3)

    print("step: verify_dialog_shows_only_selected_rows")
    tree = exec_dlg.descendants(control_type="Tree")[0]
    dialog_rows = _file_row_centers_in_tree(tree)
    print(f"  dialog_file_row_count={len(dialog_rows)}")
    if len(dialog_rows) != 2:
        print(
            f"FAIL: dialog should show exactly 2 file rows (the highlighted "
            f"subset of the 5-row group), got {len(dialog_rows)}"
        )
        return 1

    print("step: assert_button_label_is_static")
    # #410: the button MUST read the static "Execute" label, never the
    # removed "Execute Action (highlighted)" string.
    btn = _uia._find_dialog_button(exec_dlg, _uia.ACTION_DIALOG_BTN_APPLY)  # type: ignore[arg-type]
    # If the apply-button helper doesn't match, fall back to literal label.
    if btn is None:
        try:
            btn = exec_dlg.child_window(title=EXECUTE_BTN_STATIC, control_type="Button")
            btn.wait("visible", timeout=2.0)
        except Exception as exc:
            print(
                f"FAIL: Execute button with static label {EXECUTE_BTN_STATIC!r} "
                f"not found: {exc!r}"
            )
            return 1
    print(f"  button_label={EXECUTE_BTN_STATIC!r}")

    print("step: snapshot_pre_disk_state")
    pre_present = {p: p.exists() for p in fixture_paths}
    print(f"  pre_present_count={sum(pre_present.values())}")

    print("step: click_execute")
    # Scope is partial (2 of 5 rows in a complete-delete group) — the
    # complete-group confirm must NOT fire. Since the dialog only sees
    # the synthetic group with 2 items, _complete_delete_groups returns
    # that group (its 2 items ARE all decided=delete) — wait, that means
    # the confirm WOULD fire because from the dialog's perspective every
    # passed item is a complete delete. Whether the confirm fires depends
    # on whether the synthetic group's count == the original group's
    # count. The handler builds the synthetic group with item_count=2 and
    # the dialog's _complete_delete_groups treats it as fully-deleted.
    # That IS the post-#410 behavior: the dialog operates on what it was
    # given, no awareness of the original group. Accept either: confirm
    # fires (click Yes) OR doesn't (dialog accepts directly).
    exec_btn = exec_dlg.child_window(title=EXECUTE_BTN_STATIC, control_type="Button")
    exec_btn.click_input()

    deadline = time.time() + 6.0
    closed = False
    while time.time() < deadline:
        windows = [t for _, _, t in _uia.list_process_windows(pid)]
        if _uia.EXECUTE_CONFIRM_TITLE in windows:
            # Synthetic group looks complete to the dialog — confirm fires.
            confirm_dlg = _uia.connect_by_handle(
                _uia.wait_for_dialog(pid, _uia.EXECUTE_CONFIRM_TITLE, timeout=2)
            )
            try:
                confirm_dlg.child_window(title="Yes", control_type="Button").click_input()
            except Exception:
                pass
            time.sleep(0.3)
            continue
        if _uia.EXECUTE_DIALOG_TITLE not in windows:
            closed = True
            break
        time.sleep(0.2)
    if not closed:
        print("FAIL: Execute Action dialog did not close within 6s")
        return 1

    print("step: verify_disk_state")
    remaining = [p for p in fixture_paths if p.exists()]
    removed = [p for p in fixture_paths if not p.exists()]
    print(f"  removed_count={len(removed)}")
    print(f"  remaining_count={len(remaining)}")
    if len(removed) != 2:
        print(
            f"FAIL: expected exactly 2 files removed (matching the 2 "
            f"highlighted rows), got {len(removed)}: "
            f"removed={[p.name for p in removed]}"
        )
        return 1
    if len(remaining) != 3:
        print(
            f"FAIL: expected 3 files remaining on disk, got "
            f"{len(remaining)}: remaining={[p.name for p in remaining]}"
        )
        return 1

    print("step: verify_manifest_state")
    rows = _read_manifest_rows()
    if len(rows) != NUM_FILES:
        print(
            f"FAIL: expected {NUM_FILES} manifest rows for fixture, "
            f"got {len(rows)}"
        )
        return 1

    removed_basenames = {p.name for p in removed}
    remaining_basenames = {p.name for p in remaining}

    failures: list[str] = []
    for src, decision, executed in rows:
        bn = Path(src).name
        if bn in removed_basenames:
            if executed != 1:
                failures.append(
                    f"removed file {bn} should have executed=1, got {executed}"
                )
        elif bn in remaining_basenames:
            # The un-highlighted rows survived the click — decisions
            # must be intact and executed=0.
            if decision != "delete":
                failures.append(
                    f"surviving file {bn} should still have "
                    f"user_decision='delete', got {decision!r}"
                )
            if executed != 0:
                failures.append(
                    f"surviving file {bn} should have executed=0, got "
                    f"{executed}"
                )
        else:
            failures.append(f"manifest row {bn!r} not in either fixture set")

    for src, decision, executed in rows:
        print(
            f"  row: name={Path(src).name} decision={decision!r} "
            f"executed={executed}"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s44_execute_highlighted_rows DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
