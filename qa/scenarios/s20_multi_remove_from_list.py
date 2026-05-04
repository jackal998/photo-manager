"""Scenario 20 — Right-click multi-selection → Remove from List.

Required sources: qa/sandbox/near-duplicates + qa/sandbox/format-dup
  → 2 groups (5 near-dup files in one group, 2 format-dup HEIC+JPG
  in another).

Drives the right-click multi-row branch routed through
ContextMenuHandler._create_multi_selection_menu →
file_operations.remove_items_from_list. Two sub-branches:

  (a) File-multi: left-click + Ctrl+click 3 file rows → right-click
      one of them → "Remove from List" → status bar "Removed 3 items
      from list"; the three target rows marked 'removed' in the
      manifest, others unchanged.
  (b) Group + file: click the format-dup group header (selects the
      whole group as one item) → Ctrl+click one remaining near-dup
      file → right-click that file → "Remove from List" → status bar
      "Removed 2 items from list" (1 group + 1 file in the
      user-visible count). DB-side: BOTH files in the format-dup
      group AND the lone near-dup file all marked 'removed' —
      verifies the group→file expansion in remove_items_from_list
      (paths_for_db spans children of selected groups).

Distinct from s15 (Set Action via single-row context menu) and from
s21 (menu-bar Remove from List). The multi-selection context-menu
branch runs through a separate handler with the group expansion
logic that no other scenario exercises.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# Branch A — three near-dup files to remove via file-multi click.
ROW_A1 = "neardup_00_q95.jpg"
ROW_A2 = "neardup_01_q88.jpg"
ROW_A3 = "neardup_02_q80.jpg"

# Branch B — the lone near-dup file to combine with the format-dup group.
ROW_B_FILE = "neardup_03_q72.jpg"

# Untouched control row — must keep its pre value across both branches.
ROW_UNTOUCHED = "neardup_04_q65.jpg"

# Format-dup group members.
FORMAT_DUP_HEIC = "scene_a.heic"
FORMAT_DUP_JPG = "scene_a.jpg"

NEAR_DUP_FILES = (ROW_A1, ROW_A2, ROW_A3, ROW_B_FILE, ROW_UNTOUCHED)
FORMAT_DUP_FILES = (FORMAT_DUP_HEIC, FORMAT_DUP_JPG)
ALL_FILES = NEAR_DUP_FILES + FORMAT_DUP_FILES


def _read_decisions() -> dict[str, str]:
    """Return {basename: user_decision} for every fixture row."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest"
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (d or "") for p, d in rows}


def _compute_group_numbers() -> dict[str, int]:
    """Return ``{basename: group_number}`` replicating ManifestRepository.load.

    The display label "Group N" is not a DB column — the repository
    derives it at load time by sorting ``group_id`` (the reference
    file's path) alphabetically and skipping groups with <2 surviving
    members. Mirrors infrastructure/manifest_repository.py:load.
    """
    from collections import defaultdict

    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, group_id, user_decision "
            "FROM migration_manifest"
        ).fetchall()
    finally:
        conn.close()

    by_group: dict[str, list[str]] = defaultdict(list)
    for source_path, group_id, user_decision in rows:
        if (user_decision or "") == "removed":
            continue
        if group_id:
            by_group[group_id].append(source_path)

    out: dict[str, int] = {}
    n = 0
    for gid in sorted(by_group):
        members = by_group[gid]
        if len(members) < 2:
            continue
        n += 1
        for source_path in members:
            out[Path(source_path).name] = n
    return out


