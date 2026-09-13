// Group header row: expand/collapse toggle + group summary.

import type { MouseEvent } from "react";
import { cn } from "@/lib/utils";
import { ChevronRight, ChevronDown } from "lucide-react";
import { rowGroupTestid } from "@/testids";

interface GroupRowProps {
  groupNumber: number;
  memberCount: number;
  /** The group's member file paths (#735) — carried into the group-row
   *  context menu's "Remove from List" scope. */
  memberPaths: string[];
  expanded: boolean;
  onToggle: () => void;
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
  memberPaths,
  expanded,
  onToggle,
  onContextMenu,
}: GroupRowProps) {
  function handleContextMenu(e: MouseEvent) {
    e.preventDefault();
    onContextMenu?.(memberPaths, groupNumber, e.clientX, e.clientY);
  }

  return (
    <button
      data-testid={rowGroupTestid(String(groupNumber))}
      onClick={onToggle}
      onContextMenu={handleContextMenu}
      className={cn(
        // Daylight group framing (#878): a warm band, a top border opening the
        // frame, and an accent left strip. The frame is CLOSED by the bottom
        // border FileRow draws under the group's last child (isLastInGroup) —
        // the tree is virtualised, so that cannot be a CSS sibling rule.
        "w-full flex items-center gap-2 px-3 py-1.5 bg-group-band hover:bg-subtle",
        "border-t border-t-group-line border-b border-b-group-line",
        "border-l-4 border-l-warm",
        "text-sm font-semibold text-ink text-left"
      )}
      aria-expanded={expanded}
    >
      {expanded ? (
        <ChevronDown className="h-4 w-4 flex-shrink-0 text-ink-muted" />
      ) : (
        <ChevronRight className="h-4 w-4 flex-shrink-0 text-ink-muted" />
      )}
      <span>
        Group {groupNumber}
        <span className="mx-1 text-ink-faint">·</span>
        {memberCount} {memberCount === 1 ? "file" : "files"}
      </span>
    </button>
  );
}
