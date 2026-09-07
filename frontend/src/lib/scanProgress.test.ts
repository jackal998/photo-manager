// Unit tests for the #740 scan-progress pure helpers: stage-label i18n
// mapping (with the required "unknown stage doesn't crash" guard), throughput
// formatting, and the ETA min-samples gate. Mirrors the Qt #424 contract
// (app/views/dialogs/scan_dialog.py _format_throughput / _format_eta).

import { describe, it, expect } from "vitest";

import {
  ETA_MIN_SAMPLES_SECONDS,
  formatEta,
  formatThroughput,
  isEtaReady,
  stageLabel,
} from "./scanProgress";

// A stand-in for useT()'s bound t() — returns the fallback unless the key is
// explicitly seeded, so tests can simulate "catalog has this key" without
// pulling in the real zustand i18n store.
function fakeT(catalog: Record<string, string> = {}) {
  return (key: string, fallback: string) => catalog[key] ?? fallback;
}

describe("stageLabel", () => {
  it("maps every real SSE stage_name to its localized label", () => {
    const t = fakeT();
    expect(stageLabel("WALK", t)).toBe("Walking sources");
    expect(stageLabel("HASH", t)).toBe("Hashing files");
    expect(stageLabel("EXIFTOOL", t)).toBe("Reading EXIF + scoring signals");
    expect(stageLabel("CLASSIFY", t)).toBe("Classifying duplicates");
    expect(stageLabel("SCORE", t)).toBe("Scoring keepers");
    expect(stageLabel("WRITE", t)).toBe("Writing manifest");
  });

  it("uses the catalog translation when the i18n store has loaded one", () => {
    const t = fakeT({ "web.scan.stage_hash": "正在計算雜湊" });
    expect(stageLabel("HASH", t)).toBe("正在計算雜湊");
  });

  it("falls back to the raw stage id for an unmapped stage — never crashes", () => {
    const t = fakeT();
    expect(stageLabel("SOME_FUTURE_STAGE", t)).toBe("SOME_FUTURE_STAGE");
  });

  it("falls back to 'Running…' for an empty stage name", () => {
    const t = fakeT();
    expect(stageLabel("", t)).toBe("Running…");
  });
});

describe("formatThroughput", () => {
  it("suppresses (null) at zero or negative rate", () => {
    expect(formatThroughput(0)).toBeNull();
    expect(formatThroughput(-1)).toBeNull();
  });

  it("shows one decimal below 10/s", () => {
    expect(formatThroughput(7.53)).toBe("7.5 files/sec");
  });

  it("rounds to a whole number at/above 10/s", () => {
    expect(formatThroughput(123.6)).toBe("124 files/sec");
    expect(formatThroughput(10)).toBe("10 files/sec");
  });
});

describe("formatEta", () => {
  it("suppresses (null) at zero/negative rate or nothing remaining", () => {
    expect(formatEta(100, 0)).toBeNull();
    expect(formatEta(100, -5)).toBeNull();
    expect(formatEta(0, 10)).toBeNull();
    expect(formatEta(-5, 10)).toBeNull();
  });

  it("renders sub-second as <1s", () => {
    expect(formatEta(1, 10)).toBe("<1s");
  });

  it("renders seconds under a minute", () => {
    expect(formatEta(45, 1)).toBe("~45s");
  });

  it("renders minutes + seconds under an hour", () => {
    // 150 remaining / 1 per sec = 150s = 2m 30s
    expect(formatEta(150, 1)).toBe("~2m 30s");
  });

  it("renders hours + minutes at/above an hour", () => {
    // 7290s = 2h 01m
    expect(formatEta(7290, 1)).toBe("~2h 01m");
  });
});

describe("isEtaReady", () => {
  it("is false before the min-samples window elapses", () => {
    expect(isEtaReady(ETA_MIN_SAMPLES_SECONDS - 0.1, 100)).toBe(false);
  });

  it("is true once the window elapses AND a total is known", () => {
    expect(isEtaReady(ETA_MIN_SAMPLES_SECONDS, 100)).toBe(true);
  });

  it("stays false for an indeterminate stage (total=0) no matter how long", () => {
    expect(isEtaReady(999, 0)).toBe(false);
  });
});
