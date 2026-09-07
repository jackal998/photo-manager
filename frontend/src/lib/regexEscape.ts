// Shared regex-escaping helper — single source of truth for both RegexPanel's
// Simple->Regex write-through encoder and patternSummary's Regex->Simple
// reverse-parser. The two MUST use the same escaping rules or the reverse
// parser (patternSummary.ts::reverseParseSimple) would fail to recognise
// patterns RegexPanel itself produced.

/** Escape a string for safe embedding in a regex (subset of JS special chars). */
export function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
