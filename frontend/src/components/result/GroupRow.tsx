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
        "w-full flex items-center gap-2 px-3 py-1.5 bg-neutral-100 hover:bg-neutral-200",
        "text-sm font-medium text-neutral-800 border-b border-neutral-200 text-left"
      )}
      aria-expanded={expanded}
    >
      {expanded ? (
        <ChevronDown className="h-4 w-4 flex-shrink-0 text-neutral-500" />
      ) : (
        <ChevronRight className="h-4 w-4 flex-shrink-0 text-neutral-500" />
      )}
      <span>
        Group {groupNumber}
        <span className="mx-1 text-neutral-400">·</span>
        {memberCount} {memberCount === 1 ? "file" : "files"}
      </span>
    </button>
  );
}
