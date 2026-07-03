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
import { formatEta, isEtaReady } from "@/lib/scanProgress";
import { getSettings, patchSettings } from "@/api/client";
import type { WebScanRequest } from "@/api/types";
import {
  SCAN_ADD_SOURCE,
  SCAN_ADVANCED,
  SCAN_AGGRESSIVE_DELETE,
  SCAN_AUTO_SELECT,
  SCAN_AUTOTUNE,
  SCAN_COLOR_THRESHOLD,
  SCAN_DHASH_THRESHOLD,
  SCAN_DIALOG,
  SCAN_OUTPUT_PATH,
  SCAN_OUTPUT_BROWSE,
  SCAN_PHASH_THRESHOLD,
  SCAN_SOURCE_LIST,
  SCAN_START_BUTTON,
} from "@/testids";

import { SourceRow, type SourceEntry } from "./scan/SourceRow";
import { ScanProgress } from "./scan/ScanProgress";
import { FsBrowser } from "./FsBrowser";
import { RescanConfirmDialog } from "./dialogs/RescanConfirmDialog";

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

// Grouping-sensitivity threshold defaults — mirror Qt ScanDialog's slider
// defaults exactly (#736). Per-scan only: read at scan-start, never persisted
// (s23b_verify_settings.py hard-asserts their absence from GET /api/settings).
const DEFAULT_PHASH_THRESHOLD = 10;
const DEFAULT_DHASH_THRESHOLD = 10;
const DEFAULT_MEAN_COLOR_THRESHOLD = 30;

/**
 * Parse a threshold `<input type="number">`'s raw string value to an int
 * clamped to `[min, max]`. An empty or non-numeric value falls back to
 * `fallback` so the request built in `startScanNow` always carries a valid
 * int (never NaN) — the min/max attrs are a soft browser hint only, this is
 * the authoritative guard.
 */
