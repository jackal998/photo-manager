// ExecuteTree — virtualized list of decided rows (user_decision !== "").
// Mirrors the ResultTree virtualizer pattern but scoped to the execute dialog:
//   - Only shows rows where user_decision matches the active TypeFilter.
//   - Groups with no matching decided rows are omitted entirely.
//   - Selecting a row fires onSelectFile so the preview pane can update.
//   - Each row carries data-testid="execute-row-{groupId}-{basename}" per §5.3.
//   - A ref is exposed so the parent can scroll to a group (AllDeleteBanner jump).

import {
  useRef,
  useMemo,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { EXECUTE_TREE, executeRowTestid } from "@/testids";
import type { Group } from "@/api/types";
import type { TypeFilterValue } from "./TypeFilter";

// ---------------------------------------------------------------------------
// Public handle — lets ExecuteDialog scroll to a group on banner click.
// ---------------------------------------------------------------------------

export interface ExecuteTreeHandle {
  scrollToGroup(groupId: string): void;
}

// ---------------------------------------------------------------------------
// Virtual row descriptors — flat list built from filtered groups.
// ---------------------------------------------------------------------------

type GroupHeaderVRow = {
  kind: "group-header";
  groupId: string;
  memberCount: number;
};

type FileVRow = {
  kind: "file";
  groupId: string;
  filePath: string;
  basename: string;
  folder: string;
  decision: "delete" | "ignore";
  thumbnailUrl: string;
  isSelected: boolean;
  isLocked: boolean;
};

type VRow = GroupHeaderVRow | FileVRow;

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ExecuteTreeProps {
  groups: Group[];
  filter: TypeFilterValue;
  /** Multi-selection (#697): the file paths currently selected in the dialog. */
  selectedPaths: string[];
  /**
   * A row was clicked. ``mods`` carries the Ctrl/Cmd + Shift state and
   * ``orderedFilePaths`` is the visible row order (for Shift-range math) — the
   * parent owns the selection state and resolves it via lib/multiSelect.
   */
  onSelectFile: (
    filePath: string,
    mods: { ctrl: boolean; shift: boolean },
    orderedFilePaths: string[]
  ) => void;
  /** Right-click a row — opens the execute-dialog context menu (cluster B). */
  onContextMenu?: (
    filePath: string,
    isLocked: boolean,
    x: number,
    y: number
  ) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const ExecuteTree = forwardRef<ExecuteTreeHandle, ExecuteTreeProps>(
  function ExecuteTree(
    { groups, filter, selectedPaths, onSelectFile, onContextMenu },
    ref
  ) {
    const scrollRef = useRef<HTMLDivElement>(null);

    // Build flat virtual-row list from decided rows matching the filter.
    const vrows = useMemo<VRow[]>(() => {
      const rows: VRow[] = [];
      for (const group of groups) {
        const decided = group.items.filter((item) => {
          if (item.user_decision === "") return false;
          if (filter === "all") return true;
          if (filter === "delete") return item.user_decision === "delete";
          if (filter === "ignore") return item.user_decision === "ignore";
          return false;
        });
        if (decided.length === 0) continue;

        const groupId = String(group.group_number);
        rows.push({
          kind: "group-header",
          groupId,
          memberCount: decided.length,
        });

        for (const item of decided) {
          rows.push({
            kind: "file",
            groupId,
            filePath: item.file_path,
            basename: item.basename,
            folder: item.folder,
            decision: item.user_decision as "delete" | "ignore",
            thumbnailUrl: item.thumbnail_url,
            isSelected: selectedPaths.includes(item.file_path),
            isLocked: item.is_locked,
          });
        }
      }
      return rows;
    }, [groups, filter, selectedPaths]);

    // Visible file paths in render order — the domain over which a Shift+click
    // computes its inclusive range. Passed up to the parent on every click.
    const orderedFilePaths = useMemo<string[]>(() => {
      const paths: string[] = [];
      for (const vrow of vrows) {
        if (vrow.kind === "file") paths.push(vrow.filePath);
      }
      return paths;
    }, [vrows]);

    // Build index from groupId → first vrow index so we can jump to it.
    const groupStartIndex = useMemo<Map<string, number>>(() => {
      const map = new Map<string, number>();
      for (let i = 0; i < vrows.length; i++) {
        const vrow = vrows[i];
        if (vrow.kind === "group-header" && !map.has(vrow.groupId)) {
          map.set(vrow.groupId, i);
        }
      }
      return map;
    }, [vrows]);

    const virtualizer = useVirtualizer({
      count: vrows.length,
      getScrollElement: () => scrollRef.current,
      estimateSize: (index) => {
        const vrow = vrows[index];
        return vrow?.kind === "group-header" ? 30 : 56;
      },
      overscan: 8,
      initialRect: { width: 1024, height: 3000 },
    });

    // Expose scrollToGroup for AllDeleteBanner jump anchors.
    useImperativeHandle(ref, () => ({
      scrollToGroup(groupId: string) {
        const idx = groupStartIndex.get(groupId);
        if (idx !== undefined) {
          virtualizer.scrollToIndex(idx, { align: "start" });
        }
      },
    }));

    const handleFileClick = useCallback(
      (filePath: string, mods: { ctrl: boolean; shift: boolean }) => {
        onSelectFile(filePath, mods, orderedFilePaths);
      },
      [onSelectFile, orderedFilePaths]
    );

    if (vrows.length === 0) {
      return (
        <div
          data-testid={EXECUTE_TREE}
          className="flex items-center justify-center h-32 text-sm text-neutral-400"
        >
          No decided rows match the current filter.
        </div>
      );
    }

    return (
      <div
        data-testid={EXECUTE_TREE}
        ref={scrollRef}
        className="flex-1 overflow-auto border border-neutral-200 rounded"
        style={{ contain: "strict" }}
      >
        <div
          style={{ height: virtualizer.getTotalSize(), position: "relative" }}
        >
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
                  <GroupHeaderRow
                    groupId={vrow.groupId}
                    memberCount={vrow.memberCount}
                  />
                ) : (
                  <ExecuteFileRow
                    vrow={vrow}
                    onClick={handleFileClick}
                    onContextMenu={onContextMenu}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }
);

// ---------------------------------------------------------------------------
// Sub-rows (not exported — internal to this file)
// ---------------------------------------------------------------------------

function GroupHeaderRow({
  groupId,
  memberCount,
}: {
  groupId: string;
  memberCount: number;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-neutral-100 border-b border-neutral-200 text-xs font-semibold text-neutral-600 select-none">
      <span>Group {groupId}</span>
      <span className="text-neutral-400 font-normal">
        {memberCount} {memberCount === 1 ? "file" : "files"}
      </span>
    </div>
  );
}

function ExecuteFileRow({
  vrow,
  onClick,
  onContextMenu,
}: {
  vrow: FileVRow;
  onClick: (
    filePath: string,
    mods: { ctrl: boolean; shift: boolean }
  ) => void;
  onContextMenu?: (
    filePath: string,
    isLocked: boolean,
    x: number,
    y: number
  ) => void;
}) {
  // §5.3 scoped row testid: execute-row-{groupId}-{basename}
  const testId = executeRowTestid(vrow.groupId, vrow.basename);

  return (
    <button
      type="button"
      data-testid={testId}
      aria-selected={vrow.isSelected}
      onClick={(e) =>
        onClick(vrow.filePath, {
          ctrl: e.ctrlKey || e.metaKey,
          shift: e.shiftKey,
        })
      }
      onContextMenu={(e) => {
        if (!onContextMenu) return;
        e.preventDefault();
        onContextMenu(vrow.filePath, vrow.isLocked, e.clientX, e.clientY);
      }}
      className={[
        "flex items-center gap-3 w-full px-3 py-2 text-left select-none border-b border-neutral-100 hover:bg-neutral-50 focus:outline-none focus:ring-inset focus:ring-1 focus:ring-neutral-300",
        vrow.isSelected ? "bg-blue-50 hover:bg-blue-100" : "",
      ]
        .join(" ")
        .trim()}
    >
      {/* Thumbnail */}
      <div className="flex-shrink-0 w-10 h-10 bg-neutral-200 rounded overflow-hidden flex items-center justify-center">
        <img
          loading="lazy"
          src={vrow.thumbnailUrl}
          width={40}
          height={40}
          alt={vrow.basename}
          className="object-cover w-full h-full"
        />
      </div>

      {/* Name + folder */}
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{vrow.basename}</div>
        <div className="text-xs text-neutral-500 truncate">{vrow.folder}</div>
      </div>

      {/* Decision badge */}
      <span
        className={[
          "flex-shrink-0 inline-block text-xs rounded px-1.5 py-0.5",
          vrow.decision === "delete"
            ? "bg-red-100 text-red-700"
            : "bg-neutral-100 text-neutral-600",
        ].join(" ")}
      >
        {vrow.decision === "delete" ? "Delete" : "Remove"}
      </span>
    </button>
  );
}
