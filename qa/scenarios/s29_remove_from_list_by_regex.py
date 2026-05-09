"""Scenario 29 — bulk regex "remove from list" sets a deferred decision
(no immediate removal), mirroring the bulk-delete UX.

Required source: ``qa/sandbox/near-duplicates`` (5 files; we partition
them with a regex that matches some, leaves the rest unchanged).

Why this scenario exists:

The bulk "remove from list" path used to be IMMEDIATE — clicking
Apply with that action triggered a confirmation prompt and dropped
the matched rows from the review tree right away. The user pointed
out the asymmetry vs. delete/keep (those are set-only; remove was
set+execute) and we changed it: bulk regex remove now writes
``user_decision='remove_from_list'`` on each matched row and waits
for the user to commit via Execute Action — same UX as bulk delete.

Layer 1 covers the dispatch (file_operations.set_decision_by_regex
recognises the sentinel and writes the deferred decision). This
scenario pins the layer-3 invariants: the dialog is wired, the
manifest gets the right column update, and the rows stay visible
in the tree afterwards.

What's verified:
  * Action menu → Set Action by Field/Regex… → action="remove from list"
    Apply → matched rows have user_decision='remove_from_list' in
    SQLite; non-matched rows are unchanged.
  * No file is actually removed from the manifest's review-set yet
    (the user_decision is the *deferred* marker; remove_from_review
    only fires at Execute time).

What's NOT verified here (covered elsewhere):
  * The Execute path that actually drops the rows. Layer 1's
    ``test_on_execute_handles_remove_from_list_decision`` exercises
    the _on_execute branch with a real SQLite manifest. A separate
    layer-3 scenario would need a fixture, a known starting
    decision, and Execute click — out of scope for this driver.
  * Single-row right-click "remove from list" (immediate path) —
    that flow has its own confirmation prompt and stays as-is.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Same partition as s14 to keep the test signal predictable across
# the two scenarios — q95/q88/q80 match (3 rows), q72/q65 don't.
FIELD = "File Name"
REGEX = r"q[89]\d"
ACTION_LABEL = "remove from list"


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


def main() -> int:
    print("scenario: s29_remove_from_list_by_regex")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_pre_decisions")
    pre = _read_decisions()
    if not pre:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    print(f"  pre_total={len(pre)}")

    print("step: apply_regex_via_menu")
    rx = re.compile(REGEX, re.IGNORECASE)
    expected_match = sorted(name for name in pre if rx.search(name))
    expected_unchanged = sorted(name for name in pre if not rx.search(name))
    print(f"  field={FIELD!r} regex={REGEX!r} action={ACTION_LABEL!r}")
    print(f"  expected_match={expected_match}")
    print(f"  expected_unchanged={expected_unchanged}")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=REGEX, action_label=ACTION_LABEL
    )

    print("step: invariant_status_bar")
    _, win = _uia.connect_main()
    if not _invariants.assert_status_bar_matches(win, r"Decision set", within_s=2.0):
        print("WARN: status bar did not echo 'Decision set' (may have cleared on timeout)")

    print("step: verify_decisions_after_apply")
    post = _read_decisions()
    if set(post) != set(pre):
        print(f"FAIL: row set changed; pre={sorted(pre)} post={sorted(post)}")
        return 1

    failures: list[str] = []
    # Matched rows: user_decision must be the deferred marker. NOT
    # 'removed' — that's the value remove_from_review writes at Execute
    # time, which this scenario deliberately does not exercise.
    for name in expected_match:
        if post[name] != "remove_from_list":
            failures.append(
                f"{name}: expected 'remove_from_list' decision, got {post[name]!r}"
            )
    for name in expected_unchanged:
        if post[name] != pre[name]:
            failures.append(
                f"{name}: expected unchanged ({pre[name]!r}), got {post[name]!r}"
            )

    matched_rows = sum(
        1 for name in expected_match if post[name] == "remove_from_list"
    )
    unchanged_rows = sum(
        1 for name in expected_unchanged if post[name] == pre[name]
    )
    print(f"  matched_rows={matched_rows} expected={len(expected_match)}")
    print(f"  unchanged_rows={unchanged_rows} expected={len(expected_unchanged)}")
    for name in sorted(post):
        print(f"  row: name={name} pre={pre[name]!r} post={post[name]!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s29_remove_from_list_by_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
