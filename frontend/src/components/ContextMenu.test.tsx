// ContextMenu tests — real behaviour only.
//
// Covers:
//   1. Renders with CONTEXT_MENU testid and role=menu.
//   2. All expected item testids are present.
//   3. Lock/Unlock item shown conditionally based on isLocked prop.
//   4. Clicking Keep calls store.setDecisions(targetPaths, "") and onClose.
//   5. Clicking Delete calls store.setDecisions(targetPaths, "delete") and onClose.
//   6. Clicking Remove calls store.removeFromList(targetPaths, false,
//      { undoable: true }) and onClose (#694 finalize — NOT a staged
//      setDecisions(..., "ignore"); #909 — `undoable` raises the undo toast).
//   7. Clicking Lock calls store.setLocks(targetPaths, true) and onClose.
//   8. Clicking Unlock calls store.setLocks(targetPaths, false) and onClose.
//   9. Clicking Open folder calls store.revealInExplorer(filePath) and onClose.
//  10. Clicking Apply best copy calls store.applyBestCopy(groupNumber) and onClose.
//  11. Pressing Esc calls onClose.
//  12. Clicking outside the menu calls onClose.
//  13. Multi-target: the decision/lock verbs act on the WHOLE targetPaths set,
//      while Open folder stays scoped to the single right-clicked filePath.
//  14. (#735) File variant: shows By-Field + Execute-selected; By-Field calls
//      openActionDialog(resolved field) and Execute-selected calls the wired
//      onExecuteSelected prop.
//  15. (#735) Group variant: renders By-Field + Remove + Apply-best-copy
//      (#744), hides Execute-selected/Keep/Delete/Lock/Open-folder; Remove
//      calls removeFromList with the group's member paths (targetPaths);
//      Apply-best-copy calls applyBestCopy(groupNumber) regardless of variant.
//  16. (#897) The menu is placed through lib/menuPlacement from its measured
//      size: in a short window it flips above the cursor, and when taller than
//      the window it scrolls inside it.

