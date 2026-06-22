// A single source row inside the ScanDialog source list.
// Each row has: label text input, path text input, recursive checkbox, remove button.

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import {
  scanSourceLabelTestid,
  scanSourcePathTestid,
  scanSourceRecursiveTestid,
} from "@/testids";

export interface SourceEntry {
  id: string;
  label: string;
  path: string;
  recursive: boolean;
}

interface SourceRowProps {
  entry: SourceEntry;
  /** 0-based position of this row in the source list (used for stable testids). */
  idx: number;
  /** Whether the scan is currently running (disables edits). */
  disabled: boolean;
  onChange: (updated: SourceEntry) => void;
  onRemove: (id: string) => void;
}

export function SourceRow({ entry, idx, disabled, onChange, onRemove }: SourceRowProps) {
  return (
    <div className={cn("flex items-center gap-2 py-1")} role="group" aria-label={`Source ${entry.label || "unnamed"}`}>
      {/* Label */}
      <label className="sr-only" htmlFor={`source-label-${entry.id}`}>
        Source label
      </label>
      <input
        id={`source-label-${entry.id}`}
        type="text"
        data-testid={scanSourceLabelTestid(idx)}
        placeholder="Label"
        value={entry.label}
        disabled={disabled}
        onChange={(e) => onChange({ ...entry, label: e.target.value })}
        className="w-28 rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
        aria-label="Source label"
      />

      {/* Path */}
      <label className="sr-only" htmlFor={`source-path-${entry.id}`}>
        Source path
      </label>
      <input
        id={`source-path-${entry.id}`}
        type="text"
        data-testid={scanSourcePathTestid(idx)}
        placeholder="Path"
        value={entry.path}
        disabled={disabled}
        onChange={(e) => onChange({ ...entry, path: e.target.value })}
        className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
        aria-label="Source path"
      />

      {/* Recursive checkbox */}
      <label className="flex items-center gap-1 text-sm select-none">
        <Checkbox
          checked={entry.recursive}
          disabled={disabled}
          onCheckedChange={(checked) =>
            onChange({ ...entry, recursive: checked === true })
          }
          data-testid={scanSourceRecursiveTestid(idx)}
          aria-label="Recursive"
        />
        <span className="text-neutral-600">Recursive</span>
      </label>

      {/* Remove */}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled}
        onClick={() => onRemove(entry.id)}
        aria-label="Remove source"
        className="shrink-0 text-neutral-500 hover:text-red-600"
      >
        ✕
      </Button>
    </div>
  );
}
