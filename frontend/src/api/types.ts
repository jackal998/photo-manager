// API boundary types mirroring the photo-manager backend contract.
// Keep in sync with app/web/models.py and app/web/routes/*.py.

// ---------------------------------------------------------------------------
// Core data shapes
// ---------------------------------------------------------------------------

export type SimilarityKind = "percent" | "ref" | "passenger" | "near_dup" | "none";

export interface Similarity {
  kind: SimilarityKind;
  percent: number | null;
}

export interface FileRow {
  file_path: string;
  basename: string;
  folder: string;
  action: string;
  user_decision: "" | "delete" | "ignore";
  is_locked: boolean;
  is_ref_winner: boolean;
  similarity: Similarity;
  score: number | null;
  file_size_bytes: number;
  pixel_width: number | null;
  pixel_height: number | null;
  shot_date: string | null;
  creation_date: string | null;
  phash: string | null;
  hamming_distance: number | null;
  thumbnail_url: string;
  /** Added V1 video-playback: "image" for photos, "video" for video files. */
  media_type?: "image" | "video";
}

export interface Group {
  group_number: number;
  member_count: number;
  items: FileRow[];
}

export interface ManifestResponse {
  manifest_path: string;
  groups: Group[];
  total_groups: number;
  total_files: number;
}

export type DecisionValue = "" | "delete" | "ignore";

// ---------------------------------------------------------------------------
// SSE event payloads (discriminated union keyed by event name)
// ---------------------------------------------------------------------------

export interface ScanLogEvent {
  event: "log";
  msg: string;
}

export interface ScanStageEvent {
  event: "stage";
  stage_name: string;
  completed: number;
  total: number;
  files_per_sec: number;
}

export interface ScanFinishedEvent {
  event: "finished";
  output_path: string;
}

export interface ScanFailedEvent {
  event: "failed";
  msg: string;
}

export interface ScanCompletedEmptyEvent {
  event: "completed_empty";
}

export interface ScanHashPoolMeasuredEvent {
  event: "hash_pool_measured";
  // Informational payload — surface in the log; shape varies by pool type.
  [key: string]: unknown;
}

export interface ScanReadKneeMeasuredEvent {
  event: "read_knee_measured";
  device: string;
  knee: number;
  // Additional fields may be present; forward-compatible.
  [key: string]: unknown;
}

/** Discriminated union of all SSE event types. Discriminant is `event`. */
export type ScanEvent =
  | ScanLogEvent
  | ScanStageEvent
  | ScanFinishedEvent
  | ScanFailedEvent
  | ScanCompletedEmptyEvent
  | ScanHashPoolMeasuredEvent
  | ScanReadKneeMeasuredEvent;

// ---------------------------------------------------------------------------
// POST /api/scan request body
// ---------------------------------------------------------------------------

export interface WebScanRequest {
  sources: Record<string, string>;
  output_path: string;
  recursive_map?: Record<string, boolean>;
  threshold?: number;
  mean_color_threshold?: number;
  dhash_threshold?: number;
  limit?: number;
  workers?: number;
  exif_workers?: number;
  hash_pool?: string;
  auto_select_enabled?: boolean;
  auto_select_aggressive_delete?: boolean;
  autotune_read_knee?: boolean;
  autotune_knees?: Record<string, number>;
}

// ---------------------------------------------------------------------------
// GET /api/fs/browse response
// ---------------------------------------------------------------------------

export interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  is_manifest: boolean;
}

export interface FsBrowseResponse {
  path: string;
  parent: string | null;
  entries: FsEntry[];
}

// ---------------------------------------------------------------------------
// GET /api/settings response / PATCH /api/settings request
// ---------------------------------------------------------------------------

/** The server-allowlisted settings keys with their value types. */
export interface SettingsMap {
  "sorting.defaults": unknown;
  "ui.prune_singletons": unknown;
  "ui.scan_dialog.autotune_read_knee": unknown;
  "ui.locale"?: string | null;
  /** ScanDialog source persistence (#678-E / s23a/s23b): [{path, recursive}, ...]. */
  "sources.list"?: unknown;
  /** ScanDialog output-path persistence (#678-E / s23a/s23b). */
  "sources.output"?: string | null;
}

// ---------------------------------------------------------------------------
// GET /api/i18n/{locale}
// ---------------------------------------------------------------------------

export interface I18nResponse {
  locale: string;
  strings: Record<string, string>;
  available: [string, string][];
}

// ---------------------------------------------------------------------------
// Phase 2C1 — file-mutating route request / response types.
// Mirrors app/web/models.py and app/web/routes/execute.py exactly.
// ---------------------------------------------------------------------------

// POST /api/execute

export interface ExecuteRequest {
  manifest_path: string;
  scope_paths?: string[] | null;
  recycle?: boolean;
  force_locked?: boolean;
}

export interface ExecuteResult {
  success_paths: string[];
  /** Each element is a [path, reason] pair. */
  failed: [string, string][];
  ignored: string[];
  missing: string[];
  db_write_failed: string[];
  log_path: string | null;
  groups: Group[];
}

// POST /api/remove

export interface RemoveRequest {
  manifest_path: string;
  file_paths: string[];
  force_locked?: boolean;
}

export interface RemoveResult {
  removed: number;
  groups: Group[];
}

// POST /api/prune

export interface PruneRequest {
  manifest_path: string;
  include_actioned?: boolean;
  /**
   * Explicit-list prune (#686): when set, prune EXACTLY these paths (intersected
   * server-side with the actual singletons, under-roots, unlocked) instead of by
   * category. Mirrors the Qt desktop's `_apply_singleton_prune(to_prune)`. When
   * omitted, the server keeps the category behaviour (all plain + optionally all
   * actioned via `include_actioned`).
   */
  paths?: string[];
}

export interface PruneResult {
  pruned: string[];
  locked_skipped: string[];
  groups: Group[];
}

// POST /api/prune/candidates (#686)

export interface PruneCandidatesRequest {
  manifest_path: string;
}

export interface PruneCandidatesResult {
  plain: string[];
  actioned: string[];
  locked: string[];
}

// POST /api/save

export interface SaveRequest {
  manifest_path: string;
  target_path?: string | null;
}

export interface SaveResult {
  saved_to: string;
  updated: number;
}

// POST /api/reveal

export interface RevealRequest {
  file_path: string;
}

export interface RevealResult {
  status: string;
}

// ---------------------------------------------------------------------------
// 409 error body shapes from the execute routes.
// The server returns { detail: { code, locked_paths? } } on 409.
// ---------------------------------------------------------------------------

export interface LockedPathsError {
  code: "locked_paths";
  locked_paths: string[];
  // Present on the bulk-decide route (#674): the FULL matched count, so the
  // ActionDialog can size the unlocked subset (matched_total - locked_paths)
  // without a separate preview. Absent on the execute/remove 409s.
  matched_total?: number;
}

export interface ExecuteAlreadyRunningError {
  code: "execute_already_running";
}

// ---------------------------------------------------------------------------
// POST /api/action/bulk-decide request / response types.
// ---------------------------------------------------------------------------

export interface BulkDecideRequest {
  manifest_path: string;
  field: string;
  pattern: string;
  action: string;
  force_locked?: boolean;
  // #674 — apply the decision to the unlocked subset only (mutually exclusive
  // with force_locked). Omitted/false on every other call.
  skip_locked?: boolean;
  preview?: boolean;
}

export interface BulkDecideResult {
  matched: number;
  affected_paths: string[];
  /** "decision" | "lock" | "unlock" — the action that was applied (or would be applied in preview mode). */
  action_applied: string;
  groups: Group[];
}
