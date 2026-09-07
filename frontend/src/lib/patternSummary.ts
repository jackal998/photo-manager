// patternSummary.ts — pattern-to-human-summary helper for the pattern-aware
// bulk-delete confirm dialog (#741 sub-item C).
//
// Qt analog: app/views/dialogs/select_dialog.py::_build_pattern_summary +
// ::_try_parse_simple + ::_is_plain_or_escaped.
//
// Pure function of (fieldDisplay, pattern) — no live UI state dependency.
// Both the Simple-mode (op, text) pair AND the numeric threshold/top-N
// sub-panel values are fully reconstructible from the pattern string alone
// (the numeric pseudo-pattern prefixes __cmp__:/__top_n__: and the
// Simple->Regex encoding in RegexPanel.tsx are both lossless), so this
// helper can never go stale relative to what field+pattern actually encode
// — unlike deriving the summary from a second, separately-tracked "Simple
// inputs" state that could drift out of sync with the regex.

import { escapeRegex } from "./regexEscape";

export type SimpleOp = "contains" | "starts_with" | "ends_with" | "exact";

/** Translate function shape returned by useT() — kept local to avoid a
 *  circular import back into the i18n module for a one-line type. */
export type TFunc = (
  key: string,
  fallback: string,
  params?: Record<string, string | number>
) => string;

const CMP_PREFIX = "__cmp__:";
const TOP_N_PREFIX = "__top_n__:";

// Fallback English text per Simple op — mirrors the literal fallbacks
// RegexPanel.tsx passes to t() for the same keys, so a summary rendered
// before the i18n catalog loads still reads correctly.
const SIMPLE_OP_FALLBACK: Record<SimpleOp, string> = {
  contains: "contains",
  starts_with: "starts with",
  ends_with: "ends with",
  exact: "exactly matches",
};

// ---------------------------------------------------------------------------
// Numeric pseudo-pattern decoders — mirror core/app_service/action_resolve.py
// decode_cmp_pattern / decode_top_n_pattern exactly (prefix + first-":"-split).
// ---------------------------------------------------------------------------

function decodeCmpPattern(pattern: string): { op: string; value: string } | null {
  if (!pattern.startsWith(CMP_PREFIX)) return null;
  const rest = pattern.slice(CMP_PREFIX.length);
  const idx = rest.indexOf(":");
  if (idx === -1) return null;
  return { op: rest.slice(0, idx), value: rest.slice(idx + 1) };
}

function decodeTopNPattern(pattern: string): { n: string; order: "asc" | "desc" } | null {
  if (!pattern.startsWith(TOP_N_PREFIX)) return null;
  const rest = pattern.slice(TOP_N_PREFIX.length);
  const idx = rest.indexOf(":");
  if (idx === -1) return null;
  const n = rest.slice(0, idx);
  const order = rest.slice(idx + 1);
  if (order !== "asc" && order !== "desc") return null;
  return { n, order };
}

// ---------------------------------------------------------------------------
// Simple-mode reverse parser — mirrors _is_plain_or_escaped + _try_parse_simple
// ---------------------------------------------------------------------------

/** True iff `text` round-trips through escapeRegex — i.e. could have come
 *  from RegexPanel's Simple->Regex encoder applied to plain user input. */
function isPlainOrEscaped(text: string): boolean {
  const decoded: string[] = [];
  let i = 0;
  while (i < text.length) {
    if (text[i] === "\\") {
      if (i + 1 >= text.length) return false; // dangling backslash
      decoded.push(text[i + 1]);
      i += 2;
    } else {
      decoded.push(text[i]);
      i += 1;
    }
  }
  return escapeRegex(decoded.join("")) === text;
}

/**
 * Reverse-parse `pattern` into a Simple-mode (op, text) pair, or null when
 * the pattern isn't Simple-representable (a "complex" regex).
 */
export function reverseParseSimple(
  pattern: string
): { op: SimpleOp; text: string } | null {
  if (pattern === "") return { op: "contains", text: "" };

  const hasCaret = pattern.startsWith("^");
  let hasDollar = false;
  if (pattern.endsWith("$")) {
    // Count consecutive backslashes immediately before the trailing $ — an
    // even count means the $ itself is unescaped (anchors the end).
    let backslashes = 0;
    let idx = pattern.length - 2;
    while (idx >= 0 && pattern[idx] === "\\") {
      backslashes += 1;
      idx -= 1;
    }
    hasDollar = backslashes % 2 === 0;
  }

  let body = pattern;
  if (hasCaret) body = body.slice(1);
  if (hasDollar) body = body.slice(0, -1);

  if (!isPlainOrEscaped(body)) return null;

  const decoded: string[] = [];
  let i = 0;
  while (i < body.length) {
    if (body[i] === "\\" && i + 1 < body.length) {
      decoded.push(body[i + 1]);
      i += 2;
    } else {
      decoded.push(body[i]);
      i += 1;
    }
  }
  const text = decoded.join("");

  if (hasCaret && hasDollar) return { op: "exact", text };
  if (hasCaret) return { op: "starts_with", text };
  if (hasDollar) return { op: "ends_with", text };
  return { op: "contains", text };
}

// ---------------------------------------------------------------------------
// Public summary builder
// ---------------------------------------------------------------------------

/**
 * Build a human-readable summary of `pattern` on `fieldDisplay` (the
 * already-localised field label) for the delete-confirm dialog.
 *
 * Dispatch order mirrors Qt's _build_pattern_summary:
 *   1. Numeric threshold (__cmp__:) -> "{field} {op} {value}".
 *   2. Numeric top-N (__top_n__:)   -> "{order} {n} per group by {field}".
 *   3. Simple-representable regex   -> "{field} {op} '{text}'".
 *   4. Anything else (complex regex) -> "{field} regex '{pattern}'".
 */
export function buildPatternSummary(
  fieldDisplay: string,
  pattern: string,
  t: TFunc
): string {
  const cmp = decodeCmpPattern(pattern);
  if (cmp !== null) {
    return t(
      "web.action_dialog.pattern_summary_numeric_threshold",
      "{field} {op} {value}",
      { field: fieldDisplay, op: cmp.op, value: cmp.value }
    );
  }

  const topN = decodeTopNPattern(pattern);
  if (topN !== null) {
    const orderLabel =
      topN.order === "desc"
        ? t("web.action_dialog.numeric_order_top", "Top")
        : t("web.action_dialog.numeric_order_bottom", "Bottom");
    return t(
      "web.action_dialog.pattern_summary_numeric_topn",
      "{order} {n} per group by {field}",
      { order: orderLabel, n: topN.n, field: fieldDisplay }
    );
  }

  const parsed = reverseParseSimple(pattern);
  if (parsed !== null) {
    const opLabel = t(
      `web.action_dialog.simple_op_${parsed.op}`,
      SIMPLE_OP_FALLBACK[parsed.op]
    );
    return t(
      "web.action_dialog.pattern_summary_simple",
      "{field} {op} '{text}'",
      { field: fieldDisplay, op: opLabel, text: parsed.text }
    );
  }

  return t(
    "web.action_dialog.pattern_summary_regex",
    "{field} regex '{pattern}'",
    { field: fieldDisplay, pattern }
  );
}
