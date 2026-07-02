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
import { describe, it, expect, beforeAll } from "vitest";
import { useEffect } from "react";

import { VideoTile } from "./VideoTile";
import { GroupMediaProvider } from "./GroupMediaProvider";
import { useGroupMedia } from "@/hooks/useGroupMedia";
import type { FileRow } from "@/api/types";

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
  thumbnail_url: "",
  media_type: "video",
};

const TILE_TESTID = "grid-video-tile-1-holiday.mp4";
const VIDEO_TESTID = `${TILE_TESTID}-video`;

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
});
