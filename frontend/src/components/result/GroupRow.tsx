// Group header row: expand/collapse toggle + group summary + the Q5 bulk verb.

import type { KeyboardEvent, MouseEvent } from "react";
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { ChevronRight, ChevronDown } from "lucide-react";
import { useT } from "@/i18n/useT";
import { formatBytes } from "@/lib/format";
import { groupFolderSuffix, groupSizeTotal } from "@/lib/groupSummary";
import { groupKeepBestTestid, rowGroupTestid } from "@/testids";
import type { FileRow } from "@/api/types";

interface GroupRowProps {
  groupNumber: number;
  memberCount: number;
  /** The group's members, in the order the tree renders them (#735 carries
   *  their paths into the group-row context menu's "Remove from List" scope;
   *  slice G derives the size total and the folder suffix from them). */
  items: readonly FileRow[];
  expanded: boolean;
  onToggle: () => void;
  /** Q5's "Keep best · delete rest" — omit to render the header without it
   *  (no manifest loaded). */
  onKeepBest?: () => void;
  /** Right-click on the group header (#735) — opens the reduced group
   *  context menu (Set Action by Field… + Remove from List + Apply
   *  best-copy, #744). */
  onContextMenu?: (
    memberPaths: string[],
    groupNumber: number,
    x: number,
    y: number
  ) => void;
}

export function GroupRow({
  groupNumber,
  memberCount,
  items,
  expanded,
  onToggle,
  onKeepBest,
  onContextMenu,
}: GroupRowProps) {
  const t = useT();

  const memberPaths = useMemo(
    () => items.map((item) => item.file_path),
    [items]
  );
  const sizeTotal = useMemo(() => groupSizeTotal(items), [items]);
  const folderSuffix = useMemo(() => groupFolderSuffix(items), [items]);

  function handleContextMenu(e: MouseEvent) {
    e.preventDefault();
    onContextMenu?.(memberPaths, groupNumber, e.clientX, e.clientY);
  }

  // The row was a <button> until slice G, which puts a real <button> (the Q5
  // verb) inside it — and a button nested in a button is invalid HTML that
  // React will happily render and assistive tech will not. So the ROW becomes
  // role="button" and keeps every contract that mattered: same testid, same
  // aria-expanded, same click-anywhere-to-collapse, same place in the tab
  // order (tabIndex 0 = what <button> gave it), plus the Enter/Space handling
  // the native element was providing for free.
  function handleKeyDown(e: KeyboardEvent) {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (e.target !== e.currentTarget) return; // a key press on the Q5 button
    e.preventDefault();
    onToggle();
  }

  return (
    <div
      data-testid={rowGroupTestid(String(groupNumber))}
      role="button"
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={handleKeyDown}
      onContextMenu={handleContextMenu}
      className={cn(
        // Daylight group framing (#878): a warm band, a top border opening the
        // frame, and an accent left strip. The frame is CLOSED by the bottom
        // border FileRow draws under the group's last child (isLastInGroup) —
        // the tree is virtualised, so that cannot be a CSS sibling rule.
        //
        // Layout REPLY §"Group header": «height 44px (comfortable and compact
        // — the group band does not compress) … padding 0 16px 0 12px … gap
        // 10px». The height is NOT density-keyed, which is why it is a lone
        // constant in rowMetrics rather than a column of that file's table.
        "w-full h-[44px] flex items-center gap-[10px] pl-3 pr-4 bg-group-band hover:bg-subtle",
        "border-t border-t-group-line border-b border-b-group-line",
        "border-l-4 border-l-warm",
        "text-left cursor-pointer"
      )}
      aria-expanded={expanded}
    >
      {/* 16px box, 11px glyph (REPLY: the caret is KEPT, at H2's size) */}
      <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center text-ink-muted">
        {expanded ? (
          <ChevronDown className="h-[11px] w-[11px]" />
        ) : (
          <ChevronRight className="h-[11px] w-[11px]" />
        )}
      </span>
      {/* Copy audit R8: this row was hardcoded English, so a zh_TW session
          still read "Group 3 · 5 files". `tree.*` is the one desktop
          namespace never mirrored into `web.*` — web.tree.group_label is
          that mirror.
          H7: the count and the size total sit IMMEDIATELY after the title,
          not right-aligned after a spacer — «at 1280px a right-aligned count
          lands in the middle of nowhere once the "keep best" button takes the
          right end, and the count is part of the group's identity per Q4». */}
      <span className="flex min-w-0 items-baseline gap-[6px] truncate">
        <span className="text-[14px] font-bold text-ink">
          {t("web.tree.group_label", "Group {n}", { n: groupNumber })}
        </span>
        <span className="text-ink-faint">·</span>
        <span className="text-[13px] font-medium text-ink-muted">
          {memberCount}{" "}
          {memberCount === 1
            ? t("web.tree.file_singular", "file")
            : t("web.tree.file_plural", "files")}
        </span>
        {/* Derived per-group byte sum — Q4 permits a derived total; it is the
            one number that says how much this group is costing. */}
        <span className="font-mono text-[13px] text-ink-muted">
          · {formatBytes(sizeTotal)}
        </span>
        {folderSuffix !== null && (
          <span className="truncate font-mono text-[11px] text-ink-muted">
            {folderSuffix.kind === "leaf"
              ? `· ${folderSuffix.leaf}`
              : // Only ever reached with count >= 2 (one folder is the leaf
                // branch), so there is no singular form to split out.
                `· ${t("web.tree.folders_suffix", "{n} folders", {
                  n: folderSuffix.count,
                })}`}
          </span>
        )}
      </span>
      <span className="flex-1" />
      {onKeepBest !== undefined && (
        // Q5: «Promote it to the group header … it is the fast path through
        // the entire screen and a right-click action is invisible to the users
        // who most need it». The ml-4 is load-bearing, not spacing taste —
        // «at least 16px of gap between this button and the caret's hit area
        // … a misclick from collapse into a bulk decision is the expensive
        // misclick on this screen».
        <button
          type="button"
          data-testid={groupKeepBestTestid(String(groupNumber))}
          onClick={(e) => {
            // The row toggles on click; this button must not collapse the very
            // group it just wrote decisions into.
            e.stopPropagation();
            onKeepBest();
          }}
          className={cn(
            "ml-4 h-[28px] flex-shrink-0 rounded-[8px] px-3",
            "bg-panel border border-hairline-input",
            "text-[12px] font-semibold text-ink",
            "hover:bg-panel-hover hover:border-ink-hairline",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warm"
          )}
        >
          {t("web.tree.keep_best", "Keep best · delete rest")}
        </button>
      )}
    </div>
  );
}
