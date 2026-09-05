// ScanProgress tests (#740) — localized stage labels, throughput, and ETA.
//
// Covers:
//   1. A known SSE stage_name renders its localized label (English fallback,
//      since the test catalog is empty by default — same text a user with no
//      catalog loaded yet would see).
//   2. The catalog's translation wins over the fallback when loaded (proves
//      the mapping actually goes through useT()/the i18n store, not a
//      hardcoded string).
//   3. An unmapped/unknown stage falls back to the raw id — never crashes.
//   4. An empty stage name falls back to "Running…" (pre-#740 behaviour).
//   5. Throughput text shows when filesPerSec > 0, hidden at 0.
//   6. ETA text shows when the `eta` prop is a string, hidden when null.

import { type ComponentProps } from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { ScanProgress } from "./ScanProgress";
import { useI18nStore } from "@/i18n/useI18nStore";
import {
  SCAN_ETA_TEXT,
  SCAN_STATUS_TEXT,
  SCAN_THROUGHPUT_TEXT,
} from "@/testids";

function setup(overrides: Partial<ComponentProps<typeof ScanProgress>> = {}) {
  const onCancel = vi.fn();
  render(
    <ScanProgress
      stageName="HASH"
      completed={10}
      total={100}
      filesPerSec={0}
      eta={null}
      log={[]}
      onCancel={onCancel}
      {...overrides}
    />
  );
  return { onCancel };
}

describe("ScanProgress", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("renders the localized label for a known stage (English fallback)", () => {
    setup({ stageName: "HASH" });
    expect(screen.getByTestId(SCAN_STATUS_TEXT)).toHaveTextContent("Hashing files");
  });

  it("renders every known stage's distinct localized label", () => {
    const expected: Record<string, string> = {
      WALK: "Walking sources",
      HASH: "Hashing files",
      EXIFTOOL: "Reading EXIF + scoring signals",
      CLASSIFY: "Classifying duplicates",
      SCORE: "Scoring keepers",
      WRITE: "Writing manifest",
    };
    for (const [stageName, label] of Object.entries(expected)) {
      const { unmount } = render(
        <ScanProgress
          stageName={stageName}
          completed={0}
          total={0}
          filesPerSec={0}
          eta={null}
          log={[]}
          onCancel={vi.fn()}
        />
      );
      expect(screen.getByTestId(SCAN_STATUS_TEXT)).toHaveTextContent(label);
      unmount();
    }
  });

  it("prefers the loaded catalog's translation over the English fallback", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: { "web.scan.stage_hash": "正在計算雜湊" },
    });
    setup({ stageName: "HASH" });
    expect(screen.getByTestId(SCAN_STATUS_TEXT)).toHaveTextContent("正在計算雜湊");
  });

  it("falls back to the raw stage id for an unmapped stage — does not crash", () => {
    setup({ stageName: "SOME_FUTURE_STAGE" });
    expect(screen.getByTestId(SCAN_STATUS_TEXT)).toHaveTextContent("SOME_FUTURE_STAGE");
  });

  it("falls back to 'Running…' when stageName is empty", () => {
    setup({ stageName: "" });
    expect(screen.getByTestId(SCAN_STATUS_TEXT)).toHaveTextContent("Running…");
  });

  it("shows throughput text when filesPerSec > 0", () => {
    setup({ filesPerSec: 7.5 });
    expect(screen.getByTestId(SCAN_THROUGHPUT_TEXT)).toHaveTextContent("7.5 files/sec");
  });

  it("hides throughput text when filesPerSec is 0", () => {
    setup({ filesPerSec: 0 });
    expect(screen.queryByTestId(SCAN_THROUGHPUT_TEXT)).not.toBeInTheDocument();
  });

  it("shows ETA text when the eta prop is a meaningful string", () => {
    setup({ eta: "~2m 30s" });
    expect(screen.getByTestId(SCAN_ETA_TEXT)).toHaveTextContent("ETA ~2m 30s");
  });

  it("hides ETA text when the eta prop is null", () => {
    setup({ eta: null });
    expect(screen.queryByTestId(SCAN_ETA_TEXT)).not.toBeInTheDocument();
  });

  it("hides the whole throughput/ETA row when both are absent", () => {
    setup({ filesPerSec: 0, eta: null });
    expect(screen.queryByTestId(SCAN_THROUGHPUT_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByTestId(SCAN_ETA_TEXT)).not.toBeInTheDocument();
  });
});
