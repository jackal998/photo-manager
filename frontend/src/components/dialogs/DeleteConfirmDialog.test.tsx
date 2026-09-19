// DeleteConfirmDialog tests — real behaviour only.
//
// Covers:
//   1. Not rendered when open=false.
//   2. Renders with EXECUTE_ALL_DELETE_CONFIRM testid when open=true.
//   3. Both button testids are present.
//   4. Body contains the deleteCount digit.
//   5. Clicking Yes calls onConfirm.
//   6. Clicking Cancel calls onCancel.
//   7. Plural copy: "N files will be deleted" for N > 1.
//   8. Singular copy: "1 file will be deleted" for N === 1.
//   9. Body names the qualifying group IDs (#733 Qt-parity copy).
//  10. patternSummary prop (#741 sub-item C): overrides title + shows the
//      summary sentence + confirm button reads "Mark N files for deletion".
//  11. Generic callers (no patternSummary) are unaffected — additive prop.
//  12. #917 / REPLY Q6 — the auditable body when `groups` is passed: pinned
//      totals line, folder buckets with subtotals, one reason line per file,
//      the Recycle-Bin sentence, the pinned/scrolling layout, and zh_TW copy.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import type { FileRow, Group, Similarity } from "@/api/types";
import { useI18nStore } from "@/i18n/useI18nStore";
import { DeleteConfirmDialog } from "./DeleteConfirmDialog";
import {
  ACTION_DELETE_CONFIRM_SUMMARY,
  EXECUTE_ALL_DELETE_CONFIRM,
  EXECUTE_ALL_DELETE_CONFIRM_NO,
  EXECUTE_ALL_DELETE_CONFIRM_YES,
  EXECUTE_DELETE_CONFIRM_FOLDER,
  EXECUTE_DELETE_CONFIRM_REASON,
  EXECUTE_DELETE_CONFIRM_TOTALS,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mkRow(
  basename: string,
  folder: string,
  decision: FileRow["user_decision"],
  bytes: number,
  similarity: Similarity = { kind: "percent", percent: 100 }
): FileRow {
  return {
    file_path: `${folder}/${basename}`,
    basename,
    folder,
    action: "",
    user_decision: decision,
    is_locked: false,
    is_ref_winner: similarity.kind === "ref",
    similarity,
    score: null,
    file_size_bytes: bytes,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: null,
    thumbnail_url: "",
  };
}

/** One group whose Ref sits in /photos/trip and whose two delete rows sit in
 *  two DIFFERENT folders — the mixed case the folder bucketing exists for. */
function twoFolderGroups(): Group[] {
  return [
    {
      group_number: 1,
      member_count: 3,
      items: [
        mkRow("beach-01.jpg", "/photos/trip", "", 5_000, {
          kind: "ref",
          percent: null,
        }),
        mkRow("beach-01-copy.jpg", "/photos/trip", "delete", 12_288),
        mkRow("beach-01-web.jpg", "/photos/keepers", "delete", 12_288, {
          kind: "percent",
          percent: 92,
        }),
      ],
    },
  ];
}

function renderDialog(open = true, deleteCount = 5, groupIds = ["3"]) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const result = render(
    <DeleteConfirmDialog
      open={open}
      deleteCount={deleteCount}
      groupIds={groupIds}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
  return { ...result, onConfirm, onCancel };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DeleteConfirmDialog", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("is not rendered when open=false", () => {
    renderDialog(false);
    expect(
      screen.queryByTestId(EXECUTE_ALL_DELETE_CONFIRM)
    ).not.toBeInTheDocument();
  });

  it("renders with EXECUTE_ALL_DELETE_CONFIRM testid when open=true", () => {
    renderDialog(true);
    expect(
      screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)
    ).toBeInTheDocument();
  });

  it("both button testids present when open", () => {
    renderDialog(true);
    expect(
      screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES)
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_NO)
    ).toBeInTheDocument();
  });

  it("body contains the deleteCount digit", () => {
    renderDialog(true, 7);
    // The description renders "7 files will be deleted"
    const dialog = screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM);
    expect(dialog).toHaveTextContent("7");
  });

  it("clicking Yes calls onConfirm", async () => {
    const user = userEvent.setup();
    const { onConfirm, onCancel } = renderDialog(true, 3);
    await user.click(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("clicking Cancel calls onCancel", async () => {
    const user = userEvent.setup();
    const { onConfirm, onCancel } = renderDialog(true, 3);
    await user.click(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_NO));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("uses plural copy for N > 1", () => {
    renderDialog(true, 5);
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)).toHaveTextContent(
      "5 files will be deleted"
    );
  });

  it("uses singular copy for N === 1", () => {
    renderDialog(true, 1);
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)).toHaveTextContent(
      "1 file will be deleted"
    );
  });

  it("body names the qualifying group IDs", () => {
    renderDialog(true, 3, ["3", "5"]);
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)).toHaveTextContent(
      "Group(s) 3, 5 will have EVERY file deleted"
    );
  });

  // 10. patternSummary prop (#741 sub-item C).
  it("shows the pattern summary and 'Mark N files for deletion' when patternSummary is set", () => {
    render(
      <DeleteConfirmDialog
        open={true}
        deleteCount={5}
        patternSummary="File Name contains 'IMG'"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByTestId(ACTION_DELETE_CONFIRM_SUMMARY)).toHaveTextContent(
      "File Name contains 'IMG'"
    );
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES)).toHaveTextContent(
      "Mark 5 files for deletion"
    );
    // The generic "will be deleted" / "delete all" copy must NOT appear —
    // the deferred-decision wording is the whole point of this variant.
    expect(
      screen.queryByText(/delete all files\?/i)
    ).not.toBeInTheDocument();
  });

  it("clicking the pattern-summary confirm button still calls onConfirm", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <DeleteConfirmDialog
        open={true}
        deleteCount={1}
        patternSummary="Score >= 90"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );
    await user.click(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  // 11. Generic (group-based) callers are unaffected by the additive prop.
  it("omitting patternSummary keeps the generic group-delete copy unchanged", () => {
    renderDialog(true, 3, ["3", "5"]);
    expect(
      screen.queryByTestId(ACTION_DELETE_CONFIRM_SUMMARY)
    ).not.toBeInTheDocument();
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES)).toHaveTextContent(
      "Yes, delete all"
    );
  });
});

