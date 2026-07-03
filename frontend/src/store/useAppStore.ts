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
  postPruneCandidates,
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
  PreviewMode,
  PreviewState,
  ResultViewState,
  ScanState,
  SelectionState,
  SettingsState,
} from "./types";
import { loadColumnWidths, saveColumnWidths } from "../lib/columnWidths";
import { MIN_COLUMN_WIDTH, type ColumnId } from "../lib/resultColumns";
import { loadPanelWidths, savePanelWidths, clampPanelWidth } from "../lib/panelWidths";
import { normalizePrunePref } from "../lib/prune";
import type { PrunePref } from "../lib/prune";
import { ACTION_DIALOG_FIELD_OPTIONS } from "../lib/actionDialogFields";

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
  selectedGroupId: null,
};

const initialSelection: SelectionState = {
  selectedPaths: [],
  anchorPath: null,
  scrollToPath: null,
};

// sortColumn null = no sort = server order (the default every result-tree
// scenario relies on). columnWidths / panelWidths hydrate from localStorage
// (s47 / s39 cross-launch persistence) merged over the defaults — read once
// at store creation.
const initialResultView: ResultViewState = {
  sortColumn: null,
  sortDirection: "asc",
  columnWidths: loadColumnWidths(),
  panelWidths: loadPanelWidths(),
};

