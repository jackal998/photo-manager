// ExecuteContextMenu — right-click menu for a row inside the Execute dialog.
//
// Faithful port of Qt's execute-dialog row menu (execute_action_dialog.py
// _on_tree_context_menu): Set Action {delete, keep, remove from list}, Lock /
// Unlock, and Set Action by Field…. It is DISTINCT from the result-tree
// ContextMenu in two ways the desktop contract mandates:
//   - "Remove from list" FINALIZES the row (outcome='ignored' via
//     store.removeFromList) behind a confirm, rather than staging a decision.
//     The confirm + removeFromList call is owned by the parent (ExecuteDialog),
//     reached via onRequestRemove, so this component stays presentational.
//   - It adds "Set Action by Field…" → store.openActionDialog() (the same
//     ActionDialog the toolbar opens; manifest-wide bulk decide).
//
// Why a separate component (not the shared ContextMenu): the Execute dialog is
// a Radix *modal*. A menu mounted at App level would be unclickable over the
// modal (Radix sets pointer-events:none outside the dialog) and clicking it
// would count as interact-outside and close the dialog. This menu is mounted
// INSIDE the dialog's DialogContent (positioned with dialog-relative coords by
// the parent), so it inherits pointer-events:auto and is never "outside".
//
// It reuses the CONTEXT_MENU testid + the CTX_* item testids so the shared
// qa helpers (right_click_row / click_context_item) drive it unchanged.

import { useEffect, useRef } from "react";
import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import {
  CONTEXT_MENU,
  CTX_LOCK,
  CTX_SET_ACTION_BY_FIELD,
  CTX_SET_ACTION_DELETE,
  CTX_SET_ACTION_KEEP,
  CTX_SET_ACTION_REMOVE,
  CTX_UNLOCK,
} from "@/testids";

export interface ExecuteContextMenuProps {
  /** Left offset relative to the menu layer (dialog content box), in px. */
  x: number;
  /** Top offset relative to the menu layer (dialog content box), in px. */
  y: number;
  filePath: string;
  isLocked: boolean;
  onClose: () => void;
  /** Open the parent's remove-from-list confirmation for this path. */
  onRequestRemove: (filePath: string) => void;
}

export function ExecuteContextMenu({
  x,
  y,
  filePath,
  isLocked,
  onClose,
  onRequestRemove,
}: ExecuteContextMenuProps) {
  const setDecision = useAppStore((s) => s.setDecision);
  const setLock = useAppStore((s) => s.setLock);
  const openActionDialog = useAppStore((s) => s.openActionDialog);
  const t = useT();

  const menuRef = useRef<HTMLDivElement>(null);

  // Dismiss on outside mousedown (capture so it beats the row's own handlers).
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
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  function handleDelete() {
    void setDecision(filePath, "delete");
    onClose();
  }

  function handleKeep() {
    void setDecision(filePath, "");
    onClose();
  }

  // Remove from list FINALIZES (outcome='ignored') — gated behind the parent's
  // confirm dialog, mirroring Qt's "Remove from List" QMessageBox (default No).
  function handleRemove() {
    onClose();
    onRequestRemove(filePath);
  }

  function handleLock() {
    void setLock(filePath, true);
    onClose();
  }

  function handleUnlock() {
    void setLock(filePath, false);
    onClose();
  }

  function handleSetByField() {
    onClose();
    openActionDialog();
  }

  return (
    <div
      ref={menuRef}
      data-testid={CONTEXT_MENU}
      role="menu"
      className="absolute z-50 min-w-[170px] rounded-md border border-neutral-200 bg-white py-1 shadow-md text-sm pointer-events-auto"
      style={{ left: x, top: y }}
    >
      <button
        data-testid={CTX_SET_ACTION_DELETE}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleDelete}
      >
        {t("web.context_menu.delete", "Delete")}
      </button>
      <button
        data-testid={CTX_SET_ACTION_KEEP}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleKeep}
      >
        {t("web.context_menu.keep", "Keep (clear decision)")}
      </button>
      <button
        data-testid={CTX_SET_ACTION_REMOVE}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleRemove}
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
      <div className="my-1 border-t border-neutral-100" role="separator" />
      <button
        data-testid={CTX_SET_ACTION_BY_FIELD}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleSetByField}
      >
        {t("web.context_menu.set_action_by_field", "Set Action by Field…")}
      </button>
    </div>
  );
}
