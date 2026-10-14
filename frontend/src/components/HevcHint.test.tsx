// Tests for the #869 HEVC notice component. WHEN it appears is lib/hevcHint's
// job (lib/hevcHint.test.ts drives the real probe); these pin what the user
// sees and can do once it is up:
//   - the link opens the Store page of the product that fixes the problem;
//   - it wears the Q9 caution banner, not an invented or stock-scale visual;
//   - Dismiss closes it for now, "Don't show again" closes it for good — and
//     only the second one may write the persisted flag;
//   - a locale can move the product name inside its sentence.

import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";

import { HevcHint } from "./HevcHint";
import { useHevcHintStore } from "@/lib/hevcHint";
import { useI18nStore } from "@/i18n/useI18nStore";
import {
  PREVIEW_HEVC_HINT,
  PREVIEW_HEVC_HINT_DISMISS,
  PREVIEW_HEVC_HINT_NEVER,
  PREVIEW_HEVC_HINT_STORE_LINK,
} from "@/testids";

const DISMISSED_KEY = "pm.hevc-hint.dismissed.v1";

function raise(): void {
  act(() => {
    useHevcHintStore.setState({ visible: true, shown: true });
  });
}

describe("HevcHint", () => {
  beforeEach(() => {
    useHevcHintStore.setState({ visible: false, shown: false });
    useI18nStore.setState({ locale: "en", catalog: {} });
    localStorage.clear();
  });

  it("renders nothing until a transcode raises it", () => {
    render(<HevcHint />);
    expect(screen.queryByTestId(PREVIEW_HEVC_HINT)).not.toBeInTheDocument();
  });

  it("says why videos are converted and links the Store package that fixes it", () => {
    render(<HevcHint />);
    raise();

    const hint = screen.getByTestId(PREVIEW_HEVC_HINT);
    expect(hint).toHaveTextContent("This browser cannot decode HEVC");
    const link = screen.getByTestId(PREVIEW_HEVC_HINT_STORE_LINK);
    expect(link).toHaveTextContent("HEVC Video Extensions");
    // 9NMZLZ57R3T7 = the retail "HEVC Video Extensions"; the OEM-only
    // "from Device Manufacturer" variant (9N4WGH0Z6VHQ) is not offered to
    // every machine, so it would be a dead end for many users.
    expect(link).toHaveAttribute(
      "href",
      "ms-windows-store://pdp/?productid=9NMZLZ57R3T7"
    );
  });

  it("wears the Q9 caution banner with its ▲ glyph, actions as buttons below it", () => {
    render(<HevcHint />);
    raise();

    const banner = screen.getByRole("status");
    for (const cls of [
      "bg-caution-bg",
      "border-caution-line",
      "border-l-[3px]",
      "text-caution-ink",
    ]) {
      expect(banner.className).toContain(cls);
    }
    expect(banner.className).not.toMatch(/amber|yellow|red|green/);
    expect(banner.querySelector('[aria-hidden="true"]')?.textContent?.trim()).toBe("▲");
    // «Caution never becomes a button … the action is a normal button below
    // it.» — neither action lives inside the banner.
    expect(banner).not.toContainElement(screen.getByTestId(PREVIEW_HEVC_HINT_DISMISS));
    expect(banner).not.toContainElement(screen.getByTestId(PREVIEW_HEVC_HINT_NEVER));
  });

  it("Dismiss closes it for this page life without persisting anything", () => {
    render(<HevcHint />);
    raise();

    fireEvent.click(screen.getByTestId(PREVIEW_HEVC_HINT_DISMISS));

    expect(screen.queryByTestId(PREVIEW_HEVC_HINT)).not.toBeInTheDocument();
    expect(localStorage.getItem(DISMISSED_KEY)).toBeNull();
  });

  it("Don't show again closes it and persists the choice", () => {
    render(<HevcHint />);
    raise();

    fireEvent.click(screen.getByTestId(PREVIEW_HEVC_HINT_NEVER));

    expect(screen.queryByTestId(PREVIEW_HEVC_HINT)).not.toBeInTheDocument();
    expect(localStorage.getItem(DISMISSED_KEY)).toBe("1");
  });

  it("puts the link where the locale's template puts {link}", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        "web.preview.hevc_hint":
          "此瀏覽器無法解碼 HEVC，影片會先轉檔再播放。從 Microsoft Store 安裝 {link} 即可直接播放。",
        "web.preview.hevc_hint_link": "HEVC 視訊延伸模組",
      },
    });
    render(<HevcHint />);
    raise();

    const hint = screen.getByTestId(PREVIEW_HEVC_HINT);
    expect(hint).toHaveTextContent("安裝 HEVC 視訊延伸模組 即可直接播放");
    // The placeholder is consumed, never printed.
    expect(hint.textContent).not.toContain("{link}");
  });
});
