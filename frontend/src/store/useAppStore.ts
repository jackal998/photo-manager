// Zustand store implementing AppStore from store/types.ts.
// Uses immer middleware so draft mutations are safe inside each action.

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { WritableDraft } from "immer";

import {
  cancelScan,
  getManifest,
  getSettings,
  patchDecisions,
  patchLocks,
  patchSettings,
  startScan,
} from "../api/client";
import type {
  FileRow,
  ScanFailedEvent,
  ScanFinishedEvent,
  ScanHashPoolMeasuredEvent,
  ScanLogEvent,
  ScanReadKneeMeasuredEvent,
  ScanStageEvent,
} from "../api/types";
import type {
  AppStore,
  ManifestState,
  ScanState,
  SettingsState,
} from "./types";

// ---------------------------------------------------------------------------
// Type alias for immer-wrapped set function inside create()
// ---------------------------------------------------------------------------

type ImmerSetFn = (
  nextStateOrUpdater:
    | AppStore
    | Partial<AppStore>
    | ((state: WritableDraft<AppStore>) => void)
) => void;

// ---------------------------------------------------------------------------
// Initial state slices
// ---------------------------------------------------------------------------

const initialScan: ScanState = {
  taskId: null,
  status: "idle",
  stageName: "",
  completed: 0,
  total: 0,
  filesPerSec: 0,
  log: [],
  error: null,
  outputPath: null,
};

const initialManifest: ManifestState = {
  path: null,
  groups: [],
  totalGroups: 0,
  totalFiles: 0,
  loading: false,
  error: null,
};

