// Turn a raw API failure string into a user-worded sentence plus an optional
// technical detail line (copy audit S2).
//
// Before this, the status bar rendered the backend's developer text verbatim
// — "HTTP 422: Execute failed: [WinError 32] The process cannot access the
// file…" — in English, in both locales, as the WHOLE user-facing message.
// The routes raise a small, closed set of shapes (app/web/routes/execute.py,
// action.py, reveal), and api/client.ts flattens them into two string forms:
//
//   "409 Conflict: <code>"        — ApiConflictError.message
//   "HTTP <status>: <detail>"     — checkResponse()
//
// so the recognisable half maps onto a `web.error.*` sentence and the raw
// remainder is demoted to a secondary line. An unrecognised string is never
// swallowed: it becomes the detail of a generic sentence, so no information
// is lost and no failure renders as an empty alert.

export interface ApiErrorText {
  /** Translated, user-worded sentence. Never empty. */
  message: string;
  /** Raw server text, or null when there is nothing beyond the sentence. */
  detail: string | null;
}

type Translate = (
  key: string,
  fallback: string,
  params?: Record<string, string | number>
) => string;

const CONFLICT_RE = /^409 Conflict:\s*(\S+)\s*$/;
const HTTP_RE = /^HTTP (\d+):\s*([\s\S]*)$/;

/** 409 `detail.code` values the client already discriminates on. */
const CONFLICT_CODES: Record<string, [string, string]> = {
  locked_paths: ["web.error.locked_paths", "Some of the rows in scope are locked."],
  execute_already_running: [
    "web.error.already_running",
    "An action is already running — wait for it to finish.",
  ],
};

/**
 * `detail=f"<verb> failed: {exc}"` prefixes raised by the mutating routes.
 * Order matters only in that each prefix is unique; matching is exact.
 */
const DETAIL_PREFIXES: [string, string, string][] = [
  ["Execute failed:", "web.error.execute_failed", "The action could not be completed."],
  ["Remove failed:", "web.error.remove_failed", "The rows could not be taken off the list."],
  ["Prune failed:", "web.error.prune_failed", "The singleton groups could not be pruned."],
  [
    "Classify failed:",
    "web.error.classify_failed",
    "The singleton groups could not be examined.",
  ],
  ["Save failed:", "web.error.save_failed", "The manifest could not be saved."],
  [
    "Bulk decide failed:",
    "web.error.bulk_decide_failed",
    "The bulk action could not be applied.",
  ],
  [
    "Apply best copy failed:",
    "web.error.apply_best_copy_failed",
    "The best-copy decisions could not be applied.",
  ],
  [
    "Failed to launch Explorer:",
    "web.error.reveal_failed",
    "The file manager could not be opened.",
  ],
];

const UNKNOWN: [string, string] = ["web.error.unknown", "The request could not be completed."];
const NOT_FOUND: [string, string] = [
  "web.error.not_found",
  "The file or manifest could not be found.",
];

/** Non-empty trimmed string, or null. */
function orNull(s: string): string | null {
  const trimmed = s.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Map `raw` onto a translated sentence + optional technical detail.
 *
 * `raw` is whatever the store captured from `err.message`; callers pass the
 * bound `t` from useT(). Pure — exported for unit tests without React.
 */
export function describeApiError(raw: string, t: Translate): ApiErrorText {
  const conflict = CONFLICT_RE.exec(raw);
  if (conflict !== null) {
    const [key, fallback] = CONFLICT_CODES[conflict[1]] ?? UNKNOWN;
    return { message: t(key, fallback), detail: null };
  }

  const http = HTTP_RE.exec(raw);
  if (http !== null) {
    const status = http[1];
    const detail = http[2];
    for (const [prefix, key, fallback] of DETAIL_PREFIXES) {
      if (detail.startsWith(prefix)) {
        return {
          message: t(key, fallback),
          detail: orNull(detail.slice(prefix.length)),
        };
      }
    }
    if (status === "404") {
      const [key, fallback] = NOT_FOUND;
      return { message: t(key, fallback), detail: orNull(detail) };
    }
    // A {code, message} body already arrives human-readable (client.ts
    // prefers detail.message), so it IS the sentence — nothing to demote.
    return { message: t(UNKNOWN[0], UNKNOWN[1]), detail: orNull(detail) };
  }

  return { message: t(UNKNOWN[0], UNKNOWN[1]), detail: orNull(raw) };
}
