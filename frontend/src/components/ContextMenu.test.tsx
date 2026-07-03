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
//  14. (#735) File variant: shows By-Field + Execute-selected; By-Field calls
//      openActionDialog(resolved field) and Execute-selected calls the wired
//      onExecuteSelected prop.
//  15. (#735) Group variant: renders ONLY By-Field + Remove, hides
//      Execute-selected/Keep/Delete/Lock/Open-folder/Apply-best-copy; Remove
//      calls removeFromList with the group's member paths (targetPaths).

import type { ComponentProps } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

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

  beforeEach(() => {
    setDecisionsMock = vi.fn().mockResolvedValue(undefined);
    removeFromListMock = vi.fn().mockResolvedValue(undefined);
    setLocksMock = vi.fn().mockResolvedValue(undefined);
    revealMock = vi.fn().mockResolvedValue(undefined);
    openActionDialogMock = vi.fn();
    useAppStore.setState({
      setDecisions: setDecisionsMock,
      removeFromList: removeFromListMock,
      setLocks: setLocksMock,
      revealInExplorer: revealMock,
      openActionDialog: openActionDialogMock,
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

    it("By-Field with no clickedCol calls openActionDialog(undefined) and onClose", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, [FILE_PATH], {
        onExecuteSelected: vi.fn(),
      });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      expect(openActionDialogMock).toHaveBeenCalledWith(undefined);
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("By-Field with a mapped clickedCol resolves and passes the field label", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, [FILE_PATH], {
        onExecuteSelected: vi.fn(),
        clickedCol: "size",
      });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      expect(openActionDialogMock).toHaveBeenCalledWith("Size (Bytes)");
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("By-Field with an unmapped clickedCol (e.g. score) falls back to no pre-fill", async () => {
      const user = userEvent.setup();
      renderMenu(false, [FILE_PATH], {
        onExecuteSelected: vi.fn(),
        clickedCol: "score",
      });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      expect(openActionDialogMock).toHaveBeenCalledWith(undefined);
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

    it("renders ONLY By-Field and Remove", () => {
      renderMenu(false, GROUP_PATHS, { variant: "group" });
      expect(screen.getByTestId(CTX_SET_ACTION_BY_FIELD)).toBeInTheDocument();
      expect(screen.getByTestId(CTX_SET_ACTION_REMOVE)).toBeInTheDocument();
    });

    it("hides Execute-selected, Keep, Delete, Lock, Open-folder, Apply-best-copy", () => {
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
      expect(screen.queryByTestId(CTX_APPLY_BEST_COPY)).not.toBeInTheDocument();
    });

    it("Remove calls removeFromList with the group's member paths (targetPaths)", async () => {
      const user = userEvent.setup();
      const { onClose } = renderMenu(false, GROUP_PATHS, { variant: "group" });
      await user.click(screen.getByTestId(CTX_SET_ACTION_REMOVE));
      expect(removeFromListMock).toHaveBeenCalledWith(GROUP_PATHS);
      expect(setDecisionsMock).not.toHaveBeenCalled();
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("By-Field opens argless (group right-click never threads a column)", async () => {
      const user = userEvent.setup();
      renderMenu(false, GROUP_PATHS, { variant: "group" });
      await user.click(screen.getByTestId(CTX_SET_ACTION_BY_FIELD));
      expect(openActionDialogMock).toHaveBeenCalledWith(undefined);
    });
  });
});
