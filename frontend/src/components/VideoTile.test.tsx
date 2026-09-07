// Component tests for VideoTile — a single click-to-play video tile in
// GroupGrid.
//
// The tile is FAITHFUL to Qt: it renders a dark poster until clicked, then
// mounts a native <video> that autoplays and registers with the
// GroupMediaProvider. These tests pin that contract:
//   - no <video> before the click (avoids an N-way HEVC decode storm on load),
//   - after the click a <video> with the "{testId}-video" data-testid mounts,
//   - the element registers on mount and unregisters on unmount.
// Each assertion maps to a real regression (eager mount; dropped testid; leaked
// registry entry) — no "branch was reached" padding, per CLAUDE.md.

import { act, render, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { useEffect } from "react";

import { VideoTile } from "./VideoTile";
import { GroupMediaProvider } from "./GroupMediaProvider";
import { useGroupMedia } from "@/hooks/useGroupMedia";
import type { FileRow } from "@/api/types";

// #787 — the HEVC capability probe, mocked so these tests exercise the wiring
// (does the tile consult it, with its own path, when the <video> is created on
// click?). The probe's own logic is tested in lib/videoCapabilities.test.ts.
// The default `false` mirrors what the real module reports under jsdom, so
// every pre-existing test below is unaffected.
const capabilities = vi.hoisted(() => ({
  canPlayHevc: vi.fn(() => Promise.resolve(false)),
  prefersTranscodedVideo: vi.fn<(filePath: string) => boolean>(() => false),
}));
vi.mock("@/lib/videoCapabilities", () => capabilities);

// jsdom does not implement HTMLMediaElement playback methods; stub them so the
// autoPlay <video> mount doesn't emit "Not implemented" errors.
beforeAll(() => {
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: () => Promise.resolve(),
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: () => {},
  });
  Object.defineProperty(HTMLMediaElement.prototype, "load", {
    configurable: true,
    value: () => {},
  });
});

const VIDEO_ROW: FileRow = {
  file_path: "/clips/holiday.mp4",
  basename: "holiday.mp4",
  folder: "/clips",
  action: "keep",
  user_decision: "",
  is_locked: false,
  is_ref_winner: true,
  similarity: { kind: "ref", percent: null },
  score: null,
  file_size_bytes: 4_000_000,
  pixel_width: null,
  pixel_height: null,
  shot_date: null,
  creation_date: null,
  phash: null,
  hamming_distance: null,
  thumbnail_url: "/api/image?path=%2Fclips%2Fholiday.mp4&size=512",
  media_type: "video",
};

const TILE_TESTID = "grid-video-tile-1-holiday.mp4";
const VIDEO_TESTID = `${TILE_TESTID}-video`;
const POSTER_TESTID = `${TILE_TESTID}-poster`;

// Probe child that surfaces the live registry paths into a shared array so the
// test can assert register/unregister without mocking the whole context.
function RegistryProbe({ paths }: { paths: string[] }) {
  const { subscribe, getRegistry } = useGroupMedia();
  useEffect(() => {
    const sync = () => {
      paths.length = 0;
      getRegistry().forEach((_, p) => paths.push(p));
    };
    sync();
    return subscribe(sync);
  }, [subscribe, getRegistry, paths]);
  return null;
}

