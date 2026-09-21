// Pure helpers for the singleton-prune flow (#686) — the web port of the Qt
// desktop's `_maybe_offer_singleton_prune` decision tree. Kept dependency-free
// and side-effect-free so the pref normalization + set arithmetic can be
// unit-tested in isolation from the store and the network.
//
// Note: singleton CLASSIFICATION lives on the backend (POST /api/prune/candidates),
// not here — the web review view drops single-member groups, so the frontend
// can't see singletons in its own groups (the Qt desktop keeps them in its
// in-memory vm; the web reloads from the repo, which orphan-skips them).

// ---------------------------------------------------------------------------
// The 3-value standing preference (mirrors Qt's ui.prune_singletons enum).
// ---------------------------------------------------------------------------

/**
 * ``"ask"``    — offer the prune dialog whenever singletons appear (app default).
 * ``"always"`` — silently prune; never show the dialog again.
 * ``"never"``  — silently keep; never prune.
 */
export type PrunePref = "ask" | "always" | "never";

const PRUNE_PREFS: readonly PrunePref[] = ["ask", "always", "never"];

/**
 * Coerce an arbitrary settings value (the server stores `ui.prune_singletons`
 * as an opaque JSON value) into a safe {@link PrunePref}. Anything that is not
 * one of the three canonical strings — `null`, a stale legacy boolean, a typo —
 * falls back to the app default ``"ask"`` (the safe, always-confirm behaviour).
 */
export function normalizePrunePref(v: unknown): PrunePref {
  return typeof v === "string" && (PRUNE_PREFS as readonly string[]).includes(v)
    ? (v as PrunePref)
    : "ask";
}

/**
 * Resolve the explicit prune set the backend's explicit-paths mode expects,
 * mirroring the Qt "ask"-branch tail:
 *
 *     to_prune = (plain if prune_plain) + (actioned if prune_actioned) + prunable_locked
 *
 * `lockedToPrune` is folded in **unconditionally** — the lock dialog's
 * Unlock&Apply verdict commits those singletons regardless of the subsequent
 * prune-dialog verdict (so "Keep all" still prunes the unlocked-locked ones).
 */
export function computePruneSet(
  buckets: { plain: string[]; actioned: string[] },
  opts: { prunePlain: boolean; pruneActioned: boolean; lockedToPrune: string[] }
): string[] {
  const out: string[] = [];
  if (opts.prunePlain) out.push(...buckets.plain);
  if (opts.pruneActioned) out.push(...buckets.actioned);
  out.push(...opts.lockedToPrune);
  return out;
}
