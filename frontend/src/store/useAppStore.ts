// Zustand store implementing AppStore from store/types.ts.
// Uses immer middleware so draft mutations are safe inside each action.

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { WritableDraft } from "immer";

import {
  ApiConflictError,
  bulkDecide,
  cancelScan,
  getManifest,
  getSettings,
  patchDecisions,
  patchLocks,
  patchSettings,
  postExecute,
  postPrune,
  postRemove,
  postReveal,
  postSave,
  startScan,
} from "../api/client";
import type {
  DecisionValue,
  FileRow,
  ScanFailedEvent,
  ScanFinishedEvent,
  ScanHashPoolMeasuredEvent,
  ScanLogEvent,
  ScanReadKneeMeasuredEvent,
  ScanStageEvent,
} from "../api/types";
import type {
  ActionState,
  AppStore,
  ExecuteState,
  ManifestState,
  PreviewState,
  ScanState,
  SelectionState,
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

// Monotonic sequence for bulk-decide preview ordering. Only the latest
// preview/apply is allowed to commit its result — out-of-order responses from
// superseded requests are dropped (see previewBulkDecide / applyBulkDecide).
let _bulkDecideSeq = 0;

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

const initialPreview: PreviewState = {
  selectedFilePath: null,
  fullResPath: null,
};

const initialSelection: SelectionState = {
  selectedPaths: [],
  anchorPath: null,
};

const initialExecute: ExecuteState = {
  executeOpen: false,
  executeRunning: false,
  executeResult: null,
  executeError: null,
  lockConflict: null,
  prunePrompt: null,
};

const initialAction: ActionState = {
  actionDialogOpen: false,
  field: "File Name",
  pattern: "",
  action: "delete",
  previewMatched: -1,
  previewSample: [],
  previewTruncated: false,
  actionError: null,
  actionRunning: false,
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAppStore = create<AppStore>()(
  immer((set, get) => ({
    scan: { ...initialScan },
    manifest: { ...initialManifest },
    settings: { ...initialSettings },
    preview: { ...initialPreview },
    selection: { ...initialSelection },
    execute: { ...initialExecute },
    action: { ...initialAction },

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
          // A fresh manifest invalidates any prior selection (paths may vanish).
          state.selection.selectedPaths = [];
          state.selection.anchorPath = null;
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
    // Batch decision / lock actions (multi-selection context menu)
    // -----------------------------------------------------------------------

    async setDecisions(paths, decision) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null || paths.length === 0) return;

      // Snapshot prior values per path so a failed PATCH reverts every row.
      const previous = new Map<string, DecisionValue>();
      for (const p of paths) {
        const prev = readFileRowField(
          get().manifest,
          p,
          (row) => row.user_decision
        );
        if (prev !== null) previous.set(p, prev);
        applyFileRowPatch(set as ImmerSetFn, p, (row) => {
          row.user_decision = decision;
        });
      }

      try {
        await patchDecisions(
          manifestPath,
          paths.map((p) => ({ file_path: p, decision }))
        );
      } catch (err) {
        for (const [p, prev] of previous) {
          applyFileRowPatch(set as ImmerSetFn, p, (row) => {
            row.user_decision = prev;
          });
        }
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    async setLocks(paths, locked) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null || paths.length === 0) return;

      const previous = new Map<string, boolean>();
      for (const p of paths) {
        const prev = readFileRowField(
          get().manifest,
          p,
          (row) => row.is_locked
        );
        if (prev !== null) previous.set(p, prev);
        applyFileRowPatch(set as ImmerSetFn, p, (row) => {
          row.is_locked = locked;
        });
      }

      try {
        await patchLocks(
          manifestPath,
          paths.map((p) => ({ file_path: p, locked }))
        );
      } catch (err) {
        for (const [p, prev] of previous) {
          applyFileRowPatch(set as ImmerSetFn, p, (row) => {
            row.is_locked = prev;
          });
        }
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    // -----------------------------------------------------------------------
    // Selection actions (main result-tree multi-selection)
    // -----------------------------------------------------------------------

    setSelection(paths) {
      set((state) => {
        state.selection.selectedPaths = [...paths];
        state.selection.anchorPath =
          paths.length > 0 ? paths[paths.length - 1] : null;
      });
    },

    toggleSelection(path) {
      set((state) => {
        const idx = state.selection.selectedPaths.indexOf(path);
        if (idx === -1) {
          state.selection.selectedPaths.push(path);
        } else {
          state.selection.selectedPaths.splice(idx, 1);
        }
        // Ctrl/Cmd+click re-anchors so a following Shift+click ranges from here.
        state.selection.anchorPath = path;
      });
    },

    extendSelection(toPath, orderedVisiblePaths) {
      set((state) => {
        const anchor = state.selection.anchorPath;
        const ai = anchor === null ? -1 : orderedVisiblePaths.indexOf(anchor);
        const ti = orderedVisiblePaths.indexOf(toPath);
        if (ai === -1 || ti === -1) {
          // No usable anchor (or one scrolled out of the visible order) →
          // behave like a plain click on toPath.
          state.selection.selectedPaths = [toPath];
          state.selection.anchorPath = toPath;
          return;
        }
        const [lo, hi] = ai <= ti ? [ai, ti] : [ti, ai];
        state.selection.selectedPaths = orderedVisiblePaths.slice(lo, hi + 1);
        // Anchor stays put — successive Shift+clicks re-range from the origin.
      });
    },

    clearSelection() {
      set((state) => {
        state.selection.selectedPaths = [];
        state.selection.anchorPath = null;
      });
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

    // -----------------------------------------------------------------------
    // Preview actions
    // -----------------------------------------------------------------------

    setSelectedFile(path) {
      set((state) => {
        state.preview.selectedFilePath = path;
      });
    },

    openFullRes(path) {
      set((state) => {
        state.preview.fullResPath = path;
      });
    },

    closeFullRes() {
      set((state) => {
        state.preview.fullResPath = null;
      });
    },

    // -----------------------------------------------------------------------
    // Execute dialog actions
    // -----------------------------------------------------------------------

    openExecuteDialog() {
      set((state) => {
        state.execute.executeOpen = true;
        state.execute.executeError = null;
        state.execute.lockConflict = null;
      });
    },

    closeExecuteDialog() {
      set((state) => {
        state.execute.executeOpen = false;
        state.execute.executeError = null;
        state.execute.lockConflict = null;
      });
    },

    async executeDecisions(opts = {}) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      // Capture the in-scope decided paths BEFORE the request so that if a
      // locked_paths 409 fires, "Unlocked Only" can filter them correctly.
      const scopedPaths: string[] = opts.scopePaths
        ? [...opts.scopePaths]
        : get().manifest.groups.flatMap((g) =>
            g.items
              .filter((f) => f.user_decision !== "")
              .map((f) => f.file_path)
          );

      set((state) => {
        state.execute.executeRunning = true;
        state.execute.executeError = null;
        state.execute.lockConflict = null;
      });

      try {
        const result = await postExecute({
          manifest_path: manifestPath,
          scope_paths: opts.scopePaths ?? null,
          recycle: true,
          force_locked: opts.forceLocked ?? false,
        });

        set((state) => {
          state.execute.executeRunning = false;
          state.execute.executeResult = result;
          // Replace groups from the authoritative server response.
          state.manifest.groups = result.groups;
          // Executed/removed rows leave the manifest — drop the stale selection.
          state.selection.selectedPaths = [];
          state.selection.anchorPath = null;
          // Surface prune prompt when singleton groups remain after execute.
          const singletons = result.groups
            .filter((g) => g.member_count === 1)
            .flatMap((g) => g.items.map((f) => f.file_path));
          if (singletons.length > 0) {
            state.execute.prunePrompt = { candidates: singletons };
          }
        });
      } catch (err) {
        if (err instanceof ApiConflictError) {
          if (err.code === "locked_paths") {
            set((state) => {
              state.execute.executeRunning = false;
              state.execute.lockConflict = {
                paths: err.lockedPaths ?? [],
                op: "execute",
                originalPaths: scopedPaths,
              };
            });
          } else {
            // execute_already_running or other 409
            set((state) => {
              state.execute.executeRunning = false;
              state.execute.executeError = err.message;
            });
          }
        } else {
          set((state) => {
            state.execute.executeRunning = false;
            state.execute.executeError =
              err instanceof Error ? err.message : String(err);
          });
        }
      }
    },

    async removeFromList(paths, forceLocked = false) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      try {
        const result = await postRemove({
          manifest_path: manifestPath,
          file_paths: paths,
          force_locked: forceLocked,
        });

        set((state) => {
          state.manifest.groups = result.groups;
          // Removed rows are gone — clear any selection referencing them.
          state.selection.selectedPaths = [];
          state.selection.anchorPath = null;
        });
      } catch (err) {
        if (err instanceof ApiConflictError && err.code === "locked_paths") {
          set((state) => {
            state.execute.lockConflict = {
              paths: err.lockedPaths ?? [],
              op: "remove",
              // Preserve the original path list so "Unlocked Only" can filter
              // out the locked paths and remove the rest.
              originalPaths: [...paths],
            };
          });
        } else {
          set((state) => {
            state.manifest.error =
              err instanceof Error ? err.message : String(err);
          });
        }
      }
    },

    async pruneSingletons(includeActioned = false) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      try {
        const result = await postPrune({
          manifest_path: manifestPath,
          include_actioned: includeActioned,
        });

        set((state) => {
          state.manifest.groups = result.groups;
          // Clear any stale prune prompt.
          state.execute.prunePrompt = null;
          // Pruned rows leave the manifest — drop the stale selection.
          state.selection.selectedPaths = [];
          state.selection.anchorPath = null;
        });
      } catch (err) {
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    async saveManifest(targetPath) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      try {
        await postSave({
          manifest_path: manifestPath,
          target_path: targetPath ?? null,
        });
      } catch (err) {
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    async revealInExplorer(path) {
      try {
        await postReveal({ file_path: path });
      } catch (err) {
        // Non-fatal: surface as a transient executeError.
        set((state) => {
          state.execute.executeError =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    // -----------------------------------------------------------------------
    // Action dialog actions (Set Action by Field / bulk-decide)
    // -----------------------------------------------------------------------

    openActionDialog() {
      set((state) => {
        state.action = {
          ...initialAction,
          actionDialogOpen: true,
        };
      });
    },

    closeActionDialog() {
      set((state) => {
        state.action.actionDialogOpen = false;
        state.action.actionError = null;
        state.action.actionRunning = false;
      });
    },

    setActionField(field) {
      set((state) => {
        state.action.field = field;
        // Clear preview when field changes.
        state.action.previewMatched = -1;
        state.action.previewSample = [];
        state.action.previewTruncated = false;
        state.action.actionError = null;
      });
    },

    setActionPattern(pattern) {
      set((state) => {
        state.action.pattern = pattern;
        // Clear stale preview on every pattern keystroke.
        state.action.previewMatched = -1;
        state.action.previewSample = [];
        state.action.previewTruncated = false;
        state.action.actionError = null;
      });
    },

    setActionAction(action) {
      set((state) => {
        state.action.action = action;
      });
    },

    async previewBulkDecide() {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;
      const { field, pattern, action: actionValue } = get().action;

      // Claim this preview's order. Only the latest preview is allowed to
      // commit — an out-of-order response from a superseded request must be
      // dropped, or the displayed count/sample could mis-state what Apply
      // touches (adversarial-review ship-blocker: the old component-side
      // seqRef guarded an already-committed store write, i.e. did nothing).
      const seq = ++_bulkDecideSeq;

      set((state) => {
        state.action.actionRunning = true;
        state.action.actionError = null;
      });

      try {
        const result = await bulkDecide({
          manifest_path: manifestPath,
          field,
          pattern,
          action: actionValue,
          preview: true,
        });

        if (seq !== _bulkDecideSeq) return; // superseded by a newer request
        set((state) => {
          state.action.actionRunning = false;
          state.action.previewMatched = result.matched;
          // Server may return up to a bounded sample; show all returned paths.
          state.action.previewSample = result.affected_paths;
          // If matched count exceeds the returned sample size, list is truncated.
          state.action.previewTruncated =
            result.matched > result.affected_paths.length;
        });
      } catch (err) {
        if (seq !== _bulkDecideSeq) return; // stale error — drop
        set((state) => {
          state.action.actionRunning = false;
          state.action.actionError =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    async applyBulkDecide(opts = {}) {
      const { forceLocked = false, skipLocked = false } = opts;
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;
      const { field, pattern, action: actionValue } = get().action;

      // A real apply supersedes any in-flight preview (groups are about to
      // change), so bump the sequence to invalidate pending preview responses.
      _bulkDecideSeq++;

      set((state) => {
        state.action.actionRunning = true;
        state.action.actionError = null;
      });

      try {
        const result = await bulkDecide({
          manifest_path: manifestPath,
          field,
          pattern,
          action: actionValue,
          force_locked: forceLocked,
          skip_locked: skipLocked,
          preview: false,
        });

        set((state) => {
          state.action.actionRunning = false;
          state.action.previewMatched = result.matched;
          state.action.previewSample = result.affected_paths;
          state.action.previewTruncated =
            result.matched > result.affected_paths.length;
          // Replace groups from the authoritative server response.
          state.manifest.groups = result.groups;
          // Bulk-decide may drop rows (ignore/remove) — clear the selection.
          state.selection.selectedPaths = [];
          state.selection.anchorPath = null;
        });
      } catch (err) {
        set((state) => {
          state.action.actionRunning = false;
        });
        // RE-THROW the locked-rows conflict so the component can open the
        // lock-confirm dialog (and retry with force_locked=true). Swallowing
        // it here made the 409 → Unlock & Apply flow unreachable in production
        // (adversarial-review ship-blocker, masked by a store-mocking test).
        if (err instanceof ApiConflictError && err.code === "locked_paths") {
          throw err;
        }
        set((state) => {
          state.action.actionError =
            err instanceof Error ? err.message : String(err);
        });
      }
    },

    clearActionError() {
      set((state) => {
        state.action.actionError = null;
      });
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