const initialSettings: SettingsState = {
  values: {
    "sorting.defaults": null,
    "ui.prune_singletons": null,
    "ui.scan_dialog.autotune_read_knee": null,
  },
  loading: false,
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAppStore = create<AppStore>()(
  immer((set, get) => ({
    scan: { ...initialScan },
    manifest: { ...initialManifest },
    settings: { ...initialSettings },

    // -----------------------------------------------------------------------
    // Scan actions
    // -----------------------------------------------------------------------

    async startScan(req) {
      const result = await startScan(req);
      set((state) => {
        state.scan = {
          ...initialScan,
          taskId: result.task_id,
          status: "running",
        };
      });
    },

    async cancelScan() {
      const { taskId } = get().scan;
      if (taskId === null) return;
      await cancelScan(taskId);
    },

    ingestScanEvent(name, payload) {
      set((state) => {
        switch (name) {
          case "log": {
            const ev = payload as ScanLogEvent;
            state.scan.log.push(ev.msg);
            break;
          }
          case "stage": {
            const ev = payload as ScanStageEvent;
            state.scan.stageName = ev.stage_name;
            state.scan.completed = ev.completed;
            state.scan.total = ev.total;
            state.scan.filesPerSec = ev.files_per_sec;
            break;
          }
          case "finished": {
            const ev = payload as ScanFinishedEvent;
            state.scan.status = "finished";
            state.scan.outputPath = ev.output_path;
            // Surface output_path in the log so the UI can show it.
            state.scan.log.push(`Scan finished. Output: ${ev.output_path}`);
            break;
          }
          case "failed": {
            const ev = payload as ScanFailedEvent;
            // Clean cancel is msg === "Scan cancelled."
            if (ev.msg === "Scan cancelled.") {
              state.scan.status = "cancelled";
              state.scan.log.push("Scan cancelled.");
            } else {
              state.scan.status = "failed";
              state.scan.error = ev.msg;
              state.scan.log.push(`Scan failed: ${ev.msg}`);
            }
            break;
          }
          case "completed_empty": {
            state.scan.status = "finished";
            state.scan.log.push("Scan complete — no files found.");
            break;
          }
          case "hash_pool_measured": {
            const ev = payload as ScanHashPoolMeasuredEvent;
            state.scan.log.push(
              `Hash pool measured: ${JSON.stringify(ev)}`
            );
            break;
          }
          case "read_knee_measured": {
            const ev = payload as ScanReadKneeMeasuredEvent;
            state.scan.log.push(
              `Read knee measured: device=${ev.device}, knee=${ev.knee}`
            );
            break;
          }
          // Unknown event names are intentionally ignored — forward-compatible.
        }
      });
    },

    resetScan() {
      set((state) => {
        state.scan = { ...initialScan };
      });
    },

    // -----------------------------------------------------------------------
    // Manifest actions
    // -----------------------------------------------------------------------

    async loadManifest(path) {
      set((state) => {
        state.manifest.loading = true;
        state.manifest.error = null;
      });
      try {
        const data = await getManifest(path);
        set((state) => {
          state.manifest.path = data.manifest_path;
          state.manifest.groups = data.groups;
          state.manifest.totalGroups = data.total_groups;
          state.manifest.totalFiles = data.total_files;
          state.manifest.loading = false;
        });
      } catch (err) {
        set((state) => {
          state.manifest.loading = false;
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    async setDecision(filePath, decision) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      // Save previous value for possible revert.
      const previousDecision = readFileRowField(
        get().manifest,
        filePath,
        (row) => row.user_decision
      );

      // Optimistic apply.
      applyFileRowPatch(set as ImmerSetFn, filePath, (row) => {
        row.user_decision = decision;
      });

      try {
        await patchDecisions(manifestPath, [{ file_path: filePath, decision }]);
      } catch (err) {
        // Revert on failure.
        // Guard is `!== null` (not falsy) because DecisionValue includes ""
        // (the no-decision state) — a falsy check would wrongly skip reverting
        // a row whose prior decision was the empty string.
        if (previousDecision !== null) {
          applyFileRowPatch(set as ImmerSetFn, filePath, (row) => {
            row.user_decision = previousDecision;
          });
        }
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    async setLock(filePath, locked) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      // Save previous value for revert.
      const previousLocked = readFileRowField(
        get().manifest,
        filePath,
        (row) => row.is_locked
      );

      // Optimistic apply.
      applyFileRowPatch(set as ImmerSetFn, filePath, (row) => {
        row.is_locked = locked;
      });

      try {
        await patchLocks(manifestPath, [{ file_path: filePath, locked }]);
      } catch (err) {
        // Revert on failure.
        // Guard is `!== null` (not falsy) because `false` (unlocked) is a
        // valid previous value — a falsy check would wrongly skip reverting
        // a row that was previously unlocked.
        if (previousLocked !== null) {
          applyFileRowPatch(set as ImmerSetFn, filePath, (row) => {
            row.is_locked = previousLocked;
          });
        }
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    // -----------------------------------------------------------------------
    // Settings actions
    // -----------------------------------------------------------------------

    async loadSettings() {
      set((state) => {
        state.settings.loading = true;
      });
      try {
        const values = await getSettings();
        set((state) => {
          state.settings.values = values;
          state.settings.loading = false;
        });
      } catch {
        set((state) => {
          state.settings.loading = false;
        });
      }
    },

    async saveSettings(updates) {
      await patchSettings(updates);
      // Reload to get canonical server values.
      await get().loadSettings();
    },
  }))
);

// ---------------------------------------------------------------------------
// Internal helpers — not exported
// ---------------------------------------------------------------------------

/**
 * Read a single field from the FileRow matching filePath.
 * Returns null if the row is not found.
 */
function readFileRowField<T>(
  manifest: ManifestState,
  filePath: string,
  read: (row: FileRow) => T
): T | null {
  for (const group of manifest.groups) {
    const row = group.items.find((f) => f.file_path === filePath);
    if (row !== undefined) return read(row);
  }
  return null;
}

/**
 * Locate a FileRow by file_path and run a mutating patch function on it.
 * Immer makes the mutation safe.
 */
function applyFileRowPatch(
  set: ImmerSetFn,
  filePath: string,
  patch: (row: WritableDraft<FileRow>) => void
): void {
  set((state) => {
    for (const group of state.manifest.groups) {
      const row = group.items.find((f) => f.file_path === filePath);
      if (row !== undefined) {
        patch(row);
        break;
      }
    }
  });
}
