// Copy audit R8 — the group header row was hardcoded English, so a zh_TW
// session read "Group 3 · 5 files" in the middle of an otherwise translated
// tree. `tree.*` was the one desktop namespace never mirrored into `web.*`.

import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";

import { GroupRow } from "./GroupRow";
import { useI18nStore } from "@/i18n/useI18nStore";
import { rowGroupTestid } from "@/testids";

function renderRow(memberCount: number) {
  render(
    <GroupRow
      groupNumber={3}
      memberCount={memberCount}
      memberPaths={["/a.jpg", "/b.jpg"]}
      expanded
      onToggle={vi.fn()}
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
      },
    });
    const row = renderRow(5);
    expect(row).toHaveTextContent("群組 3");
    expect(row).toHaveTextContent("5 個檔案");
    // The defect this replaces: English leaking into a zh_TW tree.
    expect(row).not.toHaveTextContent("Group");
    expect(row).not.toHaveTextContent("files");
  });
});
