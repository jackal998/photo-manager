// ResultTree — virtualized dense table of duplicate groups.
// Uses @tanstack/react-virtual for performance with large manifests
// (thousands of files). Rows are heterogeneous: a group header row
// followed by the group's file rows (hidden when collapsed).

import { useRef, useMemo, useCallback, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useAppStore } from "@/store/useAppStore";
import { MAIN_RESULT_TREE } from "@/testids";
import { GroupRow } from "./result/GroupRow";
import { FileRow } from "./result/FileRow";
import type { DecisionValue } from "@/api/types";

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
        for (let i = 0; i < group.items.length; i++) {
          rows.push({
            kind: "file",
            groupNumber: group.group_number,
            fileIndex: i,
          });
        }
      }
    }
    return rows;
  }, [groups, collapsed]);

  // Build a lookup map for O(1) group access by group_number.
  const groupByNumber = useMemo(() => {
    const map = new Map<number, (typeof groups)[number]>();
    for (const g of groups) {
      map.set(g.group_number, g);
    }
    return map;
  }, [groups]);

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

  // Selection / preview callbacks.
  const handleSelect = useCallback(
    (filePath: string) => {
      setSelectedFile(filePath);
    },
    [setSelectedFile]
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
                  const group = groupByNumber.get(vrow.groupNumber);
                  if (!group) return null;
                  const fileRow = group.items[vrow.fileIndex];
                  if (!fileRow) return null;
                  return (
                    <FileRow
                      row={fileRow}
                      groupId={String(vrow.groupNumber)}
                      onDecision={handleDecision}
                      onLock={handleLock}
                      onSelect={handleSelect}
                      onOpenFullRes={handleOpenFullRes}
                      onContextMenu={handleContextMenu}
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
