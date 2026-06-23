// Right-click context menu for a file row in the result tree.
//
// Positioning: floating fixed div placed at {x, y} from the right-click event.
// Dismiss: click outside (via mousedown listener) or Esc key.
// Controlled entirely via props — the caller owns the mount/unmount state.
//
// ctx-apply-best-copy is stubbed (no-op handler kept per §5.7) — the
// best-copy auto-selection logic lives in a future store action.

import { useEffect, useRef } from "react";
import { useAppStore } from "@/store/useAppStore";
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

export interface ContextMenuProps {
  x: number;
  y: number;
  /** The single right-clicked row — drives the Lock/Unlock label and Open folder. */
  filePath: string;
  isLocked: boolean;
  /**
   * The rows the decision/lock verbs act on. Equals the current multi-selection
   * when the right-clicked row belongs to it, else just [filePath]. Resolved by
   * App.handleContextMenu (the file-manager target-resolution rule), so a
   * right-click outside the selection still acts on just that one row.
   */
  targetPaths: string[];
  onClose: () => void;
}

export function ContextMenu({
  x,
  y,
  filePath,
  isLocked,
  targetPaths,
  onClose,
}: ContextMenuProps) {
  const setDecisions = useAppStore((s) => s.setDecisions);
  const setLocks = useAppStore((s) => s.setLocks);
  const revealInExplorer = useAppStore((s) => s.revealInExplorer);

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
    // The result-tree "Remove from list" STAGES user_decision='ignore' (the web's
    // commit-at-Execute model — distinct from the execute-dialog menu, which
    // finalizes outcome='ignored'). Desktop finalizes immediately here; that
    // parity gap is tracked as #694, intentionally not changed in this PR.
    void setDecisions(targetPaths, "ignore");
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

  // ctx-apply-best-copy is intentionally a no-op stub — the best-copy store
  // action is not yet implemented. The menu item is kept so QA can locate it.
  function handleApplyBestCopy() {
    // TODO: wire store.applyBestCopy(filePath) when that action lands.
    onClose();
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
        Keep (clear decision)
      </button>
      <button
        data-testid={CTX_SET_ACTION_DELETE}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleSetDelete}
      >
        Delete
      </button>
      <button
        data-testid={CTX_SET_ACTION_REMOVE}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleSetRemove}
      >
        Remove from list
      </button>
      <div className="my-1 border-t border-neutral-100" role="separator" />
      {isLocked ? (
        <button
          data-testid={CTX_UNLOCK}
          role="menuitem"
          className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
          onClick={handleUnlock}
        >
          Unlock
        </button>
      ) : (
        <button
          data-testid={CTX_LOCK}
          role="menuitem"
          className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
          onClick={handleLock}
        >
          Lock
        </button>
      )}
      <button
        data-testid={CTX_OPEN_FOLDER}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none"
        onClick={handleOpenFolder}
      >
        Open folder
      </button>
      <div className="my-1 border-t border-neutral-100" role="separator" />
      <button
        data-testid={CTX_APPLY_BEST_COPY}
        role="menuitem"
        className="w-full text-left px-3 py-1.5 hover:bg-neutral-100 focus:bg-neutral-100 focus:outline-none text-neutral-400"
        onClick={handleApplyBestCopy}
      >
        Apply best copy (coming soon)
      </button>
    </div>
  );
}
