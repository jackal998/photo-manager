"""Scenario 69 — Video file scanned and appears in the Qt manifest (web video-playback V1 guard).

Required source: qa/sandbox/video-playback (clip.mp4 VP9-in-MP4 fixture).

Qt intent:
  Verify the scanner walks, hashes, and groups the MP4 fixture correctly —
  confirming the video file reaches the manifest with a proper group_id.
  This is the Qt-side prerequisite for the web playback scenario (s69 web
  port): if the file doesn't appear in the manifest, the browser player
  never gets a row to select.

  The clip.mp4 fixture is a 2-second VP9-in-MP4 clip generated once with
  ffmpeg (see qa/sandbox/video-playback/). VP9-in-MP4 is used because:
  (a) .mp4 is in scanner/media.VIDEO_EXTENSIONS so it is walked by the
      scanner; and (b) VP9 is an open codec always present in headless
      Chromium (no proprietary codec gate in CI).
  A byte-identical twin is written by the Qt runner so the pair forms a
  2-member EXACT group and survives the singleton-drop (s05 pattern).

  Verification: reads the persisted manifest via SQLite after the GUI scan
  completes; asserts clip.mp4 is present with a non-null group_id and
  user_decision==''. Uses the same SQLite verification path as s11/s08/s09.

Web parity:
  The web counterpart (qa/web/scenarios/s69_video_playback.py) extends this
  by also asserting the browser renders a <video> element for the row and
  that real decode + playback occur (readyState >= 2, currentTime > 0 after
  play()). That layer-3 web assertion is the primary V1 deliverable.

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
            f"SKIP s69: video fixture missing at {clip_src}. "
            f"Generate with: ffmpeg -y -f lavfi -i testsrc=duration=2:size=320x240:rate=15 "
            f"-c:v libvpx-vp9 -b:v 200k {clip_src}",
            file=sys.stderr,
        )
        return

    # ── Copy fixture into a disposable tmpdir so the scan root is controlled
    # and the twin doesn't pollute the sandbox.
    tmpdir = tempfile.mkdtemp(prefix="qa_s69_qt_")
    twin_name = "clip_twin.mp4"
    try:
        shutil.copy(str(clip_src), str(Path(tmpdir) / _CLIP_NAME))
        # Byte-identical twin to form a 2-member group (s05 pattern).
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
        f"Check that VIDEO_EXTENSIONS includes .mp4 and the walker scanned {tmpdir!r}."
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
