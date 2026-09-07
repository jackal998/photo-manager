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

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";

import { DeleteConfirmDialog } from "./DeleteConfirmDialog";
import {
  ACTION_DELETE_CONFIRM_SUMMARY,
  EXECUTE_ALL_DELETE_CONFIRM,
  EXECUTE_ALL_DELETE_CONFIRM_NO,
  EXECUTE_ALL_DELETE_CONFIRM_YES,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
