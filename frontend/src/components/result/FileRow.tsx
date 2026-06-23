// File row inside a group: thumbnail + metadata columns + decision + lock.

import type { MouseEvent } from "react";
import { cn } from "@/lib/utils";
import {
  similarityLabel,
  formatBytes,
  formatScore,
  formatDate,
  formatDims,
} from "@/lib/format";
import type { FileRow as FileRowData } from "@/api/types";
import { DecisionControl } from "./DecisionControl";
import { LockToggle } from "./LockToggle";
import {
  rowFileTestid,
  rowDecisionTestid,
  rowLockTestid,
} from "@/testids";

interface FileRowProps {
  row: FileRowData;
  groupId: string;
  onDecision: (filePath: string, value: import("@/api/types").DecisionValue) => void;
  onLock: (filePath: string, locked: boolean) => void;
  onSelect?: (
    filePath: string,
    mods: { ctrl: boolean; shift: boolean }
  ) => void;
  onOpenFullRes?: (filePath: string) => void;
  onContextMenu?: (filePath: string, isLocked: boolean, x: number, y: number) => void;
  isSelected?: boolean;
}

export function FileRow({ row, groupId, onDecision, onLock, onSelect, onOpenFullRes, onContextMenu, isSelected }: FileRowProps) {
  const simLabel = similarityLabel(row.similarity);
  // Passenger rows get a subtle amber highlight on the similarity badge.
  const isPassenger = row.similarity.kind === "passenger";

  function handleClick(e: MouseEvent) {
    // Ctrl/Cmd toggles, Shift extends a range; a plain click replaces. The
    // store interprets the modifiers (ResultTree.handleRowSelect).
    onSelect?.(row.file_path, {
      ctrl: e.ctrlKey || e.metaKey,
      shift: e.shiftKey,
    });
  }

  function handleDoubleClick() {
    onOpenFullRes?.(row.file_path);
  }

  function handleContextMenu(e: MouseEvent) {
    e.preventDefault();
    onContextMenu?.(row.file_path, row.is_locked, e.clientX, e.clientY);
  }

  return (
    <div
      data-testid={rowFileTestid(groupId, row.basename)}
      className={cn(
        "flex items-start gap-3 px-4 py-2 border-b border-neutral-100 hover:bg-neutral-50 cursor-pointer",
        row.is_ref_winner && "bg-blue-50 hover:bg-blue-100",
        // Multi-selection highlight wins over the ref-winner tint (tailwind-merge
        // resolves the bg conflict in favour of the later class).
        isSelected && "bg-sky-100 ring-1 ring-inset ring-sky-400 hover:bg-sky-100"
      )}
      aria-selected={isSelected}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
    >
      {/* Thumbnail */}
      <div className="flex-shrink-0 w-16 h-16 bg-neutral-200 rounded overflow-hidden flex items-center justify-center">
        <img
          loading="lazy"
          src={row.thumbnail_url}
          width={64}
          height={64}
          alt={row.basename}
          className="object-cover w-full h-full"
        />
      </div>

      {/* Name + folder */}
      <div className="flex-shrink-0 w-40 min-w-0">
        <div className="flex items-center gap-1 flex-wrap">
          {row.is_ref_winner && (
            <span className="inline-block text-xs font-semibold bg-blue-100 text-blue-700 rounded px-1 py-0.5 leading-none">
              Ref
            </span>
          )}
          <span className="font-semibold text-sm truncate">{row.basename}</span>
        </div>
        <div className="text-xs text-neutral-500 truncate" title={row.folder}>
          {row.folder}
        </div>
      </div>

      {/* Similarity */}
      <div className="w-16 flex-shrink-0 text-sm">
        <span
          className={cn(
            "inline-block text-xs rounded px-1 py-0.5",
            isPassenger
              ? "bg-amber-100 text-amber-800"
              : "bg-neutral-100 text-neutral-700"
          )}
        >
          {simLabel}
        </span>
      </div>

      {/* Action */}
      <div className="w-24 flex-shrink-0 text-xs text-neutral-600 truncate" title={row.action}>
        {row.action || "—"}
      </div>

      {/* Score */}
      <div className="w-12 flex-shrink-0 text-xs text-right text-neutral-600">
        {formatScore(row.score)}
      </div>

      {/* Dimensions */}
      <div className="w-20 flex-shrink-0 text-xs text-neutral-600">
        {formatDims(row.pixel_width, row.pixel_height)}
      </div>

      {/* File size */}
      <div className="w-16 flex-shrink-0 text-xs text-right text-neutral-600">
        {formatBytes(row.file_size_bytes)}
      </div>

      {/* Shot date */}
      <div className="w-24 flex-shrink-0 text-xs text-neutral-600">
        {formatDate(row.shot_date)}
      </div>

      {/* Decision control */}
      <div className="flex-shrink-0">
        <DecisionControl
          value={row.user_decision}
          onChange={(val) => onDecision(row.file_path, val)}
          disabled={row.is_locked}
          data-testid={rowDecisionTestid(groupId, row.basename)}
        />
      </div>

      {/* Lock toggle */}
      <div className="flex-shrink-0 pt-1">
        <LockToggle
          checked={row.is_locked}
          onChange={(locked) => onLock(row.file_path, locked)}
          data-testid={rowLockTestid(groupId, row.basename)}
        />
      </div>
    </div>
  );
}
