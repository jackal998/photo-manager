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
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

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
  op: "execute" | "remove" | "decision" | "apply-best-copy" | "prune" | "restore",
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

  it("body mentions the skip fate (DEFERRED copy) for remove op", () => {
    seedLockConflict("remove", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(/about to be skipped/i)
    ).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Singular/plural grammar (review finding on the web-audit-remediation
  // branch: the i18n pass had flattened "file(s)"/"row(s)"/"group(s)" into
  // fixed templates, so n=1 read "1 locked file(s) are about to be deleted").
  // ---------------------------------------------------------------------------

  it("uses singular grammar for n=1 (execute op): '1 locked file is about to be deleted'", () => {
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(/^1 locked file is about to be deleted/)
    ).toBeInTheDocument();
  });

  it("uses plural grammar for n>1 (execute op): '2 locked files are about to be deleted'", () => {
    seedLockConflict("execute", ["/a.jpg", "/b.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(/^2 locked files are about to be deleted/)
    ).toBeInTheDocument();
  });

  it("uses singular grammar for n=1 (apply-best-copy op): '1 locked row'", () => {
    seedLockConflict("apply-best-copy", ["/a.jpg"], null, undefined, 1);
    render(<LockConfirmDialog />);
    expect(screen.getByText(/1 locked row\./)).toBeInTheDocument();
    expect(screen.queryByText(/1 locked rows\./)).not.toBeInTheDocument();
  });

  it("uses plural grammar for n>1 (apply-best-copy op): '2 locked rows'", () => {
    seedLockConflict("apply-best-copy", ["/a.jpg", "/b.jpg"], null, undefined, 1);
    render(<LockConfirmDialog />);
    expect(screen.getByText(/2 locked rows\./)).toBeInTheDocument();
  });

  it("uses singular grammar + 'it' pronoun for n=1 (prune op)", () => {
    seedLockConflict("prune", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(
        "1 locked singleton group would be pruned. Unlock and prune it, or keep it in the list."
      )
    ).toBeInTheDocument();
  });

  it("uses plural grammar + 'them' pronoun for n>1 (prune op)", () => {
    seedLockConflict("prune", ["/a.jpg", "/b.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(
        "2 locked singleton groups would be pruned. Unlock and prune them, or keep them in the list."
      )
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

  it("Unlock & Apply (decision) re-runs setDecisions with the ORIGINAL scope, confirming the dialog's rows by name, and the pending decision", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg"];
    seedLockConflict("decision", lockedPaths, originalPaths, "delete");
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(setDecisionsMock).toHaveBeenCalledWith(originalPaths, "delete", {
      confirmedLocked: lockedPaths,
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
    // Daylight (issue 878): "default" variant = bg-warm (accent terracotta);
    // "destructive" = bg-danger-warm.
    expect(decisionBtn.className).toContain("bg-warm");
    expect(decisionBtn.className).not.toContain("bg-danger-warm");
    unmount();

    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const executeBtn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(executeBtn.className).toContain("bg-danger-warm");
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
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
  });
});

// ---------------------------------------------------------------------------
// #972 — Execute copy follows the locked rows' own decision: Skip rows leave
// the review (left on disk), Delete rows go to the Recycle Bin. A mixed set
// names both fates; an unknown row counts as Delete (never understated).
// ---------------------------------------------------------------------------

function seedGroupDecisions(decisions: Record<string, "" | "delete" | "ignore">) {
  const items = Object.entries(decisions).map(([file_path, user_decision]) => ({
    file_path,
    user_decision,
  }));
  act(() => {
    useAppStore.setState((s) => ({
      ...s,
      manifest: {
        ...s.manifest,
        groups: [{ group_number: 1, member_count: items.length, items }],
      },
    }) as never);
  });
}

function clearGroups() {
  act(() => {
    useAppStore.setState((s) => ({
      ...s,
      manifest: { ...s.manifest, groups: [] },
    }) as never);
  });
}

describe("LockConfirmDialog — Execute copy by locked-row fate (#972)", () => {
  afterEach(() => {
    clearGroups();
    clearLockConflict();
  });

  it("Skip-only: title and body say skipped / left on disk, never deleted", () => {
    seedGroupDecisions({ "/a.jpg": "ignore", "/b.jpg": "ignore" });
    seedLockConflict("execute", ["/a.jpg", "/b.jpg"]);
    render(<LockConfirmDialog />);
    expect(screen.getByText("Locked files in skip scope")).toBeInTheDocument();
    expect(
      screen.getByText(/^2 locked files are about to be skipped \(left on disk/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/Recycle Bin/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/deleted/i)).not.toBeInTheDocument();
  });

  it("Skip-only, n=1: singular grammar 'is about to be skipped'", () => {
    seedGroupDecisions({ "/a.jpg": "ignore" });
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(/^1 locked file is about to be skipped/)
    ).toBeInTheDocument();
  });

  it("Delete-only keeps the original delete wording and title", () => {
    seedGroupDecisions({ "/a.jpg": "delete" });
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    expect(screen.getByText("Locked files in delete scope")).toBeInTheDocument();
    expect(
      screen.getByText(/^1 locked file is about to be deleted \(sent to the Recycle Bin\)/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/skipped/i)).not.toBeInTheDocument();
  });

  it("mixed set names both fates with their own counts", () => {
    seedGroupDecisions({ "/a.jpg": "ignore", "/b.jpg": "delete", "/c.jpg": "delete" });
    seedLockConflict("execute", ["/a.jpg", "/b.jpg", "/c.jpg"]);
    render(<LockConfirmDialog />);
    expect(screen.getByText("Locked files in delete scope")).toBeInTheDocument();
    expect(
      screen.getByText(
        /^2 locked files will be deleted \(sent to the Recycle Bin\), and 1 locked file will be skipped \(left on disk/
      )
    ).toBeInTheDocument();
  });

  it("a locked path missing from the groups counts as Delete, not Skip", () => {
    seedGroupDecisions({ "/a.jpg": "ignore" });
    seedLockConflict("execute", ["/a.jpg", "/unknown.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(
        /^1 locked file will be deleted \(sent to the Recycle Bin\), and 1 locked file will be skipped/
      )
    ).toBeInTheDocument();
  });

  it("Unlock & Apply still re-runs the original scope with forceLocked for Skip-only", async () => {
    const executeDecisionsMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ executeDecisions: executeDecisionsMock } as never);
    seedGroupDecisions({ "/a.jpg": "ignore" });
    seedLockConflict("execute", ["/a.jpg"], ["/a.jpg", "/z.jpg"]);
    render(<LockConfirmDialog />);
    await userEvent.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(executeDecisionsMock).toHaveBeenCalledWith({
      scopePaths: ["/a.jpg", "/z.jpg"],
      forceLocked: true,
    });
  });
});

// ---------------------------------------------------------------------------
// #981 — op="restore" (POST /api/restore 409 locked_paths, the Undo of an
// immediate Skip). Restore deletes nothing, so the copy must say the rows go
// back into review and are unlocked, and the confirm is NOT destructive.
// ---------------------------------------------------------------------------

describe("LockConfirmDialog — op restore (#981)", () => {
  let restoreMock: ReturnType<typeof vi.fn>;
  let executeDecisionsMock: ReturnType<typeof vi.fn>;
  let removeFromListMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    restoreMock = vi.fn().mockResolvedValue(undefined);
    executeDecisionsMock = vi.fn().mockResolvedValue(undefined);
    removeFromListMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      restoreToReview: restoreMock,
      executeDecisions: executeDecisionsMock,
      removeFromList: removeFromListMock,
    } as never);
    clearLockConflict();
  });

  afterEach(() => {
    clearLockConflict();
  });

  it("states that the rows go back into review, unlocked, and nothing is deleted", () => {
    const paths = ["/photos/locked.jpg"];
    seedLockConflict("restore", paths);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText("Locked rows going back into review")
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /^1 locked file is about to go back into review and be unlocked\. Nothing is deleted: restored files stay on disk\./
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/Recycle Bin/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/about to be deleted/i)).not.toBeInTheDocument();
  });

  it("uses plural grammar for n>1", () => {
    seedLockConflict("restore", ["/a.jpg", "/b.jpg"]);
    render(<LockConfirmDialog />);
    expect(
      screen.getByText(/^2 locked files are about to go back into review/)
    ).toBeInTheDocument();
  });

  it("Unlock & Apply (restore) re-runs restoreToReview over the ORIGINAL list with forceLocked:true", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg"];
    seedLockConflict("restore", lockedPaths, originalPaths);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(restoreMock).toHaveBeenCalledWith(originalPaths, { forceLocked: true });
    expect(executeDecisionsMock).not.toHaveBeenCalled();
    expect(removeFromListMock).not.toHaveBeenCalled();
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
  });

  it("Unlocked Only (restore) restores only the unlocked subset, without force", async () => {
    const user = userEvent.setup();
    const lockedPaths = ["/photos/locked.jpg"];
    const originalPaths = ["/photos/ok.jpg", "/photos/locked.jpg", "/photos/also-ok.jpg"];
    seedLockConflict("restore", lockedPaths, originalPaths);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCKED_ONLY));
    expect(restoreMock).toHaveBeenCalledWith(["/photos/ok.jpg", "/photos/also-ok.jpg"]);
    expect(executeDecisionsMock).not.toHaveBeenCalled();
  });

  it("Cancel (restore) clears lockConflict and writes nothing", async () => {
    const user = userEvent.setup();
    seedLockConflict("restore", ["/photos/a.jpg"]);
    render(<LockConfirmDialog />);
    await user.click(screen.getByTestId(LOCK_CONFIRM_BTN_CANCEL));
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
    expect(restoreMock).not.toHaveBeenCalled();
  });

  it("Unlock & Apply uses the default (non-destructive) variant for op='restore'", () => {
    seedLockConflict("restore", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
  });
});

