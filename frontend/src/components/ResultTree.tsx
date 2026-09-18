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
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store/useAppStore";
import { MAIN_RESULT_TREE } from "@/testids";
import { GroupRow } from "./result/GroupRow";
import { FileRow } from "./result/FileRow";
import { ColumnHeaderRow } from "./result/ColumnHeaderRow";
import {
  clampResizeWidth,
  effectiveColumnWidths,
  isScoreCompact,
  makeRowComparator,
  visibleColumns,
  type ColumnId,
} from "@/lib/resultColumns";
import { estimateRowSize } from "@/lib/rowMetrics";
import { filterGroups } from "@/lib/rowFilter";
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

/** Stable empty array for a group whose items are momentarily missing, so the
 *  GroupRow's derivation memos do not re-run on every parent render. */
const EMPTY_ITEMS: readonly FileRowData[] = [];

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

// `isLast` closes the Daylight group frame (#878). It is carried as ROW DATA
// rather than expressed as a CSS `:last-child` rule because the tree is
// virtualised: only the rows near the viewport are mounted, so the DOM's last
// child is whatever the virtualizer happened to render, not the group's.
type FileVRow = {
  kind: "file";
  groupNumber: number;
  fileIndex: number; // index into group.items
  isLast: boolean; // last visible child of its group
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
  const allGroups = useAppStore((s) => s.manifest.groups);
  // #878 slice TB — the toolbar filter is applied HERE, once, so everything
  // downstream (the per-group sort memo, `vrows`, the keyboard range, the group
  // header's own count and size total) describes what is actually on screen.
  // `filterGroups` returns the store's array by identity when the box is empty,
  // so the unfiltered render keeps every memo below it.
  const filterText = useAppStore((s) => s.resultView.filterText);
  const groups = useMemo(
    () => filterGroups(allGroups, filterText),
    [allGroups, filterText]
  );
  const setDecision = useAppStore((s) => s.setDecision);
  const setLock = useAppStore((s) => s.setLock);
  const applyBestCopy = useAppStore((s) => s.applyBestCopy);
  const keepBestPending = useAppStore((s) => s.keepBestPending);
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
  // #878 layout slice R — row density. Read here rather than in each row so
  // the virtualiser's `estimateSize` and the rows it sizes read ONE value.
  const density = useAppStore((s) => s.resultView.density);

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
            isLast: i === items.length - 1,
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

  // The table's own available width, measured off the SAME element and the SAME
  // ResizeObserver as the header height above. The header is a block inside the
  // scroll container, so its box width is the container's content width — what
  // the column budget has to fit — and it does not grow when the columns
  // overflow it. `null` means "not measured yet" (first paint, or a headless
  // layout that reports a 0-width box): `visibleColumns` renders the full set
  // for null, so a missing measurement can never masquerade as a narrow table.
  const [tableWidth, setTableWidth] = useState<number | null>(null);

  useLayoutEffect(() => {
    const header = headerRef.current;
    if (header === null) return;
    const measure = () => {
      const rect = header.getBoundingClientRect();
      const next = rect.height;
      // Skip no-op state updates — a ResizeObserver fires on every layout
      // pass that touches the header (a column drag is one per mousemove).
      setScrollMargin((prev) => (prev === next ? prev : next));
      const w = rect.width > 0 ? rect.width : null;
      setTableWidth((prev) => (prev === w ? prev : w));
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

  // ── Resize ↔ shedding, and why they have to be kept apart ────────────────
  //
  // Shedding is need-based, so the user's widths are an input: a column they
  // narrow can pay for another, a column they widen can cost one. That is right
  // BETWEEN gestures and wrong DURING one — a live drag calls setColumnWidth
  // per mousemove, so re-planning on every move lets the column being dragged
  // disappear out from under the cursor while the window listeners keep
  // widening it. Release then persists a width for a column that is no longer
  // on screen: no header cell, no handle, no double-click target, and at that
  // window size no way back. Three guards, each closing one step of that:
  //
  //   1. FREEZE — the plan is computed from the widths as they were when the
  //      drag started, and re-planned once on release.
  //   2. CEILING — a sheddable column's committed width is capped at the width
  //      where it still fits, so a drag can never shed the column it is on.
  //   3. HEAL — a stored width that would hide a sheddable column falls back to
  //      that column's default, in the plan AND in the store, so a blob written
  //      before guards 1-2 existed cannot keep a column hidden.
  const [dragWidths, setDragWidths] = useState<Record<ColumnId, number> | null>(
    null
  );
  // Every input this callback reads lives in a ref, so its identity NEVER
  // changes. ColumnHeaderRow's drag effect keys on the onResize identity and
  // re-assigns the drag's own width ref when it re-runs (#796) — a callback
  // that changed on each mousemove would reset the pending width to the one the
  // drag started from, and a release right after a move would persist that.
  const columnWidthsRef = useRef(columnWidths);
  columnWidthsRef.current = columnWidths;
  const tableWidthRef = useRef(tableWidth);
  tableWidthRef.current = tableWidth;
  const dragWidthsRef = useRef<Record<ColumnId, number> | null>(null);

  const handleColumnResize = useCallback(
    (column: ColumnId, width: number, persist = true) => {
      const live = columnWidthsRef.current;
      // Guard 1 — the first live move of a drag freezes the plan's input.
      if (!persist && dragWidthsRef.current === null) {
        dragWidthsRef.current = live;
        setDragWidths(live);
      }
      // Guard 2 — cap against the OTHER columns' widths (the ceiling never
      // depends on the dragged column's own current value).
      const capped = clampResizeWidth(
        column,
        width,
        tableWidthRef.current,
        dragWidthsRef.current ?? live
      );
      setColumnWidth(column, capped, persist);
      // Guard 1 — release re-plans against what was actually committed.
      if (persist) {
        dragWidthsRef.current = null;
        setDragWidths(null);
      }
    },
    [setColumnWidth]
  );

  // Guard 3 — the healed map is what the plan sees. `effectiveColumnWidths` is
  // the single definition; the effect below writes the same correction back to
  // the store so it does not survive into the next launch.
  const planWidths = dragWidths ?? columnWidths;
  const healedWidths = useMemo(
    () => effectiveColumnWidths(tableWidth, planWidths),
    [tableWidth, planWidths]
  );

  useEffect(() => {
    if (dragWidths !== null) return; // never correct the store mid-drag
    for (const id of Object.keys(healedWidths) as ColumnId[]) {
      if (healedWidths[id] !== columnWidths[id]) {
        setColumnWidth(id, healedWidths[id], true);
      }
    }
  }, [healedWidths, columnWidths, dragWidths, setColumnWidth]);

  // One shed decision per width change, shared by the header and every row —
  // two independent computations would be two chances for the header to head a
  // column the rows no longer draw.
  const visibleCols = useMemo<ReadonlySet<ColumnId>>(
    () => new Set(visibleColumns(tableWidth, healedWidths).map((c) => c.id)),
    [tableWidth, healedWidths]
  );
  // The COMPACT density's score cell is the same 72px box as the <940px
  // collapse and likewise drops the "keep" label, so the two states raise ONE
  // flag — which is also what keeps the header cell and the body cells the
  // same width (they read the same flag).
  const scoreCompact =
    isScoreCompact(tableWidth, healedWidths) || density === "compact";

  const virtualizer = useVirtualizer({
    count: vrows.length,
    getScrollElement: () => scrollRef.current,
    // Four answers, two variables — 72 / 78 / 52 / 56 (lib/rowMetrics.ts).
    // `isLast` is on the vrow already (it is what closes the group frame), so
    // the last-in-group breath costs the virtualiser no extra lookup.
    estimateSize: (index) => {
      const vrow = vrows[index];
      return estimateRowSize(
        vrow.kind,
        vrow.kind === "file" && vrow.isLast,
        density
      );
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

  // #878 slice TB — the density switch changes EVERY row's height, and nothing
  // re-reads `estimateSize` on its own: @tanstack/react-virtual caches a size
  // per index and only re-measures when told to. Without this the cached
  // comfortable sizes survive the switch, so the total scroll height and every
  // row offset past the first screen stay 72px-based while the rows render at
  // 52px — which shows up as keyboard scroll-into-view landing short, not as a
  // red unit test (`overscan: 10` hides it near the top). Slice R deliberately
  // left this to TB, which owns the switch.
  //
  // Gated on a real CHANGE, not merely on `density` being a dependency:
  // `measure()` DISCARDS the cache, so an unconditional call throws away the
  // sizes the first layout pass just measured and leaves every row on its
  // estimate. Both scrollMargin.test.tsx and keyboard.test.tsx caught that —
  // rows landed 16px out — which is the one place this class of bug is visible.
  const measuredDensityRef = useRef(density);
  useEffect(() => {
    if (measuredDensityRef.current === density) return;
    measuredDensityRef.current = density;
    virtualizer.measure();
  }, [density, virtualizer]);

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
      // The auto-select already put the SELECTION on the keeper rows; seed the
      // keyboard cursor at the same row (#849 review) so the first ArrowDown
      // continues from the row the app just scrolled to instead of jumping
      // back to the top of the manifest. One-shot contract unchanged — this
      // runs inside the same guarded branch and clearScrollTarget still fires.
      setActiveRow({ kind: "file", filePath: scrollToPath });
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

  // Put the cursor on a GROUP header — from an arrow press or from a click on
  // the header, which must behave identically. The group becomes the preview
  // target, and the FILE selection is cleared: a group header is not part of
  // the multi-selection model, and Qt's `set_decision_to_highlighted`
  // (`app/views/handlers/file_operations.py:1071`) filters `type=="file"` out
  // of the CURRENT selection — so with the cursor on a header, `d`/`k` are
  // no-ops there too. Leaving the old file selection behind would let a
  // decision land on a row the user has visibly moved off.
  const activateGroupRow = useCallback(
    (groupNumber: number) => {
      setActiveRow({ kind: "group", groupNumber });
      setSelectedGroup(groupNumber);
      setSelection([]);
    },
    [setSelectedGroup, setSelection]
  );

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
        activateGroupRow(vrow.groupNumber);
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
      activateGroupRow,
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
      // A right-click is also a cursor move (#849 review): App resets the
      // selection to this row when it is outside the current one
      // (`App.tsx:109-120`), so the cursor has to follow or the next ArrowDown
      // continues from wherever the last LEFT click was — scrolling the tree
      // back to a row the user has since moved away from.
      setActiveRow({ kind: "file", filePath });
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

  // The mounted window. `aria-activedescendant` must name an element that is
  // actually in the DOM: the cursor survives being virtualized away (that is
  // the whole point of the pattern), but a dangling IDREF is invalid ARIA and
  // makes a screen reader announce nothing, so the attribute is dropped while
  // the active row is outside the window and comes back when it re-mounts.
  const virtualItems = virtualizer.getVirtualItems();
  const activeDomId =
    activeIndex >= 0 &&
    virtualItems.some((virtualItem) => virtualItem.index === activeIndex)
      ? rowDomId(vrows[activeIndex])
      : undefined;

  if (manifest.loading) {
    return (
      <div
        data-testid={MAIN_RESULT_TREE}
        className="flex items-center justify-center h-48 text-sm text-ink-muted"
      >
        Loading manifest…
      </div>
    );
  }

  if (manifest.path === null) {
    return (
      <div
        data-testid={MAIN_RESULT_TREE}
        className="flex items-center justify-center h-48 text-sm text-ink-muted"
      >
        Run a scan or open a manifest to see results.
      </div>
    );
  }

  if (manifest.groups.length === 0) {
    return (
      <div
        data-testid={MAIN_RESULT_TREE}
        className="flex items-center justify-center h-48 text-sm text-ink-muted"
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
      // The measured width the column budget is evaluated against (L3
      // shedding). Mirrored onto the root so a scenario can assert WHY a column
      // is missing instead of inferring it from a viewport size.
      data-table-width={tableWidth ?? ""}
      ref={scrollRef}
      // #709 — the container is the keyboard focus target; the active row is
      // named by aria-activedescendant rather than by moving DOM focus, because
      // a virtualized row can be unmounted while it is still the cursor.
      tabIndex={0}
      role="tree"
      aria-activedescendant={activeDomId}
      onKeyDown={handleKeyDown}
      // `group` is what lets the CURSOR ROW draw the Q7 focus ring: DOM focus
      // never leaves this container (aria-activedescendant, above), so the row
      // can never match `:focus-visible` itself — `group-focus-visible:` reads
      // the state off the container that really is focused, while keeping
      // `:focus-visible` semantics so a mouse user never sees the ring.
      // The container's own focus cue is a hairline, deliberately: at 2px
      // accent it framed the whole tree in the same stroke the cursor ROW
      // draws, and the loudest accent stroke on screen has to be the one
      // saying "you are here". This one only says "the tree has focus", which
      // still needs saying — the cursor may not be placed yet.
      className="group h-full overflow-auto bg-panel border border-hairline rounded focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ink-hairline"
      style={{ contain: "strict" }}
    >
      {/* Sticky sort/resize column header (#685). Inside the scroll container so
          it scrolls horizontally with the body but stays pinned vertically. */}
      <ColumnHeaderRow
        ref={headerRef}
        columnWidths={columnWidths}
        visibleCols={visibleCols}
        scoreCompact={scoreCompact}
        density={density}
        sortColumn={sortColumn}
        sortDirection={sortDirection}
        onToggleSort={toggleSort}
        onResize={handleColumnResize}
      />
      {/* Total height spacer for the virtualizer */}
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualItems.map((virtualItem) => {
          const vrow = vrows[virtualItem.index];
          // Resolved once: the wrapper needs the row's state for ARIA, and the
          // FileRow branch below needs the row itself.
          const fileRow =
            vrow.kind === "file"
              ? orderedItemsByGroup.get(vrow.groupNumber)?.[vrow.fileIndex]
              : undefined;
          const isSelected =
            fileRow !== undefined && selectedPaths.includes(fileRow.file_path);
          // Where the roving keyboard cursor sits (#709). The ring below is a
          // STROKE, not a third tint: both warm fills are already taken by
          // selection and hover, and a row can be focused AND selected — the
          // ring composes over them instead of replacing them (Q7).
          const isCursor = virtualItem.index === activeIndex;

          return (
            <div
              key={virtualItem.key}
              // The row identity aria-activedescendant points at (#709).
              id={rowDomId(vrow)}
              role="treeitem"
              // The treeitem is this wrapper, so the state a screen reader
              // reads off it has to live here — `aria-selected` sits on the
              // FileRow div and `aria-expanded` on the GroupRow button, both
              // CHILDREN of the treeitem, which exposes neither on its own.
              aria-selected={vrow.kind === "file" ? isSelected : undefined}
              aria-expanded={
                vrow.kind === "group-header"
                  ? !collapsed.has(vrow.groupNumber)
                  : undefined
              }
              data-index={virtualItem.index}
              data-cursor={isCursor ? "" : undefined}
              ref={virtualizer.measureElement}
              className={cn(
                // 6px to match the row (Q7). Harmless on every other row —
                // nothing here paints a background.
                "rounded-md",
                isCursor &&
                  "group-focus-visible:outline-2 group-focus-visible:outline-focus-ring group-focus-visible:-outline-offset-2"
              )}
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
                  items={orderedItemsByGroup.get(vrow.groupNumber) ?? EMPTY_ITEMS}
                  keepBestPending={keepBestPending.includes(vrow.groupNumber)}
                  onKeepBest={() => {
                    // Q5: «it must never override a lock». skipLocked is the
                    // server flag that means exactly that (locked rows keep
                    // both their decision and their lock), and it is also what
                    // keeps the promise of "no confirm step" — without it the
                    // route answers 409 and the LockConfirmDialog opens, which
                    // is the dialog Q5 replaced with the undo toast. The
                    // right-click item deliberately keeps the 409 → dialog
                    // flow it has had since #744.
                    void applyBestCopy(vrow.groupNumber, { skipLocked: true });
                  }}
                  expanded={!collapsed.has(vrow.groupNumber)}
                  onToggle={() => {
                    toggleGroup(vrow.groupNumber);
                    // GROUP-row click also selects the group for grid preview
                    // (mirrors Qt main_window.py:756 — GROUP selection → show_grid),
                    // and is where the keyboard cursor resumes from (#709).
                    // Same helper the arrow path uses, so a header reached by
                    // mouse and by keyboard leaves the app in one state.
                    activateGroupRow(vrow.groupNumber);
                  }}
                  onContextMenu={handleGroupContextMenu}
                />
              ) : (
                fileRow !== undefined && (
                  <FileRow
                    row={fileRow}
                    groupId={String(vrow.groupNumber)}
                    groupNumber={vrow.groupNumber}
                    columnWidths={columnWidths}
                    visibleCols={visibleCols}
                    scoreCompact={scoreCompact}
                    density={density}
                    onDecision={handleDecision}
                    onLock={handleLock}
                    onSelect={handleRowSelect}
                    onOpenFullRes={handleOpenFullRes}
                    onContextMenu={handleContextMenu}
                    isSelected={isSelected}
                    isLastInGroup={vrow.isLast}
                  />
                )
              )}
              {/* The ▸ cursor caret (Q7). It is what makes the keyboard
                  cursor survive grayscale and colour-blindness, where the
                  ring's hue is doing nothing — so it is not optional.
                  Positioned inside the row's EXISTING left gutter (FileRow's
                  px-4, GroupRow's 4px strip + px-3) rather than given a
                  reserved column of its own: that way appearing and
                  disappearing costs zero layout, and so does the whole
                  feature — no row moves by a pixel relative to master.
                  aria-hidden because aria-activedescendant already tells a
                  screen reader where the cursor is. */}
              {isCursor && (
                <span
                  aria-hidden="true"
                  data-cursor-caret=""
                  className="pointer-events-none absolute left-1 top-1/2 z-10 hidden -translate-y-1/2 text-[10px] leading-none text-focus-ring group-focus-visible:block"
                >
                  ▸
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
