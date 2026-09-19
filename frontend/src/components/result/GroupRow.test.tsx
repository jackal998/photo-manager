// Copy audit R8 — the group header row was hardcoded English, so a zh_TW
// session read "Group 3 · 5 files" in the middle of an otherwise translated
// tree. `tree.*` was the one desktop namespace never mirrored into `web.*`.
//
// Layout slice G (#878) adds the derived totals and the Q5 bulk verb to the
// same row, so this file also covers what the header says about the group and
// what the button does with it.

import type { ComponentProps } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";

import { GroupRow } from "./GroupRow";
import type { FileRow as FileRowData } from "@/api/types";
import { useI18nStore } from "@/i18n/useI18nStore";
import { groupKeepBestTestid, rowGroupTestid } from "@/testids";

function makeItem(overrides: Partial<FileRowData> = {}): FileRowData {
  return {
    file_path: "/photos/2021-trip/a.jpg",
    basename: "a.jpg",
    folder: "/photos/2021-trip",
    action: "REVIEW_DUPLICATE",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "percent", percent: 95 },
    score: 71,
    file_size_bytes: 1_048_576,
    pixel_width: 1920,
    pixel_height: 1080,
    shot_date: null,
    creation_date: null,
    phash: "aabbcc001122",
    hamming_distance: 3,
    thumbnail_url: "/api/image?path=/photos/2021-trip/a.jpg&size=512",
    ...overrides,
  };
}

function renderRow(
  memberCount: number,
  items: FileRowData[] = [makeItem()],
  extra: Partial<ComponentProps<typeof GroupRow>> = {}
) {
  render(
    <GroupRow
      groupNumber={3}
      memberCount={memberCount}
      items={items}
      expanded
      onToggle={vi.fn()}
      {...extra}
    />
  );
  return screen.getByTestId(rowGroupTestid("3"));
}

describe("GroupRow copy", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("renders the English group label and a plural file count", () => {
    expect(renderRow(5)).toHaveTextContent("Group 3");
    expect(screen.getByTestId(rowGroupTestid("3"))).toHaveTextContent("5 files");
  });

  it("uses the singular noun for a one-file group", () => {
    const row = renderRow(1);
    expect(row).toHaveTextContent("1 file");
    expect(row.textContent).not.toContain("1 files");
  });

  it("renders the zh_TW catalog values when the locale is Chinese", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        "web.tree.group_label": "群組 {n}",
        "web.tree.file_plural": "個檔案",
        "web.tree.keep_best": "保留最佳 · 其餘刪除",
      },
    });
    const row = renderRow(5, [makeItem()], { onKeepBest: vi.fn() });
    expect(row).toHaveTextContent("群組 3");
    expect(row).toHaveTextContent("5 個檔案");
    expect(row).toHaveTextContent("保留最佳 · 其餘刪除");
    // The defect this replaces: English leaking into a zh_TW tree.
    expect(row).not.toHaveTextContent("Group");
    expect(row).not.toHaveTextContent("files");
    expect(row).not.toHaveTextContent("Keep best");
  });
});

describe("GroupRow derived totals (Q4)", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("shows the summed group size beside the count", () => {
    // 1 MiB + 3 MiB = 4 MiB — a number the manifest never sends, so a broken
    // sum renders a plausible-looking wrong figure rather than nothing.
    const row = renderRow(2, [
      makeItem({ file_size_bytes: 1_048_576 }),
      makeItem({ file_path: "/photos/2021-trip/b.jpg", file_size_bytes: 3_145_728 }),
    ]);
    expect(row).toHaveTextContent("4.0 MB");
  });

  it("shows the shared folder's LEAF when every member sits in one folder", () => {
    const row = renderRow(2, [
      makeItem(),
      makeItem({ file_path: "/photos/2021-trip/b.jpg", basename: "b.jpg" }),
    ]);
    expect(row).toHaveTextContent("2021-trip");
    // The full path is what H6 was rejected for — the leaf only.
    expect(row.textContent).not.toContain("/photos/2021-trip");
  });

  it("shows the folder COUNT when members span folders", () => {
    const row = renderRow(2, [
      makeItem(),
      makeItem({
        file_path: "/photos/backup/b.jpg",
        basename: "b.jpg",
        folder: "/photos/backup",
      }),
    ]);
    expect(row).toHaveTextContent("2 folders");
    expect(row.textContent).not.toContain("2021-trip");
  });
});

