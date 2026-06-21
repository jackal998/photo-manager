// Typed fetch wrappers for every endpoint in the 2B1 API contract.
// Base URL is same-origin in prod; set VITE_API_BASE for dev proxy override.

import type {
  DecisionValue,
  FsBrowseResponse,
  ManifestResponse,
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
