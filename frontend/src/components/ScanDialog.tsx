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
import { Checkbox } from "@/components/ui/checkbox";

import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { getSettings, patchSettings } from "@/api/client";
import type { WebScanRequest } from "@/api/types";
import {
  SCAN_ADD_SOURCE,
  SCAN_ADVANCED,
  SCAN_AGGRESSIVE_DELETE,
  SCAN_AUTO_SELECT,
  SCAN_AUTOTUNE,
  SCAN_DIALOG,
  SCAN_OUTPUT_PATH,
  SCAN_OUTPUT_BROWSE,
  SCAN_SOURCE_LIST,
  SCAN_START_BUTTON,
} from "@/testids";

import { SourceRow, type SourceEntry } from "./scan/SourceRow";
import { ScanProgress } from "./scan/ScanProgress";
import { FsBrowser } from "./FsBrowser";

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

/** Split a path into its directory and final segment (for save-mode picker). */
function splitPath(p: string): { dir: string | undefined; name: string } {
  const trimmed = p.trim();
  const m = /^(.*)[/\\]([^/\\]*)$/.exec(trimmed);
  if (m !== null) return { dir: m[1], name: m[2] };
  return { dir: undefined, name: trimmed };
}

const DEFAULT_OUTPUT_NAME = "migration_manifest.sqlite";

/**
 * Map persisted `sources.list` (from GET /api/settings) into SourceEntry rows.
 *
 * Mirrors the Qt ScanDialog._load_from_settings malformed-entry guard: only
 * dict-shaped entries with a non-empty string `path` survive. The label is
 * derived from the basename (the web SourceRow needs a non-empty label for
 * `canStart`; the persisted shape carries only {path, recursive}, matching
 * what _save_to_settings writes). Recursive is taken from the explicit
 * persisted flag (web always writes it; a missing flag defaults to false,
 * the web blankSource convention).
 */
function sourcesFromSettings(raw: unknown): SourceEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: SourceEntry[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== "object") continue;
    const path = (item as { path?: unknown }).path;
    if (typeof path !== "string" || path.trim() === "") continue;
    out.push({
      id: nextId(),
      label: splitPath(path).name,
      path,
      recursive: (item as { recursive?: unknown }).recursive === true,
    });
  }
  return out;
}

