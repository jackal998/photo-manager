"""Web scenario s70 — V2 video transcode fallback: synthetic error → src swap.

Verifies the client-side swap-once logic end-to-end WITHOUT requiring ffmpeg:

  1. Scan the existing clip.mp4 fixture + byte-identical copies so the group
     survives singleton-drop (s05/s69 duplicate-to-observe pattern; the third
     copy exists for the #869 steps below).
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
scenario used the desktop tree to observe the player state.

#869 — the HEVC notice, both probe verdicts FORCED
-------------------------------------------------
After the codec-independent swap above, the same manifest is re-opened in two
fresh pages (a new page = a new page life, so the #787 probe's memo and the
notice's once-per-session state start clean).  Each page gets an init script
that runs before any app code and pins the two things the notice reads:
``navigator.mediaCapabilities.decodingInfo`` (the #787 probe's input) and
``navigator.userAgentData.platform`` (``"Windows"`` — the CI runner is Linux,
and its Chromium's real HEVC answer is not ours to choose).

  6. forced ``supported``: a transcode happens (synthetic error → transcode
     request) and the notice NEVER appears.
  7. forced ``unsupported``: the notice appears after the first transcode,
     is still exactly ONE after a second transcode (the twin), and after
     Dismiss a third transcode (the third copy) does not bring it back.

Each forced branch first reads the stubbed values back from the page, so a
seam that silently stopped applying fails loudly instead of passing branch 6
vacuously.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_row,
    load_manifest,
    run_scan,
    wait_manifest_loaded,
)
from qa.web.testid_constants import (
    PREVIEW_HEVC_HINT,
    PREVIEW_HEVC_HINT_DISMISS,
    PREVIEW_HEVC_HINT_STORE_LINK,
    PREVIEW_SINGLE_IMAGE,
    row_file_testid,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_CLIP = _REPO / "qa" / "sandbox" / "video-playback" / "clip.mp4"
_CLIP_NAME = "clip.mp4"
_TWIN_NAME = "clip_twin.mp4"
# #869 step 7 needs THREE distinct transcodes in one page life. Re-selecting a
# video whose transcode URL that page already loaded can be answered from the
# browser's media cache with no new request, so the step that proves "no
# notice after Dismiss" transcodes a file no earlier step touched.
_THIRD_NAME = "clip_third.mp4"

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
# #869 — forced HEVC verdict (init script) + helpers
# ---------------------------------------------------------------------------

# Runs before any page script on every navigation of the page it is added to.
# __SUPPORTED__ is replaced with a JS boolean literal per branch.
_FORCE_HEVC_VERDICT_JS = """
(() => {
  const supported = __SUPPORTED__;
  Object.defineProperty(Navigator.prototype, "mediaCapabilities", {
    configurable: true,
    get: () => ({
      decodingInfo: async () => ({
        supported,
        smooth: supported,
        powerEfficient: supported,
      }),
    }),
  });
  Object.defineProperty(Navigator.prototype, "userAgentData", {
    configurable: true,
    get: () => ({ platform: "Windows", brands: [], mobile: false }),
  });
})();
"""


def _open_forced_page(ctx: PWContext, *, supported: bool, db_path: str) -> "Page":
    """A fresh page life whose HEVC probe answers ``supported``, on "Windows"."""
    page = ctx.new_page()
    page.add_init_script(
        _FORCE_HEVC_VERDICT_JS.replace("__SUPPORTED__", "true" if supported else "false")
    )
    page.goto("/")
    load_manifest(page, db_path)
    seam = page.evaluate(
        """async () => ({
            platform: navigator.userAgentData.platform,
            supported: (await navigator.mediaCapabilities.decodingInfo({
                type: "file",
                video: {
                    contentType: 'video/mp4; codecs="hvc1.1.6.L93.B0"',
                    width: 1920, height: 1080, bitrate: 10000000, framerate: 30,
                },
            })).supported,
        })"""
    )
    assert seam == {"platform": "Windows", "supported": supported}, (
        f"#869 seam did not apply: the page reports {seam!r}, expected "
        f"platform='Windows', supported={supported}. The forced branch would "
        f"otherwise assert against the runner's real engine."
    )
    return page


def _row_testid(manifest: dict, basename: str) -> str:
    """The file-row testid of ``basename`` in whichever group holds it."""
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            if Path(item["file_path"]).name == basename:
                return row_file_testid(str(group["group_number"]), basename)
    raise AssertionError(f"{basename} not found in the manifest")


def _transcode_row(page: "Page", row_testid: str, basename: str) -> None:
    """Select a video row and force the swap to the transcode; wait for its request.

    The fixture is VP9-in-MP4, which the #787 container gate never preempts,
    so every video starts on the original bytes and the synthetic ``error`` is
    what makes the surface choose the transcode (the reactive path).  The
    awaited request must name THIS file, so a late request from the previously
    selected video cannot satisfy it.
    """
    click_row(page, row_testid)
    page.get_by_test_id(PREVIEW_SINGLE_IMAGE).wait_for(state="visible", timeout=10_000)
    with page.expect_request(
        lambda r: "transcode=h264" in r.url and basename in urllib.parse.unquote(r.url),
        timeout=5_000,
    ):
        page.evaluate(
            """(testid) => {
                const el = document.querySelector('[data-testid="' + testid + '"]');
                if (el) el.dispatchEvent(new Event('error', { bubbles: true }));
            }""",
            PREVIEW_SINGLE_IMAGE,
        )


def _hint_count(page: "Page") -> int:
    return page.get_by_test_id(PREVIEW_HEVC_HINT).count()


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
        shutil.copy(str(_CLIP), os.path.join(src_dir, _THIRD_NAME))

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
            # Through the shared helper, which aims at the filename cell: a bare
            # row click targets the row box's CENTRE, and since #878 layout
            # slice R centred the row's cells that point is the decision
            # control, whose segments stopPropagation so staging a decision does
            # not also select the row.
            click_row(page, row_testid)

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

            # ── #869 step 6: forced `supported` → transcode, NO notice ───────
            clip_testid = _row_testid(manifest, _CLIP_NAME)
            twin_testid = _row_testid(manifest, _TWIN_NAME)
            third_testid = _row_testid(manifest, _THIRD_NAME)

            supported_page = _open_forced_page(ctx, supported=True, db_path=db_path)
            _transcode_row(supported_page, clip_testid, _CLIP_NAME)
            # The notice is raised from an effect committed with the swap;
            # give it a beat before asserting it is absent.
            supported_page.wait_for_timeout(500)
            count_supported = _hint_count(supported_page)
            assert count_supported == 0, (
                f"#869: the HEVC notice appeared ({count_supported}) although the "
                f"engine reports HEVC supported — it must only advise installing "
                f"the codec when the probe says 'unsupported'."
            )
            print(
                "s70 #869 forced=supported: transcode requested, "
                f"hevc hint count={count_supported}",
                flush=True,
            )
            supported_page.close()

            # ── #869 step 7: forced `unsupported` → the notice, exactly once ─
            unsupported_page = _open_forced_page(ctx, supported=False, db_path=db_path)
            _transcode_row(unsupported_page, clip_testid, _CLIP_NAME)
            hint = unsupported_page.get_by_test_id(PREVIEW_HEVC_HINT)
            hint.wait_for(state="visible", timeout=5_000)
            href = (
                unsupported_page.get_by_test_id(PREVIEW_HEVC_HINT_STORE_LINK)
                .get_attribute("href")
                or ""
            )
            assert href.startswith("ms-windows-store://pdp/?productid="), (
                f"#869: the notice's Store link is {href!r}, not a Microsoft Store "
                f"product-page link."
            )

            # A second transcoded video in the same page life: still ONE notice.
            _transcode_row(unsupported_page, twin_testid, _TWIN_NAME)
            unsupported_page.wait_for_timeout(500)
            after_second = _hint_count(unsupported_page)
            assert after_second == 1, (
                f"#869: expected exactly one HEVC notice after two transcodes, "
                f"found {after_second}."
            )

            # Dismissed for the session: a third transcode must not re-raise it.
            unsupported_page.get_by_test_id(PREVIEW_HEVC_HINT_DISMISS).click()
            hint.wait_for(state="detached", timeout=5_000)
            _transcode_row(unsupported_page, third_testid, _THIRD_NAME)
            unsupported_page.wait_for_timeout(500)
            after_dismiss = _hint_count(unsupported_page)
            assert after_dismiss == 0, (
                f"#869: the HEVC notice came back ({after_dismiss}) after Dismiss — "
                f"it is once per session."
            )
            print(
                "s70 #869 forced=unsupported: hevc hint count=1 after 2 transcodes, "
                f"count={after_dismiss} after dismiss + 3rd transcode",
                flush=True,
            )
            unsupported_page.close()

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
