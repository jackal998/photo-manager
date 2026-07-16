// LockConfirmDialog tests — real behaviour only.
//
// Covers:
//   1. Not rendered when lockConflict is null.
//   2. Rendered with correct testid when lockConflict is set.
//   3. All three button testids present when open.
//   4. Cancel clears lockConflict and does NOT call execute/remove.
//   5. Unlock & Apply (execute op) re-runs the ORIGINAL scope with forceLocked
//      (must NOT broaden to the whole manifest — over-deletion guard).
//   6. Unlock & Apply (remove op) removes the full ORIGINAL list with force
//      (must NOT narrow to just the locked subset — under-action guard).
//   7. Unlocked Only (execute op) calls executeDecisions({forceLocked:false}).
//   8. Unlocked Only (remove op) calls removeFromList([], false).
//   9. Body text is context-sensitive (IMMEDIATE for execute, DEFERRED for remove).
//  10. Locked path list is rendered.
//  11. op="decision" (#733): Unlock & Apply / Unlocked Only re-run setDecisions
//      with the pending decision; Unlock & Apply is NOT the destructive variant.
//  12. op="apply-best-copy" (#744): Unlock & Apply / Unlocked Only re-run
//      applyBestCopy(groupNumber, {forceLocked|skipLocked}); NOT destructive.

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import { LockConfirmDialog } from "./LockConfirmDialog";
import {
  LOCK_CONFIRM_BTN_CANCEL,
  LOCK_CONFIRM_BTN_UNLOCK_APPLY,
  LOCK_CONFIRM_BTN_UNLOCKED_ONLY,
  LOCK_CONFIRM_DIALOG,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function seedLockConflict(
  op: "execute" | "remove" | "decision" | "apply-best-copy",
  paths: string[] = ["/photos/a.jpg"],
  originalPaths: string[] | null = null,
  pendingDecision?: "" | "delete" | "ignore",
  groupNumber?: number
) {
  act(() => {
    useAppStore.setState((s) => ({
      ...s,
      execute: {
        ...s.execute,
        lockConflict: {
          paths,
          op,
          // Default originalPaths to the locked paths themselves when not
          // explicitly provided (simulates: all paths in scope were locked).
          originalPaths: originalPaths ?? [...paths],
          pendingDecision,
          groupNumber,
        },
      },
    }));
  });
}

function clearLockConflict() {
  act(() => {
    useAppStore.setState((s) => ({
      ...s,
      execute: { ...s.execute, lockConflict: null },
    }));
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("LockConfirmDialog", () => {
  let executeDecisionsMock: ReturnType<typeof vi.fn>;
  let removeFromListMock: ReturnType<typeof vi.fn>;
  let setDecisionsMock: ReturnType<typeof vi.fn>;
  let applyBestCopyMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    executeDecisionsMock = vi.fn().mockResolvedValue(undefined);
    removeFromListMock = vi.fn().mockResolvedValue(undefined);
    setDecisionsMock = vi.fn().mockResolvedValue(undefined);
    applyBestCopyMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      executeDecisions: executeDecisionsMock,
      removeFromList: removeFromListMock,
      setDecisions: setDecisionsMock,
      applyBestCopy: applyBestCopyMock,
    } as never);
    clearLockConflict();
  });

  it("is not rendered when lockConflict is null", () => {
    render(<LockConfirmDialog />);
    expect(screen.queryByTestId(LOCK_CONFIRM_DIALOG)).not.toBeInTheDocument();
  });

  it("renders with LOCK_CONFIRM_DIALOG testid when lockConflict is set", () => {
    seedLockConflict("execute");
    render(<LockConfirmDialog />);
    expect(screen.getByTestId(LOCK_CONFIRM_DIALOG)).toBeInTheDocument();
  });

  it("all three button testids are present when open", () => {
    seedLockConflict("execute");
    render(<LockConfirmDialog />);
    expect(screen.getByTestId(LOCK_CONFIRM_BTN_CANCEL)).toBeInTheDocument();
    expect(
      screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY)
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY)
    ).toBeInTheDocument();
  });

  it("Cancel clears lockConflict and does not call execute/remove", async () => {
    const user = userEvent.setup();
    seedLockConflict("execute");
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_CANCEL));
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
    expect(executeDecisionsMock).not.toHaveBeenCalled();
    expect(removeFromListMock).not.toHaveBeenCalled();
  });

  it("Unlock & Apply (execute) re-runs the ORIGINAL scope with force, not the whole manifest", async () => {
    const user = userEvent.setup();
    // Simulate a SCOPED execute ("Execute selected"): the conflict's original
    // scope is a subset of the manifest. Re-running unscoped would delete files
    // the user never selected — the over-deletion bug this test guards.
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/sel1.jpg", "/photos/locked.jpg"];
    seedLockConflict("execute", lockedPaths, originalPaths);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(executeDecisionsMock).toHaveBeenCalledWith({
      scopePaths: originalPaths,
      forceLocked: true,
    });
    expect(removeFromListMock).not.toHaveBeenCalled();
  });

  it("Unlock & Apply (remove) removes the full ORIGINAL list with force, not just the locked subset", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg"];
    seedLockConflict("remove", lockedPaths, originalPaths);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    // Removes the FULL original list (force-unlocking the locked one), so the
    // user's unlocked files are not silently dropped from the operation.
    expect(removeFromListMock).toHaveBeenCalledWith(originalPaths, true);
    expect(executeDecisionsMock).not.toHaveBeenCalled();
  });

  it("Unlocked Only (execute) calls executeDecisions with scopePaths excluding locked paths", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg", "/photos/also-ok.jpg"];
    seedLockConflict("execute", lockedPaths, originalPaths);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY));
    // Must pass scopePaths = originalPaths minus the locked ones.
    expect(executeDecisionsMock).toHaveBeenCalledWith({
      scopePaths: ["/photos/ok.jpg", "/photos/also-ok.jpg"],
    });
    expect(removeFromListMock).not.toHaveBeenCalled();
  });

  it("Unlocked Only (remove) calls removeFromList with paths excluding locked paths", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg"];
    seedLockConflict("remove", lockedPaths, originalPaths);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY));
    // Must pass only the non-locked paths so removeFromList removes them.
    expect(removeFromListMock).toHaveBeenCalledWith(["/photos/ok.jpg"], false);
    expect(executeDecisionsMock).not.toHaveBeenCalled();
  });

  it("body mentions DELETE for execute op (IMMEDIATE danger copy), correctly scoped to the Recycle Bin", () => {
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    // The description should call out DELETE for execute operations, but
    // must NOT claim deletion is permanent — actual semantics are
    // send2trash to the OS Recycle Bin (matches DeleteConfirmDialog).
    expect(
      screen.getByText(/about to be deleted \(sent to the Recycle Bin\)/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/permanently/i)).not.toBeInTheDocument();
  });

  it("body mentions remove (DEFERRED copy) for remove op", () => {
    seedLockConflict("remove", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(/remove list/i)
    ).toBeInTheDocument();
  });

  it("renders the locked path list when open", () => {
    const paths = ["/photos/locked1.jpg", "/photos/locked2.jpg"];
    seedLockConflict("execute", paths);
    render(<LockConfirmDialog />);
    expect(screen.getByText("/photos/locked1.jpg")).toBeInTheDocument();
    expect(screen.getByText("/photos/locked2.jpg")).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // #733 — op="decision" (PATCH /api/decision 409 locked_paths)
  // ---------------------------------------------------------------------------

  it("Unlock & Apply (decision) re-runs setDecisions with the ORIGINAL scope, forceLocked:true, and the pending decision", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg"];
    seedLockConflict("decision", lockedPaths, originalPaths, "delete");
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(setDecisionsMock).toHaveBeenCalledWith(originalPaths, "delete", {
      forceLocked: true,
    });
    expect(executeDecisionsMock).not.toHaveBeenCalled();
    expect(removeFromListMock).not.toHaveBeenCalled();
  });

  it("Unlocked Only (decision) re-runs setDecisions with the pending decision, excluding locked paths", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = [
      "/photos/ok.jpg",
      "/photos/locked.jpg",
      "/photos/also-ok.jpg",
    ];
    seedLockConflict("decision", lockedPaths, originalPaths, "ignore");
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY));
    expect(setDecisionsMock).toHaveBeenCalledWith(
      ["/photos/ok.jpg", "/photos/also-ok.jpg"],
      "ignore"
    );
    expect(executeDecisionsMock).not.toHaveBeenCalled();
    expect(removeFromListMock).not.toHaveBeenCalled();
  });

  it("Unlocked Only (decision) is a no-op when every original path was locked", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    seedLockConflict("decision", lockedPaths, lockedPaths, "delete");
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY));
    expect(setDecisionsMock).not.toHaveBeenCalled();
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
  });

  it("Cancel (decision) clears lockConflict and does not call setDecisions", async () => {
    const user = userEvent.setup();
    seedLockConflict("decision", ["/photos/a.jpg"], undefined, "delete");
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_CANCEL));
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
    expect(setDecisionsMock).not.toHaveBeenCalled();
  });

  it("body for op='decision' says nothing is deleted yet (DEFERRED copy, Qt #417 parity)", () => {
    seedLockConflict("decision", ["/a.jpg"], ["/a.jpg", "/b.jpg"], "delete");
    render(<LockConfirmDialog />);
    expect(screen.getByText(/nothing is deleted yet/i)).toBeInTheDocument();
  });

  it("Unlock & Apply uses the default (non-destructive) variant for op='decision', but destructive for op='execute'", () => {
    seedLockConflict("decision", ["/a.jpg"], ["/a.jpg"], "delete");
    const { unmount } = render(<LockConfirmDialog />);
    const decisionBtn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    // "default" variant = bg-neutral-900; "destructive" = bg-red-600.
    expect(decisionBtn.className).toContain("bg-neutral-900");
    expect(decisionBtn.className).not.toContain("bg-red-600");
    unmount();

    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const executeBtn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(executeBtn.className).toContain("bg-red-600");
  });

  // ---------------------------------------------------------------------------
  // #744 — op="apply-best-copy" (POST /api/action/apply-best-copy 409 locked_paths)
  // ---------------------------------------------------------------------------

  it("Unlock & Apply (apply-best-copy) re-runs applyBestCopy(groupNumber, {forceLocked:true})", async () => {
    const user = userEvent.setup();
    seedLockConflict(
      "apply-best-copy",
      ["/photos/peer.jpg"],
      null,
      undefined,
      3
    );
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(applyBestCopyMock).toHaveBeenCalledWith(3, { forceLocked: true });
    expect(executeDecisionsMock).not.toHaveBeenCalled();
    expect(removeFromListMock).not.toHaveBeenCalled();
    expect(setDecisionsMock).not.toHaveBeenCalled();
  });

  it("Unlocked Only (apply-best-copy) re-runs applyBestCopy(groupNumber, {skipLocked:true})", async () => {
    const user = userEvent.setup();
    seedLockConflict(
      "apply-best-copy",
      ["/photos/peer.jpg"],
      null,
      undefined,
      5
    );
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY));
    expect(applyBestCopyMock).toHaveBeenCalledWith(5, { skipLocked: true });
  });

  it("Cancel (apply-best-copy) clears lockConflict and does not call applyBestCopy", async () => {
    const user = userEvent.setup();
    seedLockConflict("apply-best-copy", ["/photos/a.jpg"], null, undefined, 1);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_CANCEL));
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
    expect(applyBestCopyMock).not.toHaveBeenCalled();
  });

  it("body for op='apply-best-copy' says nothing is deleted yet (non-destructive queueing copy)", () => {
    seedLockConflict("apply-best-copy", ["/a.jpg"], null, undefined, 1);
    render(<LockConfirmDialog />);
    expect(screen.getByText(/nothing is deleted yet/i)).toBeInTheDocument();
  });

  it("Unlock & Apply uses the default (non-destructive) variant for op='apply-best-copy'", () => {
    seedLockConflict("apply-best-copy", ["/a.jpg"], null, undefined, 1);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-neutral-900");
    expect(btn.className).not.toContain("bg-red-600");
  });
});