// ---------------------------------------------------------------------------
// 12. #917 / REPLY Q6 — the auditable body.
// ---------------------------------------------------------------------------

describe("DeleteConfirmDialog — folder buckets and reason lines (#917)", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  function renderWithGroups(groups: Group[], deleteCount = 2) {
    return render(
      <DeleteConfirmDialog
        open={true}
        deleteCount={deleteCount}
        groups={groups}
        groupIds={["1"]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
  }

  it("pins a totals line carrying the delete count and the reclaimed size", () => {
    renderWithGroups(twoFolderGroups());
    // 12_288 × 2 = 24 KB — the number the user is about to act on.
    expect(screen.getByTestId(EXECUTE_DELETE_CONFIRM_TOTALS)).toHaveTextContent(
      "2 files · 24.0 KB"
    );
  });

  it("buckets the rows by folder, each header carrying its own subtotal", () => {
    renderWithGroups(twoFolderGroups());
    const headers = screen.getAllByTestId(EXECUTE_DELETE_CONFIRM_FOLDER);
    expect(headers).toHaveLength(2);
    expect(headers[0]).toHaveTextContent("/photos/keepers");
    expect(headers[0]).toHaveTextContent("1 file · 12.0 KB");
    expect(headers[1]).toHaveTextContent("/photos/trip");
    expect(headers[1]).toHaveTextContent("1 file · 12.0 KB");
  });

  it("gives every delete row a reason line naming the group's Ref", () => {
    renderWithGroups(twoFolderGroups());
    const reasons = screen
      .getAllByTestId(EXECUTE_DELETE_CONFIRM_REASON)
      .map((el) => el.textContent);
    expect(reasons).toEqual([
      "92% match — same group",
      "Exact duplicate of beach-01.jpg",
    ]);
  });

  it("renders each similarity kind's own reason phrase", () => {
    renderWithGroups(
      [
        {
          group_number: 1,
          member_count: 6,
          items: [
            mkRow("ref.jpg", "/p", "delete", 1_000, { kind: "ref", percent: null }),
            mkRow("exact.jpg", "/p", "delete", 1_000, {
              kind: "percent",
              percent: 100,
            }),
            mkRow("near.jpg", "/p", "delete", 1_000, {
              kind: "percent",
              percent: 91,
            }),
            mkRow("passenger.jpg", "/p", "delete", 1_000, {
              kind: "passenger",
              percent: 88,
            }),
            mkRow("neardup.jpg", "/p", "delete", 1_000, {
              kind: "near_dup",
              percent: null,
            }),
            mkRow("none.jpg", "/p", "delete", 1_000, { kind: "none", percent: null }),
          ],
        },
      ],
      6
    );
    const reasons = screen
      .getAllByTestId(EXECUTE_DELETE_CONFIRM_REASON)
      .map((el) => el.textContent);
    expect(reasons).toEqual([
      // The Ref row is itself marked delete — it is not a duplicate "of"
      // anything, so it gets its own phrase rather than naming itself.
      "Group reference copy — the whole group is marked delete",
      "Exact duplicate of ref.jpg",
      "91% match — same group",
      "Linked match, 88%*",
      "Near-duplicate of ref.jpg",
      "No comparable image — same group",
    ]);
  });

  it("always carries the Recycle-Bin safety sentence", () => {
    renderWithGroups(twoFolderGroups());
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)).toHaveTextContent(
      "Files are moved to the Recycle Bin and can be restored."
    );
  });

  it("scrolls the row list inside a ≥60vh modal, totals and confirm pinned outside it", () => {
    renderWithGroups(twoFolderGroups());
    const content = screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM);
    // The modal is the tall flex column Q6 asks for…
    expect(content.className).toContain("min-h-[60vh]");
    expect(content.className).toContain("flex-col");
    // …the scroll lives on the list, which must be allowed to shrink
    // (min-h-0) or it grows the dialog instead of scrolling…
    const scroller = content.querySelector(".overflow-y-auto");
    expect(scroller).not.toBeNull();
    expect(scroller?.className).toContain("min-h-0");
    expect(
      within(scroller as HTMLElement).getAllByTestId(EXECUTE_DELETE_CONFIRM_FOLDER)
    ).toHaveLength(2);
    // …and neither the totals nor the confirm button is inside it.
    expect(
      scroller?.contains(screen.getByTestId(EXECUTE_DELETE_CONFIRM_TOTALS))
    ).toBe(false);
    expect(
      scroller?.contains(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES))
    ).toBe(false);
  });

  it("reads the totals, subtotal and reason copy from the zh_TW catalog", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      // The zh strings as committed in translations/zh_TW.yml — a key typo in
      // the component would silently fall back to English here.
      catalog: {
        "web.delete_confirm.count_size": "{n} 個{fileWord} · {size}",
        "web.delete_confirm.file_singular": "檔案",
        "web.delete_confirm.file_plural": "檔案",
        "web.delete_confirm.reason_exact": "與 {ref} 完全重複",
        "web.delete_confirm.reason_near": "{percent}% 相似 — 同一群組",
        "web.delete_confirm.recycle_note": "檔案將移至資源回收筒，可還原。",
      },
    });
    renderWithGroups(twoFolderGroups());
    expect(screen.getByTestId(EXECUTE_DELETE_CONFIRM_TOTALS)).toHaveTextContent(
      "2 個檔案 · 24.0 KB"
    );
    expect(
      screen.getAllByTestId(EXECUTE_DELETE_CONFIRM_FOLDER)[0]
    ).toHaveTextContent("1 個檔案 · 12.0 KB");
    const reasons = screen
      .getAllByTestId(EXECUTE_DELETE_CONFIRM_REASON)
      .map((el) => el.textContent);
    expect(reasons).toEqual(["92% 相似 — 同一群組", "與 beach-01.jpg 完全重複"]);
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)).toHaveTextContent(
      "檔案將移至資源回收筒，可還原。"
    );
  });

  it("narrows the list and the pinned total to the execute scope", () => {
    // PR #921 review, HIGH: "Execute (only selected)" sends
    // scope_paths=selection; a dialog listing the whole group names files that
    // will NOT be deleted and pins a total the server contradicts.
    render(
      <DeleteConfirmDialog
        open={true}
        deleteCount={1}
        groups={twoFolderGroups()}
        scopePaths={["/photos/trip/beach-01-copy.jpg"]}
        groupIds={["1"]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getAllByTestId(EXECUTE_DELETE_CONFIRM_FOLDER)).toHaveLength(1);
    expect(screen.getByTestId(EXECUTE_DELETE_CONFIRM_TOTALS)).toHaveTextContent(
      "1 file · 12.0 KB"
    );
    const reasons = screen.getAllByTestId(EXECUTE_DELETE_CONFIRM_REASON);
    expect(reasons).toHaveLength(1);
    // The out-of-scope row is gone from the list…
    expect(screen.queryByText("beach-01-web.jpg")).toBeNull();
    // …while the Ref it duplicates is still named, though it is not in scope.
    expect(reasons[0]).toHaveTextContent("Exact duplicate of beach-01.jpg");
  });

  it("lists every delete row when scopePaths is null (unscoped Execute)", () => {
    renderWithGroups(twoFolderGroups());
    expect(screen.getAllByTestId(EXECUTE_DELETE_CONFIRM_REASON)).toHaveLength(2);
    expect(screen.getByTestId(EXECUTE_DELETE_CONFIRM_TOTALS)).toHaveTextContent(
      "2 files · 24.0 KB"
    );
  });

  it("omitting groups keeps the compact dialog with no row list (additive prop)", () => {
    renderDialog(true, 3, ["3"]);
    expect(screen.queryByTestId(EXECUTE_DELETE_CONFIRM_TOTALS)).toBeNull();
    expect(screen.queryByTestId(EXECUTE_DELETE_CONFIRM_FOLDER)).toBeNull();
    expect(
      screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM).className
    ).not.toContain("min-h-[60vh]");
  });
});
