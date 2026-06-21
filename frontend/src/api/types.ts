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

/** The three server-allowlisted settings keys with their value types. */
export interface SettingsMap {
  "sorting.defaults": unknown;
  "ui.prune_singletons": unknown;
  "ui.scan_dialog.autotune_read_knee": unknown;
}
