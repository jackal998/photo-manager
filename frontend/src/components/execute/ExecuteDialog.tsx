// ExecuteDialog — modal for reviewing and applying decided file actions.
//
// Responsibilities:
//   - Reads execute + manifest state from the Zustand store.
//   - TypeFilter (All / Delete only / Remove only) controls which decided rows
//     are shown in ExecuteTree.
//   - AllDeleteBanner surfaces groups where every member is marked delete.
//   - Inline preview pane (execute-preview-pane) shows the thumbnail of the
//     currently selected ExecuteTree row.
//   - Footer buttons:
//       Execute           → store.executeDecisions()
//       Execute selected  → store.executeDecisions({ scopePaths: [...selected] })
//       Cancel            → store.closeExecuteDialog()
//   - Does NOT render LockConfirmDialog — that is a sibling mounted by the
//     Integrate phase; we just let store.lockConflict drive it.

import { useRef, useState, useCallback, useMemo } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

import { useAppStore } from "@/store/useAppStore";
import {
  EXECUTE_DIALOG,
  EXECUTE_BTN_EXECUTE,
  EXECUTE_BTN_EXECUTE_SELECTED,
  EXECUTE_PREVIEW_PANE,
  EXECUTE_PREVIEW_IMAGE,
} from "@/testids";

