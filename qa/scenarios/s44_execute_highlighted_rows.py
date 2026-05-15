"""Scenario 44 — Execute Action scoped to highlighted rows (#211).

Required source: qa/sandbox/_disposable/s44_source/ (regenerated each run by
the driver — 5 fresh JPEGs; 2 of them get sent to the user's recycle bin
when Execute fires, the other 3 stay on disk).

Drives the selection-scoped Execute flow end-to-end:
  regen disposable fixture (5 JPEGs) → scan → close & load →
  open Execute Action dialog → mark all rows delete via regex (.+) →
  left-click first file row + ctrl-click second file row →
  verify the OK button label flips to "Execute Action (highlighted)" →
  click Execute → (no confirm dialog: scope is partial) →
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
  - The Execute button text swap on selection change (label key
    ``execute_dialog.execute_button_highlighted``).
  - ``_selected_file_paths`` reading PATH_ROLE from COL_NAME.
  - The scope-filtered iteration inside ``_on_execute``.
  - The complete-group confirm NOT firing when scope is partial (the
    confirm copy claims "EVERY file deleted" which is false when only
    part of the group is in scope).

Click coordinates are read live from UIA ``TreeItem.rectangle()`` rather
than hard-coded pixel offsets (#229). The previous ``top+83 / top+105``
anchors were tuned at 1x DPI; at 2x DPI the first click landed on the
group header and only one file made it into the execute scope.
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

EXECUTE_BTN_HIGHLIGHTED = "Execute Action (highlighted)"


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


def _file_row_centers(tree) -> list[tuple[int, int]]:
    """Return ``(cx, cy)`` screen-pixel centers of each file row in the
    dialog tree, in visual order.

    Reads UIA TreeItem rectangles straight from accessibility, so coords
    are correct at any DPI (#229: the previous hard-coded ``top+83/+105``
    anchors were tuned at 1x DPI and missed the file rows at 2x — the
    first click landed on the group header, so only one file made it
    into the execute scope).

    The dialog's QTreeView exposes one TreeItem per CELL (column), so
    each visual row appears as ~``NUM_COLUMNS`` siblings sharing one Y
    band. We cluster by ``rect.top``, skip the cluster whose leftmost
    cell carries a "Group N" label (the group header is not a file row),
    and return the remaining bands' centers.
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

    print("step: open_execute_action_dialog")
    _, win = _uia.connect_main()
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: mark_all_delete_via_regex")
    _uia.mark_all_via_regex(
        exec_dlg, field="File Name", regex=".+", action_label="delete"
    )

    # Reconnect after the inner dialog closed.
    exec_dlg = _uia.connect_by_handle(exec_dlg.handle)

    print("step: locate_dialog_tree")
    tree = exec_dlg.descendants(control_type="Tree")[0]
    tree_rect = tree.rectangle()
    file_rows = _file_row_centers(tree)
    print(f"  tree_rect={tree_rect}")
    print(f"  file_row_count_in_tree={len(file_rows)}")
    if len(file_rows) < 2:
        print(
            f"FAIL: dialog tree exposed {len(file_rows)} file row(s) via UIA; "
            f"need ≥2 to highlight two rows"
        )
        return 1
    (row0_cx, row0_y), (row1_cx, row1_y) = file_rows[0], file_rows[1]
    print(f"  click_coords_row0=({row0_cx},{row0_y}) row1=({row1_cx},{row1_y})")

    print("step: highlight_two_file_rows")
    _uia._focus(exec_dlg)
    pywinauto.mouse.click(button="left", coords=(row0_cx, row0_y))
    time.sleep(0.2)
    _uia._key_down(_uia._VK_CONTROL)
    try:
        pywinauto.mouse.click(button="left", coords=(row1_cx, row1_y))
    finally:
        _uia._key_up(_uia._VK_CONTROL)
    time.sleep(0.3)

    print("step: assert_button_text_swapped")
    # _find_dialog_button matches on button title — under selection
    # the OK button advertises the highlighted label. If the swap
    # didn't fire, lookup fails and we surface a useful error.
    try:
        exec_dlg.child_window(
            title=EXECUTE_BTN_HIGHLIGHTED, control_type="Button"
        ).wait("visible", timeout=2.0)
    except Exception as exc:
        print(
            f"FAIL: Execute button did not pick up the highlighted label "
            f"{EXECUTE_BTN_HIGHLIGHTED!r} after ctrl-clicking 2 rows: "
            f"{exc!r}"
        )
        return 1
    print(f"  button_label={EXECUTE_BTN_HIGHLIGHTED!r}")

    print("step: snapshot_pre_disk_state")
    pre_present = {p: p.exists() for p in fixture_paths}
    print(f"  pre_present_count={sum(pre_present.values())}")

    print("step: click_execute")
    # Scope is partial (2 of 5 rows in a complete-delete group) — the
    # complete-group confirm must NOT fire. The dialog should accept
    # directly. If a confirm dialog DOES appear, the scope filter is
    # broken and this scenario times out below; surface that as a
    # FAIL rather than letting the test hang on a stray modal.
    exec_btn = exec_dlg.child_window(
        title=EXECUTE_BTN_HIGHLIGHTED, control_type="Button"
    )
    exec_btn.click_input()

    # Wait for the Execute dialog to close as the signal that execution
    # finished. Confirm dialog shouldn't appear; if it does, fail loud.
    deadline = time.time() + 5.0
    closed = False
    while time.time() < deadline:
        windows = [t for _, _, t in _uia.list_process_windows(pid)]
        if _uia.EXECUTE_CONFIRM_TITLE in windows:
            print(
                f"FAIL: {_uia.EXECUTE_CONFIRM_TITLE!r} confirm dialog "
                f"appeared with partial scope — _complete_delete_groups_in_scope "
                f"is not filtering by scope correctly"
            )
            return 1
        if _uia.EXECUTE_DIALOG_TITLE not in windows:
            closed = True
            break
        time.sleep(0.2)
    if not closed:
        print("FAIL: Execute Action dialog did not close within 5s")
        return 1

    print("step: verify_disk_state")
    # The two highlighted rows: rows 0 and 1 of the tree. The tree
    # build_model orders rows by (action_sort, similarity); under our
    # all-delete regex every row has decision='delete', and the
    # tree's sort is stable on group_number then file_path. We don't
    # rely on which two specific files vanish — only that exactly 2
    # of the 5 are gone (the click hit 2 rows) and 3 remain.
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
            # must be intact and executed=0. This is the "Unselected
            # decided rows remain in the list untouched" acceptance
            # criterion at the persistence layer.
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