def main() -> int:
    print("scenario: s20_multi_remove_from_list")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Setup: scan + close & load ────────────────────────────────────────
    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    # ── Snapshot pre-decisions + map basenames to group numbers ───────────
    print("step: snapshot_pre_decisions")
    pre = _read_decisions()
    if set(pre) != set(ALL_FILES):
        print(
            f"FAIL: fixture row mismatch; "
            f"got={sorted(pre)} expected={sorted(ALL_FILES)}"
        )
        return 1
    print(f"  pre={dict(sorted(pre.items()))}")

    group_map = _compute_group_numbers()
    print(f"  group_map={group_map!r}")
    near_group = group_map.get(ROW_A1)
    format_group = group_map.get(FORMAT_DUP_HEIC)
    print(f"  near_group={near_group} format_group={format_group}")
    if near_group is None or format_group is None or near_group == format_group:
        print(
            f"FAIL: expected two distinct groups, got near={near_group} "
            f"format={format_group}"
        )
        return 1

    failures: list[str] = []

    # ── (a) File-multi branch ─────────────────────────────────────────────
    print(f"step: branch_a_file_multi targets=[{ROW_A1!r}, {ROW_A2!r}, {ROW_A3!r}]")
    _uia.left_click_tree_row(win, ROW_A1)
    _uia.ctrl_click_tree_row(win, ROW_A2)
    _uia.ctrl_click_tree_row(win, ROW_A3)
    _uia.right_click_tree_row(win, ROW_A3)
    _uia.select_popup_menu_path(pid, ["Remove from List"])

    inv_a = _invariants.assert_status_bar_matches(
        win, r"Removed 3 items from list", within_s=2.5
    )
    if not inv_a:
        failures.append("branch A: status bar did not echo 'Removed 3 items from list'")

    post_a = _read_decisions()
    print(f"  post_a={dict(sorted(post_a.items()))}")
    for target in (ROW_A1, ROW_A2, ROW_A3):
        if post_a.get(target) != "removed":
            failures.append(
                f"branch A: {target} user_decision="
                f"{post_a.get(target)!r}, expected 'removed'"
            )
    for other in ALL_FILES:
        if other in (ROW_A1, ROW_A2, ROW_A3):
            continue
        if post_a.get(other) != pre.get(other):
            failures.append(
                f"branch A leaked into {other}: "
                f"pre={pre.get(other)!r} post={post_a.get(other)!r}"
            )

    # ── (b) Group + file branch ───────────────────────────────────────────
    # Click the format-dup group header (selects the group as one item),
    # then ctrl+click ROW_B_FILE (still untouched after branch A). The
    # right-click+menu surfaces remove_items_from_list with [group, file].
    group_label = f"Group {format_group}"
    print(f"step: branch_b_group_and_file group={group_label!r} file={ROW_B_FILE!r}")
    _uia.left_click_tree_row(win, group_label)
    _uia.ctrl_click_tree_row(win, ROW_B_FILE)
    _uia.right_click_tree_row(win, ROW_B_FILE)
    _uia.select_popup_menu_path(pid, ["Remove from List"])

    # User-visible count in the status bar = len(file_paths) + len(group_numbers)
    # = 1 + 1 = 2. The DB-side path expansion (format-dup files + ROW_B_FILE)
    # is what the post-state assertion verifies separately.
    inv_b = _invariants.assert_status_bar_matches(
        win, r"Removed 2 items from list", within_s=2.5
    )
    if not inv_b:
        failures.append("branch B: status bar did not echo 'Removed 2 items from list'")

    post_b = _read_decisions()
    print(f"  post_b={dict(sorted(post_b.items()))}")
    # Group→file expansion: BOTH format-dup files should now be 'removed'.
    for target in (FORMAT_DUP_HEIC, FORMAT_DUP_JPG):
        if post_b.get(target) != "removed":
            failures.append(
                f"branch B: format-dup file {target} user_decision="
                f"{post_b.get(target)!r}, expected 'removed' "
                f"(group→file expansion in remove_items_from_list)"
            )
    if post_b.get(ROW_B_FILE) != "removed":
        failures.append(
            f"branch B: lone file {ROW_B_FILE} user_decision="
            f"{post_b.get(ROW_B_FILE)!r}, expected 'removed'"
        )
    # ROW_UNTOUCHED must keep its pre value across both branches.
    if post_b.get(ROW_UNTOUCHED) != pre.get(ROW_UNTOUCHED):
        failures.append(
            f"untouched {ROW_UNTOUCHED}: pre={pre.get(ROW_UNTOUCHED)!r} "
            f"post={post_b.get(ROW_UNTOUCHED)!r}"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: invariant_actions_enabled")
    inv_actions = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv_actions:
        print(
            "FAIL: manifest-gated menu items not all enabled after "
            "Remove from List"
        )
        return 1

    print("scenario: s20_multi_remove_from_list DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
