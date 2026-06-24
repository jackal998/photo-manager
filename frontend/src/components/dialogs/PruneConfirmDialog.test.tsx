// PruneConfirmDialog tests (#686) — real behaviour of the three layouts +
// the actioned opt-in checkbox + the dynamic Remove label + the remember flip.
//
// Drives the REAL store-fed component with mocked applyPrune / saveSettings so
// each assertion pins WHAT the dialog computes (the prune set, the pref flip),
// not just that a handler ran.

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import type { AppStore } from "@/store/types";
import { PruneConfirmDialog } from "./PruneConfirmDialog";
import {
  PRUNE_BTN_KEEP,
  PRUNE_BTN_REMOVE,
  PRUNE_CONFIRM_DIALOG,
  PRUNE_INCLUDE_ACTIONED,
  PRUNE_REMEMBER,
} from "@/testids";

let applyPruneMock: ReturnType<typeof vi.fn>;
let saveSettingsMock: ReturnType<typeof vi.fn>;

function seedPrune(p: {
  plain?: string[];
  actioned?: string[];
  lockedToPrune?: string[];
}) {
  act(() => {
    useAppStore.setState((s) => ({
      execute: {
        ...s.execute,
        prunePrompt: {
          plain: p.plain ?? [],
          actioned: p.actioned ?? [],
          lockedToPrune: p.lockedToPrune ?? [],
        },
      },
    }));
  });
}

function clearPrune() {
  act(() => {
    useAppStore.setState((s) => ({
      execute: { ...s.execute, prunePrompt: null },
    }));
  });
}

beforeEach(() => {
  applyPruneMock = vi.fn().mockResolvedValue(undefined);
  saveSettingsMock = vi.fn().mockResolvedValue(undefined);
  act(() => {
    useAppStore.setState({
      applyPrune: applyPruneMock,
      saveSettings: saveSettingsMock,
    } as Partial<AppStore>);
  });
  clearPrune();
});

describe("PruneConfirmDialog", () => {
  it("is not rendered when prunePrompt is null", () => {
    render(<PruneConfirmDialog />);
    expect(screen.queryByTestId(PRUNE_CONFIRM_DIALOG)).not.toBeInTheDocument();
  });

  it("renders when prunePrompt is set", () => {
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"] });
    expect(screen.getByTestId(PRUNE_CONFIRM_DIALOG)).toBeInTheDocument();
  });

  it("plain-only: no actioned checkbox; Remove prunes only the plain bucket", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg", "/p/b.jpg"] });

    expect(screen.queryByTestId(PRUNE_INCLUDE_ACTIONED)).not.toBeInTheDocument();
    // Dynamic label reflects the plain count.
    expect(screen.getByTestId(PRUNE_BTN_REMOVE)).toHaveTextContent("Remove 2");

    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/a.jpg", "/p/b.jpg"]);
  });

  it("actioned-only: no checkbox; Remove opts the actioned bucket in", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ actioned: ["/p/x.jpg"] });

    expect(screen.queryByTestId(PRUNE_INCLUDE_ACTIONED)).not.toBeInTheDocument();
    // Count falls back to the actioned bucket when plain is empty.
    expect(screen.getByTestId(PRUNE_BTN_REMOVE)).toHaveTextContent("Remove 1");

    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/x.jpg"]);
  });

  it("mixed: actioned checkbox UNCHECKED → Remove prunes only the plain bucket", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"], actioned: ["/p/x.jpg"] });

    expect(screen.getByTestId(PRUNE_INCLUDE_ACTIONED)).toBeInTheDocument();
    // Label reflects the dominant (plain) bucket.
    expect(screen.getByTestId(PRUNE_BTN_REMOVE)).toHaveTextContent("Remove 1");

    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/a.jpg"]);
  });

  it("mixed: actioned checkbox CHECKED → Remove prunes both buckets", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"], actioned: ["/p/x.jpg"] });

    await user.click(screen.getByTestId(PRUNE_INCLUDE_ACTIONED));
    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/a.jpg", "/p/x.jpg"]);
  });

  it("Keep all prunes NOTHING from the buckets (only lockedToPrune folds in)", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"], actioned: ["/p/x.jpg"] });

    await user.click(screen.getByTestId(PRUNE_BTN_KEEP));
    // Empty lockedToPrune → applyPrune([]) (closes the dialog, prunes nothing).
    expect(applyPruneMock).toHaveBeenCalledWith([]);
  });

  it("lock-confirmed locked singletons prune even on Keep all (Qt tail)", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ actioned: ["/p/x.jpg"], lockedToPrune: ["/p/locked.jpg"] });

    await user.click(screen.getByTestId(PRUNE_BTN_KEEP));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/locked.jpg"]);
  });

  it("Remove also folds lockedToPrune into the pruned set", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"], lockedToPrune: ["/p/locked.jpg"] });

    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/a.jpg", "/p/locked.jpg"]);
  });

  it("Remove + remember flips the pref to 'always'", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"] });

    await user.click(screen.getByTestId(PRUNE_REMEMBER));
    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(saveSettingsMock).toHaveBeenCalledWith({
      "ui.prune_singletons": "always",
    });
  });

  it("Keep + remember flips the pref to 'never'", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    seedPrune({ plain: ["/p/a.jpg"] });

    await user.click(screen.getByTestId(PRUNE_REMEMBER));
    await user.click(screen.getByTestId(PRUNE_BTN_KEEP));
    expect(saveSettingsMock).toHaveBeenCalledWith({
      "ui.prune_singletons": "never",
    });
  });

  it("opt-in / remember reset between opens (no leak from a prior event)", async () => {
    const user = userEvent.setup();
    render(<PruneConfirmDialog />);
    // First open: check the actioned box, then keep (no prune).
    seedPrune({ plain: ["/p/a.jpg"], actioned: ["/p/x.jpg"] });
    await user.click(screen.getByTestId(PRUNE_INCLUDE_ACTIONED));
    await user.click(screen.getByTestId(PRUNE_BTN_KEEP));
    clearPrune();
    // Second open with the same buckets: the box must start UNCHECKED again, so
    // a plain-only Remove must NOT sweep the actioned bucket.
    applyPruneMock.mockClear();
    seedPrune({ plain: ["/p/a.jpg"], actioned: ["/p/x.jpg"] });
    await user.click(screen.getByTestId(PRUNE_BTN_REMOVE));
    expect(applyPruneMock).toHaveBeenCalledWith(["/p/a.jpg"]);
  });
});
