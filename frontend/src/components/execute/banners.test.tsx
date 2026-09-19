// Copy audit E2 + E3 — the two Execute-dialog warning banners were hardcoded
// English. The all-delete banner was assembled from five JSX fragments, an
// order no locale can rewrite; the hidden-destructive banner flattened its
// count noun to "row(s)", the shape this catalog's own comments record as a
// review finding ("1 file(s)").

import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";

import { AllDeleteBanner } from "./AllDeleteBanner";
import { HiddenDestructiveBanner } from "./HiddenDestructiveBanner";
import { ExecuteResultSummary } from "./ExecuteResultSummary";
import { useI18nStore } from "@/i18n/useI18nStore";
import {
  EXECUTE_ALL_DELETE_BANNER,
  EXECUTE_HIDDEN_DESTRUCTIVE_BANNER,
  EXECUTE_RESULT_FAILED,
  EXECUTE_RESULT_MISSING,
  executeAllDeleteJumpTestid,
} from "@/testids";

describe("AllDeleteBanner copy", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("keeps every group id as its own clickable jump anchor", () => {
    const onJump = vi.fn();
    render(<AllDeleteBanner allDeleteGroupIds={["3", "7"]} onJumpToGroup={onJump} />);
    const banner = screen.getByTestId(EXECUTE_ALL_DELETE_BANNER);
    expect(banner).toHaveTextContent("Groups");
    expect(banner).toHaveTextContent("will have ALL files deleted");
    screen.getByTestId(executeAllDeleteJumpTestid("7")).click();
    expect(onJump).toHaveBeenCalledWith("7");
  });

  it("uses the singular group noun for a single group", () => {
    render(<AllDeleteBanner allDeleteGroupIds={["3"]} onJumpToGroup={vi.fn()} />);
    const banner = screen.getByTestId(EXECUTE_ALL_DELETE_BANNER);
    expect(banner.textContent).toContain("Group 3 will");
    expect(banner.textContent).not.toContain("Groups 3");
  });

  it("renders a zh_TW template — including its different word order — around the anchors", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        "web.execute_dialog.warning_prefix": "警告：",
        "web.execute_dialog.warning_complete_groups":
          "{groupWord} {groups} 中的所有檔案都將被刪除。請先檢閱下方的決策再按「執行」。",
        "web.execute_dialog.group_plural": "群組",
      },
    });
    render(<AllDeleteBanner allDeleteGroupIds={["3", "7"]} onJumpToGroup={vi.fn()} />);
    const banner = screen.getByTestId(EXECUTE_ALL_DELETE_BANNER);
    expect(banner).toHaveTextContent("群組 3, 7 中的所有檔案都將被刪除");
    // The anchors survive the template split.
    expect(screen.getByTestId(executeAllDeleteJumpTestid("3"))).toBeInTheDocument();
    // No stray placeholder leaked into the rendered sentence.
    expect(banner.textContent).not.toContain("{groups}");
  });

  it("still renders the whole sentence if a translation drops the {groups} marker", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        "web.execute_dialog.warning_complete_groups": "整組都會被刪除。",
      },
    });
    render(<AllDeleteBanner allDeleteGroupIds={["3"]} onJumpToGroup={vi.fn()} />);
    const banner = screen.getByTestId(EXECUTE_ALL_DELETE_BANNER);
    expect(banner).toHaveTextContent("整組都會被刪除。");
    expect(screen.getByTestId(executeAllDeleteJumpTestid("3"))).toBeInTheDocument();
  });
});

