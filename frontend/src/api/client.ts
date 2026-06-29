// Typed fetch wrappers for every endpoint in the 2B1 API contract.
// Base URL is same-origin in prod; set VITE_API_BASE for dev proxy override.

import type {
  BulkDecideRequest,
  BulkDecideResult,
  DecisionValue,
  ExecuteRequest,
  ExecuteResult,
  FsBrowseResponse,
  I18nResponse,
  LockedPathsError,
  ManifestResponse,
  PruneCandidatesRequest,
  PruneCandidatesResult,
  PruneRequest,
  PruneResult,
  RemoveRequest,
  RemoveResult,
  RevealRequest,
  RevealResult,
  SaveRequest,
  SaveResult,
  SettingsMap,
  WebScanRequest,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Throw an Error with status + server detail message on non-2xx responses. */
async function checkResponse(res: Response): Promise<void> {
  if (res.ok) return;
  let detail: string = res.statusText;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else detail = JSON.stringify(body.detail);
  } catch {
    // body was not JSON; keep statusText
  }
  throw new Error(`HTTP ${res.status}: ${detail}`);
}

/** POST JSON, return parsed JSON typed as T. */
async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await checkResponse(res);
  return res.json() as Promise<T>;
}

/** GET, return parsed JSON typed as T. */
async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  await checkResponse(res);
  return res.json() as Promise<T>;
}

/** PATCH JSON, return parsed JSON typed as T. */
async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await checkResponse(res);
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// POST /api/scan
// ---------------------------------------------------------------------------

export interface StartScanResult {
  task_id: string;
}

export async function startScan(req: WebScanRequest): Promise<StartScanResult> {
  return postJson<StartScanResult>("/api/scan", req);
}

// ---------------------------------------------------------------------------
// POST /api/scan/{id}/cancel
// ---------------------------------------------------------------------------

export interface CancelScanResult {
  status: "cancel_requested";
}

export async function cancelScan(taskId: string): Promise<CancelScanResult> {
  return postJson<CancelScanResult>(`/api/scan/${taskId}/cancel`, {});
}

// ---------------------------------------------------------------------------
// GET /api/scan/{id}/events  — SSE stream URL helper (not a fetch wrapper)
// ---------------------------------------------------------------------------

/** Returns the URL string to pass to `new EventSource(...)`. */
export function scanEventsUrl(taskId: string): string {
  return `${BASE}/api/scan/${taskId}/events`;
}

// ---------------------------------------------------------------------------
// GET /api/manifest?path=<path>
// ---------------------------------------------------------------------------

export async function getManifest(path: string): Promise<ManifestResponse> {
  return getJson<ManifestResponse>(
    `/api/manifest?path=${encodeURIComponent(path)}`
  );
}

// ---------------------------------------------------------------------------
// PATCH /api/decision
// ---------------------------------------------------------------------------

export interface DecisionEntry {
  file_path: string;
  decision: DecisionValue;
}

export interface PatchDecisionsResult {
  updated: number;
}

export async function patchDecisions(
  manifestPath: string,
  decisions: DecisionEntry[]
): Promise<PatchDecisionsResult> {
  return patchJson<PatchDecisionsResult>("/api/decision", {
    manifest_path: manifestPath,
    decisions,
  });
}

// ---------------------------------------------------------------------------
// PATCH /api/lock
// ---------------------------------------------------------------------------

export interface LockEntry {
  file_path: string;
  locked: boolean;
}

export interface PatchLocksResult {
  updated: number;
}

export async function patchLocks(
  manifestPath: string,
  locks: LockEntry[]
): Promise<PatchLocksResult> {
  return patchJson<PatchLocksResult>("/api/lock", {
    manifest_path: manifestPath,
    locks,
  });
}

// ---------------------------------------------------------------------------
// GET /api/settings
// ---------------------------------------------------------------------------

export async function getSettings(): Promise<SettingsMap> {
  return getJson<SettingsMap>("/api/settings");
}

// ---------------------------------------------------------------------------
// PATCH /api/settings
// ---------------------------------------------------------------------------

export interface PatchSettingsResult {
  updated: number;
}

export async function patchSettings(
  updates: Partial<SettingsMap>
): Promise<PatchSettingsResult> {
  return patchJson<PatchSettingsResult>("/api/settings", { updates });
}

// ---------------------------------------------------------------------------
// GET /api/fs/browse?path=<dir|empty>
// ---------------------------------------------------------------------------

export async function browseFs(path?: string): Promise<FsBrowseResponse> {
  const qs = path !== undefined ? `?path=${encodeURIComponent(path)}` : "";
  return getJson<FsBrowseResponse>(`/api/fs/browse${qs}`);
}

// ---------------------------------------------------------------------------
// Phase 2C1 — file-mutating routes
// ---------------------------------------------------------------------------

