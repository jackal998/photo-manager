// Score mini-bar geometry (#878 slice b).
//
// The keep-worthiness score is a float in [0, 1] (scanner/scoring.py:
// "Final score = max(0.0, min(1.0, Tier2 − format_penalty − derived_penalty)")
// and reaches the web client unscaled (core/app_service/review_view.py:141
// passes the record's `score` through). `null` means UNSCORED, not zero — a
// Live Photo MOV passenger, an isolated file — and the Qt delegate this ports
// (app/views/delegates/score_bar.py on feat/review-ux-daylight) drew NO bar in
// that case and only the em dash.
//
// The normalisation rule is ported, not the code: that delegate clamps with
// `max(0.0, min(1.0, score))` before multiplying by the track width, so a row
// whose score somehow lands outside the domain still paints a bar inside the
// cell instead of overflowing it. NaN is the JSON/JS failure mode Python never
// had (a malformed number survives `JSON.parse` as NaN, and `NaN%` is an
// invalid CSS width that silently leaves the fill at its previous size), so it
// is folded into the same "no bar" answer as null.

/**
 * The fraction of the score track to fill, always in [0, 1].
 *
 * `null` / `undefined` / `NaN` → 0 (no bar). Out-of-domain values clamp.
 */
export function scoreBarRatio(score: number | null | undefined): number {
  if (score === null || score === undefined) return 0;
  if (!Number.isFinite(score)) return 0;
  if (score <= 0) return 0;
  if (score >= 1) return 1;
  return score;
}

/** The same ratio as a CSS width percentage, e.g. `"42%"`. */
export function scoreBarWidth(score: number | null | undefined): string {
  return `${scoreBarRatio(score) * 100}%`;
}