describe("HiddenDestructiveBanner copy", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("uses the singular row noun for one hidden row, never 'row(s)'", () => {
    render(<HiddenDestructiveBanner count={1} />);
    const banner = screen.getByTestId(EXECUTE_HIDDEN_DESTRUCTIVE_BANNER);
    expect(banner).toHaveTextContent("1 pending delete row hidden");
    expect(banner.textContent).not.toContain("row(s)");
  });

  it("uses the plural row noun for several hidden rows", () => {
    render(<HiddenDestructiveBanner count={4} />);
    expect(screen.getByTestId(EXECUTE_HIDDEN_DESTRUCTIVE_BANNER)).toHaveTextContent(
      "4 pending delete rows hidden"
    );
  });

  it("renders the zh_TW translation when the catalog carries one", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        // As shipped since layout slice E: no leading ⚠ in the COPY — the
        // caution role's ▲ is rendered by the component.
        "web.execute_dialog.warning_hidden_destructive":
          "目前的篩選條件隱藏了 {n} {rowWord}待刪除的項目 — 切換到「全部決策」或「僅刪除」即可看到。",
        "web.execute_dialog.row_plural": "列",
      },
    });
    render(<HiddenDestructiveBanner count={4} />);
    expect(screen.getByTestId(EXECUTE_HIDDEN_DESTRUCTIVE_BANNER)).toHaveTextContent(
      "隱藏了 4 列待刪除的項目"
    );
  });
});

// ---------------------------------------------------------------------------
// The caution role (layout slice E · open questions Q9)
// ---------------------------------------------------------------------------
//
// Both banners were on Tailwind's stock `amber-*` scale — the last off-palette
// colour in the themed tree, a cold yellow rectangle on warm paper. Q9 gives
// caution its own role: pale `caution-bg` under `caution-ink`, a `caution-line`
// frame with a 3px left rule, and ▲ as the glyph that carries it in grayscale
// (danger is the only DARK fill with a light label; caution and positive are
// both pale, so their glyphs are what separate them).
//
// These are class-level assertions on purpose — the COMPUTED rgb values are
// `qa/web/scenarios/s74_daylight_theme.py`, because a jsdom test cannot tell a
// live token from a deleted one. What jsdom CAN catch, and what this pins, is
// the regression that actually happened: a banner drifting back onto a utility
// scale nobody owns.

describe("caution role on the execute banners", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  for (const [name, node] of [
    [
      "all-delete",
      <AllDeleteBanner allDeleteGroupIds={["3"]} onJumpToGroup={vi.fn()} />,
    ],
    ["hidden-destructive", <HiddenDestructiveBanner count={2} />],
  ] as const) {
    it(`gives the ${name} banner the caution tokens and the ▲ glyph`, () => {
      const { container } = render(node);
      const banner = container.firstElementChild as HTMLElement;
      for (const cls of [
        "bg-caution-bg",
        "border-caution-line",
        "border-l-[3px]",
        "text-caution-ink",
        "rounded-[8px]",
      ]) {
        expect(banner.className).toContain(cls);
      }
      // Never back onto the stock scale.
      expect(banner.className).not.toMatch(/amber|yellow/);
      expect(banner.textContent).toContain("▲");
      // One warning mark, not two: the ⚠ that used to lead the sentence left
      // the copy when the component took ownership of the glyph.
      expect(banner.textContent).not.toContain("⚠");
    });
  }

  it("marks the glyph decorative so a screen reader reads only the sentence", () => {
    render(<HiddenDestructiveBanner count={2} />);
    const banner = screen.getByTestId(EXECUTE_HIDDEN_DESTRUCTIVE_BANNER);
    const glyph = banner.querySelector('[aria-hidden="true"]');
    expect(glyph?.textContent?.trim()).toBe("▲");
    expect(banner).toHaveAttribute("role", "alert");
  });

  it("keeps the post-execute split: caution for missing, danger for failed", () => {
    // The two lists mean different things — "the file was already gone" is the
    // outcome the user asked for, reported; "we could not delete it" is a
    // failure. Retoning both to one colour would erase that, which is the
    // easy mistake when the amber one is the thing being replaced.
    render(
      <ExecuteResultSummary missing={["C:/a.jpg"]} failed={[["C:/b.jpg", "in use"]]} />
    );
    const missing = screen.getByTestId(EXECUTE_RESULT_MISSING);
    expect(missing.className).toContain("bg-caution-bg");
    expect(missing.className).not.toMatch(/amber|yellow/);
    expect(missing.textContent).toContain("▲");
    const failed = screen.getByTestId(EXECUTE_RESULT_FAILED);
    expect(failed.className).toContain("border-danger-warm");
    expect(failed.className).not.toContain("caution");
    expect(failed.querySelector("p")!.className).toContain("text-danger-warm");
  });
});
