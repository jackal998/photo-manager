"""Scenario 9 — Walker exclusion rules.

Required sources: qa/sandbox/walker-exclusions
Probes: only the 2 real photos appear; sidecar.json, Thumbs.db, desktop.ini
correctly skipped. When the fixture also contains a symlink (created at
fixture-build time on platforms that permit it — see
``scripts/make_qa_sandbox._ensure_walker_symlink``), additionally probes
that the walker's symlink-skip guard fires end-to-end through the GUI
scan worker.
"""
from __future__ import annotations

import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
SYMLINK_FIXTURE = (
    REPO / "qa" / "sandbox" / "walker-exclusions" / "symlink_to_real.jpg"
)


def main() -> int:
    print("scenario: s09_walker_exclusions")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    # Check the "→ N files" line — should show 2 if exclusions worked
    for line in log.splitlines():
        if "files" in line.lower() and "→" in line:
            print(f"  walker_count_line: {line.strip()[:200]}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")
    # Surface filenames seen — these should be the 2 real photos only
    files = []
    for r in rows:
        for c in r.cells:
            if "." in c and "\\" not in c and "/" not in c and len(c) < 60:
                if any(c.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".webp")):
                    files.append(c)
    print(f"  files_in_results={files}")
    # Excluded files we DON'T want to see
    excluded = ["sidecar.json", "Thumbs.db", "desktop.ini"]
    for ex in excluded:
        present = any(ex in r.cells[1] if len(r.cells) > 1 else False for r in rows)
        print(f"  excluded_file_present: {ex}={present}")

    # Optional symlink probe — fires only when the fixture builder was able
    # to create symlink_to_real.jpg (i.e. the dev box / runner has the
    # SeCreateSymbolicLink privilege on Windows, or is on Linux/macOS).
    # When the symlink IS present, assert it does NOT appear in result
    # rows — the walker's is_symlink() guard at scanner/walker.py should
    # have skipped it. This is the layer-3 companion to test_walker.py
    # tests at lines 50/75 (which themselves skip on Windows-without-
    # elevation but run on the windows-latest CI runner).
    print("step: probe_symlink_exclusion")
    if SYMLINK_FIXTURE.is_symlink():
        symlink_name = SYMLINK_FIXTURE.name
        leaked = any(
            symlink_name in r.cells[1] if len(r.cells) > 1 else False
            for r in rows
        )
        print(f"  symlink_fixture_present=True name={symlink_name!r}")
        print(f"  symlink_in_results={leaked}")
        if leaked:
            print(f"FAIL: walker leaked symlink {symlink_name!r} into result rows")
            return 1
    else:
        # Fall through silently — fixture not creatable on this platform.
        # The layer-1 mock test (test_skips_files_when_path_reports_as_symlink)
        # covers the same code branch via monkeypatched is_symlink().
        print(f"  symlink_fixture_present=False (path={SYMLINK_FIXTURE})")
        print("  symlink_check=skipped (fixture not creatable on this platform)")

    print("scenario: s09_walker_exclusions DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