import { AllDeleteBanner } from "./AllDeleteBanner";
import { HiddenDestructiveBanner } from "./HiddenDestructiveBanner";
import { TypeFilter, type TypeFilterValue } from "./TypeFilter";
import { ExecuteTree, type ExecuteTreeHandle } from "./ExecuteTree";
import { ExecuteContextMenu } from "./ExecuteContextMenu";
import { DeleteConfirmDialog } from "@/components/dialogs/DeleteConfirmDialog";
import { RemoveFromListConfirmDialog } from "@/components/dialogs/RemoveFromListConfirmDialog";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns the group IDs (as strings) whose visible scope is entirely "delete".
 *
 * The "visible scope" is governed by the active type filter (#676/#502),
 * mirroring Qt's ``_complete_delete_groups(paths_filter=visible)``:
 *   - filter "all":    every member (including undecided) must be delete —
 *     the unfiltered all-delete-group check.
 *   - filter "delete": narrow to the visible delete rows; any group holding
 *     ≥1 delete row qualifies (every visible row is delete by definition).
 *   - filter "ignore": the visible rows are all ignore, so no group qualifies.
 *
 * Narrowing matters because the banner's jump anchors scroll the ExecuteTree,
 * which omits groups with no row matching the filter — an unfiltered banner
 * would point at groups the user can't see under a non-"all" filter.
 */
export function findAllDeleteGroupIds(
  groups: ReturnType<typeof useAppStore.getState>["manifest"]["groups"],
  filter: TypeFilterValue
): string[] {
  const ids: string[] = [];
  for (const group of groups) {
    if (group.items.length === 0) continue;
    if (filter === "all") {
      if (group.items.every((item) => item.user_decision === "delete")) {
        ids.push(String(group.group_number));
      }
      continue;
    }
    const visible = group.items.filter(
      (item) => item.user_decision === filter
    );
    if (
      visible.length > 0 &&
      visible.every((item) => item.user_decision === "delete")
    ) {
      ids.push(String(group.group_number));
    }
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ExecuteDialog() {
  const executeOpen = useAppStore((s) => s.execute.executeOpen);
  const executeRunning = useAppStore((s) => s.execute.executeRunning);
  const executeError = useAppStore((s) => s.execute.executeError);
  const groups = useAppStore((s) => s.manifest.groups);
  const scopeGroupNumbers = useAppStore((s) => s.execute.scopeGroupNumbers);
  const closeExecuteDialog = useAppStore((s) => s.closeExecuteDialog);
  const executeDecisions = useAppStore((s) => s.executeDecisions);
  const removeFromList = useAppStore((s) => s.removeFromList);

  // -------------------------------------------------------------------------
  // Local dialog state
  // -------------------------------------------------------------------------

  const [filter, setFilter] = useState<TypeFilterValue>("all");
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  // Pre-execute all-delete confirmation gate (§5.5).
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  // Pending execute opts queued while the delete-confirm dialog is open.
  const pendingExecOptsRef = useRef<{ scopePaths?: string[]; forceLocked?: boolean } | null>(null);

  const treeRef = useRef<ExecuteTreeHandle>(null);

  // Row context menu (cluster B). Coordinates are stored RELATIVE to the
  // menu layer (the dialog content box) so the menu — which lives inside the
  // Radix modal to dodge its pointer-events/interact-outside traps — lands
  // under the cursor even though DialogContent is transform-positioned.
  const menuLayerRef = useRef<HTMLDivElement>(null);
  const [ctxMenu, setCtxMenu] = useState<{
    open: boolean;
    x: number;
    y: number;
    filePath: string;
    isLocked: boolean;
  }>({ open: false, x: 0, y: 0, filePath: "", isLocked: false });
  // Pending "remove from list" target awaiting confirm (null = closed).
  const [removeConfirmPath, setRemoveConfirmPath] = useState<string | null>(
    null
  );

  // -------------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------------

  /**
   * The groups this dialog operates on. When the "Execute (only selected)"
   * entry scoped the dialog (scopeGroupNumbers !== null), restrict every
   * downstream view — tree, banners, decided-paths, the Execute scope — to the
   * selected rows' groups (#430 group-pull). When unscoped this is identically
   * `groups`, so the toolbar/menu "Execute…" path is unchanged.
   */
  const scopedGroups = useMemo(() => {
    if (scopeGroupNumbers === null) return groups;
    const wanted = new Set(scopeGroupNumbers);
    return groups.filter((g) => wanted.has(g.group_number));
  }, [groups, scopeGroupNumbers]);

  /** All decided rows (across scoped groups), filtered by current TypeFilter. */
  const decidedPaths = useMemo(() => {
    const paths: string[] = [];
    for (const group of scopedGroups) {
      for (const item of group.items) {
        if (item.user_decision === "") continue;
        if (filter === "delete" && item.user_decision !== "delete") continue;
        if (filter === "ignore" && item.user_decision !== "ignore") continue;
        paths.push(item.file_path);
      }
    }
    return paths;
  }, [scopedGroups, filter]);

  const allDeleteGroupIds = useMemo(
    () => findAllDeleteGroupIds(scopedGroups, filter),
    [scopedGroups, filter]
  );

  // #676/#502 — count of pending delete rows HIDDEN by the active filter.
  // Mirrors Qt's _hidden_pending_delete_count: zero under "all" (deletes
  // are visible) and "delete" (deletes ARE the visible subset); under
  // "ignore" the delete rows are hidden, so the user must be told they are
  // still staged but not part of this filtered Execute's scope.
  const hiddenDeleteCount = useMemo(() => {
    if (filter !== "ignore") return 0;
    let n = 0;
    for (const group of scopedGroups) {
      for (const item of group.items) {
        if (item.user_decision === "delete") n++;
      }
    }
    return n;
  }, [scopedGroups, filter]);

  // Selected file's thumbnail URL for the preview pane.
  const previewThumbnailUrl = useMemo(() => {
    if (selectedFilePath === null) return null;
    for (const group of scopedGroups) {
      const item = group.items.find((f) => f.file_path === selectedFilePath);
      if (item) return item.thumbnail_url;
    }
    return null;
  }, [selectedFilePath, scopedGroups]);

  // -------------------------------------------------------------------------
  // Handlers
  // -------------------------------------------------------------------------

  const handleSelectFile = useCallback((filePath: string) => {
    setSelectedFilePath(filePath);
  }, []);

  const handleJumpToGroup = useCallback((groupId: string) => {
    treeRef.current?.scrollToGroup(groupId);
  }, []);

  // Row right-click → open the execute context menu at the cursor, converting
  // the viewport click coords to coords relative to the menu layer.
  const handleRowContextMenu = useCallback(
    (filePath: string, isLocked: boolean, clientX: number, clientY: number) => {
      const rect = menuLayerRef.current?.getBoundingClientRect();
      setCtxMenu({
        open: true,
        x: rect ? clientX - rect.left : clientX,
        y: rect ? clientY - rect.top : clientY,
        filePath,
        isLocked,
      });
    },
    []
  );

  const handleCtxClose = useCallback(() => {
    setCtxMenu((prev) => ({ ...prev, open: false }));
  }, []);

  // The menu's "Remove from list" defers the destructive finalize to a confirm.
  const handleRequestRemove = useCallback((filePath: string) => {
    setRemoveConfirmPath(filePath);
  }, []);

  const handleRemoveConfirm = useCallback(() => {
    const path = removeConfirmPath;
    setRemoveConfirmPath(null);
    if (path !== null) void removeFromList([path]);
  }, [removeConfirmPath, removeFromList]);

  const handleRemoveCancel = useCallback(() => {
    setRemoveConfirmPath(null);
  }, []);

  // Returns true when all decided rows (in the given scope) are "delete".
  // Used as the pre-execute safety gate (§5.5).
  const isAllDeleteScope = useCallback(
    (scopePaths?: string[]) => {
      const scope = scopePaths ? new Set(scopePaths) : null;
      let hasDecided = false;
      for (const group of scopedGroups) {
        for (const item of group.items) {
          if (item.user_decision === "") continue;
          if (scope !== null && !scope.has(item.file_path)) continue;
          hasDecided = true;
          if (item.user_decision !== "delete") return false;
        }
      }
      return hasDecided;
    },
    [scopedGroups]
  );

  const handleExecute = useCallback(() => {
    // #676/#502 "visible = committed": when a non-"all" type filter is
    // active, scope the commit to the currently-visible rows (decidedPaths)
    // so the filter can never silently execute — or hide — decisions the
    // user can't see. The unfiltered, UNSCOPED path keeps scope_paths=null
    // (commit every decided row), preserving the original no-argument call.
    // When the dialog is group-scoped ("Execute (only selected)"), decidedPaths
    // is already confined to the scoped groups, so route through the scoped
    // branch even under the "all" filter so the commit stays inside the scope.
    if (filter === "all" && scopeGroupNumbers === null) {
      if (isAllDeleteScope()) {
        pendingExecOptsRef.current = {};
        setDeleteConfirmOpen(true);
      } else {
        void executeDecisions();
      }
      return;
    }
    const scopePaths = decidedPaths;
    if (isAllDeleteScope(scopePaths)) {
      pendingExecOptsRef.current = { scopePaths };
      setDeleteConfirmOpen(true);
    } else {
      void executeDecisions({ scopePaths });
    }
  }, [executeDecisions, isAllDeleteScope, filter, decidedPaths, scopeGroupNumbers]);

  const handleExecuteSelected = useCallback(() => {
    if (selectedFilePath === null) return;
    const scopePaths = [selectedFilePath];
    if (isAllDeleteScope(scopePaths)) {
      pendingExecOptsRef.current = { scopePaths };
      setDeleteConfirmOpen(true);
    } else {
      void executeDecisions({ scopePaths });
    }
  }, [executeDecisions, selectedFilePath, isAllDeleteScope]);

  const handleDeleteConfirmConfirm = useCallback(() => {
    setDeleteConfirmOpen(false);
    const opts = pendingExecOptsRef.current ?? {};
    pendingExecOptsRef.current = null;
    void executeDecisions(opts);
  }, [executeDecisions]);

  const handleDeleteConfirmCancel = useCallback(() => {
    setDeleteConfirmOpen(false);
    pendingExecOptsRef.current = null;
  }, []);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen && !executeRunning) {
        closeExecuteDialog();
      }
    },
    [executeRunning, closeExecuteDialog]
  );

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  // Count of delete-scoped rows for the DeleteConfirmDialog body.
  const deleteCount = useMemo(
    () =>
      scopedGroups.reduce(
        (acc, g) =>
          acc + g.items.filter((i) => i.user_decision === "delete").length,
        0
      ),
    [scopedGroups]
  );

  return (
    <>
    <DeleteConfirmDialog
      open={deleteConfirmOpen}
      deleteCount={deleteCount}
      onConfirm={handleDeleteConfirmConfirm}
      onCancel={handleDeleteConfirmCancel}
    />
    <Dialog open={executeOpen} onOpenChange={handleOpenChange}>
      <DialogContent
        data-testid={EXECUTE_DIALOG}
        className="max-w-4xl w-full max-h-[90vh] flex flex-col"
        onInteractOutside={(e) => {
          if (executeRunning) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (executeRunning) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>Execute Actions</DialogTitle>
        </DialogHeader>

        {/* Toolbar row: TypeFilter */}
        <div className="flex items-center gap-3 mt-2">
          <span className="text-sm text-neutral-600">Show:</span>
          <TypeFilter value={filter} onChange={setFilter} />
          <span className="text-xs text-neutral-400 ml-auto">
            {decidedPaths.length}{" "}
            {decidedPaths.length === 1 ? "file" : "files"}
          </span>
        </div>

        {/* All-delete warning banner (narrowed to the visible filter scope) */}
        {allDeleteGroupIds.length > 0 && (
          <AllDeleteBanner
            allDeleteGroupIds={allDeleteGroupIds}
            onJumpToGroup={handleJumpToGroup}
          />
        )}

        {/* Hidden-destructive banner — the active filter hides pending deletes */}
        {hiddenDeleteCount > 0 && (
          <HiddenDestructiveBanner count={hiddenDeleteCount} />
        )}

        {/* Main area: tree + preview */}
        <div className="flex gap-3 flex-1 min-h-0 mt-2" style={{ height: "480px" }}>
          {/* Execute tree */}
          <div className="flex-1 min-w-0 flex flex-col">
            <ExecuteTree
              ref={treeRef}
              groups={scopedGroups}
              filter={filter}
              selectedFilePath={selectedFilePath}
              onSelectFile={handleSelectFile}
              onContextMenu={handleRowContextMenu}
            />
          </div>

          {/* Preview pane */}
          <div
            data-testid={EXECUTE_PREVIEW_PANE}
            className="w-56 flex-shrink-0 flex flex-col items-center justify-start gap-2 rounded border border-neutral-200 p-3 bg-neutral-50"
          >
            {previewThumbnailUrl !== null ? (
              <img
                data-testid={EXECUTE_PREVIEW_IMAGE}
                src={previewThumbnailUrl}
                alt="Preview"
                className="w-full object-contain rounded max-h-48"
              />
            ) : (
              <div className="text-xs text-neutral-400 text-center pt-8">
                Select a row to preview
              </div>
            )}
          </div>
        </div>

        {/* Error message */}
        {executeError !== null && (
          <p role="alert" className="text-sm text-red-600 mt-1">
            {executeError}
          </p>
        )}

        {/* Footer */}
        <DialogFooter className="mt-3 gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={closeExecuteDialog}
            disabled={executeRunning}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="outline"
            data-testid={EXECUTE_BTN_EXECUTE_SELECTED}
            onClick={handleExecuteSelected}
            disabled={executeRunning || selectedFilePath === null}
          >
            Execute selected
          </Button>
          <Button
            type="button"
            data-testid={EXECUTE_BTN_EXECUTE}
            onClick={handleExecute}
            disabled={executeRunning || decidedPaths.length === 0}
          >
            {executeRunning ? "Executing…" : "Execute"}
          </Button>
        </DialogFooter>

        {/* Row context-menu layer (cluster B). Absolute-fills DialogContent so
            it is INSIDE the Radix modal (pointer-events:auto, never counts as
            interact-outside) yet overlays the virtualized tree without being
            clipped by its scroll container. pointer-events-none on the layer
            keeps the tree clickable when no menu is open. */}
        <div
          ref={menuLayerRef}
          className="absolute inset-0 pointer-events-none"
        >
          {ctxMenu.open && (
            <ExecuteContextMenu
              x={ctxMenu.x}
              y={ctxMenu.y}
              filePath={ctxMenu.filePath}
              isLocked={ctxMenu.isLocked}
              onClose={handleCtxClose}
              onRequestRemove={handleRequestRemove}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
    <RemoveFromListConfirmDialog
      open={removeConfirmPath !== null}
      basename={
        removeConfirmPath !== null
          ? removeConfirmPath.split(/[\\/]/).pop() ?? ""
          : ""
      }
      onConfirm={handleRemoveConfirm}
      onCancel={handleRemoveCancel}
    />
    </>
  );
}
