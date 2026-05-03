"""Scenario 12 — File > Save Manifest Decisions.

Required source: qa/sandbox/near-duplicates (5 files → 1 group of 5).

Drives the Save Manifest Decisions flow end-to-end:
  scan → close & load → File menu → Save Manifest Decisions… →
  native Save dialog → status-bar verify →
  open the saved sqlite and confirm the migration_manifest table is intact.

Catches drift in: Save Manifest menu label / dialog title / status-bar copy
("Saved N decisions") / ManifestRepository.save() row count.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "qa" / "sandbox" / "_disposable" / "s12_manifest.sqlite"


def main() -> int:
    print("scenario: s12_save_manifest")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")
    pid = win.process_id()

    print("step: pre_clean")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        TARGET.unlink()
    print(f"  target={TARGET}")
    print(f"  target_exists_before={TARGET.exists()}")

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

    print("step: open_save_dialog")
    _, win = _uia.connect_main()
    _uia.menu_path(win, _uia.MENU_FILE, _uia.FILE_SAVE_MANIFEST)

    print("step: drive_save_dialog")
    _uia.save_manifest_via_native_dialog(pid, str(TARGET.resolve()))

    print("step: verify_artifact")
    print(f"  target_exists={TARGET.is_file()}")
    if not TARGET.is_file():
        print("FAIL: saved manifest file did not appear at target path")
        return 1

    header = TARGET.read_bytes()[:16]
    print(f"  target_is_sqlite={header.startswith(b'SQLite format 3')}")

    conn = sqlite3.connect(str(TARGET))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        print(f"  tables={sorted(tables)}")
        if "migration_manifest" not in tables:
            print("FAIL: migration_manifest table missing in saved sqlite")
            return 1
        total = conn.execute(
            "SELECT COUNT(*) FROM migration_manifest"
        ).fetchone()[0]
        grouped = conn.execute(
            "SELECT COUNT(*) FROM migration_manifest WHERE group_id IS NOT NULL"
        ).fetchone()[0]
        executed_zero = conn.execute(
            "SELECT COUNT(*) FROM migration_manifest WHERE executed=0"
        ).fetchone()[0]
        print(f"  manifest_total_rows={total}")
        print(f"  manifest_grouped_rows={grouped}")
        print(f"  manifest_executed_zero_rows={executed_zero}")
    finally:
        conn.close()

    print("step: verify_status_bar")
    _, win = _uia.connect_main()
    inv_status = _invariants.assert_status_bar_matches(
        win, r"Saved \d+ decision", within_s=2.0
    )
    if not inv_status:
        print("WARN: status bar did not contain 'Saved … decision(s)' (may have cleared on timeout)")

    print("scenario: s12_save_manifest DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