describe("VideoTile", () => {
  beforeEach(() => {
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
  });

  it("renders no <video> before the tile is clicked (click-to-mount, no eager decode)", () => {
    const { container } = render(
      <GroupMediaProvider>
        <VideoTile row={VIDEO_ROW} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );

    // The tile container is present, but there is no media element yet — the
    // dark poster with a ▶ affordance stands in until the click.
    expect(
      container.querySelector(`[data-testid="${TILE_TESTID}"]`)
    ).not.toBeNull();
    expect(container.querySelector("video")).toBeNull();
    expect(
      container.querySelector(`[data-testid="${VIDEO_TESTID}"]`)
    ).toBeNull();
  });

  it("renders the poster <img> with row.thumbnail_url before the click", () => {
    const { container } = render(
      <GroupMediaProvider>
        <VideoTile row={VIDEO_ROW} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );

    // Failure mode: the tile falling back to the Qt-parity-breaking dark-only
    // placeholder — no real frame shown behind the ▶ glyph (issue #734).
    const poster = container.querySelector<HTMLImageElement>(
      `[data-testid="${POSTER_TESTID}"]`
    );
    expect(poster).not.toBeNull();
    expect(poster!.tagName.toLowerCase()).toBe("img");
    expect(poster!.src).toContain(VIDEO_ROW.thumbnail_url);
  });

  it("falls back to the dark background (glyph stays) when the poster fails to load", () => {
    const { container } = render(
      <GroupMediaProvider>
        <VideoTile row={VIDEO_ROW} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );

    const poster = container.querySelector(
      `[data-testid="${POSTER_TESTID}"]`
    )!;
    act(() => {
      fireEvent.error(poster);
    });

    // Failure mode: a poster load failure (404, unsupported codec on the
    // backend rescue attempt) leaving a broken-image icon on screen instead
    // of the dark fallback the rest of the grid uses.
    expect(
      container.querySelector(`[data-testid="${POSTER_TESTID}"]`)
    ).toBeNull();
    expect(container.querySelector('[aria-label="Play video"]')).not.toBeNull();
  });

  it("mounts a <video> with the '-video' testid after the click", () => {
    const { container } = render(
      <GroupMediaProvider>
        <VideoTile row={VIDEO_ROW} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );

    const tile = container.querySelector(`[data-testid="${TILE_TESTID}"]`)!;
    act(() => {
      fireEvent.click(tile);
    });

    const video = container.querySelector<HTMLVideoElement>(
      `[data-testid="${VIDEO_TESTID}"]`
    );
    // Failure mode: dropping the inner testid (or renaming the suffix) makes
    // s71's `get_by_test_id("{tile}-video")` time out. The element must exist
    // AND be a <video>.
    expect(video).not.toBeNull();
    expect(video!.tagName.toLowerCase()).toBe("video");
    expect(video!.hasAttribute("autoplay")).toBe(true);
  });

  it("registers with the provider on <video> mount and unregisters on unmount", () => {
    const paths: string[] = [];

    const { container, unmount } = render(
      <GroupMediaProvider>
        <RegistryProbe paths={paths} />
        <VideoTile row={VIDEO_ROW} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );

    // Before the click there is no <video>, so nothing is registered.
    expect(paths).not.toContain(VIDEO_ROW.file_path);

    const tile = container.querySelector(`[data-testid="${TILE_TESTID}"]`)!;
    act(() => {
      fireEvent.click(tile);
    });

    // The ref callback fires on mount → the element registers under its path.
    expect(paths).toContain(VIDEO_ROW.file_path);

    // Failure mode: an unmount that doesn't call unregister leaves a stale
    // element ref in the registry, so broadcast play/seek would drive a
    // detached node. Unmounting must clear the entry.
    act(() => {
      unmount();
    });
    expect(paths).not.toContain(VIDEO_ROW.file_path);
  });

  // -------------------------------------------------------------------------
  // #787 — HEVC capability pre-check picks the INITIAL src
  // -------------------------------------------------------------------------

  it("mounts the transcode src when the engine is known HEVC-incapable (#787)", () => {
    // The decision is read at CLICK time (the moment the <video> is created),
    // not at tile mount — by then the probe kicked off on mount has resolved.
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    const movRow: FileRow = {
      ...VIDEO_ROW,
      file_path: "/clips/holiday.mov",
      basename: "holiday.mov",
    };

    const { container } = render(
      <GroupMediaProvider>
        <VideoTile row={movRow} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );
    act(() => {
      fireEvent.click(container.querySelector(`[data-testid="${TILE_TESTID}"]`)!);
    });

    expect(capabilities.prefersTranscodedVideo).toHaveBeenCalledWith("/clips/holiday.mov");
    const video = container.querySelector<HTMLVideoElement>("video")!;
    expect(video.getAttribute("src") ?? "").toContain("transcode=h264");
  });

  // Two-attempt contract, both directions. Same regression as the other two
  // surfaces: with the hint choosing the STARTING source, a first error on a
  // hint-started transcode used to be terminal, so an ffmpeg-less server's 501
  // killed a tile that would have played the original bytes fine.

  const MOV_ROW: FileRow = {
    ...VIDEO_ROW,
    file_path: "/clips/holiday.mov",
    basename: "holiday.mov",
  };

  function mountPlayer(row: FileRow) {
    const rendered = render(
      <GroupMediaProvider>
        <VideoTile row={row} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );
    act(() => {
      fireEvent.click(
        rendered.container.querySelector(`[data-testid="${TILE_TESTID}"]`)!
      );
    });
    return rendered;
  }

  function tileSrc(container: HTMLElement): string {
    return container.querySelector("video")?.getAttribute("src") ?? "";
  }

  function fireTileError(container: HTMLElement): void {
    act(() => {
      fireEvent.error(container.querySelector("video")!);
    });
  }

  it("hint → error → falls back to the ORIGINAL bytes (#787 round 2)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    const { container } = mountPlayer(MOV_ROW);
    expect(tileSrc(container)).toContain("transcode=h264");

    fireTileError(container);

    expect(container.textContent).not.toContain("Video cannot be played");
    expect(tileSrc(container)).not.toContain("transcode=h264");
    expect(tileSrc(container)).toContain("/api/media?path=");
  });

  it("native → error → swaps to the transcode (unchanged behaviour)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
    const { container } = mountPlayer(VIDEO_ROW);
    expect(tileSrc(container)).not.toContain("transcode=h264");

    fireTileError(container);

    expect(container.textContent).not.toContain("Video cannot be played");
    expect(tileSrc(container)).toContain("transcode=h264");
  });

  it("hint → error → native → error → terminal (#787 round 2)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    const { container } = mountPlayer(MOV_ROW);

    fireTileError(container);
    expect(tileSrc(container)).not.toContain("transcode=h264");
    fireTileError(container);

    expect(container.textContent).toContain("Video cannot be played");
    expect(container.querySelector("video")).toBeNull();
  });

  it("native → error → transcode → error → terminal (unchanged behaviour)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
    const { container } = mountPlayer(VIDEO_ROW);

    fireTileError(container);
    expect(tileSrc(container)).toContain("transcode=h264");
    fireTileError(container);

    expect(container.textContent).toContain("Video cannot be played");
    expect(container.querySelector("video")).toBeNull();
  });

  it("mounts the original-bytes src when the probe has no verdict (#787)", () => {
    // Pre-#787 behaviour, and what jsdom / the qa VP9-in-MP4 fixtures get.
    capabilities.prefersTranscodedVideo.mockReturnValue(false);

    const { container } = render(
      <GroupMediaProvider>
        <VideoTile row={VIDEO_ROW} data-testid={TILE_TESTID} />
      </GroupMediaProvider>
    );
    act(() => {
      fireEvent.click(container.querySelector(`[data-testid="${TILE_TESTID}"]`)!);
    });

    const video = container.querySelector<HTMLVideoElement>("video")!;
    expect(video.getAttribute("src") ?? "").not.toContain("transcode=h264");
  });
});
