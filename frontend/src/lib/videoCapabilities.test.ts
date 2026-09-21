// Tests for the #787 HEVC capability probe.
//
// Every case here maps to a real user-visible outcome, not a covered branch:
//   - a wrongly-shaped decodingInfo config makes the probe throw and the whole
//     feature silently degrade (the live headless-Chromium probe confirmed
//     decodingInfo REJECTS a video config missing width/height/bitrate/
//     framerate), so the config we send is asserted;
//   - "the API said no" vs "there is no API" decide whether a file starts on
//     the transcode or on the original bytes;
//   - a non-memoized probe re-runs decodingInfo per tile in a video grid;
//   - preempting a .mp4 would break every existing video scenario (s69/s70/s71
//     use a VP9-in-MP4 fixture) and, in the field, would force a pointless
//     ffmpeg encode on a file that plays natively.
//
// The module memoizes at module scope, so each test imports a FRESH instance
// via vi.resetModules() rather than exporting a test-only reset hook.

import { describe, it, expect, vi, afterEach } from "vitest";

// Structural shape of the slice of MediaDecodingConfiguration this module
// sends (the DOM lib's own type names are not in scope for eslint's no-undef).
interface ProbeConfig {
  type: string;
  video?: {
    contentType: string;
    width: number;
    height: number;
    bitrate: number;
    framerate: number;
  };
}

type Decoder = (config: ProbeConfig) => Promise<{
  supported: boolean;
  smooth: boolean;
  powerEfficient: boolean;
}>;

/** Install a fake navigator.mediaCapabilities. Returns the spy. */
function installMediaCapabilities(decodingInfo: Decoder) {
  const spy = vi.fn(decodingInfo);
  Object.defineProperty(navigator, "mediaCapabilities", {
    configurable: true,
    writable: true,
    value: { decodingInfo: spy },
  });
  return spy;
}

/** Answer `supported` for exactly the codec strings in `supportedCodecs`. */
function decoderSupporting(supportedCodecs: string[]): Decoder {
  return (config) => {
    const contentType = config.video?.contentType ?? "";
    const supported = supportedCodecs.some((c) => contentType.includes(c));
    return Promise.resolve({ supported, smooth: supported, powerEfficient: false });
  };
}

async function freshModule() {
  vi.resetModules();
  return await import("./videoCapabilities");
}

afterEach(() => {
  // jsdom has no navigator.mediaCapabilities by default — restore that.
  Reflect.deleteProperty(navigator, "mediaCapabilities");
  vi.restoreAllMocks();
});

describe("canPlayHevc", () => {
  it("supported engine → reports supported, so nothing is preempted", async () => {
    const spy = installMediaCapabilities(decoderSupporting(["hvc1"]));
    const mod = await freshModule();

    await expect(mod.canPlayHevc()).resolves.toBe(true);
    expect(mod.hevcSupportSync()).toBe("supported");
    expect(mod.prefersTranscodedVideo("C:/photos/IMG_0001.MOV")).toBe(false);

    // The config we send must be one decodingInfo accepts: type "file" plus a
    // fully-specified video configuration. A missing field makes it reject.
    const config = spy.mock.calls[0][0];
    expect(config.type).toBe("file");
    expect(config.video).toMatchObject({
      contentType: 'video/mp4; codecs="hvc1.1.6.L93.B0"',
      width: 1920,
      height: 1080,
      bitrate: 10_000_000,
      framerate: 30,
    });
  });

  it("unsupported engine → transcode is preferred for an HEVC container", async () => {
    installMediaCapabilities(decoderSupporting([]));
    const mod = await freshModule();

    await expect(mod.canPlayHevc()).resolves.toBe(false);
    expect(mod.hevcSupportSync()).toBe("unsupported");
    expect(mod.prefersTranscodedVideo("C:/photos/IMG_0001.MOV")).toBe(true);
    expect(mod.prefersTranscodedVideo("/clips/holiday.m4v")).toBe(true);
  });

  it("engine that only advertises hev1 still counts as supported", async () => {
    // Both fourCCs describe the same decoder; an engine answering for only one
    // of them must not be read as HEVC-incapable.
    installMediaCapabilities(decoderSupporting(["hev1"]));
    const mod = await freshModule();

    await expect(mod.canPlayHevc()).resolves.toBe(true);
    expect(mod.hevcSupportSync()).toBe("supported");
  });

  it("missing MediaCapabilities API → unknown, so behaviour is unchanged", async () => {
    // This is the jsdom case, and the pre-2019-browser case. Absence of an
    // answer must never be read as a negative answer.
    expect(
      (navigator as Navigator & { mediaCapabilities?: unknown }).mediaCapabilities
    ).toBeUndefined();
    const mod = await freshModule();

    await expect(mod.canPlayHevc()).resolves.toBe(false);
    expect(mod.hevcSupportSync()).toBe("unknown");
    expect(mod.prefersTranscodedVideo("C:/photos/IMG_0001.MOV")).toBe(false);
  });

  it("decodingInfo rejects → unknown, and the rejection does not escape", async () => {
    installMediaCapabilities(() =>
      Promise.reject(new TypeError("Failed to execute 'decodingInfo'"))
    );
    const mod = await freshModule();

    await expect(mod.canPlayHevc()).resolves.toBe(false);
    expect(mod.hevcSupportSync()).toBe("unknown");
    expect(mod.prefersTranscodedVideo("C:/photos/IMG_0001.MOV")).toBe(false);
  });

  it("probe is memoized across callers", async () => {
    // A grid of N video tiles calls this on every tile mount; without the memo
    // that is N decodingInfo round trips per group change.
    const spy = installMediaCapabilities(decoderSupporting(["hvc1"]));
    const mod = await freshModule();

    const results = await Promise.all([
      mod.canPlayHevc(),
      mod.canPlayHevc(),
      mod.canPlayHevc(),
    ]);
    await mod.canPlayHevc();

    expect(results).toEqual([true, true, true]);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("prefersTranscodedVideo container gate", () => {
  it("never preempts a .mp4, even on an HEVC-incapable engine", async () => {
    // .mp4 carries H.264/VP9/AV1 as often as HEVC — the qa fixtures used by
    // s69/s70/s71 are VP9-in-MP4, and preempting them would both break those
    // scenarios and force a needless transcode of a natively-playable file.
    installMediaCapabilities(decoderSupporting([]));
    const mod = await freshModule();
    await mod.canPlayHevc();
    expect(mod.hevcSupportSync()).toBe("unsupported");

    expect(mod.prefersTranscodedVideo("/tmp/s70_source/clip.mp4")).toBe(false);
    expect(mod.prefersTranscodedVideo("/tmp/s70_source/clip.avi")).toBe(false);
  });

  it("matches the extension case-insensitively", async () => {
    // iPhone writes IMG_1234.MOV in upper case; Explorer shows .mov.
    installMediaCapabilities(decoderSupporting([]));
    const mod = await freshModule();
    await mod.canPlayHevc();

    expect(mod.prefersTranscodedVideo("D:/DCIM/IMG_1234.MOV")).toBe(true);
    expect(mod.prefersTranscodedVideo("D:/DCIM/img_1234.mov")).toBe(true);
    // A basename that merely CONTAINS ".mov" is not a .mov file.
    expect(mod.prefersTranscodedVideo("D:/DCIM/movie.mp4")).toBe(false);
  });
});
