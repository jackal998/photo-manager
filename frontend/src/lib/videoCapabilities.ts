// videoCapabilities — memoized "can this engine decode HEVC?" probe (#787).
//
// WHY
// ---
// The video surfaces (PreviewPane / FullResViewer / VideoTile) load the
// original bytes first and swap to the H.264 transcode REACTIVELY, on the
// <video> element's error event. On an engine that cannot decode HEVC at all,
// that first attempt is guaranteed to fail: a wasted network fetch + decode
// round trip before the (already slow) transcode even starts. This module lets
// a surface skip straight to the transcode when the engine is KNOWN incapable.
//
// The reactive error swap stays exactly as it was — this is a hint that picks a
// better STARTING src, never a replacement for the safety net.
//
// WHAT IS PROBED
// --------------
// navigator.mediaCapabilities.decodingInfo with a `type: "file"` HEVC config
// matching what the backend actually serves natively: /api/media streams the
// original bytes with a container Content-Type (`video/mp4`, `video/quicktime`
// — app/web/routes/media.py `_CONTENT_TYPE`), and the owner's library is
// ~99% HEVC. Both HEVC fourCCs are tried: `hvc1` (Apple's, what iPhone .MOV /
// .MP4 carry) and `hev1`. Either one supported ⇒ the engine can play HEVC.
//
// THREE-STATE, NOT BOOLEAN
// ------------------------
// "the API said no" and "there is no API to ask" are different facts, and only
// the first one justifies changing behaviour. `hevcSupportSync()` therefore
// reports "unknown" when the API is absent or the call throws, and only
// "unsupported" on a real negative answer from the engine. jsdom (which has no
// navigator.mediaCapabilities) lands on "unknown" and every existing surface
// keeps its current behaviour with no new test mocks — which is also why the
// probe never runs during unit tests unless a test opts in.
//
// CONTAINER GATE
// --------------
// The engine's HEVC verdict says nothing about THIS file: an H.264 or VP9 .mp4
// plays fine on an HEVC-incapable engine. Nothing in FileRow carries the codec
// (only `media_type: "video"`), so the extension is the only signal available
// client-side, and the two costs are wildly asymmetric:
//   - false negative (an HEVC file we did not preempt) → today's behaviour:
//     one failed attempt, then the reactive swap. Cheap.
//   - false positive (a natively-playable file we preempted) → a pointless
//     ffmpeg encode, a multi-second stall, worse quality, and a terminal
//     "Video cannot be played" if ffmpeg is missing (the route 501s and the
//     swap-once handler has no second chance left). Expensive.
// So preemption is limited to the Apple containers that are HEVC in practice
// (.mov / .m4v). .mp4 is deliberately excluded — it is the one extension that
// genuinely carries anything (the qa fixtures are VP9-in-MP4), so its files
// keep the reactive path.

/** What we know about this engine's HEVC decoding. */
export type HevcSupport = "supported" | "unsupported" | "unknown";

// The two HEVC codec strings worth asking about. Main profile, level 3.1 —
// a representative 1080p iPhone capture.
const HEVC_CODECS = ["hvc1.1.6.L93.B0", "hev1.1.6.L93.B0"] as const;

// decodingInfo rejects a video configuration that omits any of these.
const PROBE_VIDEO = {
  width: 1920,
  height: 1080,
  bitrate: 10_000_000,
  framerate: 30,
} as const;

// Containers that are HEVC in practice for this app's library. See the
// CONTAINER GATE note above for why .mp4 is not here.
const LIKELY_HEVC_EXTENSIONS = [".mov", ".m4v"] as const;

// Module-level memo: one probe per page life, shared by every surface.
let support: HevcSupport = "unknown";
let inflight: Promise<boolean> | null = null;

async function probe(): Promise<boolean> {
  // jsdom and pre-2019 engines have no navigator.mediaCapabilities. The DOM
  // typings declare it as always present, so the widened annotation is what
  // lets us check for it at all.
  const mc: typeof navigator.mediaCapabilities | undefined =
    navigator.mediaCapabilities;
  if (mc === undefined || typeof mc.decodingInfo !== "function") {
    support = "unknown";
    return false;
  }
  try {
    for (const codec of HEVC_CODECS) {
      const info = await mc.decodingInfo({
        type: "file",
        video: { ...PROBE_VIDEO, contentType: `video/mp4; codecs="${codec}"` },
      });
      if (info.supported) {
        support = "supported";
        return true;
      }
    }
    support = "unsupported";
    return false;
  } catch {
    // A throwing/rejecting decodingInfo tells us nothing about the engine —
    // stay on the reactive path rather than guessing.
    support = "unknown";
    return false;
  }
}

/**
 * Resolve whether this engine can decode HEVC. Memoized: the underlying
 * decodingInfo call happens at most once per page life however many callers
 * ask. Resolves `false` both when HEVC is unsupported and when the answer is
 * unknowable — callers that need to tell those apart read `hevcSupportSync()`.
 */
export function canPlayHevc(): Promise<boolean> {
  inflight ??= probe();
  return inflight;
}

/**
 * The probe's answer as known RIGHT NOW, without awaiting. "unknown" until the
 * probe resolves (and forever if the API is absent or threw), so a first paint
 * never waits on it.
 */
export function hevcSupportSync(): HevcSupport {
  return support;
}

/**
 * Should this file's <video> start on the H.264 transcode instead of the
 * original bytes? True only when the engine gave a real "no" AND the container
 * is one that is HEVC in practice. Everything else keeps the original-bytes
 * start and the reactive error swap.
 */
export function prefersTranscodedVideo(filePath: string): boolean {
  if (support !== "unsupported") return false;
  const lower = filePath.toLowerCase();
  return LIKELY_HEVC_EXTENSIONS.some((ext) => lower.endsWith(ext));
}
