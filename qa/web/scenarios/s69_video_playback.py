"""Web scenario s69 — Video playback: GET /api/media + <video> element.

Verifies the V1 video streaming slice end-to-end:
  1. Scan qa/sandbox/video-playback (clip.mp4 + byte-identical twin so the pair
     survives manifest singleton-drop, matching the s05/s06 duplicate-to-observe
     pattern).
  2. Select the video file row in the result tree.
  3. Assert the preview pane renders a <video> element (not <img>).
  4. Assert real decode: readyState >= 2 (HAVE_CURRENT_DATA) AND duration > 0.
  5. Call video.play(), wait, assert currentTime > 0 (frames advanced).

Fixture: qa/sandbox/video-playback/clip.mp4 — a 2-second VP9-in-MP4 test clip
generated once with ffmpeg lavfi testsrc. VP9 is an open codec always present
in Chromium; MP4 container is used because .mp4 is in scanner/media.VIDEO_EXTENSIONS
(walked by the scanner), whereas .webm is not.

The twin is created at runtime in a tmpdir (s05 pattern) so we don't commit a
second copy of the fixture.  The manifest db is placed beside the source subdir
in tmpdir.

Divergences from a Qt equivalent:
  (i)  Qt drives the single-view player via pywinauto UIA; the web port
       asserts the DOM <video> element and JS readyState/currentTime directly
       via page.evaluate — the same observables, different access path.
  (ii) Fixture is VP9-in-MP4 (open codec, CI-safe), not MOV/H.264.  Real
       .MOV / H.264 playback on actual user files is verified by LEAD on rig.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, wait_manifest_loaded
from qa.web.testid_constants import row_file_testid

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_CLIP = _REPO / "qa" / "sandbox" / "video-playback" / "clip.mp4"
_CLIP_NAME = "clip.mp4"
_TWIN_NAME = "clip_twin.mp4"

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan video fixture, select row, assert <video> renders and plays.

    Raises AssertionError on any regression.
    """
    assert _CLIP.exists(), (
        f"Video fixture missing: {_CLIP}. "
        f"Generate it with: ffmpeg -y -f lavfi -i testsrc=duration=2:size=320x240:rate=15 "
        f"-c:v libvpx-vp9 -b:v 200k {_CLIP}"
    )

    tmpdir = tempfile.mkdtemp(prefix="qa_s69_")
    src_dir = os.path.join(tmpdir, "s69_source")
    db_path = os.path.join(tmpdir, "s69_manifest.db")
    try:
        os.makedirs(src_dir, exist_ok=True)
        shutil.copy(str(_CLIP), os.path.join(src_dir, _CLIP_NAME))
        # Byte-identical twin so the pair forms a 2-member group and survives
        # the singleton-drop (s05 duplicate-to-observe pattern).
        shutil.copy(str(_CLIP), os.path.join(src_dir, _TWIN_NAME))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[src_dir],
                output_path=db_path,
                scan_timeout=120_000,
            )
            assert os.path.exists(db_path), (
                f"Scan did not write manifest: {db_path}"
            )

            wait_manifest_loaded(page, timeout=30_000)

            # ── Verify manifest contains the video row ───────────────────────
            manifest = _get_manifest(base_url, db_path)
            all_items = [
                item
                for group in manifest.get("groups", [])
                for item in group.get("items", [])
            ]
            video_items = [
                it for it in all_items
                if Path(it["file_path"]).name == _CLIP_NAME
            ]
            assert video_items, (
                f"clip.mp4 not found in manifest. Basenames: "
                f"{[Path(it['file_path']).name for it in all_items]}"
            )
            item = video_items[0]

            # ── Assert media_type field is 'video' ───────────────────────────
            assert item.get("media_type") == "video", (
                f"Expected media_type='video', got {item.get('media_type')!r}"
            )

            # ── Click the file row to select it ─────────────────────────────
            file_path = item["file_path"]
            basename = Path(file_path).name
            group_number = manifest["groups"][0]["group_number"]
            row_testid = row_file_testid(str(group_number), basename)
            row_locator = page.get_by_test_id(row_testid)
            row_locator.wait_for(state="visible", timeout=10_000)
            row_locator.click()

            # ── Assert PreviewPane renders <video>, not <img> ────────────────
            preview_testid = "preview-single-image"
            preview = page.get_by_test_id(preview_testid)
            preview.wait_for(state="visible", timeout=10_000)

            tag = page.evaluate(
                """(testid) => {
                    const el = document.querySelector('[data-testid="' + testid + '"]');
                    return el ? el.tagName.toLowerCase() : null;
                }""",
                preview_testid,
            )
            assert tag == "video", (
                f"PreviewPane rendered <{tag}> instead of <video> for a .mp4 row. "
                f"Check that media_type='video' is set on the row and that "
                f"PreviewPane.tsx branches on row.media_type."
            )

            # ── Assert real decode: readyState >= 2 AND duration > 0 ─────────
            decode_info = page.evaluate(
                """(testid) => {
                    const el = document.querySelector('[data-testid="' + testid + '"]');
                    if (!el) return null;
                    return { readyState: el.readyState, duration: el.duration };
                }""",
                preview_testid,
            )
            assert decode_info is not None, "Could not query <video> element"

            # Allow a brief settle for decode if readyState is still 0/1.
            if decode_info["readyState"] < 2:
                page.wait_for_function(
                    """(testid) => {
                        const el = document.querySelector('[data-testid="' + testid + '"]');
                        return el && el.readyState >= 2;
                    }""",
                    arg=preview_testid,
                    timeout=10_000,
                )
                decode_info = page.evaluate(
                    """(testid) => {
                        const el = document.querySelector('[data-testid="' + testid + '"]');
                        return { readyState: el.readyState, duration: el.duration };
                    }""",
                    preview_testid,
                )

            assert decode_info["readyState"] >= 2, (
                f"<video> readyState={decode_info['readyState']} (want >= 2 = HAVE_CURRENT_DATA). "
                f"The browser cannot decode the clip. Confirm clip.mp4 is a valid VP9-in-MP4."
            )
            assert decode_info["duration"] > 0, (
                f"<video> duration={decode_info['duration']} (want > 0). "
                f"The clip may be empty or unplayable."
            )

            # ── Assert frames advance on play ────────────────────────────────
            page.evaluate(
                """(testid) => {
                    const el = document.querySelector('[data-testid="' + testid + '"]');
                    if (el) el.play();
                }""",
                preview_testid,
            )
            # Wait up to 2 s for currentTime to advance past 0.
            page.wait_for_function(
                """(testid) => {
                    const el = document.querySelector('[data-testid="' + testid + '"]');
                    return el && el.currentTime > 0;
                }""",
                arg=preview_testid,
                timeout=5_000,
            )
            current_time = page.evaluate(
                """(testid) => {
                    const el = document.querySelector('[data-testid="' + testid + '"]');
                    return el ? el.currentTime : -1;
                }""",
                preview_testid,
            )
            assert current_time > 0, (
                f"<video>.currentTime={current_time} after play() — frames did not advance. "
                f"The /api/media endpoint may not be serving bytes correctly."
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
