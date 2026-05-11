"""Scenario 32 — Lock-confirm dialog for bulk regex (#182, supersedes #175).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Drives the new unified lock-confirm flow (replaces the old silent
skip-locked behavior #175 shipped). The confirm dialog fires whenever a
bulk regex would touch a locked row; verdict drives outcome:

  * Unlock & Apply to All  — unlock locked rows + apply to everything
  * Apply to Unlocked Only — skip locked rows (today's silent behavior,
                             now an explicit user choice)
  * Cancel                 — abort without writing anything

This scenario exercises the most interesting verdict end-to-end —
**Apply to Unlocked Only** — because it tests the integration without
mutating lock state (so post-state is easy to assert via sqlite). The
other two verdicts are pinned at layer 1 in
``tests/test_file_operations.py::TestSetDecisionByRegexLockConfirm``.

Flow:
  scan → close & load → bulk-lock q95 via regex (lock sentinel, free) →
  bulk-delete q[89]\\d via regex → lock-confirm dialog appears
  (q95 locked, q88/q80 unlocked) → click "Apply to Unlocked Only" →
  verify q95 still locked + no decision, q88+q80 now decision='delete'.

Sister to s14 (same fixture + partition; non-locked bulk delete) and
s30 / s31 (regex-dialog UX). Replaces the old s32 module name
``s32_lock_protects_from_bulk_regex`` — the underlying lock-vs-bulk
relationship changed semantics, so the scenario name follows.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

LOCK_REGEX = r"q95"
DESTRUCTIVE_REGEX = r"q[89]\d"
FIELD = "File Name"


def _read_state() -> dict[str, tuple[str, bool]]:
    """Return {basename: (user_decision, is_locked)} for every fixture row."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, is_locked "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: ((d or ""), bool(loc)) for p, d, loc in rows}


def _print_state(label: str, state: dict[str, tuple[str, bool]]) -> None:
    for name in sorted(state):
        decision, locked = state[name]
        glyph = "🔒" if locked else "  "
        print(f"  {label}  {glyph} {name}  decision={decision!r}")


def main() -> int:
    print("scenario: s32_lock_confirm_bulk_regex")
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
    _print_state("init", initial)
    if any(loc for _d, loc in initial.values()):
        print("FAIL: fresh manifest has unexpected locked rows")
        return 1

    # ── Step A: lock q95 (lock sentinel is FREE — no confirm dialog) ─────
    print(f"step: lock_via_regex regex={LOCK_REGEX!r} action='lock'")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=LOCK_REGEX, action_label="lock"
    )
    after_lock = _read_state()
    _print_state("lkd ", after_lock)
    locked_rows = [n for n, (_d, loc) in after_lock.items() if loc]
    if locked_rows != ["neardup_00_q95.jpg"]:
        print(f"FAIL: expected only neardup_00_q95.jpg locked, got {locked_rows}")
        return 1

    # ── Step B: destructive regex hits q95 (locked) + q88/q80 (unlocked).
    # The lock-confirm dialog must appear; clicking "Apply to Unlocked Only"
    # leaves q95 untouched and applies 'delete' to q88 + q80. ────────────
    print(
        f"step: delete_via_regex regex={DESTRUCTIVE_REGEX!r} action='delete' "
        f"verdict={_uia.LOCK_CONFIRM_APPLY_UNLOCKED_ONLY!r}"
    )
    _uia.mark_all_via_regex_standalone(
        win,
        field=FIELD,
        regex=DESTRUCTIVE_REGEX,
        action_label="delete",
        expect_lock_confirm=_uia.LOCK_CONFIRM_APPLY_UNLOCKED_ONLY,
    )
    after_delete = _read_state()
    _print_state("del ", after_delete)

    # q95 — locked, decision stays cleared (skipped at user's request)
    q95 = after_delete["neardup_00_q95.jpg"]
    if q95 != ("", True):
        print(
            f"FAIL: locked q95 must stay decision='' is_locked=True; "
            f"got decision={q95[0]!r} locked={q95[1]}"
        )
        return 1
    # q88 + q80 — unlocked matches, decision now 'delete'
    expected_deleted = ["neardup_01_q88.jpg", "neardup_02_q80.jpg"]
    actually_deleted = sorted(
        n for n, (d, _l) in after_delete.items() if d == "delete"
    )
    if actually_deleted != expected_deleted:
        print(
            f"FAIL: expected {expected_deleted} to receive delete; "
            f"got {actually_deleted}"
        )
        return 1
    # Non-matches untouched
    for non_match in ("neardup_03_q72.jpg", "neardup_04_q65.jpg"):
        if after_delete[non_match][0] != "":
            print(f"FAIL: non-match {non_match} unexpectedly modified")
            return 1

    print("scenario: s32_lock_confirm_bulk_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