/**
 * Typed error thrown when the server returns HTTP 409 on the execute/remove
 * routes.  Two discriminated variants mirror the backend detail shapes:
 *   code "locked_paths"          — LockedPathsError
 *   code "execute_already_running" — plain {code}
 *
 * Callers check `err instanceof ApiConflictError` and inspect `err.code`.
 */
export class ApiConflictError extends Error {
  readonly code: string;
  readonly lockedPaths: string[] | null;
  // The FULL matched count carried by the bulk-decide 409 (#674); null on the
  // execute/remove 409s which don't send it. Lets the ActionDialog size the
  // unlocked subset without a stale preview.
  readonly matchedTotal: number | null;

  constructor(
    code: string,
    lockedPaths: string[] | null = null,
    matchedTotal: number | null = null,
  ) {
    super(`409 Conflict: ${code}`);
    this.name = "ApiConflictError";
    this.code = code;
    this.lockedPaths = lockedPaths;
    this.matchedTotal = matchedTotal;
  }
}

/**
 * POST JSON with special handling for 409 responses:
 * parses the detail body and throws ApiConflictError instead of a generic Error.
 */
async function postJsonOrConflict<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (res.status === 409) {
    let code = "conflict";
    let lockedPaths: string[] | null = null;
    let matchedTotal: number | null = null;
    try {
      const errBody = (await res.json()) as { detail?: unknown };
      const detail = errBody.detail as LockedPathsError | { code: string } | undefined;
      if (detail && typeof detail === "object" && "code" in detail) {
        code = detail.code;
        if (code === "locked_paths" && "locked_paths" in detail) {
          lockedPaths = (detail as LockedPathsError).locked_paths;
          // matched_total is bulk-decide-only (#674); execute/remove omit it.
          const mt = (detail as LockedPathsError).matched_total;
          if (typeof mt === "number") {
            matchedTotal = mt;
          }
        }
      }
    } catch {
      // body not JSON; keep defaults
    }
    throw new ApiConflictError(code, lockedPaths, matchedTotal);
  }

  await checkResponse(res);
  return res.json() as Promise<T>;
}

// POST /api/execute

export async function postExecute(req: ExecuteRequest): Promise<ExecuteResult> {
  return postJsonOrConflict<ExecuteResult>("/api/execute", req);
}

// POST /api/remove

export async function postRemove(req: RemoveRequest): Promise<RemoveResult> {
  return postJsonOrConflict<RemoveResult>("/api/remove", req);
}

// POST /api/prune

export async function postPrune(req: PruneRequest): Promise<PruneResult> {
  return postJson<PruneResult>("/api/prune", req);
}

// POST /api/prune/candidates (#686)

export async function postPruneCandidates(
  req: PruneCandidatesRequest
): Promise<PruneCandidatesResult> {
  return postJson<PruneCandidatesResult>("/api/prune/candidates", req);
}

// POST /api/save

export async function postSave(req: SaveRequest): Promise<SaveResult> {
  return postJson<SaveResult>("/api/save", req);
}

// POST /api/reveal

export async function postReveal(req: RevealRequest): Promise<RevealResult> {
  return postJson<RevealResult>("/api/reveal", req);
}

// POST /api/action/bulk-decide

export async function bulkDecide(req: BulkDecideRequest): Promise<BulkDecideResult> {
  return postJsonOrConflict<BulkDecideResult>("/api/action/bulk-decide", req);
}

// ---------------------------------------------------------------------------
// GET /api/i18n/{locale}
// ---------------------------------------------------------------------------

export { type I18nResponse } from "./types";

export async function getI18n(locale: string): Promise<I18nResponse> {
  return getJson<I18nResponse>("/api/i18n/" + locale);
}

// ---------------------------------------------------------------------------
// GET /api/image — URL helpers (not fetch wrappers; caller passes to <img src>)
// ---------------------------------------------------------------------------

/**
 * Returns the URL for a thumbnail image (size > 0).
 * Mirrors the thumbnail_url field already embedded in FileRow, but useful
 * when a component needs to construct the URL independently.
 */
export function thumbnailUrl(path: string, size: number = 128): string {
  return `${BASE}/api/image?path=${encodeURIComponent(path)}&size=${size}`;
}

/**
 * Returns the full-resolution image URL (size=0).
 * Callers must handle a 413 response (file_too_large_for_full_res) by
 * catching the load error on the <img> element.
 */
export function fullResUrl(path: string): string {
  return `${BASE}/api/image?path=${encodeURIComponent(path)}&size=0`;
}

/**
 * Returns the URL for the raw media stream (GET /api/media).
 * Used by <video src=...> for video rows. The server supports HTTP Range
 * so native browser video controls (seek, scrub) work correctly.
 */
export function mediaUrl(path: string): string {
  return `${BASE}/api/media?path=${encodeURIComponent(path)}`;
}
