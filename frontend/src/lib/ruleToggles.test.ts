import { describe, expect, it } from "vitest";
import { DEFAULT_RULES, rulesFromSettings } from "./ruleToggles";

describe("rulesFromSettings", () => {
  it("returns the defaults when unset", () => {
    expect(rulesFromSettings(undefined)).toEqual(DEFAULT_RULES);
    expect(rulesFromSettings(null)).toEqual(DEFAULT_RULES);
    expect(rulesFromSettings("x")).toEqual(DEFAULT_RULES);
    expect(rulesFromSettings([])).toEqual(DEFAULT_RULES);
  });

  it("honours only strict booleans for known ids", () => {
    const resolved = rulesFromSettings({ B8: false, B3: "no", B4: 0, B9: false });
    expect(resolved.B8).toBe(false);
    expect(resolved.B3).toBe(true);
    expect(resolved.B4).toBe(true);
    expect(resolved).not.toHaveProperty("B9");
  });
});

describe("DEFAULT_RULES", () => {
  it("has every rule on except B7 (off since GROUPING 16)", () => {
    expect(DEFAULT_RULES).toEqual({ B3: true, B4: true, B5: true, B6: true, B7: false, B8: true });
  });
});
