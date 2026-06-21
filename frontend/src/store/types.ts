// Zustand store STATE + ACTIONS interface — types only, no implementation.
// The concrete store (useAppStore) imports this and satisfies it.

import type { DecisionValue, ExecuteResult, Group, SettingsMap, WebScanRequest } from "../api/types";

// ---------------------------------------------------------------------------
// State slices
// ---------------------------------------------------------------------------

export type ScanStatus = "idle" | "running" | "finished" | "failed" | "cancelled";

export interface ScanState {
  taskId: string | null;
  status: ScanStatus;
  stageName: string;
  completed: number;
  total: number;
  filesPerSec: number;
  log: string[];
  error: string | null;
  /** Manifest db path emitted by the "finished" SSE event. Null until finished. */
  outputPath: string | null;
}

export interface ManifestState {
  path: string | null;
  groups: Group[];
  totalGroups: number;
  totalFiles: number;
  loading: boolean;
  error: string | null;
}

export interface SettingsState {
  values: SettingsMap;
  loading: boolean;
}

// ---------------------------------------------------------------------------
// Phase 2C2 — preview state
// ---------------------------------------------------------------------------

export interface PreviewState {
  /** File path currently shown in the inline preview pane. Null = nothing selected. */
  selectedFilePath: string | null;
  /** File path open in the full-resolution dialog. Null = dialog closed. */
  fullResPath: string | null;
}

// ---------------------------------------------------------------------------
// Phase 2C2 — execute state
// ---------------------------------------------------------------------------

/** Describes a lock-conflict returned by POST /api/execute or /api/remove. */
export interface LockConflict {
  /** The paths the server reported as locked. */
  paths: string[];
  op: "execute" | "remove";
  /**
   * The original path list that triggered the conflict.
   * - execute: all decided paths that were in scope at call time (null means
   *   "all decided" — the store derives the list from manifest.groups).
   * - remove: the file_paths array that was passed to removeFromList.
   * Used by "Unlocked Only" to compute the filtered scope without re-hitting the
   * locked files.
   */
  originalPaths: string[] | null;
}

/** Prune prompt state (candidates to show in the prune confirmation). */
export interface PrunePrompt {
  /** Paths that would be pruned — shown in the confirmation dialog. */
  candidates: string[];
}

export interface ExecuteState {
  /** True when the execute dialog is open. */
  executeOpen: boolean;
  /** True while POST /api/execute (or a scoped variant) is in flight. */
  executeRunning: boolean;
  /** Last successful execute result. Null until first successful execute. */
  executeResult: ExecuteResult | null;
  /** Error message string from a failed execute (non-409). Null if none. */
  executeError: string | null;
  /** Set when a 409 locked_paths is returned by /api/execute or /api/remove. */
  lockConflict: LockConflict | null;
  /** Set when the UI wants to show a prune confirmation. */
  prunePrompt: PrunePrompt | null;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export interface AppActions {
  /**
   * POST /api/scan with the given request, set taskId, transition to "running",
   * and open the SSE stream via ingestScanEvent.
   */
  startScan(req: WebScanRequest): Promise<void>;

  /** POST /api/scan/{taskId}/cancel. */
  cancelScan(): Promise<void>;

  /**
   * Apply one SSE event from the scan stream into scan state.
   * Called for every `message` event received from EventSource.
   * Terminal events (finished / failed / completed_empty) update status.
   */
  ingestScanEvent(name: string, payload: unknown): void;

  /** Reset scan state back to "idle" (clears log, taskId, error). */
  resetScan(): void;

  /**
   * GET /api/manifest?path=<path>, set manifest state.
   * Side effect: server registers manifest roots so thumbnails resolve.
   */
  loadManifest(path: string): Promise<void>;

  /**
   * Optimistically apply a decision to the matching FileRow in manifest.groups,
   * then PATCH /api/decision.
   */
  setDecision(filePath: string, decision: DecisionValue): Promise<void>;

  /**
   * Optimistically apply a lock to the matching FileRow in manifest.groups,
   * then PATCH /api/lock.
   */
  setLock(filePath: string, locked: boolean): Promise<void>;

  /** GET /api/settings and populate settings.values. */
  loadSettings(): Promise<void>;

  /** PATCH /api/settings with the given updates map. */
  saveSettings(updates: Partial<SettingsMap>): Promise<void>;

  // -------------------------------------------------------------------------
  // Phase 2C2 — preview actions
  // -------------------------------------------------------------------------

  /** Set the file path for the inline preview pane. Pass null to clear. */
  setSelectedFile(path: string | null): void;

  /** Open the full-resolution dialog for the given file path. */
  openFullRes(path: string): void;

  /** Close the full-resolution dialog. */
  closeFullRes(): void;

  // -------------------------------------------------------------------------
  // Phase 2C2 — execute dialog actions
  // -------------------------------------------------------------------------

  /** Open the execute dialog. */
  openExecuteDialog(): void;

  /** Close the execute dialog and clear transient state (error, lockConflict). */
  closeExecuteDialog(): void;

  /**
   * POST /api/execute with the manifest path from store.
   *
   * - On success: sets executeResult, replaces manifest.groups from response.groups,
   *   clears executeRunning.
   * - On 409 locked_paths: sets lockConflict (with op="execute"), clears executeRunning.
   * - On 409 execute_already_running: sets executeError, clears executeRunning.
   * - On other errors: sets executeError, clears executeRunning.
   */
  executeDecisions(opts?: { scopePaths?: string[]; forceLocked?: boolean }): Promise<void>;

  /**
   * POST /api/remove for the given file paths.
   *
   * - On success: replaces manifest.groups from response.groups.
   * - On 409 locked_paths: sets lockConflict (with op="remove").
   */
  removeFromList(paths: string[], forceLocked?: boolean): Promise<void>;

  /**
   * POST /api/prune to remove singleton groups from the manifest.
   * Replaces manifest.groups from response.groups on success.
   */
  pruneSingletons(includeActioned?: boolean): Promise<void>;

  /**
   * POST /api/save to persist manifest decisions in-place or to a new path.
   */
  saveManifest(targetPath?: string): Promise<void>;

  /**
   * POST /api/reveal to open Explorer at the given file.
   * Non-fatal: surfaces 403 / 501 as a transient executeError message.
   */
  revealInExplorer(path: string): Promise<void>;
}

// ---------------------------------------------------------------------------
// Combined store interface
// ---------------------------------------------------------------------------

export interface AppStore extends AppActions {
  scan: ScanState;
  manifest: ManifestState;
  settings: SettingsState;
  preview: PreviewState;
  execute: ExecuteState;
}
