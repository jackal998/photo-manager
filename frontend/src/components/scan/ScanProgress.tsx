// Running-state UI for ScanDialog: progress bar, status text, throughput/ETA
// (#740), log area, cancel button. Only rendered while scan.status === "running".

import { useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useT } from "@/i18n/useT";
import { formatThroughput, stageLabel } from "@/lib/scanProgress";
import {
  SCAN_CANCEL_BUTTON,
  SCAN_ETA_TEXT,
  SCAN_PROGRESS_BAR,
  SCAN_PROGRESS_LOG,
  SCAN_STATUS_TEXT,
  SCAN_THROUGHPUT_TEXT,
} from "@/testids";

interface ScanProgressProps {
  stageName: string;
  completed: number;
  total: number;
  /** Raw files/sec from the SSE "stage" event; 0/undefined suppresses the row. */
  filesPerSec: number;
  /**
   * Pre-computed, pre-gated ETA string (already past the #424 min-samples
   * gate — see ScanDialog.tsx). Null/undefined hides the row entirely.
   */
  eta: string | null | undefined;
  log: string[];
  onCancel: () => void;
}

export function ScanProgress({
  stageName,
  completed,
  total,
  filesPerSec,
  eta,
  log,
  onCancel,
}: ScanProgressProps) {
  const t = useT();
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

  const throughputText = formatThroughput(filesPerSec);

  return (
    <div className="flex flex-col gap-3 mt-4">
      {/* Status text — localized stage label (#740; raw stage id / "Running…"
          for an unmapped or absent stage — see lib/scanProgress.ts). */}
      <p
        data-testid={SCAN_STATUS_TEXT}
        className="text-sm font-medium text-neutral-700 truncate"
        aria-live="polite"
      >
        {stageLabel(stageName, t)}
      </p>

      {/* Throughput + ETA (#740) — each suppressed independently when not
          yet meaningful, mirroring the Qt #424 receiver gating. */}
      {(throughputText !== null || eta) && (
        <p className="text-xs font-mono text-neutral-500">
          {throughputText !== null && (
            <span data-testid={SCAN_THROUGHPUT_TEXT}>{throughputText}</span>
          )}
          {throughputText !== null && eta && "  —  "}
          {eta && <span data-testid={SCAN_ETA_TEXT}>ETA {eta}</span>}
        </p>
      )}

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
