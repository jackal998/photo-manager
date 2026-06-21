// ContextMenu tests — real behaviour only.
//
// Covers:
//   1. Renders with CONTEXT_MENU testid and role=menu.
//   2. All expected item testids are present.
//   3. Lock/Unlock item shown conditionally based on isLocked prop.
//   4. Clicking Keep calls store.setDecision with "" and then onClose.
//   5. Clicking Delete calls store.setDecision with "delete" and onClose.
//   6. Clicking Remove calls store.setDecision with "ignore" and onClose.
//   7. Clicking Lock calls store.setLock(filePath, true) and onClose.
//   8. Clicking Unlock calls store.setLock(filePath, false) and onClose.
//   9. Clicking Open folder calls store.revealInExplorer and onClose.
//  10. Clicking Apply best copy (no-op stub) still calls onClose.
//  11. Pressing Esc calls onClose.
//  12. Clicking outside the menu calls onClose.

import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import { ContextMenu } from "./ContextMenu";
import {
  CONTEXT_MENU,
  CTX_APPLY_BEST_COPY,
  CTX_LOCK,
  CTX_OPEN_FOLDER,
  CTX_SET_ACTION_DELETE,
  CTX_SET_ACTION_KEEP,
  CTX_SET_ACTION_REMOVE,
  CTX_UNLOCK,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FILE_PATH = "/photos/test.jpg";

function renderMenu(isLocked = false) {
  const onClose = vi.fn();
  const result = render(
    <ContextMenu
      x={100}
      y={200}
      filePath={FILE_PATH}
      isLocked={isLocked}
      onClose={onClose}
    />
  );
  return { ...result, onClose };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ContextMenu", () => {
  let setDecisionMock: ReturnType<typeof vi.fn>;
  let setLockMock: ReturnType<typeof vi.fn>;
  let revealMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setDecisionMock = vi.fn().mockResolvedValue(undefined);
    setLockMock = vi.fn().mockResolvedValue(undefined);
    revealMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      setDecision: setDecisionMock,
      setLock: setLockMock,
      revealInExplorer: revealMock,
    } as never);
  });

  it("renders with CONTEXT_MENU testid and role=menu", () => {
    renderMenu();
    const menu = screen.getByTestId(CONTEXT_MENU);
    expect(menu).toBeInTheDocument();
    expect(menu).toHaveAttribute("role", "menu");
  });

  it("renders all expected item testids", () => {
    renderMenu();
    expect(screen.getByTestId(CTX_SET_ACTION_KEEP)).toBeInTheDocument();
    expect(screen.getByTestId(CTX_SET_ACTION_DELETE)).toBeInTheDocument();
    expect(screen.getByTestId(CTX_SET_ACTION_REMOVE)).toBeInTheDocument();
    expect(screen.getByTestId(CTX_OPEN_FOLDER)).toBeInTheDocument();
    expect(screen.getByTestId(CTX_APPLY_BEST_COPY)).toBeInTheDocument();
  });

  it("shows CTX_LOCK when isLocked=false", () => {
    renderMenu(false);
    expect(screen.getByTestId(CTX_LOCK)).toBeInTheDocument();
    expect(screen.queryByTestId(CTX_UNLOCK)).not.toBeInTheDocument();
  });

  it("shows CTX_UNLOCK when isLocked=true", () => {
    renderMenu(true);
    expect(screen.getByTestId(CTX_UNLOCK)).toBeInTheDocument();
    expect(screen.queryByTestId(CTX_LOCK)).not.toBeInTheDocument();
  });

  it("Keep calls setDecision('') and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_SET_ACTION_KEEP));
    expect(setDecisionMock).toHaveBeenCalledWith(FILE_PATH, "");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Delete calls setDecision('delete') and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_SET_ACTION_DELETE));
    expect(setDecisionMock).toHaveBeenCalledWith(FILE_PATH, "delete");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Remove calls setDecision('ignore') and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_SET_ACTION_REMOVE));
    expect(setDecisionMock).toHaveBeenCalledWith(FILE_PATH, "ignore");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Lock calls setLock(filePath, true) and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu(false);
    await user.click(screen.getByTestId(CTX_LOCK));
    expect(setLockMock).toHaveBeenCalledWith(FILE_PATH, true);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Unlock calls setLock(filePath, false) and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu(true);
    await user.click(screen.getByTestId(CTX_UNLOCK));
    expect(setLockMock).toHaveBeenCalledWith(FILE_PATH, false);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Open folder calls revealInExplorer and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_OPEN_FOLDER));
    expect(revealMock).toHaveBeenCalledWith(FILE_PATH);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Apply best copy (stub) still calls onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_APPLY_BEST_COPY));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Esc keypress calls onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking outside the menu calls onClose", () => {
    const { onClose } = renderMenu();
    // Simulate a mousedown event on the document body (outside the menu).
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
