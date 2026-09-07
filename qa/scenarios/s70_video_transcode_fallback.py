"""Scenario 70 — V2 video transcode fallback (Qt manifest guard).

Required source: qa/sandbox/video-playback (clip.mp4 VP9-in-MP4 fixture).

Qt intent:
  Mirror s69's Qt driver — verify the scanner walks, hashes, and groups the
  MP4 fixture correctly so the row is observable in the manifest.  This is
  the Qt-side prerequisite for the web transcode fallback scenario (s70 web
  port): if the file doesn't appear in the manifest, the browser player
  never gets a row to drive the synthetic-error swap.

  The swap-once client logic (onError → transcode=h264) is web-only and
  has no Qt equivalent; the Qt driver therefore pins only the manifest
  plumbing (same as s69).

  The clip.mp4 fixture is a 2-second VP9-in-MP4 clip generated once with
  ffmpeg (see qa/sandbox/video-playback/).  A byte-identical twin is written
  by the Qt runner so the pair forms a 2-member EXACT group and survives the
  singleton-drop (s05 pattern).

  Verification: reads the persisted manifest via SQLite after the GUI scan
  completes; asserts clip.mp4 is present with a non-null group_id and
  user_decision==''. Uses the same SQLite verification path as s11/s69.

Web parity:
  The web counterpart (qa/web/scenarios/s70_video_transcode_fallback.py)
  extends this by dispatching a synthetic error event on the <video> element
  and asserting the src swaps to include transcode=h264.  That frontend-swap
  assertion is the V2 deliverable.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

_VIDEO_DIR = REPO / "qa" / "sandbox" / "video-playback"
_CLIP_NAME = "clip.mp4"


def run() -> None:
    """Scan video-playback fixture; verify clip.mp4 appears in the manifest.

    Raises AssertionError or RuntimeError on failure.
    """
    clip_src = _VIDEO_DIR / _CLIP_NAME
    if not clip_src.exists():
        print(
            f"SKIP s70: video fixture missing at {clip_src}. "
            f"Generate with: ffmpeg -y -f lavfi "
            f"-i testsrc=duration=2:size=320x240:rate=15 "
            f"-c:v libvpx-vp9 -b:v 200k {clip_src}",
            file=sys.stderr,
        )
        return

    tmpdir = tempfile.mkdtemp(prefix="qa_s70_qt_")
    twin_name = "clip_twin.mp4"
    try:
        shutil.copy(str(clip_src), str(Path(tmpdir) / _CLIP_NAME))
        shutil.copy(str(clip_src), str(Path(tmpdir) / twin_name))

        _uia.open_scan_dialog()
        _uia.set_scan_source(tmpdir)
        _uia.click_start_scan()
        _uia.wait_scan_complete()

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Read the manifest via SQLite ─────────────────────────────────────────
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")

    conn = sqlite3.connect(str(MANIFEST_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT source_path, group_id, user_decision "
            "FROM migration_manifest WHERE source_path LIKE '%clip.mp4'"
        ).fetchall()
    finally:
        conn.close()

    clip_rows = [dict(r) for r in rows]
    assert clip_rows, (
        f"clip.mp4 not found in manifest after scan. "
        f"Check that VIDEO_EXTENSIONS includes .mp4 and the walker scanned the tmpdir."
    )
    for row in clip_rows:
        assert row["group_id"], (
            f"clip.mp4 has a null/empty group_id: {row}. "
            f"Expected a 2-member EXACT group (byte-identical clip + twin)."
        )
        assert row["user_decision"] == "", (
            f"clip.mp4 has unexpected user_decision={row['user_decision']!r} "
            f"immediately after scan (expected '' = undecided)."
        )
