// useDecisionShortcuts — bare 'd' / 'k' decision keyboard shortcuts (#615 parity).
//
// Web port of the Qt DecisionTreeView.keyPressEvent (app/views/components/
// decision_tree_view.py): pressing bare 'd' sets the current selection's
// decision to "delete", bare 'k' clears it back to "" (canonical no-decision /
// keep per #584). Modifier-bearing presses (Ctrl+D, Shift+K, …) fall through so
// they don't fire the shortcut — mirrors Qt's `event.modifiers() == NoModifier`
// guard. This is the SAME mutation the multi-select context menu performs
// (store.setDecisions), so d/k inherit the exact decision semantics — true
// parity, no new code path.
//
// Why a document-level listener (NOT a div onKeyDown + tabIndex):
//   - The result-tree rows are non-focusable <div>s whose decision/lock controls
//     are Radix Select / Checkbox. Focusing the scroll container on row-select
//     would STEAL focus from a just-opened Radix dropdown (the click that selects
//     a row also opens the dropdown), closing it. A document-level listener acts
//     on the live selection with no focus management.
//   - Modal isolation is handled by a single DOM predicate: if ANY modal layer is
//     open (role="dialog" | "alertdialog" | "menu"), the shortcut is inert. This
//     covers BOTH store-flag modals (Action/Execute/FullRes/Lock/Prune) AND
//     App-local-state modals (Scan/Settings) without enumerating either — Radix
//     dialogs and the custom context menus all carry those roles. Qt's "the tree
//     must have focus" precondition maps to "no modal is stealing the surface".
//
// The handler reads the live selection via getState() at fire time (no stale
// closure) and is registered ONCE in a single useEffect whose cleanup removes it
// (StrictMode add→cleanup→add ⇒ one listener).
//
// Deliberately scoped to the DECISION shortcuts only. The other keys in the Qt
// keyPressEvent are out of scope here: 'p' (play/pause) is a PreviewPane concern,
// and the arrow-key roving focus through the virtualizer is a separate FE item
// (D3b). See qa/web/scenarios/s26_keyboard_navigation.py for the honest-omit list.

import { useEffect } from "react";

import { useAppStore } from "../store/useAppStore";

export function useDecisionShortcuts(): void {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      // Bare 'd' / 'k' only — mirror Qt's NoModifier guard.
      if (e.key !== "d" && e.key !== "k") return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;

      // Don't hijack typing in an editable field (e.g. the manifest-path input).
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT")
      ) {
        return;
      }

      // Don't fire through any open modal layer (dialog / menu). A single DOM
      // predicate covers both store-flag and App-local modals — the Qt analog of
      // "the result tree must have focus".
      if (
        document.querySelector(
          '[role="dialog"], [role="alertdialog"], [role="menu"]'
        )
      ) {
        return;
      }

      const { selectedPaths } = useAppStore.getState().selection;
      if (selectedPaths.length === 0) return;

      e.preventDefault();
      void useAppStore
        .getState()
        .setDecisions(selectedPaths, e.key === "d" ? "delete" : "");
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);
}
