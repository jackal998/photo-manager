// Tests for the #869 HEVC notice decision (lib/hevcHint.ts).
//
// The three probe states are driven through the REAL #787 probe with a fake
// navigator.mediaCapabilities (the recipe lib/videoCapabilities.test.ts uses),
// so "unsupported" here is exactly what an engine's `supported: false` turns
// into — not a string a mock was told to return. Each case is a user-visible
// outcome:
//   - supported / unknown must never nag: a notice that fires on a machine
//     that already plays HEVC, or on one we know nothing about, is advice to
//     buy something the user does not need;
//   - the notice appears ONCE per page life, however many videos transcode;
//   - "Don't show again" survives a reload (a new page life), and a transcode
//     that happened before the probe answered does not burn the one showing.
//
// The probe and the notice both hold module-level state, so every test gets a
// fresh module graph (vi.resetModules) — one "page life" per import.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

type Verdict = "supported" | "unsupported" | "unknown";

const DISMISSED_KEY = "pm.hevc-hint.dismissed.v1";

/** Fake the engine's HEVC verdict. "unknown" = jsdom's own state (no API). */
function installProbe(verdict: Verdict): void {
  if (verdict === "unknown") return;
  Object.defineProperty(navigator, "mediaCapabilities", {
    configurable: true,
    writable: true,
    value: {
      decodingInfo: () =>
        Promise.resolve({
          supported: verdict === "supported",
          smooth: verdict === "supported",
          powerEfficient: false,
        }),
    },
  });
}

function setPlatform(platform: string): void {
  Object.defineProperty(navigator, "userAgentData", {
    configurable: true,
    value: { platform },
  });
}

/** A new page life whose probe has already answered `verdict`. */
async function freshPageLife(verdict: Verdict) {
  vi.resetModules();
  installProbe(verdict);
  const capabilities = await import("./videoCapabilities");
  const hint = await import("./hevcHint");
  await capabilities.canPlayHevc();
  // Guard: the fake must have produced the state the test is about.
  expect(capabilities.hevcSupportSync()).toBe(verdict);
  return hint;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "mediaCapabilities");
  Reflect.deleteProperty(navigator, "userAgentData");
  Reflect.deleteProperty(navigator, "userAgent");
  localStorage.clear();
});

describe("noteTranscodeChosen — three probe states × Don't-show-again", () => {
  const cases: Array<[Verdict, boolean, boolean]> = [
    ["unsupported", false, true],
    ["unsupported", true, false],
    ["supported", false, false],
    ["supported", true, false],
    ["unknown", false, false],
    ["unknown", true, false],
  ];

  it.each(cases)(
    "Windows, probe %s, dismissed=%s → notice shown: %s",
    async (verdict, dismissed, expected) => {
      setPlatform("Windows");
      if (dismissed) localStorage.setItem(DISMISSED_KEY, "1");
      const hint = await freshPageLife(verdict);

      hint.noteTranscodeChosen();

      expect(hint.useHevcHintStore.getState().visible).toBe(expected);
    }
  );
});

describe("noteTranscodeChosen — platform", () => {
  it("never shows off Windows, even when the engine said unsupported", async () => {
    // The Store package is Windows-only; on macOS/Linux the advice is useless.
    setPlatform("macOS");
    const hint = await freshPageLife("unsupported");

    hint.noteTranscodeChosen();

    expect(hint.useHevcHintStore.getState().visible).toBe(false);
  });

  it("detects Windows from the UA string when UA Client Hints are absent (Firefox)", async () => {
    Object.defineProperty(navigator, "userAgent", {
      configurable: true,
      value:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    });
    const hint = await freshPageLife("unsupported");

    hint.noteTranscodeChosen();

    expect(hint.useHevcHintStore.getState().visible).toBe(true);
  });
});

describe("noteTranscodeChosen — once per page life", () => {
  it("a second transcode after Dismiss does not bring the notice back", async () => {
    setPlatform("Windows");
    const hint = await freshPageLife("unsupported");

    hint.noteTranscodeChosen();
    expect(hint.useHevcHintStore.getState().visible).toBe(true);

    hint.dismissHevcHint();
    hint.noteTranscodeChosen();

    expect(hint.useHevcHintStore.getState().visible).toBe(false);
    // Dismiss is for THIS page life only — nothing persisted.
    expect(localStorage.getItem(DISMISSED_KEY)).toBeNull();
  });

  it("a transcode chosen before the probe answered does not spend the showing", async () => {
    // A reactive error swap can land while decodingInfo is still pending; the
    // verdict is "unknown" then, which must not count as the one showing.
    setPlatform("Windows");
    vi.resetModules();
    installProbe("unsupported");
    const capabilities = await import("./videoCapabilities");
    const hint = await import("./hevcHint");

    const pending = capabilities.canPlayHevc();
    hint.noteTranscodeChosen();
    expect(hint.useHevcHintStore.getState().visible).toBe(false);

    await pending;
    hint.noteTranscodeChosen();
    expect(hint.useHevcHintStore.getState().visible).toBe(true);
  });
});

describe("Don't show again", () => {
  it("persists across a reload and keeps the notice off in the next page life", async () => {
    setPlatform("Windows");
    const first = await freshPageLife("unsupported");
    first.noteTranscodeChosen();
    first.dismissHevcHintForever();

    expect(first.useHevcHintStore.getState().visible).toBe(false);
    expect(localStorage.getItem(DISMISSED_KEY)).toBe("1");

    const second = await freshPageLife("unsupported");
    second.noteTranscodeChosen();

    expect(second.useHevcHintStore.getState().visible).toBe(false);
  });
});