const initialExecute: ExecuteState = {
  executeOpen: false,
  executeRunning: false,
  executeResult: null,
  executeError: null,
  lockConflict: null,
  prunePrompt: null,
  prunePending: null,
  scopeGroupNumbers: null,
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
    resultView: {
      ...initialResultView,
      columnWidths: { ...initialResultView.columnWidths },
      panelWidths: { ...initialResultView.panelWidths },
    },
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

    async loadManifest(path, opts) {
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
          // A fresh manifest invalidates any prior selection (paths may vanish),
          // so by default we clear it. The post-scan auto-select call site
          // (opts.selectKeepers) instead selects the action==="KEEP" rows and
          // scrolls the first into view — Qt #239 parity. When auto-select was
          // off there are no KEEP rows, so keeperPaths is empty and this still
          // clears, matching the default.
          const keeperPaths = opts?.selectKeepers
            ? data.groups
                .flatMap((g) => g.items)
                .filter((row) => row.action === "KEEP")
                .map((row) => row.file_path)
            : [];
          state.selection.selectedPaths = keeperPaths;
          state.selection.anchorPath =
            keeperPaths.length > 0 ? keeperPaths[keeperPaths.length - 1] : null;
          state.selection.scrollToPath =
            keeperPaths.length > 0 ? keeperPaths[0] : null;
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
      // Routes through setDecisions so the 409 locked_paths handling (#733)
      // isn't duplicated between the single-path and batch entry points.
      await get().setDecisions([filePath], decision);
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

    async setDecisions(paths, decision, opts = {}) {
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
          paths.map((p) => ({ file_path: p, decision })),
          { forceLocked: opts.forceLocked ?? false }
        );
      } catch (err) {
        // Revert the optimistic apply on EVERY failure (conflict or not) —
        // mirrors removeFromList's 409 flow.
        for (const [p, prev] of previous) {
          applyFileRowPatch(set as ImmerSetFn, p, (row) => {
            row.user_decision = prev;
          });
        }
        if (err instanceof ApiConflictError && err.code === "locked_paths") {
          set((state) => {
            state.execute.lockConflict = {
              paths: err.lockedPaths ?? [],
              op: "decision",
              // Preserve the original path list so "Unlocked Only" can filter
              // out the locked paths and re-apply the decision to the rest.
              originalPaths: [...paths],
              pendingDecision: decision,
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

    async setLocks(paths, locked) {
      const manifestPath = get().manifest.path;
      // A no-op (nothing to lock) is a success, not a failure — callers that
      // branch on the result must not treat an empty list as a rejection.
      if (manifestPath === null || paths.length === 0) return true;

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
        return true;
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
        return false;
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

    clearScrollTarget() {
      set((state) => {
        state.selection.scrollToPath = null;
      });
    },

    // -----------------------------------------------------------------------
    // #685 — result-tree column model (sort + widths)
    // -----------------------------------------------------------------------

    toggleSort(column: ColumnId) {
      set((state) => {
        if (state.resultView.sortColumn === column) {
          // Repeat click on the active column flips direction (asc <-> desc).
          state.resultView.sortDirection =
            state.resultView.sortDirection === "asc" ? "desc" : "asc";
        } else {
          // New column always starts ascending (Qt first-click semantics).
          state.resultView.sortColumn = column;
          state.resultView.sortDirection = "asc";
        }
      });
    },

    setColumnWidth(column: ColumnId, width: number, persist = true) {
      // Clamp defensively so a fast/overshooting drag can never persist a
      // 0 / negative width that would make the column un-grabbable.
      const clamped = Math.max(MIN_COLUMN_WIDTH, Math.round(width));
      set((state) => {
        state.resultView.columnWidths[column] = clamped;
      });
      // A resize drag passes persist=false per mousemove (live feedback only) and
      // persist=true once on mouseup — avoids a synchronous localStorage write per
      // move. Read back the committed state so the blob matches the store exactly.
      if (persist) {
        saveColumnWidths({ ...get().resultView.columnWidths });
      }
    },

    // -----------------------------------------------------------------------
    // #739 — preview-panel resize + persistence (mirrors setColumnWidth)
    // -----------------------------------------------------------------------

    setPreviewWidth(width: number, persist = true) {
      // Clamp to [MIN_PANEL_WIDTH, 60% of the viewport] so a fast/overshooting
      // drag can never persist a 0-width preview pane or swallow the whole tree.
      const clamped = clampPanelWidth(width);
      set((state) => {
        state.resultView.panelWidths.preview = clamped;
      });
      // A resize drag passes persist=false per mousemove (live feedback only) and
      // persist=true once on mouseup — avoids a synchronous localStorage write per
      // move. Read back the committed state so the blob matches the store exactly.
      if (persist) {
        savePanelWidths({ ...get().resultView.panelWidths });
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

    // -----------------------------------------------------------------------
    // Preview actions
    // -----------------------------------------------------------------------

    setSelectedFile(path) {
      set((state) => {
        state.preview.selectedFilePath = path;
        // Selecting a file clears any group selection — the two are mutually
        // exclusive (mirrors Qt: FILE-row click → show_single clears the grid).
        state.preview.selectedGroupId = null;
      });
    },

    setSelectedGroup(groupId) {
      set((state) => {
        state.preview.selectedGroupId = groupId;
        // Selecting a group clears the file selection — mutually exclusive
        // (mirrors Qt: GROUP-row click → show_grid clears the single-file view).
        state.preview.selectedFilePath = null;
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

    openExecuteDialog(scopePaths = null) {
      set((state) => {
        state.execute.executeOpen = true;
        state.execute.executeError = null;
        state.execute.lockConflict = null;
        // "Execute (only selected)": expand the selected paths to their whole
        // groups (#430 group-pull) and scope the dialog to those group numbers.
        // An empty/absent selection opens unscoped (scopeGroupNumbers = null).
        if (scopePaths && scopePaths.length > 0) {
          const wanted = new Set(scopePaths);
          const touched = new Set<number>();
          for (const group of state.manifest.groups) {
            if (group.items.some((item) => wanted.has(item.file_path))) {
              touched.add(group.group_number);
            }
          }
          state.execute.scopeGroupNumbers =
            touched.size > 0 ? [...touched] : null;
        } else {
          state.execute.scopeGroupNumbers = null;
        }
      });
    },

    closeExecuteDialog() {
      set((state) => {
        state.execute.executeOpen = false;
        state.execute.executeError = null;
        state.execute.lockConflict = null;
        state.execute.scopeGroupNumbers = null;
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
        });
        // Offer / auto-run a singleton prune now the groups reflect the execute
        // (the Qt `_maybe_offer_singleton_prune` tail).
        await get().maybeOfferPrune();
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
        // #686 — a finalizing remove can collapse groups to singletons, so
        // offer / auto-run a prune (the desktop "Remove from List" parity).
        await get().maybeOfferPrune();
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

    async maybeOfferPrune() {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      // The web review view drops single-member groups (manifest_repository
      // orphan-skip), so the FE never sees singletons in its own groups (unlike
      // the Qt in-memory vm). Ask the backend, which classifies via SQL. Fail
      // safe — no offer if the classification can't be fetched.
      let plain: string[];
      let actioned: string[];
      let locked: string[];
      try {
        const candidates = await postPruneCandidates({ manifest_path: manifestPath });
        ({ plain, actioned, locked } = candidates);
      } catch {
        return;
      }
      if (plain.length === 0 && actioned.length === 0 && locked.length === 0) {
        return; // no singletons — nothing to offer
      }

      // Authoritative pref: fetch fresh (mirrors Qt reading settings.json each
      // time). The store's settings.values can be stale — the in-app
      // SettingsDialog is the only writer and QA PATCHes the server out-of-band.
      let pref: PrunePref;
      try {
        const values = await getSettings();
        pref = normalizePrunePref(values["ui.prune_singletons"]);
        set((state) => {
          state.settings.values = values;
        });
      } catch {
        pref = normalizePrunePref(get().settings.values["ui.prune_singletons"]);
      }
      if (pref === "never") return;

      // D6: locked singletons must pass the LockConfirmDialog gate FIRST, on
      // BOTH the "ask" and "always" paths — the standing "always" instruction
      // does not bypass the lock confirmation.
      if (locked.length > 0) {
        set((state) => {
          state.execute.prunePending = { plain, actioned, pref };
          state.execute.lockConflict = {
            paths: locked,
            op: "prune",
            originalPaths: null,
          };
        });
        return;
      }

      if (pref === "always") {
        // Sweep both unlocked buckets in one explicit-paths call.
        await get().applyPrune([...plain, ...actioned]);
        return;
      }

      // "ask" with no locked singletons — open the prune dialog for the
      // unlocked buckets (guaranteed non-empty: we returned early if all three
      // buckets were empty, and locked is empty on this branch).
      set((state) => {
        state.execute.prunePrompt = { plain, actioned, lockedToPrune: [] };
      });
    },

    async applyPrune(paths) {
      const manifestPath = get().manifest.path;
      if (manifestPath === null) return;

      if (paths.length === 0) {
        // Nothing to prune (e.g. Keep-all with no lock-confirmed locked) —
        // just clear the transient prune state.
        set((state) => {
          state.execute.prunePrompt = null;
          state.execute.prunePending = null;
        });
        return;
      }

      try {
        const result = await postPrune({
          manifest_path: manifestPath,
          paths,
        });

        set((state) => {
          state.manifest.groups = result.groups;
          state.execute.prunePrompt = null;
          state.execute.prunePending = null;
          // Pruned rows leave the manifest — drop the stale selection.
          state.selection.selectedPaths = [];
          state.selection.anchorPath = null;
        });
      } catch (err) {
        set((state) => {
          state.manifest.error =
            err instanceof Error ? err.message : String(err);
          state.execute.prunePrompt = null;
          state.execute.prunePending = null;
        });
      }
    },

    async resolvePruneLock(verdict) {
      const pending = get().execute.prunePending;
      const locked = get().execute.lockConflict?.paths ?? [];
      // Close the lock dialog.
      set((state) => {
        state.execute.lockConflict = null;
      });
      if (pending === null) return; // defensive — gate resolved without context

      // Close the gate now: the verdict is being resolved, so prunePending must
      // not survive any early return below (a failed unlock) and re-fire later.
      const { plain, actioned, pref } = pending;
      set((state) => {
        state.execute.prunePending = null;
      });

      let lockedToPrune: string[] = [];
      if (verdict === "unlock-apply") {
        // Unlock the locked singletons so the backend prune won't skip them,
        // then commit them to the prune set (the Qt to_prune.extend tail — they
        // prune regardless of any later "Keep all" on the prune dialog).
        const unlocked = await get().setLocks(locked, false);
        if (unlocked) {
          lockedToPrune = locked;
        } else {
          // The unlock PATCH failed and setLocks reverted the rows, so they are
          // still locked and can't be pruned — hold them back (lockedToPrune
          // stays []) rather than fold a still-locked path the backend would
          // only skip. The unrelated unlocked buckets still prune as the user
          // asked; surface a prune-context error for the held items. (PATCH
          // /api/lock and POST /api/prune are independent endpoints — a failed
          // unlock does not imply the prune would fail, so we don't abort the
          // whole gesture, only drop the rows we couldn't unlock.)
          set((state) => {
            state.manifest.error =
              "Couldn't unlock the locked items — they were held back from pruning.";
          });
        }
      }
      // "unlocked-only" / "cancel": hold the locked singletons (lockedToPrune=[]).

      if (pref === "always") {
        await get().applyPrune([...plain, ...actioned, ...lockedToPrune]);
        return;
      }

      // "ask": if there are unlocked buckets to confirm, open the dialog (the
      // lock-confirmed locked fold in on Remove OR Keep). If the unlocked
      // buckets are empty there is nothing to ask — prune the already-decided
      // set (just lockedToPrune, possibly empty) directly.
      if (plain.length === 0 && actioned.length === 0) {
        await get().applyPrune(lockedToPrune);
        return;
      }
      set((state) => {
        state.execute.prunePrompt = { plain, actioned, lockedToPrune };
      });
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

    openActionDialog(initialField) {
      set((state) => {
        state.action = {
          ...initialAction,
          actionDialogOpen: true,
          // #735: guard against non-string / unrecognised values — callers
          // wired straight to a DOM event handler (e.g. onClick={openActionDialog})
          // pass the event object as the first arg, which must fall back to
          // the default field rather than corrupt the dialog's <select>.
          ...(typeof initialField === "string" &&
          ACTION_DIALOG_FIELD_OPTIONS.has(initialField)
            ? { field: initialField }
            : {}),
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
// Derived selectors — exported for use in components
// ---------------------------------------------------------------------------

/**
 * Derive the preview mode from the current preview state.
 *
 * - 'grid'   — a group is selected (selectedGroupId non-null)
 * - 'single' — a file is selected (selectedFilePath non-null)
 * - 'empty'  — nothing selected
 *
 * The two fields are mutually exclusive (setSelectedFile clears selectedGroupId
 * and vice versa), so only one branch ever fires. The 'grid' check comes first
 * so a stale selectedFilePath (which setSelectedGroup clears) can't accidentally
 * produce 'single'.
 *
 * Usage in a component:
 *   const mode = useAppStore(selectPreviewMode);
 */
export function selectPreviewMode(state: AppStore): PreviewMode {
  // Use loose != to treat undefined the same as null. Tests call
  // useAppStore.setState with a partial preview object that may omit
  // selectedGroupId; strict !== would treat the missing field as non-null
  // and incorrectly return 'grid'.
  if (state.preview.selectedGroupId != null) return "grid";
  if (state.preview.selectedFilePath != null) return "single";
  return "empty";
}

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