function clampThresholdInput(
  raw: string,
  min: number,
  max: number,
  fallback: number
): number {
  const parsed = Number.parseInt(raw, 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

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

  // Count of loaded-manifest rows carrying a staged (un-executed) decision.
  // A re-scan rebuilds the manifest from scratch and would discard these, so
  // Start Scan gates behind a confirm when this is > 0 (Qt s27 / #142 parity).
  const pendingDecisionCount = useAppStore((s) =>
    s.manifest.groups.reduce(
      (n, g) => n + g.items.filter((f) => f.user_decision !== "").length,
      0
    )
  );

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

  // Grouping-sensitivity thresholds (#736) — per-scan React state, NEVER
  // persisted (no patchSettings key; s23b_verify_settings.py pins the
  // absence of these keys from GET /api/settings). Qt reads its sliders at
  // scan-start only and never saves them; this mirrors that exactly.
  //
  // Held as the RAW typed string (not a pre-clamped number): clamping on
  // every keystroke would corrupt a clear-and-retype (clearing snaps the
  // field back to the default immediately, so the next digit appends onto
  // that default instead of starting fresh). The int coercion + range clamp
  // (clampThresholdInput) is applied once, at request-build time in
  // startScanNow, which is also where the spec's "empty/NaN falls back to
  // the default" guarantee actually needs to hold.
  const [phashThresholdInput, setPhashThresholdInput] = useState(
    String(DEFAULT_PHASH_THRESHOLD)
  );
  const [dhashThresholdInput, setDhashThresholdInput] = useState(
    String(DEFAULT_DHASH_THRESHOLD)
  );
  const [meanColorThresholdInput, setMeanColorThresholdInput] = useState(
    String(DEFAULT_MEAN_COLOR_THRESHOLD)
  );

  // Filesystem picker target (null = picker closed).
  const [browseTarget, setBrowseTarget] = useState<BrowseTarget | null>(null);

  // Re-scan confirmation gate — open once Start Scan is clicked while pending
  // decisions exist. A nested modal layer over this dialog (same treatment as
  // the FsBrowser picker below: the onInteractOutside/onEscapeKeyDown guards
  // keep this dialog open while the confirm is up).
  const [rescanConfirmOpen, setRescanConfirmOpen] = useState(false);

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
  // The auto-select checkboxes AND the grouping-sensitivity thresholds (#736)
  // are NOT persisted (s23b pins thresholds-not-persisted) — they reset to
  // defaults each open. EXCEPTION: the auto-tune-read-knee preference (#743) IS
  // persisted — the default set below is overridden by the saved value in the
  // async load, and it's written back on Start.
  useEffect(() => {
    if (!(open && scan.status === "idle")) return;
    let cancelled = false;
    setSources([blankSource()]);
    setOutputPath("");
    setBrowseTarget(null);
    setAutoSelect(false);
    setAggressiveDelete(false);
    setAutotuneReadKnee(true);
    setPhashThresholdInput(String(DEFAULT_PHASH_THRESHOLD));
    setDhashThresholdInput(String(DEFAULT_DHASH_THRESHOLD));
    setMeanColorThresholdInput(String(DEFAULT_MEAN_COLOR_THRESHOLD));
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
        // Auto-tune-read-knee preference persists (#743): override the default
        // with the saved value when present (unlike the reset-to-default
        // auto-select checkboxes above).
        const knee = settings["ui.scan_dialog.autotune_read_knee"];
        if (typeof knee === "boolean") {
          setAutotuneReadKnee(knee);
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
        // Post-scan auto-load. When the user enabled "Auto select after scan",
        // select + scroll to the auto-selected KEEP keepers (Qt #239 parity).
        // autoSelect is frozen during a run (the checkbox is disabled while
        // running), so its value here is the one the scan actually used.
        void loadManifest(scan.outputPath, { selectKeepers: autoSelect });
      }
      onOpenChange(false);
    }
  }, [scan.status, scan.outputPath, autoSelect, loadManifest, onOpenChange]);

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

  // #740 — receiver-side ETA, gated by the Qt #424 min-samples rule: don't
  // show a number until the current stage has been running ≥5s (throughput
  // needs time to settle) AND the stage has a known total (indeterminate
  // stages — CLASSIFY/SCORE/WRITE — have nothing to estimate "remaining"
  // against). `scan.stageStartedAt` is stamped on stage TRANSITIONS only
  // (useAppStore.ts), so a fresh stage always restarts the gate's clock —
  // otherwise the prior stage's settled throughput would leak into the new
  // stage's first estimate.
  const scanEta =
    isRunning &&
    scan.stageStartedAt !== null &&
    isEtaReady((Date.now() - scan.stageStartedAt) / 1000, scan.total)
      ? formatEta(Math.max(0, scan.total - scan.completed), scan.filesPerSec)
      : null;

  const canStart =
    !isRunning &&
    outputPath.trim() !== "" &&
    sources.some((s) => s.label.trim() !== "" && s.path.trim() !== "");

  // The actual scan kickoff: validate, build the request, persist sources for
  // the next launch, fire startScan. Reached directly when there are no
  // pending decisions, or via the rescan-confirm "Discard & Rescan" verdict.
  const startScanNow = useCallback(() => {
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

    // Coerce the raw threshold inputs to valid ints exactly once, here at
    // request-build time (empty/NaN falls back to the Qt default).
    const phashThreshold = clampThresholdInput(
      phashThresholdInput,
      1,
      20,
      DEFAULT_PHASH_THRESHOLD
    );
    const dhashThreshold = clampThresholdInput(
      dhashThresholdInput,
      1,
      20,
      DEFAULT_DHASH_THRESHOLD
    );
    const meanColorThreshold = clampThresholdInput(
      meanColorThresholdInput,
      0,
      100,
      DEFAULT_MEAN_COLOR_THRESHOLD
    );

    const req: WebScanRequest = {
      sources: sourcesMap,
      output_path: outputPath.trim(),
      recursive_map: recursiveMap,
      auto_select_enabled: autoSelect,
      // Aggressive only takes effect under auto-select; never send it alone.
      auto_select_aggressive_delete: autoSelect && aggressiveDelete,
      autotune_read_knee: autotuneReadKnee,
      // Grouping-sensitivity thresholds (#736) — per-scan only, see the
      // useState declarations above. NEVER added to the patchSettings call
      // below (that would break s23b_verify_settings.py's FORBIDDEN_KEYS
      // assertion that GET /api/settings never carries these keys).
      threshold: phashThreshold,
      dhash_threshold: dhashThreshold,
      mean_color_threshold: meanColorThreshold,
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
      // Persist the auto-tune-read-knee preference (#743) — a per-user setting
      // (unlike the auto-select checkboxes + grouping thresholds, which stay
      // per-scan). Mirrors Qt, which saves this toggle across launches.
      "ui.scan_dialog.autotune_read_knee": autotuneReadKnee,
    }).catch((err) => {
      console.warn("Failed to persist scan sources; not saved for next launch:", err);
    });

    void startScan(req);
  }, [
    sources,
    outputPath,
    autoSelect,
    aggressiveDelete,
    autotuneReadKnee,
    phashThresholdInput,
    dhashThresholdInput,
    meanColorThresholdInput,
    startScan,
  ]);

  // Start Scan click. When the loaded manifest still has pending decisions, a
  // re-scan would discard them — gate behind a confirm (Qt s27 / #142). With
  // no pending decisions, start immediately (the default path every other
  // scan scenario exercises, so they prove the gate doesn't fire spuriously).
  const handleStart = useCallback(() => {
    if (pendingDecisionCount > 0) {
      setRescanConfirmOpen(true);
      return;
    }
    startScanNow();
  }, [pendingDecisionCount, startScanNow]);

  const handleRescanConfirm = useCallback(() => {
    setRescanConfirmOpen(false);
    startScanNow();
  }, [startScanNow]);

  const handleRescanCancel = useCallback(() => {
    setRescanConfirmOpen(false);
  }, []);

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
          if (isRunning || browseTarget !== null || rescanConfirmOpen)
            e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (isRunning || browseTarget !== null || rescanConfirmOpen)
            e.preventDefault();
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
            {/* DISPLAY-ONLY alphabetical sort (case-insensitive, by path).
                Sorts a copy of {entry, idx} pairs for rendering only — the
                underlying `sources` array (and therefore sourcesMap /
                recursiveMap built in startScanNow, and each row's testid
                `idx`) stays bound to the ORIGINAL insertion order, because
                dedup infers keeper source_priority from first-seen order
                (scanner/dedup.py). Mirrors the Qt folder-list display sort. */}
            {sources
              .map((entry, idx) => ({ entry, idx }))
              .sort((a, b) =>
                a.entry.path.localeCompare(b.entry.path, undefined, {
                  sensitivity: "base",
                })
              )
              .map(({ entry, idx }) => (
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

            {/* Grouping sensitivity — pHash/dHash/mean-color thresholds
                (#736). Number inputs (the spinbox half of Qt's slider+
                spinbox pair) rather than a range slider — deterministically
                testable, unlike a slider (s71 lesson). Per-scan only: see
                the useState + startScanNow wiring above. */}
            <div className="mt-1 flex flex-col gap-3 border-t border-neutral-100 pt-3">
              <span className="text-sm font-medium text-neutral-700">
                Grouping sensitivity
              </span>

              <div className="flex flex-col gap-1">
                <label
                  htmlFor="scan-phash-threshold-input"
                  className="text-sm text-neutral-700"
                >
                  pHash Similarity Threshold (default: 10, range: 1–20)
                </label>
                <input
                  id="scan-phash-threshold-input"
                  type="number"
                  min={1}
                  max={20}
                  data-testid={SCAN_PHASH_THRESHOLD}
                  value={phashThresholdInput}
                  disabled={isRunning}
                  onChange={(e) => setPhashThresholdInput(e.target.value)}
                  className="w-24 rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
                />
                <p
                  className="text-xs text-neutral-500"
                  title="Perceptual hash Hamming distance between two images. A 64-bit pHash means images can differ by at most this many bits before being flagged as near-duplicates. Lower = stricter (fewer groups, less noise); higher = more permissive (catches more slightly-edited pairs)."
                >
                  How many bits two 64-bit pHashes may differ before grouping. Lower = stricter.
                </p>
              </div>

              <div className="flex flex-col gap-1">
                <label
                  htmlFor="scan-dhash-threshold-input"
                  className="text-sm text-neutral-700"
                >
                  dHash Confidence Threshold (default: 10, range: 1–20)
                </label>
                <input
                  id="scan-dhash-threshold-input"
                  type="number"
                  min={1}
                  max={20}
                  data-testid={SCAN_DHASH_THRESHOLD}
                  value={dhashThresholdInput}
                  disabled={isRunning}
                  onChange={(e) => setDhashThresholdInput(e.target.value)}
                  className="w-24 rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
                />
                <p
                  className="text-xs text-neutral-500"
                  title="A second, independent perceptual hash (gradient / brightness based) that confirms a pHash near-duplicate. When two files' dHashes also differ by at most this many bits the match is flagged high-confidence; otherwise low-confidence. Grouping is unchanged either way — but a low-confidence (pHash-only) near-duplicate is never auto-marked for delete by aggressive auto-select. Lower = stricter confirmation."
                >
                  Second hash that confirms a pHash match. Lower = stricter confirmation.
                </p>
              </div>

              <div className="flex flex-col gap-1">
                <label
                  htmlFor="scan-color-threshold-input"
                  className="text-sm text-neutral-700"
                >
                  Mean Color Gate (default: 30, range: 0–100)
                </label>
                <input
                  id="scan-color-threshold-input"
                  type="number"
                  min={0}
                  max={100}
                  data-testid={SCAN_COLOR_THRESHOLD}
                  value={meanColorThresholdInput}
                  disabled={isRunning}
                  onChange={(e) => setMeanColorThresholdInput(e.target.value)}
                  className="w-24 rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:opacity-50"
                />
                <p
                  className="text-xs text-neutral-500"
                  title="L2 distance between the average RGB color of two images. After the pHash check, images whose mean colors differ by more than this value are excluded from grouping — catching pHash false positives where similar DCT structure but different colors were matched. 0 = disabled; higher = more permissive color gate."
                >
                  Reject a pHash match when average colours differ by more than this (L2). 0 = off.
                </p>
              </div>
            </div>
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
            filesPerSec={scan.filesPerSec}
            eta={scanEta}
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

        {/* Re-scan confirmation — nested over this dialog. Cancel keeps the
            pending decisions and leaves this dialog open (the guards above
            stop the nested-modal interact-outside from closing it); Discard &
            Rescan proceeds with the scan, which rebuilds the manifest. */}
        <RescanConfirmDialog
          open={rescanConfirmOpen}
          pendingCount={pendingDecisionCount}
          onConfirm={handleRescanConfirm}
          onCancel={handleRescanCancel}
        />
      </DialogContent>
    </Dialog>
  );
}
