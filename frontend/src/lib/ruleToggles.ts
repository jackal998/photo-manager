// PR-4a/4b — the auto-select rule toggles as the dialog sees them. Mirrors the
// server defaults in core/services/auto_select_rules.py (DEFAULT_RULES): the
// screenshot rule B7 is OFF since GROUPING 16 (matching screenshots group like
// photos), and B6 (a Live video follows its photo) is ON.

import type { RuleId, RuleToggles } from "../api/types";

export const RULE_IDS: readonly RuleId[] = ["B3", "B4", "B5", "B6", "B7", "B8"];

export const DEFAULT_RULES: RuleToggles = {
  B3: true,
  B4: true,
  B5: true,
  B6: true,
  B7: false,
  B8: true,
};

/**
 * Resolve a stored settings value to toggles. Only a strict boolean for a
 * known id is honoured; anything else (missing id, junk, non-object) takes
 * that rule's default. Unknown ids are ignored.
 */
export function rulesFromSettings(value: unknown): RuleToggles {
  const resolved: RuleToggles = { ...DEFAULT_RULES };
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return resolved;
  }
  const record = value as Record<string, unknown>;
  for (const id of RULE_IDS) {
    const flag = record[id];
    if (typeof flag === "boolean") {
      resolved[id] = flag;
    }
  }
  return resolved;
}
