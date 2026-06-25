// ResultTree — virtualized dense table of duplicate groups.
// Uses @tanstack/react-virtual for performance with large manifests
// (thousands of files). Rows are heterogeneous: a group header row
// followed by the group's file rows (hidden when collapsed).

import { useRef, useMemo, useCallback, useState, useEffect } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useAppStore } from "@/store/useAppStore";
import { MAIN_RESULT_TREE } from "@/testids";
import { GroupRow } from "./result/GroupRow";
import { FileRow } from "./result/FileRow";
import { ColumnHeaderRow } from "./result/ColumnHeaderRow";
import { makeRowComparator } from "@/lib/resultColumns";
import type { DecisionValue, FileRow as FileRowData } from "@/api/types";

// ---------------------------------------------------------------------------
// Context menu state shape — lifted to App level via the callback props
// ---------------------------------------------------------------------------
export interface ContextMenuTarget {
  filePath: string;
  isLocked: boolean;
  x: number;
  y: number;
}

// ---------------------------------------------------------------------------
// Virtual row descriptor — flattened from groups + per-group expansion state
// ---------------------------------------------------------------------------

type GroupHeaderVRow = {
  kind: "group-header";
  groupNumber: number;
  memberCount: number;
};

type FileVRow = {
  kind: "file";
  groupNumber: number;
  fileIndex: number; // index into group.items
};

type VRow = GroupHeaderVRow | FileVRow;

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface ResultTreeProps {
  onContextMenu?: (target: ContextMenuTarget) => void;
}

