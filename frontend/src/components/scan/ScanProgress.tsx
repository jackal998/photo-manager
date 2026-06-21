// Running-state UI for ScanDialog: progress bar, status text, log area, cancel button.
// Only rendered while scan.status === "running".

import { useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  SCAN_CANCEL_BUTTON,
  SCAN_PROGRESS_BAR,
  SCAN_PROGRESS_LOG,
  SCAN_STATUS_TEXT,
} from "@/testids";

interface ScanProgressProps {
  stageName: string;
  completed: number;
  total: number;
  log: string[];
  onCancel: () => void;
}

export function ScanProgress({
  stageName,
  completed,
  total,
  log,
  onCancel,
}: ScanProgressProps) {
  const logRef = useRef<HTMLDivElement>(null);

  // Auto-scroll log to the bottom whenever log lines grow.
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [log.length]);

  // Determinate when total > 0, indeterminate otherwise.
  const isDeterminate = total > 0;
  const pct = isDeterminate ? Math.round((completed / total) * 100) : 0;

  return (
    <div className="flex flex-col gap-3 mt-4">
      {/* Status text */}
      <p
        data-testid={SCAN_STATUS_TEXT}
        className="text-sm font-medium text-neutral-700 truncate"
        aria-live="polite"
      >
        {stageName || "Running…"}
      </p>

      {/* Progress bar */}
      <div data-testid={SCAN_PROGRESS_BAR} aria-label="Scan progress">
        {isDeterminate ? (
          <Progress value={pct} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} />
        ) : (
          // Indeterminate: animated pulse bar with count text
          <div className="flex flex-col gap-1">
            <div className="relative h-2 w-full overflow-hidden rounded-full bg-neutral-200">
              <div className="h-full bg-neutral-500 animate-pulse w-1/3" />
            </div>
            <span className="text-xs text-neutral-500">{completed} files found</span>
          </div>
        )}
      </div>

      {/* Log area */}
      <div
        ref={logRef}
        data-testid={SCAN_PROGRESS_LOG}
        role="log"
        aria-label="Scan log"
        aria-live="polite"
        className="h-32 overflow-y-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-xs font-mono text-neutral-700 space-y-0.5"
      >
        {log.map((line, i) => (
          // Index key is acceptable here — log lines only ever append,
          // they are never reordered or removed individually.
          <div key={`log-${i}`}>{line}</div>
        ))}
      </div>

      {/* Cancel */}
      <div className="flex justify-end">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid={SCAN_CANCEL_BUTTON}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
