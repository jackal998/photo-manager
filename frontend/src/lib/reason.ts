// Why a row is grouped and how sure the classifier is (#1047) — pure
// presentation helpers shared by the row badge, the preview badge and the
// decision chip. No React: the same strings feed FileRow and PreviewPane.

import type { FileRow } from "@/api/types";
import { classificationLabel } from "@/lib/format";

/** `t(key, fallback)` as `useT()` returns it; the fallback is the English default. */
export type TranslateFn = (key: string, fallback: string) => string;

const IDENTITY_T: TranslateFn = (_key, fallback) => fallback;

/** The decision chip a row wears, or none. `keep_all` wins over `low`: a burst
 *  stack is an instruction to keep the whole stack, which changes the decision
 *  more than a low-confidence hint does. */
export type VerdictChip = "keep_all" | "low_confidence";

export function verdictChip(row: FileRow): VerdictChip | null {
  if (row.keep_all === true) return "keep_all";
  if (row.match_confidence === "low") return "low_confidence";
  return null;
}

const CHIP_TEXT: Record<VerdictChip, [key: string, fallback: string]> = {
  keep_all: ["web.reason.keep_all_chip", "Keep all · burst"],
  low_confidence: ["web.reason.low_confidence_chip", "Low confidence"],
};

export function verdictChipLabel(
  chip: VerdictChip,
  t: TranslateFn = IDENTITY_T
): string {
  const [key, fallback] = CHIP_TEXT[chip];
  return t(key, fallback);
}

const CONFIDENCE_TEXT: Record<string, [key: string, fallback: string]> = {
  high: ["web.reason.confidence_high", "Confidence: high"],
  // PR-3 — the medium tier (REVIEW_DUPLICATE) comes from confidence_level.
  medium: ["web.reason.confidence_medium", "Confidence: medium"],
  low: ["web.reason.confidence_low", "Confidence: low"],
};

/** "Confidence: high" / "Confidence: low". An UNRECOGNISED level is returned
 *  verbatim rather than swallowed — a new classifier output must be visible,
 *  not silently blank (same rule as classificationLabel). */
export function confidenceLabel(
  level: string,
  t: TranslateFn = IDENTITY_T
): string {
  const entry = CONFIDENCE_TEXT[level];
  if (entry === undefined) return level;
  const [key, fallback] = entry;
  return t(key, fallback);
}

/** The badge tooltip: classification, reason, confidence — one per line. The
 *  reason is omitted when empty or "unique" (an ungrouped row says nothing
 *  new), and the confidence line when the manifest carries none. */
export function rowTooltip(row: FileRow, t: TranslateFn = IDENTITY_T): string {
  const lines: string[] = [classificationLabel(row.action, t)];
  if (row.reason && row.reason !== "unique") lines.push(row.reason);
  // PR-3 — prefer the server's threshold level (it has the medium tier that
  // match_confidence lacks); fall back to match_confidence on an older backend.
  const level = row.confidence_level ?? row.match_confidence;
  if (level) {
    lines.push(confidenceLabel(level, t));
  }
  return lines.join("\n");
}
