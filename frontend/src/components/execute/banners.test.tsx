// Copy audit E2 + E3 — the two Execute-dialog warning banners were hardcoded
// English. The all-delete banner was assembled from five JSX fragments, an
// order no locale can rewrite; the hidden-destructive banner flattened its
// count noun to "row(s)", the shape this catalog's own comments record as a
// review finding ("1 file(s)").

import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";

import { AllDeleteBanner } from "./AllDeleteBanner";
import { HiddenDestructiveBanner } from "./HiddenDestructiveBanner";
import { useI18nStore } from "@/i18n/useI18nStore";
import {
  EXECUTE_ALL_DELETE_BANNER,
  EXECUTE_HIDDEN_DESTRUCTIVE_BANNER,
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
        "web.execute_dialog.warning_hidden_destructive":
          "⚠ 目前的篩選條件隱藏了 {n} {rowWord}待刪除的項目 — 切換到「全部決策」或「僅刪除」即可看到。",
        "web.execute_dialog.row_plural": "列",
      },
    });
    render(<HiddenDestructiveBanner count={4} />);
    expect(screen.getByTestId(EXECUTE_HIDDEN_DESTRUCTIVE_BANNER)).toHaveTextContent(
      "隱藏了 4 列待刪除的項目"
    );
  });
});