// ---------------------------------------------------------------------------
// #984 / F5 — "Unlock & Apply" weight follows the locked files' fate. Red
// (destructive) plus the ⊗ glyph only when an execute deletes at least one
// file; every other state is the default weight with no glyph. The glyph is
// aria-hidden, so the accessible name stays "Unlock & Apply".
// ---------------------------------------------------------------------------

describe("LockConfirmDialog — Unlock & Apply weight by fate (#984, F5)", () => {
  afterEach(() => {
    clearGroups();
    clearLockConflict();
  });

  it("execute Delete-only: destructive, ⊗ glyph, delete-scope title", () => {
    seedGroupDecisions({ "/a.jpg": "delete" });
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-danger-warm");
    expect(btn.textContent).toContain("⊗");
    expect(screen.getByText("Locked files in delete scope")).toBeInTheDocument();
  });

  it("execute Skip-only: default weight, no glyph, skip-scope title", () => {
    seedGroupDecisions({ "/a.jpg": "ignore" });
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
    expect(btn.textContent).not.toContain("⊗");
    expect(screen.getByText("Locked files in skip scope")).toBeInTheDocument();
  });

  it("execute mixed (Delete + Skip): destructive with ⊗ glyph", () => {
    seedGroupDecisions({ "/a.jpg": "ignore", "/b.jpg": "delete" });
    seedLockConflict("execute", ["/a.jpg", "/b.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-danger-warm");
    expect(btn.textContent).toContain("⊗");
    expect(screen.getByText("Locked files in delete scope")).toBeInTheDocument();
  });

  it("remove: default weight, no glyph", () => {
    seedLockConflict("remove", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
    expect(btn.textContent).not.toContain("⊗");
  });

  it("prune: default weight, no glyph", () => {
    seedLockConflict("prune", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
    expect(btn.textContent).not.toContain("⊗");
  });

  it("restore: default weight, no glyph", () => {
    seedLockConflict("restore", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
    expect(btn.textContent).not.toContain("⊗");
  });

  it("decision: default weight, no glyph", () => {
    seedLockConflict("decision", ["/a.jpg"], ["/a.jpg"], "delete");
    render(<LockConfirmDialog />);
    const btn = screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    expect(btn.className).toContain("bg-warm");
    expect(btn.className).not.toContain("bg-danger-warm");
    expect(btn.textContent).not.toContain("⊗");
  });

  it("accessible name stays 'Unlock & Apply' with no ⊗ when destructive", () => {
    seedGroupDecisions({ "/a.jpg": "delete" });
    seedLockConflict("execute", ["/a.jpg"]);
    render(<LockConfirmDialog />);
    const byName = screen.getByRole("button", { name: "Unlock & Apply" });
    expect(byName).toBe(screen.getByTestId(LOCK_CONFIRM_BTN_UNLOCK_APPLY));
    expect(byName.getAttribute("aria-label")).toBeNull();
    expect(byName.textContent).toContain("⊗");
  });
});