export function ResultTree({ onContextMenu }: ResultTreeProps = {}) {
  const manifest = useAppStore((s) => s.manifest);
  const groups = useAppStore((s) => s.manifest.groups);
  const setDecision = useAppStore((s) => s.setDecision);
  const setLock = useAppStore((s) => s.setLock);
  const setSelectedFile = useAppStore((s) => s.setSelectedFile);
  const openFullRes = useAppStore((s) => s.openFullRes);
  const selectedPaths = useAppStore((s) => s.selection.selectedPaths);
  const setSelection = useAppStore((s) => s.setSelection);
  const toggleSelection = useAppStore((s) => s.toggleSelection);
  const extendSelection = useAppStore((s) => s.extendSelection);
  const scrollToPath = useAppStore((s) => s.selection.scrollToPath);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);

  // #685 — column model: sort state + per-column widths.
  const sortColumn = useAppStore((s) => s.resultView.sortColumn);
  const sortDirection = useAppStore((s) => s.resultView.sortDirection);
  const columnWidths = useAppStore((s) => s.resultView.columnWidths);
  const toggleSort = useAppStore((s) => s.toggleSort);
  const setColumnWidth = useAppStore((s) => s.setColumnWidth);

  // Collapse state: Set of group_number values that are collapsed.
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());

  const toggleGroup = useCallback((groupNumber: number) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(groupNumber)) {
        next.delete(groupNumber);
      } else {
        next.add(groupNumber);
      }
      return next;
    });
  }, []);

  // Per-group item order. When a sort is active each group's items are a
  // SORTED COPY (the Qt tree proxy sorts children within each parent — group
  // order itself is unchanged). With no sort (the default) the original
  // server-order array is reused by identity, so the unsorted render is
  // byte-identical to before #685 — zero churn for the ~21 scenarios that read
  // rows by testid. fileIndex on each FileVRow indexes INTO this ordered array.
  const orderedItemsByGroup = useMemo(() => {
    const map = new Map<number, FileRowData[]>();
    const cmp = makeRowComparator(sortColumn, sortDirection);
    for (const g of groups) {
      map.set(g.group_number, cmp ? [...g.items].sort(cmp) : g.items);
    }
    return map;
  }, [groups, sortColumn, sortDirection]);

  // Flatten all groups into a single list of virtual rows.
  const vrows = useMemo<VRow[]>(() => {
    const rows: VRow[] = [];
    for (const group of groups) {
      rows.push({
        kind: "group-header",
        groupNumber: group.group_number,
        memberCount: group.member_count,
      });
      if (!collapsed.has(group.group_number)) {
        const items = orderedItemsByGroup.get(group.group_number) ?? group.items;
        for (let i = 0; i < items.length; i++) {
          rows.push({
            kind: "file",
            groupNumber: group.group_number,
            fileIndex: i,
          });
        }
      }
    }
    return rows;
  }, [groups, collapsed, orderedItemsByGroup]);

  // Ordered list of currently-visible file paths (collapsed groups excluded) —
  // the domain over which Shift+click computes its inclusive range. Reads from
  // the sort-ordered items so the range matches the on-screen order.
  const orderedFilePaths = useMemo<string[]>(() => {
    const paths: string[] = [];
    for (const vrow of vrows) {
      if (vrow.kind !== "file") continue;
      const fileRow = orderedItemsByGroup.get(vrow.groupNumber)?.[vrow.fileIndex];
      if (fileRow) paths.push(fileRow.file_path);
    }
    return paths;
  }, [vrows, orderedItemsByGroup]);

  // Virtualizer
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: vrows.length,
    getScrollElement: () => scrollRef.current,
    // Group header ~34px, file row ~72px (thumbnail 64 + padding).
    estimateSize: (index) => {
      const vrow = vrows[index];
      return vrow.kind === "group-header" ? 34 : 72;
    },
    overscan: 10,
    // initialRect ensures the virtualizer renders rows in jsdom where
    // ResizeObserver and getBoundingClientRect both return zeroes.
    initialRect: { width: 1024, height: 4000 },
  });

  // Post-scan keeper scroll (Qt #239 parity). loadManifest({ selectKeepers })
  // sets selection.scrollToPath to the first auto-selected KEEP row; bring it
  // into view once, then clear the one-shot signal. A plain user click never
  // sets scrollToPath, so the viewport is only re-positioned right after a scan.
  useEffect(() => {
    if (scrollToPath === null) return;
    const idx = vrows.findIndex(
      (vrow) =>
        vrow.kind === "file" &&
        orderedItemsByGroup.get(vrow.groupNumber)?.[vrow.fileIndex]
          ?.file_path === scrollToPath
    );
    if (idx >= 0) {
      // "center" keeps the row clear of the sticky column header.
      virtualizer.scrollToIndex(idx, { align: "center" });
    }
    // Clear even when the target isn't currently in vrows (e.g. its group is
    // collapsed) so a stale signal can't fire on a later unrelated render.
    clearScrollTarget();
  }, [scrollToPath, vrows, orderedItemsByGroup, virtualizer, clearScrollTarget]);

  // Decision + lock callbacks — stable references via the store.
  const handleDecision = useCallback(
    (filePath: string, value: DecisionValue) => {
      void setDecision(filePath, value);
    },
    [setDecision]
  );

  const handleLock = useCallback(
    (filePath: string, locked: boolean) => {
      void setLock(filePath, locked);
    },
    [setLock]
  );

  // Selection / preview callbacks. A plain click replaces the selection,
  // Ctrl/Cmd toggles, Shift extends a range; the clicked row is always the
  // preview/focus target regardless of the modifier.
  const handleRowSelect = useCallback(
    (filePath: string, mods: { ctrl: boolean; shift: boolean }) => {
      if (mods.shift) {
        extendSelection(filePath, orderedFilePaths);
      } else if (mods.ctrl) {
        toggleSelection(filePath);
      } else {
        setSelection([filePath]);
      }
      setSelectedFile(filePath);
    },
    [
      extendSelection,
      toggleSelection,
      setSelection,
      setSelectedFile,
      orderedFilePaths,
    ]
  );

  const handleOpenFullRes = useCallback(
    (filePath: string) => {
      openFullRes(filePath);
    },
    [openFullRes]
  );

  const handleContextMenu = useCallback(
    (filePath: string, isLocked: boolean, x: number, y: number) => {
      onContextMenu?.({ filePath, isLocked, x, y });
    },
    [onContextMenu]
  );

  // ---------------------------------------------------------------------------
  // Render states: loading / empty-path / no-groups / virtualised list
  // ---------------------------------------------------------------------------

  if (manifest.loading) {
    return (
      <div
        data-testid={MAIN_RESULT_TREE}
        className="flex items-center justify-center h-48 text-sm text-neutral-500"
      >
        Loading manifest…
      </div>
    );
  }

  if (manifest.path === null) {
    return (
      <div
        data-testid={MAIN_RESULT_TREE}
        className="flex items-center justify-center h-48 text-sm text-neutral-400"
      >
        Run a scan or open a manifest to see results.
      </div>
    );
  }

  if (manifest.groups.length === 0) {
    return (
      <div
        data-testid={MAIN_RESULT_TREE}
        className="flex items-center justify-center h-48 text-sm text-neutral-400"
      >
        No duplicate groups found.
      </div>
    );
  }

  return (
    <div
      data-testid={MAIN_RESULT_TREE}
      ref={scrollRef}
      className="h-full overflow-auto border border-neutral-200 rounded"
      style={{ contain: "strict" }}
    >
      {/* Sticky sort/resize column header (#685). Inside the scroll container so
          it scrolls horizontally with the body but stays pinned vertically. */}
      <ColumnHeaderRow
        columnWidths={columnWidths}
        sortColumn={sortColumn}
        sortDirection={sortDirection}
        onToggleSort={toggleSort}
        onResize={setColumnWidth}
      />
      {/* Total height spacer for the virtualizer */}
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtualItem) => {
          const vrow = vrows[virtualItem.index];

          return (
            <div
              key={virtualItem.key}
              data-index={virtualItem.index}
              ref={virtualizer.measureElement}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualItem.start}px)`,
              }}
            >
              {vrow.kind === "group-header" ? (
                <GroupRow
                  groupNumber={vrow.groupNumber}
                  memberCount={vrow.memberCount}
                  expanded={!collapsed.has(vrow.groupNumber)}
                  onToggle={() => toggleGroup(vrow.groupNumber)}
                />
              ) : (
                (() => {
                  const items = orderedItemsByGroup.get(vrow.groupNumber);
                  const fileRow = items?.[vrow.fileIndex];
                  if (!fileRow) return null;
                  return (
                    <FileRow
                      row={fileRow}
                      groupId={String(vrow.groupNumber)}
                      columnWidths={columnWidths}
                      onDecision={handleDecision}
                      onLock={handleLock}
                      onSelect={handleRowSelect}
                      onOpenFullRes={handleOpenFullRes}
                      onContextMenu={handleContextMenu}
                      isSelected={selectedPaths.includes(fileRow.file_path)}
                    />
                  );
                })()
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
