"""Web scenario s71 — GroupGrid multi-tile video: coordinated playback surface.

Verifies the web group-grid feature end-to-end for a duplicate VIDEO group,
driving the REAL coordinated surface (click-to-mount tiles, group pause/play
broadcast, master scrub via a real pointer drag):

  1. Scan a byte-identical twin pair of clip.mp4 so the scanner produces a
     2-member EXACT-dup video group (s05/s69 duplicate-to-observe pattern;
     the singleton-drop guard that removes 1-member groups would otherwise
     swallow the single video).
  2. Open the manifest in the web UI.
  3. SELECT THE GROUP ROW (row-group-{N}) → assert the GroupGrid renders with
     the GMC_BAR controller bar visible, grid-container visible, and exactly
     2 grid-video-tile-* tile containers.
  4. CLICK tile 1 → wait for its {tile1}-video element; assert readyState >= 2
     (HAVE_CURRENT_DATA), duration > 0, and currentTime > 0 after autoplay
     (real VP9 decode, the same signal as s69 applied per-tile).
  5. CLICK tile 2 → wait for its {tile2}-video; assert it mounts and plays.
     (The web grid is FAITHFUL to Qt: each tile is a static poster until
     clicked; clicking mounts a native <video> that autoplays and registers
     with the controller. Broadcast reaches only MOUNTED tiles — matching Qt's
     thumbnail-until-click grid — so BOTH tiles must be clicked before the
     group-play broadcast can be observed on both.)
  6. Group PAUSE (gmc-play-pause) → assert BOTH videos become paused.
  7. Record a paused baseline, then group PLAY (gmc-play-pause) → assert BOTH
     videos advance beyond their paused baseline (broadcast reached both).
  8. Master scrub: drive gmc-progress-slider to ~50 % via the React-tracked
     value setter + a 'keyup' (the controller's seek-on-RELEASE path) → assert
     BOTH videos' currentTime converge near the target (keyframe tolerance).

Fixture: qa/sandbox/video-playback/clip.mp4 — same VP9-in-MP4 synthetic clip
used by s69/s70.  Copied twice at runtime into a tmpdir to form a 2-member
EXACT group (byte-identical -> identical SHA-256 hash -> same group_number).

Open-risk annotations
---------------------
GROUP_ROW_NOT_SELECTABLE:
    The group-row click calls toggleGroup(groupNumber) AND setSelectedGroup
    (PreviewState.selectedGroupId), switching the PreviewPane to grid mode.
    This scenario targets the group-row via its data-testid (row-group-{N})
    then waits for grid-container; if the handler is missing, wait_for times
    out with a message pointing at the missing wiring.

THUMBNAIL_SOURCE:
    FileRow.thumbnail_url is built for images (/api/image?...&size=512).
    Video tiles show a placeholder/dark background instead of a real poster
    frame.  The scenario asserts the tile container and the <video> element
    that mounts after the user clicks the tile — not an <img>.

CLICK_TO_MOUNT_BROADCAST:
    Faithful to Qt (preview_pane.py:347 thumbnail-until-click): each video
    tile is a static poster until clicked; clicking mounts a native <video>
    that autoplays and registers with the GroupMediaController.  The group
    broadcast (pause/play/seek) reaches ONLY mounted tiles.  This scenario
    therefore clicks BOTH tiles before exercising the group broadcast, so a
    "broadcast reached both" assertion is valid (both are mounted).  This also
    avoids an N-way HEVC decode storm on grid load — tiles decode lazily.

MASTER_SCRUB:
    The master scrub commits on RELEASE (the controller seeks on pointer-up /
    key-up, faithful to the Qt slider's sliderReleased).  A raw pointer drag on
    a native range input is unreliable in headless Chromium, so the scenario
    sets the controlled slider to ~50 % via the React-tracked value setter and
    fires 'keyup' to commit the seek through the real onKeyUp -> seekAll path,
    then polls each tile's <video>.currentTime for convergence within a
    keyframe tolerance — a stable observable, not a wall-clock timing check.
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
from qa.web.testid_constants import (
    GRID_CONTAINER,
    GMC_BAR,
    GMC_PLAY_PAUSE,
    GMC_PROGRESS_SLIDER,
    row_group_testid,
    grid_video_tile_testid,
)

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_CLIP = _REPO / "qa" / "sandbox" / "video-playback" / "clip.mp4"
_CLIP_NAME = "clip.mp4"
_TWIN_NAME = "clip_twin.mp4"

# Fraction of the slider width to drag the pointer to for the master scrub.
_SCRUB_FRACTION = 0.5

# Tolerance in seconds for the per-tile time-convergence assertion after the
# master scrub.  VP9 keyframe intervals are 15 frames = 1 second; seeking may
# land at the nearest keyframe.
_SCRUB_TOLERANCE_S = 1.5

# ---------------------------------------------------------------------------
# HTTP + DOM helpers
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _video_prop(page, video_testid: str, prop: str):
    """Return a numeric property (readyState/duration/currentTime/paused) of a
    tile's <video>, or -1 / None when the element is absent."""
    return page.evaluate(
        """([tid, p]) => {
            const el = document.querySelector('[data-testid="' + tid + '"]');
            return el ? el[p] : null;
        }""",
        [video_testid, prop],
    )


