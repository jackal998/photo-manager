// ResultTree — virtualized dense table of duplicate groups.
// Uses @tanstack/react-virtual for performance with large manifests
// (thousands of files). Rows are heterogeneous: a group header row
// followed by the group's file rows (hidden when collapsed).

import {
  useRef,
  useMemo,
  useCallback,
  useState,
  useEffect,
  useLayoutEffect,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
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
  /** The right-clicked column key (#735), e.g. "name" / "size" / "date" —
   *  undefined when the click landed outside a metadata cell. */
  col?: string;
  /** The row's group_number (#744) — Apply best-copy is group-scoped. */
  groupNumber: number;
}

/** Group-header right-click target (#735) — carries the group's member file
 *  paths for the reduced group context menu's "Remove from List". */
export interface GroupContextMenuTarget {
  memberPaths: string[];
  x: number;
  y: number;
  /** The group_number (#744) — Apply best-copy is group-scoped. */
  groupNumber: number;
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
// Roving keyboard cursor (#709)
// ---------------------------------------------------------------------------

/** The row the keyboard cursor sits on, held by IDENTITY rather than by index:
 *  a decision write rebuilds `manifest.groups` (new array, new row objects) and
 *  a sort/collapse renumbers `vrows`, so an index would silently point at a
 *  different row afterwards. Qt s26 step 1/3 pins the desktop equivalent
 *  ("selected row preserved across model rebuilds"). */
type ActiveRow =
  | { kind: "group"; groupNumber: number }
  | { kind: "file"; filePath: string };

/** Stable DOM id for a virtual row — the target of `aria-activedescendant`.
 *  Keyed on the row's identity (group number, index within the group's ordered
 *  items) rather than the virtual index so it survives scrolling. */
function rowDomId(vrow: VRow): string {
  return vrow.kind === "group-header"
    ? `result-row-g${vrow.groupNumber}`
    : `result-row-g${vrow.groupNumber}-i${vrow.fileIndex}`;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface ResultTreeProps {
  onContextMenu?: (target: ContextMenuTarget) => void;
  /** Group-header right-click (#735). */
  onGroupContextMenu?: (target: GroupContextMenuTarget) => void;
}

export function ResultTree({ onContextMenu, onGroupContextMenu }: ResultTreeProps = {}) {
  const manifest = useAppStore((s) => s.manifest);
  const groups = useAppStore((s) => s.manifest.groups);
  const setDecision = useAppStore((s) => s.setDecision);
  const setLock = useAppStore((s) => s.setLock);
  const setSelectedFile = useAppStore((s) => s.setSelectedFile);
  const setSelectedGroup = useAppStore((s) => s.setSelectedGroup);
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

  // #709 — the roving keyboard cursor. Component state (like `collapsed`), not
  // store state: it is a view concern, and the store already carries what the
  // cursor WRITES (selection / preview).
  const [activeRow, setActiveRow] = useState<ActiveRow | null>(null);

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
  const headerRef = useRef<HTMLDivElement>(null);

  // #699 — the sticky ColumnHeaderRow is a normal-flow sibling ABOVE the row
  // list inside the same scroll container, so the list origin sits one
  // header-height below the container's content top. `scrollMargin` is exactly
  // that offset: without it the virtualizer's windowing math compares
  // scrollTop (measured from the content top, header included) against row
  // coordinates measured from the list origin, and every row is off by the
  // header height. Measured at runtime rather than hardcoded — the header's
  // height follows its font, padding and the browser's text metrics.
  const [scrollMargin, setScrollMargin] = useState(0);

  useLayoutEffect(() => {
    const header = headerRef.current;
    if (header === null) return;
    const measure = () => {
      const next = header.getBoundingClientRect().height;
      // Skip no-op state updates — a ResizeObserver fires on every layout
      // pass that touches the header (a column drag is one per mousemove).
      setScrollMargin((prev) => (prev === next ? prev : next));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(header);
    return () => observer.disconnect();
    // manifest.groups gates which branch renders below: the header only exists
    // in the virtualized branch, so re-run once a manifest replaces a
    // loading/empty placeholder and the ref becomes non-null.
  }, [groups]);

  const virtualizer = useVirtualizer({
    count: vrows.length,
    getScrollElement: () => scrollRef.current,
    // Group header ~34px, file row ~72px (thumbnail 64 + padding).
    estimateSize: (index) => {
      const vrow = vrows[index];
      return vrow.kind === "group-header" ? 34 : 72;
    },
    overscan: 10,
    // The row list starts `scrollMargin` px into the scroll container's
    // content (the sticky header above it) — see the measurement effect.
    scrollMargin,
    // #709 — the sticky header COVERS the first `scrollMargin` px of the
    // viewport, so a row scrolled to the top edge would land underneath it.
    // `scrollPaddingStart` is the virtualizer's "keep this much clear at the
    // start": with it, `align: "auto"` both counts a row hidden behind the
    // header as off-screen AND targets `item.start - scrollPaddingStart`, so
    // the row's top comes to rest exactly at the header's bottom.
    scrollPaddingStart: scrollMargin,
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
      // "center" keeps the row clear of the sticky column header. With #699's
      // scrollMargin the virtualizer's coordinates are the container's own, so
      // the target really lands in the middle of the viewport (before #699 it
      // settled one header-height below centre).
      virtualizer.scrollToIndex(idx, { align: "center" });
    }
    // Clear even when the target isn't currently in vrows (e.g. its group is
    // collapsed) so a stale signal can't fire on a later unrelated render.
    clearScrollTarget();
  }, [scrollToPath, vrows, orderedItemsByGroup, virtualizer, clearScrollTarget]);

  // ---------------------------------------------------------------------------
  // Roving arrow-key cursor (#709) — Qt s26 steps 1/3 parity
  // ---------------------------------------------------------------------------

  // Where the cursor currently sits in the VISIBLE row order. -1 when nothing
  // is active yet, or when the active row left the tree (its group collapsed,
  // a rescan dropped the path) — the next arrow press then starts from an end.
  const activeIndex = useMemo(() => {
    if (activeRow === null) return -1;
    return vrows.findIndex((vrow) => {
      if (activeRow.kind === "group") {
        return (
          vrow.kind === "group-header" &&
          vrow.groupNumber === activeRow.groupNumber
        );
      }
      return (
        vrow.kind === "file" &&
        orderedItemsByGroup.get(vrow.groupNumber)?.[vrow.fileIndex]
          ?.file_path === activeRow.filePath
      );
    });
  }, [activeRow, vrows, orderedItemsByGroup]);

  // Move the cursor to `index` and mirror what a CLICK on that row does, so the
  // preview pane and the d/k decision shortcuts follow the keyboard exactly as
  // they follow the mouse. A group header is a stop (Qt's QTreeView traverses
  // its top-level rows too) but arrowing onto one never expands/collapses it —
  // it only selects the group, the way clicking its header already does.
  const activateIndex = useCallback(
    (index: number) => {
      const vrow = vrows[index];
      if (vrow === undefined) return;
      if (vrow.kind === "group-header") {
        setActiveRow({ kind: "group", groupNumber: vrow.groupNumber });
        setSelectedGroup(vrow.groupNumber);
      } else {
        const filePath = orderedItemsByGroup.get(vrow.groupNumber)?.[
          vrow.fileIndex
        ]?.file_path;
        if (filePath === undefined) return;
        setActiveRow({ kind: "file", filePath });
        setSelection([filePath]);
        setSelectedFile(filePath);
      }
      // "auto" only scrolls when the row is outside the padded viewport, so an
      // arrow press inside the visible window leaves the scroll position alone.
      virtualizer.scrollToIndex(index, { align: "auto" });
    },
    [
      vrows,
      orderedItemsByGroup,
      setSelectedGroup,
      setSelection,
      setSelectedFile,
      virtualizer,
    ]
  );

  // Scoped to the tree container, NOT to `document` (which is where the d/k
  // shortcuts had to live — `useDecisionShortcuts.ts:12-18`). That scoping IS
  // the "don't hijack typing" guard: a keystroke in the manifest-path field, a
  // dialog input or any other surface never reaches this handler, and no
  // predicate can rot. Nothing INSIDE the tree claims the arrow keys either —
  // the per-row decision control is three plain buttons (#744) and the lock
  // toggle a Radix checkbox — so a press with focus on one of those still moves
  // the cursor, which is what the Qt tree does. An in-tree editable control
  // (a filter box in the column header, say) would need a target check here.
  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      // Bare arrows only — mirrors the Qt NoModifier guard the d/k shortcuts
      // use, and leaves Shift+arrow free for a future range-extend.
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (vrows.length === 0) return;

      // The container is scrollable, so an unhandled arrow would ALSO scroll it
      // natively and fight the scrollToIndex below.
      e.preventDefault();
      const delta = e.key === "ArrowDown" ? 1 : -1;
      const next =
        activeIndex < 0
          ? delta === 1
            ? 0
            : vrows.length - 1
          : Math.min(vrows.length - 1, Math.max(0, activeIndex + delta));
      activateIndex(next);
    },
    [activeIndex, vrows.length, activateIndex]
  );

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
      // The clicked row is where the keyboard cursor picks up from (#709) —
      // under every modifier, matching "the clicked row is always the
      // preview/focus target" above.
      setActiveRow({ kind: "file", filePath });
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
    (
      filePath: string,
      isLocked: boolean,
      groupNumber: number,
      x: number,
      y: number,
      col?: string
    ) => {
      onContextMenu?.({ filePath, isLocked, x, y, col, groupNumber });
    },
    [onContextMenu]
  );

  const handleGroupContextMenu = useCallback(
    (memberPaths: string[], groupNumber: number, x: number, y: number) => {
      onGroupContextMenu?.({ memberPaths, x, y, groupNumber });
    },
    [onGroupContextMenu]
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
      // The offset the virtualizer was told the row list starts at (#699).
      // It must equal the sticky header's rendered height — s47 reads both and
      // compares, which is how a silently-reintroduced coordinate offset is
      // caught even while `overscan` hides its visual effect.
      data-scroll-margin={scrollMargin}
      ref={scrollRef}
      // #709 — the container is the keyboard focus target; the active row is
      // named by aria-activedescendant rather than by moving DOM focus, because
      // a virtualized row can be unmounted while it is still the cursor.
      tabIndex={0}
      role="tree"
      aria-activedescendant={
        activeIndex >= 0 ? rowDomId(vrows[activeIndex]) : undefined
      }
      onKeyDown={handleKeyDown}
      className="h-full overflow-auto border border-neutral-200 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400"
      style={{ contain: "strict" }}
    >
      {/* Sticky sort/resize column header (#685). Inside the scroll container so
          it scrolls horizontally with the body but stays pinned vertically. */}
      <ColumnHeaderRow
        ref={headerRef}
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
              // The row identity aria-activedescendant points at (#709).
              id={rowDomId(vrow)}
              role="treeitem"
              data-index={virtualItem.index}
              ref={virtualizer.measureElement}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                // `start` is measured from the scroll container's content top
                // (it includes scrollMargin); this spacer already begins one
                // header-height in, so subtract the margin exactly once or the
                // rows render a header-height too low (#699).
                transform: `translateY(${virtualItem.start - scrollMargin}px)`,
              }}
            >
              {vrow.kind === "group-header" ? (
                <GroupRow
                  groupNumber={vrow.groupNumber}
                  memberCount={vrow.memberCount}
                  memberPaths={
                    orderedItemsByGroup
                      .get(vrow.groupNumber)
                      ?.map((item) => item.file_path) ?? []
                  }
                  expanded={!collapsed.has(vrow.groupNumber)}
                  onToggle={() => {
                    toggleGroup(vrow.groupNumber);
                    // GROUP-row click also selects the group for grid preview
                    // (mirrors Qt main_window.py:756 — GROUP selection → show_grid).
                    setSelectedGroup(vrow.groupNumber);
                    // …and is where the keyboard cursor resumes from (#709).
                    setActiveRow({
                      kind: "group",
                      groupNumber: vrow.groupNumber,
                    });
                  }}
                  onContextMenu={handleGroupContextMenu}
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
                      groupNumber={vrow.groupNumber}
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
