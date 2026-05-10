"""Scenario 32 — Lock state protects per-file decisions from bulk regex (#164).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Drives the full lock-protection round-trip via the regex dialog's new
``lock`` / ``unlock`` action options (constants.LOCK_SENTINEL /
UNLOCK_SENTINEL):

  Step A — bulk lock via regex
      regex=``q95`` action=``lock`` → only neardup_00_q95 gets is_locked=1.
  Step B — bulk decision skips the locked row
      regex=``q[89]\\d`` action=``delete`` → q88 + q80 receive
      user_decision='delete' but q95 is skipped (still '').
  Step C — bulk unlock via regex
      regex=``q95`` action=``unlock`` → is_locked=0 again.
  Step D — same destructive regex now applies to q95
      regex=``q95`` action=``delete`` → user_decision='delete'.

This pins the heart of #164: the lock pre-filter on destructive actions
in ``set_decision_by_regex``, the persistence of is_locked across save+
load, and the lock/unlock entries in the regex action picker
(``settable_decisions(include_lock=True)``).

Sister to s14 (bulk regex delete) and s29 (bulk regex remove-from-list).
Same fixture and partition — q95 / q88 / q80 are the matched rows for
``q[89]\\d``, so the protection is observable as exactly one of three
matches surviving unchanged.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Regex partitions chosen so the test can read like a behaviour spec:
#   q95           → matches only neardup_00_q95.jpg (the lock target)
#   q[89]\d       → matches q95 + q88 + q80 (3 rows; q95 is the locked one)
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
    print("scenario: s32_lock_protects_from_bulk_regex")
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

    print("step: snapshot_initial")
    _, win = _uia.connect_main()
    initial = _read_state()
    if not initial:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    _print_state("init", initial)
    if any(loc for _d, loc in initial.values()):
        print("FAIL: fresh manifest has unexpected locked rows")
        return 1

    # ── Step A: bulk lock via regex ────────────────────────────────────────
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
    if any(d for d, _l in after_lock.values()):
        print("FAIL: lock action must NOT modify user_decision")
        return 1

    # ── Step B: bulk delete must skip the locked row ───────────────────────
    print(f"step: delete_via_regex regex={DESTRUCTIVE_REGEX!r} action='delete'")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=DESTRUCTIVE_REGEX, action_label="delete"
    )
    after_delete = _read_state()
    _print_state("del ", after_delete)

    # q95 is locked → user_decision MUST stay '' (skipped). q88 + q80 are
    # the non-locked matches → both must show 'delete'. Non-matches (q72,
    # q65) must remain unchanged from initial.
    q95 = after_delete["neardup_00_q95.jpg"]
    if q95 != ("", True):
        print(
            f"FAIL: locked q95 must NOT be set to delete; "
            f"got decision={q95[0]!r} locked={q95[1]}"
        )
        return 1
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
    for non_match in ("neardup_03_q72.jpg", "neardup_04_q65.jpg"):
        if after_delete[non_match][0] != "":
            print(f"FAIL: non-match {non_match} unexpectedly modified")
            return 1

    # ── Step C: bulk unlock via regex ──────────────────────────────────────
    print(f"step: unlock_via_regex regex={LOCK_REGEX!r} action='unlock'")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=LOCK_REGEX, action_label="unlock"
    )
    after_unlock = _read_state()
    _print_state("ulk ", after_unlock)
    if after_unlock["neardup_00_q95.jpg"][1] is not False:
        print("FAIL: unlock did not clear is_locked on q95")
        return 1

    # ── Step D: same destructive regex now bites q95 too ───────────────────
    print(f"step: redelete_via_regex regex={LOCK_REGEX!r} action='delete'")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=LOCK_REGEX, action_label="delete"
    )
    final = _read_state()
    _print_state("fnl ", final)
    if final["neardup_00_q95.jpg"][0] != "delete":
        print(
            f"FAIL: after unlock+delete-regex, q95 should be 'delete'; "
            f"got {final['neardup_00_q95.jpg'][0]!r}"
        )
        return 1

    print("scenario: s32_lock_protects_from_bulk_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
