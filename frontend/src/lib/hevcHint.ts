// hevcHint — "this browser could decode HEVC natively" notice (#869).
//
// WHY
// ---
// When the #787 probe (lib/videoCapabilities.ts) gets a real "no" for HEVC,
// every .mov / .m4v goes through the server-side H.264 transcode: read the
// whole source, an ffmpeg encode whose first byte waits for the full file,
// a cache write, a read back. On Windows the browser (Chrome, Edge, WebView2)
// decodes HEVC through Media Foundation as soon as the "HEVC Video Extensions"
// Store package is installed — zero encode, zero cache growth. Nothing told the
// user that, so the transcode was silently the default instead of the safety
// net. This module decides WHEN to say it; components/HevcHint.tsx says it.
//
// WHEN
// ----
// Once per page life, the first time a video surface actually chooses the
// transcode, and only if ALL of:
//   - the probe answered "unsupported" (a real engine verdict). "unknown" —
//     no API, or the call threw — shows nothing: the three-state contract is
//     what keeps a guess from turning into advice;
//   - the platform is Windows (the Store package is Windows-only);
//   - the user has not chosen "Don't show again" (localStorage, versioned).
// A transcode chosen while the probe is still "unknown" does not spend the
// one showing — a later transcode, once the verdict is in, can still show it.
//
// WHICH STORE PRODUCT
// -------------------
// 9NMZLZ57R3T7 is "HEVC Video Extensions" (Microsoft; the retail package any
// Windows 10/11 machine can install). 9N4WGH0Z6VHQ, "HEVC Video Extensions
// from Device Manufacturer", is the OEM-preinstall variant and is not
// offered to every machine, so it is not the one we link. Both ids were
// checked against apps.microsoft.com on 2026-09-30.

import { create } from "zustand";

import { hevcSupportSync, type HevcSupport } from "./videoCapabilities";

/** localStorage key for "Don't show again". Versioned so a future rewording
 *  that deserves a second look can bump it. */
export const HEVC_HINT_DISMISSED_KEY = "pm.hevc-hint.dismissed.v1";

/** Microsoft Store deep link for the retail "HEVC Video Extensions". */
export const HEVC_STORE_URL = "ms-windows-store://pdp/?productid=9NMZLZ57R3T7";

// navigator.userAgentData (UA Client Hints) is Chromium-only and absent from
// the DOM typings; this is the one field we read.
interface UADataPlatform {
  platform?: string;
}

/** True on Windows. Prefers UA Client Hints; falls back to the UA string
 *  (Firefox and older engines have no userAgentData). */
export function isWindowsPlatform(): boolean {
  const uaData = (navigator as Navigator & { userAgentData?: UADataPlatform })
    .userAgentData;
  if (uaData?.platform) return uaData.platform === "Windows";
  return /Windows/i.test(navigator.userAgent);
}

/** Pure decision: should a transcode, chosen now, raise the notice? */
export function shouldOfferHevcHint(
  support: HevcSupport,
  isWindows: boolean,
  dismissedForever: boolean
): boolean {
  return support === "unsupported" && isWindows && !dismissedForever;
}

/** Whether the user chose "Don't show again". Fail-open to `false`: a storage
 *  error (private mode) costs one extra notice, never a hidden one forever. */
export function loadHevcHintDismissed(): boolean {
  try {
    return localStorage.getItem(HEVC_HINT_DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveHevcHintDismissed(): void {
  try {
    localStorage.setItem(HEVC_HINT_DISMISSED_KEY, "1");
  } catch {
    // fail-open — the notice is still closed for this page life
  }
}

interface HevcHintState {
  /** The notice is on screen. */
  visible: boolean;
  /** The one showing this page life has happened (shown, then maybe closed). */
  shown: boolean;
}

export const useHevcHintStore = create<HevcHintState>()(() => ({
  visible: false,
  shown: false,
}));

/**
 * A video surface just chose the H.264 transcode (up front via the #787
 * pre-check, or reactively after a decode error). Raises the notice the first
 * time this page life that the conditions in the header hold; a no-op after.
 */
export function noteTranscodeChosen(): void {
  if (useHevcHintStore.getState().shown) return;
  if (
    !shouldOfferHevcHint(
      hevcSupportSync(),
      isWindowsPlatform(),
      loadHevcHintDismissed()
    )
  ) {
    return;
  }
  useHevcHintStore.setState({ visible: true, shown: true });
}

/** Close the notice for this page life. */
export function dismissHevcHint(): void {
  useHevcHintStore.setState({ visible: false });
}

/** Close the notice and never raise it again in this browser. */
export function dismissHevcHintForever(): void {
  saveHevcHintDismissed();
  useHevcHintStore.setState({ visible: false });
}
