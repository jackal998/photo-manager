// ScanDialog — modal dialog for configuring and starting a scan.
//
// SSE mounting decision: useScanSSE is called in App (the parent), NOT here.
// Reason: the SSE stream must survive dialog close — if the user dismisses
// the dialog while a scan is running, we still need to ingest events and
// auto-load the manifest on "finished". Mounting here would disconnect the
// EventSource on unmount. The parent mounts useScanSSE(scan.taskId) once.
//
// Props contract:
//   open: boolean            — controlled by parent (main toolbar Scan button)
//   onOpenChange: (open: boolean) => void   — fed to Radix Dialog.Root

import { useState, useEffect, useCallback } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

import { useAppStore } from "@/store/useAppStore";
import type { WebScanRequest } from "@/api/types";
import {
  SCAN_ADD_SOURCE,
  SCAN_DIALOG,
  SCAN_OUTPUT_PATH,
  SCAN_SOURCE_LIST,
  SCAN_START_BUTTON,
} from "@/testids";

import { SourceRow, type SourceEntry } from "./scan/SourceRow";
import { ScanProgress } from "./scan/ScanProgress";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _nextId = 0;
function nextId(): string {
  return String((_nextId += 1));
}

function blankSource(): SourceEntry {
  return { id: nextId(), label: "", path: "", recursive: false };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ScanDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ScanDialog({ open, onOpenChange }: ScanDialogProps) {
  const scan = useAppStore((s) => s.scan);
  const startScan = useAppStore((s) => s.startScan);
  const cancelScan = useAppStore((s) => s.cancelScan);
  const loadManifest = useAppStore((s) => s.loadManifest);
  const resetScan = useAppStore((s) => s.resetScan);

  // ---------------------------------------------------------------------------
  // Form state — local to dialog (reset on each open)
  // ---------------------------------------------------------------------------

  const [sources, setSources] = useState<SourceEntry[]>(() => [blankSource()]);
  const [outputPath, setOutputPath] = useState("");

  // Reset form when the dialog opens fresh (not while a scan is running).
  useEffect(() => {
    if (open && scan.status === "idle") {
      setSources([blankSource()]);
      setOutputPath("");
    }
  }, [open, scan.status]);

  // ---------------------------------------------------------------------------
  // Transition: scan finished → load manifest (if output path present) + close dialog
  //
  // completed_empty sets status="finished" but leaves outputPath=null.
  // The dialog closes in both cases; loadManifest is only called when
  // there is a real output path (i.e. the scan produced results).
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (scan.status === "finished") {
      if (scan.outputPath !== null) {
        void loadManifest(scan.outputPath);
      }
      onOpenChange(false);
    }
  }, [scan.status, scan.outputPath, loadManifest, onOpenChange]);

  // Transition: cancelled → close dialog cleanly
  useEffect(() => {
    if (scan.status === "cancelled") {
      onOpenChange(false);
    }
  }, [scan.status, onOpenChange]);

  // ---------------------------------------------------------------------------
  // Source list actions
  // ---------------------------------------------------------------------------

  const handleAddSource = useCallback(() => {
    setSources((prev) => [...prev, blankSource()]);
  }, []);

  const handleSourceChange = useCallback((updated: SourceEntry) => {
    setSources((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }, []);

  const handleSourceRemove = useCallback((id: string) => {
    setSources((prev) => prev.filter((s) => s.id !== id));
  }, []);

  // ---------------------------------------------------------------------------
  // Start scan
  // ---------------------------------------------------------------------------

  const isRunning = scan.status === "running";

  const canStart =
    !isRunning &&
    outputPath.trim() !== "" &&
    sources.some((s) => s.label.trim() !== "" && s.path.trim() !== "");

  const handleStart = useCallback(() => {
    const validSources = sources.filter(
      (s) => s.label.trim() !== "" && s.path.trim() !== ""
    );
    if (validSources.length === 0 || outputPath.trim() === "") return;

    const sourcesMap: Record<string, string> = {};
    const recursiveMap: Record<string, boolean> = {};
    for (const s of validSources) {
      sourcesMap[s.label.trim()] = s.path.trim();
      recursiveMap[s.label.trim()] = s.recursive;
    }

    const req: WebScanRequest = {
      sources: sourcesMap,
      output_path: outputPath.trim(),
      recursive_map: recursiveMap,
    };

    void startScan(req);
  }, [sources, outputPath, startScan]);

  // ---------------------------------------------------------------------------
  // Cancel
  // ---------------------------------------------------------------------------

  const handleCancel = useCallback(() => {
    void cancelScan();
  }, [cancelScan]);

  // ---------------------------------------------------------------------------
  // Error display (real error, not a cancel)
  // ---------------------------------------------------------------------------

  const showError = scan.status === "failed" && scan.error !== null;

  // ---------------------------------------------------------------------------
  // Close handler — prevent close while running
  // ---------------------------------------------------------------------------

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      // Don't close while a scan is running (user must cancel first)
      if (!nextOpen && isRunning) return;
      if (!nextOpen) {
        // Clean up error state so the form is fresh next open.
        resetScan();
      }
      onOpenChange(nextOpen);
    },
    [isRunning, resetScan, onOpenChange]
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        data-testid={SCAN_DIALOG}
        className="max-w-xl"
        // Prevent Radix from closing on overlay click while running
        onInteractOutside={(e) => {
          if (isRunning) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (isRunning) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>Scan Sources</DialogTitle>
        </DialogHeader>

        {/* Source list */}
        <div className="mt-4 flex flex-col gap-1">
          <label className="text-sm font-medium text-neutral-700">
            Sources
          </label>
          <div
            data-testid={SCAN_SOURCE_LIST}
            className="flex flex-col divide-y divide-neutral-100 rounded border border-neutral-200 p-2"
            role="list"
            aria-label="Scan source list"
          >
            {sources.map((entry) => (
              <div key={entry.id} role="listitem">
                <SourceRow
                  entry={entry}
                  disabled={isRunning}
                  onChange={handleSourceChange}
                  onRemove={handleSourceRemove}
                />
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={SCAN_ADD_SOURCE}
            disabled={isRunning}
            onClick={handleAddSource}
            className="mt-1 self-start"
          >
            + Add source
          </Button>
        </div>

        {/* Output path */}
        <div className="mt-4 flex flex-col gap-1">
          <label
            htmlFor="scan-output-path-input"
            className="text-sm font-medium text-neutral-700"
          >
            Output path
          </label>
          <input
            id="scan-output-path-input"
            type="text"
            data-testid={SCAN_OUTPUT_PATH}
            placeholder="e.g. C:/output/scan.db"
            value={outputPath}
            disabled={isRunning}
            onChange={(e) => setOutputPath(e.target.value)}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
          />
        </div>

        {/* Error message */}
        {showError && (
          <p role="alert" className="mt-2 text-sm text-red-600">
            Error: {scan.error}
          </p>
        )}

        {/* Running state: progress / log / cancel */}
        {isRunning && (
          <ScanProgress
            stageName={scan.stageName}
            completed={scan.completed}
            total={scan.total}
            log={scan.log}
            onCancel={handleCancel}
          />
        )}

        {/* Footer: Start button (hidden while running) */}
        {!isRunning && (
          <div className="mt-4 flex justify-end">
            <Button
              type="button"
              data-testid={SCAN_START_BUTTON}
              disabled={!canStart}
              onClick={handleStart}
            >
              Start Scan
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
