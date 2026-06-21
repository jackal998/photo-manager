// PruneConfirmDialog tests — real behaviour only.
//
// Covers:
//   1. Not rendered when prunePrompt is null.
//   2. Renders with PRUNE_CONFIRM_DIALOG testid when prunePrompt is set.
//   3. Confirm button calls store.pruneSingletons and clears prunePrompt.
//   4. Cancel button clears prunePrompt without calling pruneSingletons.
//   5. Candidate paths are rendered in the list.
//   6. Body uses plural "groups" for N > 1, singular "group" for N === 1.

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import { PruneConfirmDialog } from "./PruneConfirmDialog";
import { PRUNE_CONFIRM_DIALOG } from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function seedPrunePrompt(candidates: string[]) {
  act(() => {
    useAppStore.setState((s) => ({
      ...s,
      execute: { ...s.execute, prunePrompt: { candidates } },
    }));
  });
}

function clearPrunePrompt() {
  act(() => {
    useAppStore.setState((s) => ({
      ...s,
      execute: { ...s.execute, prunePrompt: null },
    }));
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PruneConfirmDialog", () => {
  let pruneSingletonsMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    pruneSingletonsMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ pruneSingletons: pruneSingletonsMock } as never);
    clearPrunePrompt();
  });

  it("is not rendered when prunePrompt is null", () => {
    render(<PruneConfirmDialog />);
    expect(screen.queryByTestId(PRUNE_CONFIRM_DIALOG)).not.toBeInTheDocument();
  });

  it("renders with PRUNE_CONFIRM_DIALOG testid when prunePrompt is set", () => {
    seedPrunePrompt(["/photos/a.jpg"]);
    render(<PruneConfirmDialog />);
    expect(screen.getByTestId(PRUNE_CONFIRM_DIALOG)).toBeInTheDocument();
  });

  it("Confirm calls pruneSingletons and clears prunePrompt", async () => {
    const user = userEvent.setup();
    seedPrunePrompt(["/photos/a.jpg"]);
    render(<PruneConfirmDialog />);
    // Click the "Remove singletons" button (not the cancel one).
    await user.click(screen.getByRole("button", { name: /remove singletons/i }));
    expect(pruneSingletonsMock).toHaveBeenCalledOnce();
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
  });

  it("Cancel clears prunePrompt without calling pruneSingletons", async () => {
    const user = userEvent.setup();
    seedPrunePrompt(["/photos/a.jpg"]);
    render(<PruneConfirmDialog />);
    await user.click(screen.getByRole("button", { name: /keep them/i }));
    expect(pruneSingletonsMock).not.toHaveBeenCalled();
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
  });

  it("renders candidate paths in the list", () => {
    const candidates = ["/photos/singleton1.jpg", "/photos/singleton2.jpg"];
    seedPrunePrompt(candidates);
    render(<PruneConfirmDialog />);
    expect(screen.getByText("/photos/singleton1.jpg")).toBeInTheDocument();
    expect(screen.getByText("/photos/singleton2.jpg")).toBeInTheDocument();
  });

  it("uses plural 'groups' for N > 1", () => {
    seedPrunePrompt(["/a.jpg", "/b.jpg"]);
    render(<PruneConfirmDialog />);
    expect(screen.getByTestId(PRUNE_CONFIRM_DIALOG)).toHaveTextContent(
      "2 groups"
    );
  });

  it("uses singular 'group' for N === 1", () => {
    seedPrunePrompt(["/a.jpg"]);
    render(<PruneConfirmDialog />);
    expect(screen.getByTestId(PRUNE_CONFIRM_DIALOG)).toHaveTextContent(
      "1 group"
    );
  });
});
