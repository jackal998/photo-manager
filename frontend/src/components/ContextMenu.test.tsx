// ContextMenu tests — real behaviour only.
//
// Covers:
//   1. Renders with CONTEXT_MENU testid and role=menu.
//   2. All expected item testids are present.
//   3. Lock/Unlock item shown conditionally based on isLocked prop.
//   4. Clicking Keep calls store.setDecisions(targetPaths, "") and onClose.
//   5. Clicking Delete calls store.setDecisions(targetPaths, "delete") and onClose.
//   6. Clicking Remove calls store.removeFromList(targetPaths) and onClose (#694
//      finalize — NOT a staged setDecisions(..., "ignore")).
//   7. Clicking Lock calls store.setLocks(targetPaths, true) and onClose.
//   8. Clicking Unlock calls store.setLocks(targetPaths, false) and onClose.
//   9. Clicking Open folder calls store.revealInExplorer(filePath) and onClose.
//  10. Clicking Apply best copy (no-op stub) still calls onClose.
//  11. Pressing Esc calls onClose.
//  12. Clicking outside the menu calls onClose.
//  13. Multi-target: the decision/lock verbs act on the WHOLE targetPaths set,
//      while Open folder stays scoped to the single right-clicked filePath.

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

function renderMenu(isLocked = false, targetPaths: string[] = [FILE_PATH]) {
  const onClose = vi.fn();
  const result = render(
    <ContextMenu
      x={100}
      y={200}
      filePath={FILE_PATH}
      isLocked={isLocked}
      targetPaths={targetPaths}
      onClose={onClose}
    />
  );
  return { ...result, onClose };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ContextMenu", () => {
  let setDecisionsMock: ReturnType<typeof vi.fn>;
  let removeFromListMock: ReturnType<typeof vi.fn>;
  let setLocksMock: ReturnType<typeof vi.fn>;
  let revealMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setDecisionsMock = vi.fn().mockResolvedValue(undefined);
    removeFromListMock = vi.fn().mockResolvedValue(undefined);
    setLocksMock = vi.fn().mockResolvedValue(undefined);
    revealMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      setDecisions: setDecisionsMock,
      removeFromList: removeFromListMock,
      setLocks: setLocksMock,
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

  it("Keep calls setDecisions([filePath], '') and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_SET_ACTION_KEEP));
    expect(setDecisionsMock).toHaveBeenCalledWith([FILE_PATH], "");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Delete calls setDecisions([filePath], 'delete') and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_SET_ACTION_DELETE));
    expect(setDecisionsMock).toHaveBeenCalledWith([FILE_PATH], "delete");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Remove calls removeFromList([filePath]) (finalize, #694) and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_SET_ACTION_REMOVE));
    // #694: the result-tree "Remove from list" FINALIZES (outcome='ignored') —
    // it must NOT stage a 'ignore' decision. So removeFromList fires and
    // setDecisions must NOT be called.
    expect(removeFromListMock).toHaveBeenCalledWith([FILE_PATH]);
    expect(setDecisionsMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Lock calls setLocks([filePath], true) and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu(false);
    await user.click(screen.getByTestId(CTX_LOCK));
    expect(setLocksMock).toHaveBeenCalledWith([FILE_PATH], true);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Unlock calls setLocks([filePath], false) and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu(true);
    await user.click(screen.getByTestId(CTX_UNLOCK));
    expect(setLocksMock).toHaveBeenCalledWith([FILE_PATH], false);
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

  // -------------------------------------------------------------------------
  // Multi-target behaviour — the decision/lock verbs act on the whole
  // selection, Open folder stays scoped to the single right-clicked row.
  // -------------------------------------------------------------------------

  it("Delete acts on the whole targetPaths set", async () => {
    const user = userEvent.setup();
    const many = ["/a.jpg", "/b.jpg", "/c.jpg"];
    const { onClose } = renderMenu(false, many);
    await user.click(screen.getByTestId(CTX_SET_ACTION_DELETE));
    expect(setDecisionsMock).toHaveBeenCalledWith(many, "delete");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Remove (finalize) acts on the whole targetPaths set", async () => {
    const user = userEvent.setup();
    const many = ["/a.jpg", "/b.jpg", "/c.jpg"];
    const { onClose } = renderMenu(false, many);
    await user.click(screen.getByTestId(CTX_SET_ACTION_REMOVE));
    expect(removeFromListMock).toHaveBeenCalledWith(many);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Lock acts on the whole targetPaths set", async () => {
    const user = userEvent.setup();
    const many = ["/a.jpg", "/b.jpg", "/c.jpg"];
    renderMenu(false, many);
    await user.click(screen.getByTestId(CTX_LOCK));
    expect(setLocksMock).toHaveBeenCalledWith(many, true);
  });

  it("Open folder stays scoped to the single right-clicked filePath", async () => {
    const user = userEvent.setup();
    const many = ["/a.jpg", FILE_PATH, "/c.jpg"];
    renderMenu(false, many);
    await user.click(screen.getByTestId(CTX_OPEN_FOLDER));
    // Reveal targets only the right-clicked row, never the whole selection.
    expect(revealMock).toHaveBeenCalledWith(FILE_PATH);
    expect(revealMock).toHaveBeenCalledOnce();
  });
});