describe("GroupRow keep-best button (Q5)", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("calls onKeepBest and does NOT toggle the group", () => {
    const onToggle = vi.fn();
    const onKeepBest = vi.fn();
    renderRow(5, [makeItem()], { onToggle, onKeepBest });

    fireEvent.click(screen.getByTestId(groupKeepBestTestid("3")));

    expect(onKeepBest).toHaveBeenCalledTimes(1);
    // Q5's whole placement argument: the expensive misclick on this screen is
    // collapse → bulk decision. Firing BOTH would be that misclick by design.
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("is a real button, so Enter reaches it without collapsing the group", () => {
    const onToggle = vi.fn();
    const onKeepBest = vi.fn();
    renderRow(5, [makeItem()], { onToggle, onKeepBest });

    const button = screen.getByTestId(groupKeepBestTestid("3"));
    expect(button.tagName).toBe("BUTTON");
    // The row's own Enter handler must ignore key presses that started on the
    // nested button — otherwise keyboard users collapse the group as a side
    // effect of activating the verb.
    fireEvent.keyDown(button, { key: "Enter" });
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("is absent when no keep-best handler is supplied", () => {
    renderRow(5);
    expect(screen.queryByTestId(groupKeepBestTestid("3"))).toBeNull();
  });

  it("is disabled while this group's apply is in flight", () => {
    const onKeepBest = vi.fn();
    renderRow(5, [makeItem()], { onKeepBest, keepBestPending: true });

    const button = screen.getByTestId(groupKeepBestTestid("3"));
    expect(button).toBeDisabled();
    fireEvent.click(button);
    // The second apply would read an ALREADY-APPLIED group as its "before",
    // so the undo toast would snapshot the damage instead of the original.
    expect(onKeepBest).not.toHaveBeenCalled();
  });

  it("is disabled, with a reason, while the toolbar filter is active", () => {
    // Round-2 MEDIUM: keep-best is a SERVER-side write scoped by group_number,
    // so under a filter it marks rows the user cannot see while this header
    // shows the FILTERED count beside the button. Disabled rather than
    // re-scoped — picking a "best" copy from a visible subset is a different
    // and silently wrong decision.
    const onKeepBest = vi.fn();
    renderRow(5, [makeItem()], { onKeepBest, keepBestFiltered: true });

    const button = screen.getByTestId(groupKeepBestTestid("3"));
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Clear the filter to use Keep best");
    fireEvent.click(button);
    expect(onKeepBest).not.toHaveBeenCalled();
  });

  it("carries no title and stays enabled with no filter", () => {
    // The false-positive half: a gate that refuses everything looks identical
    // to one that is working, and this button is the fast path through the
    // whole screen.
    const onKeepBest = vi.fn();
    renderRow(5, [makeItem()], { onKeepBest });

    const button = screen.getByTestId(groupKeepBestTestid("3"));
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("title");
    fireEvent.click(button);
    expect(onKeepBest).toHaveBeenCalledTimes(1);
  });
});

describe("GroupRow accessible name", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("names the row for the GROUP, not for its whole subtree", () => {
    renderRow(5, [makeItem()], { onKeepBest: vi.fn() });

    // Without an explicit name, role="button" is named by its subtree, so a
    // screen reader reads the bulk verb as part of the collapse toggle's
    // label — the audio version of the misclick Q5's 16px gap prevents.
    const row = screen.getByRole("button", { name: "Group 3, 5 files" });
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(row.getAttribute("aria-label")).not.toContain("Keep best");
  });

  it("uses the singular noun for a one-file group", () => {
    expect(renderRow(1)).toHaveAttribute("aria-label", "Group 3, 1 file");
  });

  it("reads from the zh_TW catalog", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        "web.tree.group_aria": "群組 {n}，{count} {fileWord}",
        "web.tree.file_plural": "個檔案",
      },
    });
    expect(renderRow(5)).toHaveAttribute("aria-label", "群組 3，5 個檔案");
  });

  it("still toggles on a click on the row itself, and on Enter", () => {
    const onToggle = vi.fn();
    const row = renderRow(5, [makeItem()], { onToggle, onKeepBest: vi.fn() });

    fireEvent.click(row);
    expect(onToggle).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onToggle).toHaveBeenCalledTimes(2);
    // aria-expanded is the collapse contract every keyboard scenario reads.
    expect(row).toHaveAttribute("aria-expanded", "true");
  });
});
