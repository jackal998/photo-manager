"""Scenario 11 — Video + Live Photo.

Required sources: qa/sandbox/videos, qa/sandbox/live-photo
Probes: MP4/MOV recognized, no pHash for video, IMG_0001 HEIC+MOV pair
grouped, action propagation works for video pairs.
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s11_video_live")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")
    # Look for any video-specific messages (no pHash, etc.)
    video_lines = [l for l in log.splitlines() if any(
        k in l.lower() for k in ("video", "mp4", "mov", "live", "no phash", "skip phash")
    )]
    print(f"  video_log_lines={len(video_lines)}")
    for v in video_lines[:10]:
        print(f"  v_log: {v.strip()[:200]}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")
    # Surface .mov / .mp4 entries
    video_files = []
    for r in rows:
        for c in r.cells:
            cl = c.lower()
            if cl.endswith(".mov") or cl.endswith(".mp4"):
                video_files.append(c)
    print(f"  video_files_in_results={video_files}")

    print("scenario: s11_video_live DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
