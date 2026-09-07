"""Web scenario s70 — V2 video transcode fallback: synthetic error → src swap.

Verifies the client-side swap-once logic end-to-end WITHOUT requiring ffmpeg:

  1. Scan the existing clip.mp4 fixture + a byte-identical twin so the pair
     survives singleton-drop (s05/s69 duplicate-to-observe pattern).
  2. Select the video file row → PreviewPane renders a <video> element.
  3. Dispatch a synthetic ``error`` event on the <video> via page.evaluate.
  4. Assert the <video> src now contains ``transcode=h264`` (swap fired once).
  5. Assert the src does NOT contain ``transcode=h264`` before the error (guard
     against a regression that sets the transcode URL from the start).

The transcode backend is deliberately NOT invoked — the h264 src URL returns
whatever the server returns (likely 501 if ffmpeg is absent on the dev/CI rig,
or 200 if ffmpeg is present).  This scenario pins the FRONTEND swap logic, not
the encode quality.  The real H.264 proof lives in the integration test.

Fixture: qa/sandbox/video-playback/clip.mp4 (same as s69).
Divergence from Qt: web uses page.evaluate to dispatch synthetic events; Qt
scenario uses UIA to observe the player state.
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
    """Scan video fixture, trigger synthetic error, assert src swap to h264.

    Raises AssertionError on any regression.
    """
    assert _CLIP.exists(), (
        f"Video fixture missing: {_CLIP}. "
        f"Generate with: ffmpeg -y -f lavfi "
        f"-i testsrc=duration=2:size=320x240:rate=15 "
        f"-c:v libvpx-vp9 -b:v 200k {_CLIP}"
    )

    tmpdir = tempfile.mkdtemp(prefix="qa_s70_")
    src_dir = os.path.join(tmpdir, "s70_source")
    db_path = os.path.join(tmpdir, "s70_manifest.db")
    try:
        os.makedirs(src_dir, exist_ok=True)
        shutil.copy(str(_CLIP), os.path.join(src_dir, _CLIP_NAME))
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

            # ── Locate the video row ─────────────────────────────────────────
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

            # ── Click the file row to select it ─────────────────────────────
            file_path = item["file_path"]
            basename = Path(file_path).name
            group_number = manifest["groups"][0]["group_number"]
            row_testid = row_file_testid(str(group_number), basename)
            row_locator = page.get_by_test_id(row_testid)
            row_locator.wait_for(state="visible", timeout=10_000)
            row_locator.click()

            # ── Assert <video> is rendered (not <img>) ───────────────────────
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
                f"PreviewPane rendered <{tag}> instead of <video>. "
                f"Ensure media_type='video' is set and PreviewPane.tsx branches on it."
            )

            # ── Guard: src must NOT contain transcode=h264 BEFORE the error ─
            src_before = page.evaluate(
                """(testid) => {
                    const el = document.querySelector('[data-testid="' + testid + '"]');
                    return el ? el.src : null;
                }""",
                preview_testid,
            )
            assert src_before is not None, "Could not read <video> src"
            assert "transcode=h264" not in src_before, (
                f"src already contains transcode=h264 BEFORE any error was fired: "
                f"{src_before!r}. The transcode flag should only appear after an error."
            )

            # ── Trigger the swap and assert the transcode REQUEST fires ──────
            # Dispatching a synthetic 'error' is the codec-independent way to
            # exercise the swap-once logic. We assert via the NETWORK request
            # (not by re-reading the element's src afterward): in CI the
            # transcode URL 501s (no ffmpeg), which fires a SECOND error that
            # flips the component to its terminal state and removes the <video>
            # — so reading src after the swap races with that removal. The
            # request, once made, is a stable observable that proves the swap.
            with page.expect_request(
                lambda r: "transcode=h264" in r.url, timeout=5_000
            ) as req_info:
                page.evaluate(
                    """(testid) => {
                        const el = document.querySelector('[data-testid="' + testid + '"]');
                        if (el) {
                            el.dispatchEvent(new Event('error', { bubbles: true }));
                        }
                    }""",
                    preview_testid,
                )

            transcode_req = req_info.value
            assert transcode_req is not None and "transcode=h264" in transcode_req.url, (
                "Frontend did not request the transcode URL after a <video> decode "
                "error. Check the onError handler in PreviewPane.tsx sets "
                "useTranscode=true (swapping src to /api/media?...&transcode=h264)."
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
