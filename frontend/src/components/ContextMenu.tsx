// Right-click context menu for a file row or group-header row in the result
// tree.
//
// Positioning: floating fixed div placed at {x, y} from the right-click event.
// Dismiss: click outside (via mousedown listener) or Esc key.
// Controlled entirely via props — the caller owns the mount/unmount state.
//
// `variant` selects which item set renders (#735 — Qt parity):
//   - "file" (default): the full file-row menu — unchanged from before #735,
//     plus the post-Open-Folder group (Set Action by Field… / Execute
//     Action (only selected)…).
//   - "group": the reduced group-header menu — Set Action by Field… + Remove
//     from List (mirrors Qt's group-row menu, context_menu.py:167-171).
//
// ctx-apply-best-copy (#744) appears in BOTH variants — it acts on the
// right-clicked row/group's own group (via the `groupNumber` prop), not the
// file-row/group-row targetPaths selection, so it isn't gated by variant the
// way Keep/Delete/Lock/Open-folder are.

import { useEffect, useRef } from "react";
import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { resolveInitialField } from "@/lib/actionDialogFields";
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

export interface ContextMenuProps {
  x: number;
  y: number;
  /** The single right-clicked row — drives the Lock/Unlock label and Open folder.
   *  Unused (empty string) for variant="group". */
  filePath: string;
  isLocked: boolean;
  /**
   * The rows the decision/lock/remove verbs act on. For variant="file" this
   * equals the current multi-selection when the right-clicked row belongs to
   * it, else just [filePath] (resolved by App.handleContextMenu — the
   * file-manager target-resolution rule). For variant="group" this is the
   * group's member file paths (resolved by App.handleGroupContextMenu).
   */
  targetPaths: string[];
  onClose: () => void;
  /** Which item set to render. Defaults to "file" — every pre-#735 callsite
   *  keeps rendering the full file-row menu unchanged. */
  variant?: "file" | "group";
  /** The result-tree column that was right-clicked (file variant only) —
   *  resolves the ActionDialog pre-fill field via resolveInitialField.
   *  undefined ⇒ "Set Action by Field…" opens with no pre-fill. */
  clickedCol?: string;
  /** Invoke the wired "Execute Action (only selected)…" flow. File variant
   *  only — the item is not rendered without this prop. */
  onExecuteSelected?: () => void;
  /** The right-clicked row/group's group_number (#744) — Apply best-copy
   *  acts on this group, independent of variant/targetPaths. */
  groupNumber: number;
}

