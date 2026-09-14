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

/**
 * Every `detail.code` the backend attaches, mapped to its catalog sentence.
 *
 * The first two arrive on a 409 through `ApiConflictError`; the other four
 * ride on 4xx/5xx bodies (`_path_guard.py:56,64,73`, `execute.py:386,396`,
 * `action.py:86`) and are recovered below — see `codeFromDetail`.
 */
const ERROR_CODES: Record<string, [string, string]> = {
  locked_paths: ["web.error.locked_paths", "Some of the rows in scope are locked."],
  execute_already_running: [
    "web.error.already_running",
    "An action is already running — wait for it to finish.",
  ],
  permission_denied: [
    "web.error.permission_denied",
    "This app is not allowed to reach that location.",
  ],
  platform_unsupported: [
    "web.error.platform_unsupported",
    "That action is not available on this operating system.",
  ],
  bad_request: ["web.error.bad_request", "That path is not valid."],
  invalid_pattern: [
    "web.error.invalid_pattern",
    "That pattern is not a valid regular expression.",
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

/**
 * `checkResponse` prefers `detail.message` and throws it as a bare string, so
 * a `{code, message}` body loses its code before it reaches us. These are the
 * exact sentences the code-carrying routes emit, matched by prefix. Drift here
 * is not silent damage: an unmatched sentence falls through to the branch that
 * shows the server's own words as the primary line, which is the pre-PR
 * behaviour — never worse, just untranslated.
 */
const MESSAGE_CODES: [string, string][] = [
  // app/web/routes/_path_guard.py:73 and app/web/routes/execute.py:386
  ["path is outside all allowed roots", "permission_denied"],
  ["reveal only allowed from localhost", "permission_denied"],
  // app/web/routes/execute.py:396
  ["reveal only supported on Windows", "platform_unsupported"],
  // app/web/routes/_path_guard.py:56,64
  ["path must not be empty", "bad_request"],
  ["malformed path:", "bad_request"],
];

/** Non-empty trimmed string, or null. */
function orNull(s: string): string | null {
  const trimmed = s.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Recover `(code, humanText)` from a flattened `detail`.
 *
 * Two shapes reach us. A body whose second key is not `message` (the
 * `invalid_pattern` route uses `detail`) is JSON-stringified by
 * `checkResponse`, so the code survives verbatim — and the raw blob is what
 * the user used to be shown. A `{code, message}` body arrives as the message
 * alone, so the code is matched back from the sentence.
 *
 * Returns `null` when neither applies.
 */
function codeFromDetail(detail: string): { code: string; text: string | null } | null {
  const trimmed = detail.trim();
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as Record<string, unknown>;
      if (typeof parsed.code === "string") {
        const inner =
          typeof parsed.message === "string"
            ? parsed.message
            : typeof parsed.detail === "string"
              ? parsed.detail
              : "";
        return { code: parsed.code, text: orNull(inner) };
      }
    } catch {
      // not JSON after all — fall through to the sentence match
    }
  }
  for (const [sentence, code] of MESSAGE_CODES) {
    if (trimmed.startsWith(sentence)) return { code, text: orNull(trimmed) };
  }
  return null;
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
    const [key, fallback] = ERROR_CODES[conflict[1]] ?? UNKNOWN;
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
    // A code-carrying body gets its own translated sentence, with the
    // server's wording kept as the detail — so a zh_TW user finally reads
    // this class of failure in Chinese.
    const coded = codeFromDetail(detail);
    if (coded !== null) {
      const mapped = ERROR_CODES[coded.code];
      if (mapped !== undefined) {
        return { message: t(mapped[0], mapped[1]), detail: coded.text };
      }
      // Unknown code: its own text is still the best sentence we have.
      if (coded.text !== null) return { message: coded.text, detail: null };
    }
    // Anything else here already arrived human-readable (checkResponse
    // prefers detail.message over a JSON blob), so it IS the sentence.
    // Demoting it to the faint technical line would lose the only words
    // that tell the user what happened — strictly worse than pre-PR.
    const sentence = orNull(detail);
    if (sentence !== null) return { message: sentence, detail: null };
    return { message: t(UNKNOWN[0], UNKNOWN[1]), detail: null };
  }

  return { message: t(UNKNOWN[0], UNKNOWN[1]), detail: orNull(raw) };
}