import type { ComponentProps } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import { ContextMenu } from "./ContextMenu";
import {
  CONTEXT_MENU,
  CTX_APPLY_BEST_COPY,
  CTX_EXECUTE_SELECTED,
  CTX_LOCK,
  CTX_OPEN_FOLDER,
  CTX_SET_ACTION_BY_FIELD,
  CTX_SET_ACTION_DELETE,
  CTX_SET_ACTION_KEEP,
  CTX_SET_ACTION_REMOVE,
  CTX_UNLOCK,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FILE_PATH = "/photos/test.jpg";
const GROUP_NUMBER = 3;

function renderMenu(
  isLocked = false,
  targetPaths: string[] = [FILE_PATH],
  extraProps: Partial<ComponentProps<typeof ContextMenu>> = {}
) {
  const onClose = vi.fn();
  const result = render(
    <ContextMenu
      x={100}
      y={200}
      filePath={FILE_PATH}
      isLocked={isLocked}
      targetPaths={targetPaths}
      onClose={onClose}
      groupNumber={GROUP_NUMBER}
      {...extraProps}
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
  let openActionDialogMock: ReturnType<typeof vi.fn>;
  let applyBestCopyMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setDecisionsMock = vi.fn().mockResolvedValue(undefined);
    removeFromListMock = vi.fn().mockResolvedValue(undefined);
    setLocksMock = vi.fn().mockResolvedValue(undefined);
    revealMock = vi.fn().mockResolvedValue(undefined);
    openActionDialogMock = vi.fn();
    applyBestCopyMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      setDecisions: setDecisionsMock,
      removeFromList: removeFromListMock,
      setLocks: setLocksMock,
      revealInExplorer: revealMock,
      openActionDialog: openActionDialogMock,
      applyBestCopy: applyBestCopyMock,
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
    // setDecisions must NOT be called. #909: flagged `undoable`, so the skip
    // raises the undo toast — the Execute dialog's confirmed Skip does not.
    expect(removeFromListMock).toHaveBeenCalledWith([FILE_PATH], false, {
      undoable: true,
    });
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

  it("Apply best copy calls applyBestCopy(groupNumber) and onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderMenu();
    await user.click(screen.getByTestId(CTX_APPLY_BEST_COPY));
    expect(applyBestCopyMock).toHaveBeenCalledWith(GROUP_NUMBER);
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
    expect(removeFromListMock).toHaveBeenCalledWith(many, false, {
      undoable: true,
    });
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

  // -------------------------------------------------------------------------
  // #735 — file-row By-Field + Execute-selected, group-row reduced menu.
  // -------------------------------------------------------------------------

  describe("#735 file variant", () => {
    it("shows By-Field and Execute-selected alongside the pre-existing items", () => {
      renderMenu(false, [FILE_PATH], { onExecuteSelected: vi.fn() });
      expect(screen.getByTestId(CTX_SET_ACTION_BY_FIELD)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_EXECUTE_SELECTED)).toBeInTheDocument();
      // Pre-existing entries stay present — nothing was reordered/removed.
      expect(screen.getByTestId(CTX_SET_ACTION_KEEP)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_SET_ACTION_DELETE)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_SET_ACTION_REMOVE)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_OPEN_FOLDER)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_APPLY_BEST_COPY)).toBeInTheDocument();
    });

    it("By-Field with no clickedCol passes (undefined, this row's path) and onClose", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, [FILE_PATH], {
        onExecuteSelected: vi.fn(),
      });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      // #893 — the file variant always names the RIGHT-CLICKED row as the
      // seed source, even with no column: the dialog must seed from the row
      // the user aimed at, not from whatever is selected elsewhere.
      expect(openActionDialogMock).toHaveBeenCalledWith(undefined, FILE_PATH);
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("By-Field with a mapped clickedCol resolves and passes the field label", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, [FILE_PATH], {
        onExecuteSelected: vi.fn(),
        clickedCol: "size",
      });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      expect(openActionDialogMock).toHaveBeenCalledWith("Size (Bytes)", FILE_PATH);
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("By-Field with an unmapped clickedCol (e.g. score) falls back to no pre-fill", async () => {
      const user = userEvent.setup();
      renderMenu(false, [FILE_PATH], {
        onExecuteSelected: vi.fn(),
        clickedCol: "score",
      });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      expect(openActionDialogMock).toHaveBeenCalledWith(undefined, FILE_PATH);
    });

    it("Execute-selected invokes the wired onExecuteSelected prop and onClose", async () => {
      const user = userEvent.setup();
      const onExecuteSelected = vi.fn();
      const { onClose } = renderMenu(false, [FILE_PATH], { onExecuteSelected });
      await user.click(screen.getByTestId(CTX_EXECUTE_SELECTED));
      expect(onExecuteSelected).toHaveBeenCalledOnce();
      expect(onClose).toHaveBeenCalledOnce();
    });
  });

  describe("#735 group variant", () => {
    const GROUP_PATHS = ["/g/a.jpg", "/g/b.jpg", "/g/c.jpg"];

    it("renders By-Field, Remove, and Apply-best-copy", () => {
      renderMenu(false, GROUP_PATHS, { variant: "group" });
      expect(screen.getByTestId(CTX_SET_ACTION_BY_FIELD)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_SET_ACTION_REMOVE)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_APPLY_BEST_COPY)).toBeInTheDocument();
    });

    it("hides Execute-selected, Keep, Delete, Lock, Open-folder", () => {
      renderMenu(false, GROUP_PATHS, {
        variant: "group",
        onExecuteSelected: vi.fn(),
      });
      expect(screen.queryByTestId(CTX_EXECUTE_SELECTED)).not.toBeInTheDocument();
      expect(screen.queryByTestId(CTX_SET_ACTION_KEEP)).not.toBeInTheDocument();
      expect(screen.queryByTestId(CTX_SET_ACTION_DELETE)).not.toBeInTheDocument();
      expect(screen.queryByTestId(CTX_LOCK)).not.toBeInTheDocument();
      expect(screen.queryByTestId(CTX_UNLOCK)).not.toBeInTheDocument();
      expect(screen.queryByTestId(CTX_OPEN_FOLDER)).not.toBeInTheDocument();
    });

    it("Remove calls removeFromList with the group's member paths (targetPaths)", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, GROUP_PATHS, { variant: "group" });
      await user.click(screen.getByTestId(CTX_SET_ACTION_REMOVE));
      expect(removeFromListMock).toHaveBeenCalledWith(GROUP_PATHS, false, {
        undoable: true,
      });
      expect(setDecisionsMock).not.toHaveBeenCalled();
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("By-Field opens with no field and no row seed (group right-click)", async () => {
      const user = userEvent.setup();
      renderMenu(false, GROUP_PATHS, { variant: "group" });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      // #893 — null (not undefined) is the explicit "no seed" signal. Omitting
      // it would make the store fall back to the highlighted FILE row, seeding
      // a group right-click from an unrelated row; Qt's group-row menu only
      // ever collected the numeric group fields, i.e. no regex seed at all.
      expect(openActionDialogMock).toHaveBeenCalledWith(undefined, null);
    });

    it("Apply best copy calls applyBestCopy(groupNumber) regardless of variant", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, GROUP_PATHS, { variant: "group" });
      await user.click(screen.getByTestId(CTX_APPLY_BEST_COPY));
      expect(applyBestCopyMock).toHaveBeenCalledWith(GROUP_NUMBER);
      expect(onClose).toHaveBeenCalledOnce();
    });
  });

  // -------------------------------------------------------------------------
  // #897 — the menu stays inside the window. jsdom does no layout, so the
  // menu's size and the window's are stubbed; what is under test is that the
  // component measures itself and applies lib/menuPlacement's answer (the
  // math has its own tests in menuPlacement.test.ts).
  // -------------------------------------------------------------------------

  describe("#897 viewport placement", () => {
    const restore: Array<() => void> = [];

    function stub(target: object, prop: string, value: number) {
      const prior = Object.getOwnPropertyDescriptor(target, prop);
      Object.defineProperty(target, prop, { configurable: true, get: () => value });
      restore.push(() => {
        if (prior) Object.defineProperty(target, prop, prior);
        else delete (target as Record<string, unknown>)[prop];
      });
    }

    /** A menu of this size in a 1024 x 400 window. */
    function sizeMenu(width: number, height: number) {
      stub(window, "innerWidth", 1024);
      stub(window, "innerHeight", 400);
      stub(HTMLElement.prototype, "offsetWidth", width);
      stub(HTMLElement.prototype, "offsetHeight", height);
      stub(HTMLElement.prototype, "clientHeight", height);
      stub(HTMLElement.prototype, "scrollHeight", height);
    }

    afterEach(() => {
      while (restore.length > 0) restore.pop()!();
    });

    it("flips above the cursor when a right-click near the bottom would push it off", () => {
      sizeMenu(220, 330);
      renderMenu(false, [FILE_PATH], { x: 100, y: 350 });
      const menu = screen.getByTestId(CONTEXT_MENU);
      // Bottom edge at the cursor: 350 - 330.
      expect(menu.style.top).toBe("20px");
      expect(menu.style.left).toBe("100px");
      expect(menu.style.maxHeight).toBe("");
    });

    it("scrolls inside the window when taller than it, so the last item stays reachable", () => {
      sizeMenu(220, 500);
      renderMenu(false, [FILE_PATH], { x: 100, y: 350 });
      const menu = screen.getByTestId(CONTEXT_MENU);
      expect(menu.style.top).toBe("4px");
      expect(menu.style.maxHeight).toBe("392px");
      expect(menu.style.overflowY).toBe("auto");
    });
  });
});