export function ContextMenu({
  x,
  y,
  filePath,
  isLocked,
  targetPaths,
  onClose,
  variant = "file",
  clickedCol,
  onExecuteSelected,
  groupNumber,
}: ContextMenuProps) {
  const setDecisions = useAppStore((s) => s.setDecisions);
  const removeFromList = useAppStore((s) => s.removeFromList);
  const setLocks = useAppStore((s) => s.setLocks);
  const revealInExplorer = useAppStore((s) => s.revealInExplorer);
  const openActionDialog = useAppStore((s) => s.openActionDialog);
  const applyBestCopy = useAppStore((s) => s.applyBestCopy);
  const t = useT();

  const menuRef = useRef<HTMLDivElement>(null);

  // Dismiss on outside mousedown.
  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleMouseDown, { capture: true });
    return () => {
      document.removeEventListener("mousedown", handleMouseDown, {
        capture: true,
      });
    };
  }, [onClose]);

  // Dismiss on Esc.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  function handleSetKeep() {
    void setDecisions(targetPaths, "");
    onClose();
  }

  function handleSetDelete() {
    void setDecisions(targetPaths, "delete");
    onClose();
  }

  function handleSetRemove() {
    // The result-tree "Remove from list" FINALIZES outcome='ignored' — the rows
    // leave the review tree immediately, no Execute step — matching the desktop
    // result-tree "Remove from List" (remove_from_review → finalize_outcome).
    // This closes the #694 parity gap (the web previously only STAGED
    // user_decision='ignore' here). removeFromList does the rest the desktop
    // does: on a 409 locked-paths conflict it feeds the LockConfirmDialog (the
    // web analog of Qt's LockedRowsConfirmDialog), and on success it calls
    // maybeOfferPrune (the singleton-prune offer). Like desktop, there is NO
    // confirmation dialog on this path — that belongs to the execute-dialog
    // remove. Staging a reversible 'ignore' decision stays available via the
    // per-row decision buttons (DecisionControl).
    void removeFromList(targetPaths);
    onClose();
  }

  function handleLock() {
    void setLocks(targetPaths, true);
    onClose();
  }

  function handleUnlock() {
    void setLocks(targetPaths, false);
    onClose();
  }

  function handleOpenFolder() {
    void revealInExplorer(filePath);
    onClose();
  }

  // "Set Action by Field…" (#735) — pre-fills the ActionDialog's field combo
  // from the right-clicked column (file variant) or opens with no pre-fill
  // (group variant, clickedCol is never threaded there). Mirrors Qt's
  // "Set Action by Field…" -> resolve_initial_field.
  function handleSetActionByField() {
    openActionDialog(resolveInitialField(clickedCol));
    onClose();
  }

  // "Execute Action (only selected)…" (#735, file variant only) — delegates
  // to the wired handler, which re-reads the live selection (Qt parity:
  // action_handlers.py:85-99 discards the passed items and re-reads
  // selection).
  function handleExecuteSelected() {
    onExecuteSelected?.();
    onClose();
  }

  // Apply best-copy decisions to this group (#744): within groupNumber, the
  // top-score row becomes the keeper (decision "" + locked); every row the
  // classifier positively identified as a duplicate gets "delete". Acts on
  // the right-clicked row/group's OWN group, not targetPaths.
  function handleApplyBestCopy() {
    void applyBestCopy(groupNumber);
    onClose();
  }

  const setActionByFieldItem = (
    <button
      data-testid={CTX_SET_ACTION_BY_FIELD}
      role="menuitem"
      className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
      onClick={handleSetActionByField}
    >
      {t("web.context_menu.set_action_by_field", "Set Action by Field…")}
    </button>
  );

  const removeFromListItem = (
    <button
      data-testid={CTX_SET_ACTION_REMOVE}
      role="menuitem"
      className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
      onClick={handleSetRemove}
    >
      {t("web.context_menu.remove", "Remove from list")}
    </button>
  );

  // Apply best-copy (#744) — present in BOTH variants (it acts on groupNumber,
  // independent of the file-row/group-row targetPaths distinction).
  const applyBestCopyItem = (
    <button
      data-testid={CTX_APPLY_BEST_COPY}
      role="menuitem"
      className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
      onClick={handleApplyBestCopy}
    >
      {t("web.context_menu.apply_best_copy", "Apply best-copy decisions to this group")}
    </button>
  );

  if (variant === "group") {
    // Reduced group-header menu (#735) — By-Field + Remove + Apply-best-copy
    // (#744). NO Keep/Delete trio, NO Lock/Unlock, NO Open Folder, NO
    // Execute-selected. Mirrors Qt's group-row menu (context_menu.py:167-171)
    // plus the #744 group-scoped addition.
    return (
      <div
        ref={menuRef}
        data-testid={CONTEXT_MENU}
        role="menu"
        className="fixed z-[200] min-w-[160px] rounded-md border border-neutral-200 bg-white py-1 shadow-md text-sm"
        style={{ left: x, top: y }}
      >
        {setActionByFieldItem}
        {removeFromListItem}
        <div className="my-1 border-t border-neutral-100" role="separator" />
        {applyBestCopyItem}
      </div>
    );
  }

  return (
    <div
      ref={menuRef}
      data-testid={CONTEXT_MENU}
      role="menu"
      className="fixed z-[200] min-w-[160px] rounded-md border border-neutral-200 bg-white py-1 shadow-md text-sm"
      style={{ left: x, top: y }}
    >
      <button
        data-testid={CTX_SET_ACTION_KEEP}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleSetKeep}
      >
        {t("web.context_menu.keep", "Keep (clear decision)")}
      </button>
      <button
        data-testid={CTX_SET_ACTION_DELETE}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleSetDelete}
      >
        {t("web.context_menu.delete", "Delete")}
      </button>
      <button
        data-testid={CTX_SET_ACTION_REMOVE}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleSetRemove}
      >
        {t("web.context_menu.remove", "Remove from list")}
      </button>
      <div className="my-1 border-t border-neutral-100" role="separator" />
      {isLocked ? (
        <button
          data-testid={CTX_UNLOCK}
          role="menuitem"
          className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
          onClick={handleUnlock}
        >
          {t("web.context_menu.unlock", "Unlock")}
        </button>
      ) : (
        <button
          data-testid={CTX_LOCK}
          role="menuitem"
          className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
          onClick={handleLock}
        >
          {t("web.context_menu.lock", "Lock")}
        </button>
      )}
      <button
        data-testid={CTX_OPEN_FOLDER}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleOpenFolder}
      >
        {t("web.context_menu.open_folder", "Open folder")}
      </button>
      {/* #735 — post-Open-Folder group, mirrors Qt's file-row menu
          (context_menu.py): separator -> Set Action by Field… -> Execute
          Action (only selected)…. Placed here rather than reordering the
          pre-existing Keep/Delete/Remove/Lock/Open-Folder entries above. */}
      <div className="my-1 border-t border-neutral-100" role="separator" />
      {setActionByFieldItem}
      {/* Rendered only when the caller wires the execute-selected flow (every
          file-variant callsite does). Gating here keeps the item's promise
          honest — no live-looking entry that silently no-ops. */}
      {onExecuteSelected && (
        <button
          data-testid={CTX_EXECUTE_SELECTED}
          role="menuitem"
          className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
          onClick={handleExecuteSelected}
        >
          {t("web.context_menu.execute_selected", "Execute Action (only selected)…")}
        </button>
      )}
      <div className="my-1 border-t border-neutral-100" role="separator" />
      {applyBestCopyItem}
    </div>
  );
}