def _mount_tile_and_await_decode(page, tile_testid: str) -> str:
    """Click a video tile, wait for its <video> to mount + decode, return the
    <video> element's data-testid."""
    page.get_by_test_id(tile_testid).click()

    video_testid = f"{tile_testid}-video"
    page.wait_for_selector(
        f'[data-testid="{video_testid}"]',
        state="attached",
        timeout=10_000,
    )
    page.wait_for_function(
        """(tid) => {
            const el = document.querySelector('[data-testid="' + tid + '"]');
            return el && el.readyState >= 2;
        }""",
        arg=video_testid,
        timeout=10_000,
    )
    return video_testid


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan a video duplicate group; drive grid render + tile decode + group
    broadcast + master scrub.

    Raises AssertionError on any regression.
    """
    assert _CLIP.exists(), (
        f"Video fixture missing: {_CLIP}. "
        f"Generate it with: ffmpeg -y -f lavfi -i testsrc=duration=2:size=320x240:rate=15 "
        f"-c:v libvpx-vp9 -b:v 200k {_CLIP}"
    )

    tmpdir = tempfile.mkdtemp(prefix="qa_s71_")
    src_dir = os.path.join(tmpdir, "s71_source")
    db_path = os.path.join(tmpdir, "s71_manifest.db")
    try:
        os.makedirs(src_dir, exist_ok=True)
        # Two byte-identical copies -> one EXACT-dup group of 2 video members.
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

            # ── Validate manifest: 1 group, 2 video members ──────────────────
            manifest = _get_manifest(base_url, db_path)
            groups = manifest.get("groups", [])
            assert len(groups) >= 1, (
                f"Expected >= 1 group in manifest; got {len(groups)}. "
                f"total_files={manifest.get('total_files')}"
            )

            video_groups = [
                g for g in groups
                if any(
                    it.get("media_type") == "video"
                    for it in g.get("items", [])
                )
            ]
            assert video_groups, (
                "No video group found in manifest. "
                f"Groups: {[(g['group_number'], [it['media_type'] for it in g['items']]) for g in groups]}"
            )
            group = video_groups[0]
            group_number = group["group_number"]
            items = group["items"]
            n_tiles = len(items)
            assert n_tiles >= 2, (
                f"Expected >= 2 members in the video group; got {n_tiles}. "
                f"The singleton-drop may have removed one copy."
            )
            for it in items:
                assert it.get("media_type") == "video", (
                    f"Non-video member in video group: {it}"
                )

            basenames = [Path(it["file_path"]).name for it in items]
            tile_testids = [
                grid_video_tile_testid(str(group_number), b) for b in basenames
            ]

            # ── Click the GROUP row to trigger grid mode ─────────────────────
            group_row = page.get_by_test_id(row_group_testid(str(group_number)))
            group_row.wait_for(state="visible", timeout=10_000)
            group_row.click()

            # ── Assert grid + GMC bar render, exactly 2 tile containers ──────
            grid = page.get_by_test_id(GRID_CONTAINER)
            grid.wait_for(state="visible", timeout=10_000)

            gmc = page.get_by_test_id(GMC_BAR)
            gmc.wait_for(state="visible", timeout=10_000)

            for tile_testid in tile_testids:
                page.get_by_test_id(tile_testid).wait_for(
                    state="visible", timeout=10_000
                )

            n_tile_containers = page.evaluate(
                """(gid) => document.querySelectorAll(
                    '[data-testid^="grid-video-tile-' + gid + '-"]'
                ).length""",
                str(group_number),
            )
            # Each tile also nests a "-video" element AFTER it is clicked; at
            # this point no tile has been clicked, so the container count equals
            # the member count exactly.
            assert n_tile_containers == n_tiles, (
                f"Expected {n_tiles} grid-video-tile containers before any "
                f"click; found {n_tile_containers}."
            )

            # ── Click tile 1 → assert real decode + autoplay advance ─────────
            first_video_testid = _mount_tile_and_await_decode(page, tile_testids[0])

            duration_1 = _video_prop(page, first_video_testid, "duration")
            assert duration_1 is not None and duration_1 > 0, (
                f"First tile <video> duration={duration_1} (want > 0). "
                f"The VP9 clip may not have decoded. tile: {first_video_testid}"
            )
            page.wait_for_function(
                """(tid) => {
                    const el = document.querySelector('[data-testid="' + tid + '"]');
                    return el && el.currentTime > 0;
                }""",
                arg=first_video_testid,
                timeout=5_000,
            )

            # ── Click tile 2 → assert it mounts + plays ──────────────────────
            second_video_testid = _mount_tile_and_await_decode(page, tile_testids[1])

            duration_2 = _video_prop(page, second_video_testid, "duration")
            assert duration_2 is not None and duration_2 > 0, (
                f"Second tile <video> duration={duration_2} (want > 0). "
                f"tile: {second_video_testid}"
            )
            page.wait_for_function(
                """(tid) => {
                    const el = document.querySelector('[data-testid="' + tid + '"]');
                    return el && el.currentTime > 0;
                }""",
                arg=second_video_testid,
                timeout=5_000,
            )

            video_testids = [first_video_testid, second_video_testid]

            # ── Group PAUSE → assert BOTH become paused ──────────────────────
            gmc_play = page.get_by_test_id(GMC_PLAY_PAUSE)
            gmc_play.wait_for(state="visible", timeout=5_000)
            gmc_play.click()

            for vid in video_testids:
                page.wait_for_function(
                    """(tid) => {
                        const el = document.querySelector('[data-testid="' + tid + '"]');
                        return el && el.paused === true;
                    }""",
                    arg=vid,
                    timeout=5_000,
                )

            # ── Baseline while paused, then group PLAY → assert both advance ─
            baselines = {vid: _video_prop(page, vid, "currentTime") for vid in video_testids}
            for vid, base in baselines.items():
                assert base is not None and base >= 0, (
                    f"Could not read paused baseline currentTime for {vid}: {base}"
                )

            gmc_play.click()

            for vid in video_testids:
                base = baselines[vid]
                page.wait_for_function(
                    """([tid, base]) => {
                        const el = document.querySelector('[data-testid="' + tid + '"]');
                        return el && el.currentTime > base;
                    }""",
                    arg=[vid, base],
                    timeout=8_000,
                )
                ct = _video_prop(page, vid, "currentTime")
                assert ct is not None and ct > base, (
                    f"Tile {vid} currentTime={ct} did not advance beyond paused "
                    f"baseline {base} after group PLAY — the GMC play broadcast "
                    f"did not reach this tile."
                )

            # ── Master scrub via a REAL pointer drag → assert convergence ────
            # Pause both first so continued playback doesn't invalidate the
            # convergence check, then drag the slider to ~50 % of its width.
            gmc_play.click()
            for vid in video_testids:
                page.wait_for_function(
                    """(tid) => {
                        const el = document.querySelector('[data-testid="' + tid + '"]');
                        return el && el.paused === true;
                    }""",
                    arg=vid,
                    timeout=5_000,
                )

            slider = page.get_by_test_id(GMC_PROGRESS_SLIDER)
            box = slider.bounding_box()
            assert box is not None, "GMC progress slider has no bounding box"

            slider_max = page.evaluate(
                """(tid) => {
                    const el = document.querySelector('[data-testid="' + tid + '"]');
                    return el ? parseFloat(el.max) : -1;
                }""",
                GMC_PROGRESS_SLIDER,
            )
            assert slider_max > 0, (
                f"GMC progress slider max={slider_max} (want > 0). "
                f"The master duration may not have been set by any tile player."
            )

            # A raw pointer drag on a native <input type=range> is unreliable in
            # headless Chromium (the thumb is not reliably grabbed by synthetic
            # mouse events), so drive the CONTROLLED slider deterministically:
            # set the value to ~50 % via the React-tracked nativeInputValueSetter,
            # fire 'input' (the controller's onChange updates masterPosition) then
            # 'keyup' (the controller commits the seek via onKeyUp -> seekAll,
            # faithful to the seek-on-RELEASE contract). This exercises the real
            # release handler, not a synthetic shortcut around it.
            target_ms = page.evaluate(
                """([tid, frac]) => {
                    const el = document.querySelector('[data-testid="' + tid + '"]');
                    const target = Math.floor(parseFloat(el.max) * frac);
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, String(target));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                    return target;
                }""",
                [GMC_PROGRESS_SLIDER, _SCRUB_FRACTION],
            )
            assert target_ms > 0, (
                f"GMC slider seek target={target_ms} (want > 0); the master "
                f"duration may not have been set from the tile players."
            )
            target_s = target_ms / 1000.0

            for vid in video_testids:
                page.wait_for_function(
                    """([tid, targetS, toleranceS]) => {
                        const el = document.querySelector('[data-testid="' + tid + '"]');
                        if (!el) return false;
                        return Math.abs(el.currentTime - targetS) <= toleranceS;
                    }""",
                    arg=[vid, target_s, _SCRUB_TOLERANCE_S],
                    timeout=5_000,
                )
                ct_after_scrub = _video_prop(page, vid, "currentTime")
                assert (
                    ct_after_scrub is not None
                    and abs(ct_after_scrub - target_s) <= _SCRUB_TOLERANCE_S
                ), (
                    f"Tile {vid} currentTime={ct_after_scrub:.3f}s after master "
                    f"scrub to {target_s:.3f}s (tolerance {_SCRUB_TOLERANCE_S}s). "
                    f"The GMC master scrub did not seek this tile — check the "
                    f"slider release handler calls seekAll(ms) on all registered "
                    f"video elements."
                )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