// Which field the filesystem picker is currently editing.
type BrowseTarget = { kind: "source"; id: string } | { kind: "output" };

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
  const t = useT();
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

  // Advanced settings (collapsible). Auto-tune defaults ON (opt-out, #551
  // Phase 4); auto-select + its aggressive sub-option default OFF.
  const [autoSelect, setAutoSelect] = useState(false);
  const [aggressiveDelete, setAggressiveDelete] = useState(false);
  const [autotuneReadKnee, setAutotuneReadKnee] = useState(true);

  // Filesystem picker target (null = picker closed).
  const [browseTarget, setBrowseTarget] = useState<BrowseTarget | null>(null);

  // Load persisted sources/output when the dialog opens fresh (not mid-scan).
  //
  // Mirrors Qt ScanDialog._load_from_settings: the source list + output path
  // persist across launches (#678-E / s23a/s23b). We set a blank row + empty
  // output SYNCHRONOUSLY first (the pre-persistence behaviour), then async-load
  // from GET /api/settings. The async override fills ONLY a still-PRISTINE
  // dialog (one blank row / empty output): if the user — or a test driver —
  // started editing while the fetch was in flight, their input is kept. This
  // makes load-on-open race-safe BY DESIGN: neither an empty NOR a late
  // non-empty settings response can clobber in-progress edits (e.g. a scenario
  // that reopens the dialog after a Start Scan persisted a prior source list).
  // The advanced-settings checkboxes are NOT persisted by this cluster (s23b
  // pins thresholds-not-persisted) — they always reset to defaults.
  useEffect(() => {
    if (!(open && scan.status === "idle")) return;
    let cancelled = false;
    setSources([blankSource()]);
    setOutputPath("");
    setBrowseTarget(null);
    setAutoSelect(false);
    setAggressiveDelete(false);
    setAutotuneReadKnee(true);
    void getSettings()
      .then((settings) => {
        if (cancelled) return;
        const loaded = sourcesFromSettings(settings["sources.list"]);
        if (loaded.length > 0) {
          setSources((prev) =>
            prev.length === 1 && prev[0].path === "" && prev[0].label === ""
              ? loaded
              : prev
          );
        }
        const out = settings["sources.output"];
        if (typeof out === "string" && out !== "") {
          setOutputPath((prev) => (prev === "" ? out : prev));
        }
      })
      .catch(() => {
        // Settings unreadable → keep the blank-row fallback (no trace needed;
        // the dialog is fully usable, the user just re-enters sources).
      });
    return () => {
      cancelled = true;
    };
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
  // Filesystem picker
  // ---------------------------------------------------------------------------

  const handleBrowseSource = useCallback((id: string) => {
    setBrowseTarget({ kind: "source", id });
  }, []);

  const handleBrowseConfirm = useCallback(
    (picked: string) => {
      if (browseTarget === null) return;
      if (browseTarget.kind === "output") {
        setOutputPath(picked);
      } else {
        const id = browseTarget.id;
        setSources((prev) =>
          prev.map((s) => (s.id === id ? { ...s, path: picked } : s))
        );
      }
    },
    [browseTarget]
  );

  // Derive the picker config from the current target.
  const browseSource =
    browseTarget?.kind === "source"
      ? sources.find((s) => s.id === browseTarget.id)
      : undefined;
  const outputSplit = splitPath(outputPath);

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
      auto_select_enabled: autoSelect,
      // Aggressive only takes effect under auto-select; never send it alone.
      auto_select_aggressive_delete: autoSelect && aggressiveDelete,
      autotune_read_knee: autotuneReadKnee,
    };

    // Persist the source list + output for the next launch (#678-E /
    // s23a/s23b), mirroring Qt ScanDialog._save_to_settings (called from
    // _start_scan). Fire-and-forget and non-fatal: a settings-save failure
    // must never block the scan — same posture as the desktop's swallowed
    // OSError and the locale-persist console.warn.
    void patchSettings({
      "sources.list": validSources.map((s) => ({
        path: s.path.trim(),
        recursive: s.recursive,
      })),
      "sources.output": outputPath.trim(),
    }).catch((err) => {
      console.warn("Failed to persist scan sources; not saved for next launch:", err);
    });

    void startScan(req);
  }, [sources, outputPath, autoSelect, aggressiveDelete, autotuneReadKnee, startScan]);

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
        // Prevent Radix from closing on overlay click while running, or
        // while the filesystem picker is open over this dialog (the picker
        // is a nested layer — a click inside it must not dismiss the scan
        // dialog underneath).
        onInteractOutside={(e) => {
          if (isRunning || browseTarget !== null) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (isRunning || browseTarget !== null) e.preventDefault();
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
            {sources.map((entry, idx) => (
              <div key={entry.id} role="listitem">
                <SourceRow
                  entry={entry}
                  idx={idx}
                  disabled={isRunning}
                  onChange={handleSourceChange}
                  onRemove={handleSourceRemove}
                  onBrowse={handleBrowseSource}
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
          <div className="flex items-center gap-2">
            <input
              id="scan-output-path-input"
              type="text"
              data-testid={SCAN_OUTPUT_PATH}
              placeholder="e.g. C:/output/scan.db"
              value={outputPath}
              disabled={isRunning}
              onChange={(e) => setOutputPath(e.target.value)}
              className="flex-1 rounded border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid={SCAN_OUTPUT_BROWSE}
              disabled={isRunning}
              onClick={() => setBrowseTarget({ kind: "output" })}
              className="shrink-0"
            >
              {t("web.scan.browse", "Browse…")}
            </Button>
          </div>
        </div>

        {/* Advanced settings (collapsible) */}
        <details className="mt-4 rounded border border-neutral-200 p-2">
          <summary
            data-testid={SCAN_ADVANCED}
            className="cursor-pointer select-none text-sm font-medium text-neutral-700"
          >
            Advanced settings
          </summary>
          <div className="mt-3 flex flex-col gap-2 pl-1">
            {/* Auto-tune reader concurrency — default ON (opt-out, #551 Phase 4) */}
            <label className="flex items-center gap-2 text-sm select-none">
              <Checkbox
                checked={autotuneReadKnee}
                disabled={isRunning}
                onCheckedChange={(checked) =>
                  setAutotuneReadKnee(checked === true)
                }
                data-testid={SCAN_AUTOTUNE}
                aria-label="Auto-tune reader concurrency"
              />
              <span className="text-neutral-700">
                Auto-tune reader concurrency (experimental)
              </span>
            </label>

            {/* Auto select after scan — default OFF */}
            <label className="flex items-center gap-2 text-sm select-none">
              <Checkbox
                checked={autoSelect}
                disabled={isRunning}
                onCheckedChange={(checked) => {
                  const on = checked === true;
                  setAutoSelect(on);
                  // Disabling the parent also clears the sub-option so a
                  // stale-true never reaches the scan request.
                  if (!on) setAggressiveDelete(false);
                }}
                data-testid={SCAN_AUTO_SELECT}
                aria-label="Auto select after scan"
              />
              <span className="text-neutral-700">Auto select after scan</span>
            </label>

            {/* Aggressive sub-option — indented; enabled only when auto-select on */}
            <label className="flex items-center gap-2 pl-6 text-sm select-none">
              <Checkbox
                checked={aggressiveDelete}
                disabled={isRunning || !autoSelect}
                onCheckedChange={(checked) =>
                  setAggressiveDelete(checked === true)
                }
                data-testid={SCAN_AGGRESSIVE_DELETE}
                aria-label="Also mark all other files for delete"
              />
              <span className={autoSelect ? "text-neutral-700" : "text-neutral-400"}>
                Also mark all other files for delete
              </span>
            </label>
          </div>
        </details>

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

        {/* Filesystem picker (folder for sources, save for output) */}
        <FsBrowser
          open={browseTarget !== null}
          mode={browseTarget?.kind === "output" ? "save" : "directory"}
          title={
            browseTarget?.kind === "output"
              ? t("web.browse.title_output", "Save manifest as")
              : t("web.browse.title_source", "Select source folder")
          }
          initialPath={
            browseTarget?.kind === "output"
              ? outputSplit.dir
              : browseSource?.path.trim() || undefined
          }
          defaultFilename={
            browseTarget?.kind === "output"
              ? outputSplit.name || DEFAULT_OUTPUT_NAME
              : undefined
          }
          onConfirm={handleBrowseConfirm}
          onOpenChange={(o) => {
            if (!o) setBrowseTarget(null);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
