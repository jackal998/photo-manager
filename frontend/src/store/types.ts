// Zustand store STATE + ACTIONS interface — types only, no implementation.
// The concrete store (useAppStore) imports this and satisfies it.

import type { DecisionValue, Group, SettingsMap, WebScanRequest } from "../api/types";

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
}

// ---------------------------------------------------------------------------
// Combined store interface
// ---------------------------------------------------------------------------

export interface AppStore extends AppActions {
  scan: ScanState;
  manifest: ManifestState;
  settings: SettingsState;
}
