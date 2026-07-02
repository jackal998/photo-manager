"""Scenario s71 — Group-loads guard for a video duplicate group (Qt lightweight).

Qt intent (LIGHTWEIGHT — does NOT require QMediaPlayer HEVC playback):
  Verify that the scanner walks, hashes, and groups a byte-identical pair of
  VP9-in-MP4 video files into a single EXACT-dup group with both members
  present in the manifest.  This is the Qt-side prerequisite for the web
  grid-video scenario (s71 web port): if the video group does not reach the
  manifest, the browser grid has no data to render.

  The clip.mp4 fixture (qa/sandbox/video-playback/clip.mp4) is a 2-second
  VP9-in-MP4 synthetic clip.  A byte-identical twin is copied at runtime
  into a disposable tmpdir so the pair forms a 2-member EXACT group and
  survives the scanner's singleton-drop (s05/s69 duplicate-to-observe pattern).

  Verification: reads the persisted manifest via SQLite after the GUI scan
  completes; asserts:
    - both clip.mp4 and clip_twin.mp4 are present (2 rows).
    - both share a non-null group_id (grouped together, not singletons).
    - both have user_decision == '' (undecided, untouched post-scan).

  This scenario does NOT:
    - Launch QMediaPlayer or assert video decode.
    - Drive the PreviewPane grid view or GroupMediaController.
    - Assert pixel_width / pixel_height (scanner may leave these null for video).
  Those asserts are the web layer's job (qa/web/scenarios/s71_grid_video_tiles.py).

Web parity:
  The web counterpart extends this by asserting the browser renders a
  GroupGrid with N tiles, a GroupMediaController bar, per-tile <video>
  decode, group-play broadcast, and master-scrub convergence.

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
_TWIN_NAME = "clip_twin.mp4"


def run() -> None:
    """Scan video-playback fixture pair; verify both members appear in the manifest.

    Raises AssertionError or RuntimeError on failure.
    Prints a SKIP notice and returns normally when the clip fixture is absent.
    """
    clip_src = _VIDEO_DIR / _CLIP_NAME
    if not clip_src.exists():
        print(
            f"SKIP s71: video fixture missing at {clip_src}. "
            f"Generate with: ffmpeg -y -f lavfi -i testsrc=duration=2:size=320x240:rate=15 "
            f"-c:v libvpx-vp9 -b:v 200k {clip_src}",
            file=sys.stderr,
        )
        return

    tmpdir = tempfile.mkdtemp(prefix="qa_s71_qt_")
    try:
        shutil.copy(str(clip_src), str(Path(tmpdir) / _CLIP_NAME))
        # Byte-identical twin to form a 2-member EXACT group (s05 pattern).
        shutil.copy(str(clip_src), str(Path(tmpdir) / _TWIN_NAME))

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
            "FROM migration_manifest "
            "WHERE source_path LIKE '%clip.mp4' "
            "   OR source_path LIKE '%clip_twin.mp4'"
        ).fetchall()
    finally:
        conn.close()

    clip_rows = [dict(r) for r in rows]
    assert len(clip_rows) >= 2, (
        f"Expected >= 2 rows (clip.mp4 + clip_twin.mp4) in manifest; "
        f"got {len(clip_rows)}. "
        f"Rows: {clip_rows}. "
        f"Check that both files were walked and that the pair was not singleton-dropped."
    )

    group_ids = {r["group_id"] for r in clip_rows}
    none_group_ids = {g for g in group_ids if not g}
    assert not none_group_ids, (
        f"At least one video member has a null/empty group_id: {clip_rows}. "
        f"Expected both clip.mp4 and clip_twin.mp4 to share a non-null group_id "
        f"(2-member EXACT-dup group)."
    )
    assert len(group_ids) == 1, (
        f"clip.mp4 and clip_twin.mp4 have DIFFERENT group_ids: {group_ids}. "
        f"Expected both to be in the SAME EXACT-dup group (byte-identical pair)."
    )

    for row in clip_rows:
        assert row["user_decision"] == "", (
            f"Video member has unexpected user_decision={row['user_decision']!r} "
            f"immediately after scan (expected '' = undecided): {row}"
        )

    print(
        f"probe_status: s71 OK — "
        f"video group_id={next(iter(group_ids))!r}, "
        f"members={[r['source_path'].rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1] for r in clip_rows]}"
    )
