// Tests for actionDialogFields — the web analog of Qt's COL_TO_FIELD (#735).

import { describe, it, expect } from "vitest";
import {
  WEB_COL_TO_FIELD,
  ACTION_DIALOG_FIELD_OPTIONS,
  resolveInitialField,
} from "./actionDialogFields";

describe("WEB_COL_TO_FIELD", () => {
  it("maps every Qt-parity column to its ActionDialog field-option value", () => {
    expect(WEB_COL_TO_FIELD.name).toBe("File Name");
    expect(WEB_COL_TO_FIELD.similarity).toBe("Similarity");
    expect(WEB_COL_TO_FIELD.action).toBe("Action");
    expect(WEB_COL_TO_FIELD.size).toBe("Size (Bytes)");
    expect(WEB_COL_TO_FIELD.date).toBe("Shot Date");
  });

  it("omits score and dims — no Qt COL_TO_FIELD entry for either", () => {
    expect(WEB_COL_TO_FIELD.score).toBeUndefined();
    expect(WEB_COL_TO_FIELD.dims).toBeUndefined();
  });

  it("every mapped value is a valid ActionDialog field option", () => {
    for (const value of Object.values(WEB_COL_TO_FIELD)) {
      expect(ACTION_DIALOG_FIELD_OPTIONS.has(value as string)).toBe(true);
    }
  });
});

describe("resolveInitialField", () => {
  it("returns undefined for an undefined column (argless openActionDialog)", () => {
    expect(resolveInitialField(undefined)).toBeUndefined();
  });

  it("resolves a mapped column to its field label", () => {
    expect(resolveInitialField("size")).toBe("Size (Bytes)");
  });

  it("returns undefined for an unmapped column (score/dims) or unknown string", () => {
    expect(resolveInitialField("score")).toBeUndefined();
    expect(resolveInitialField("dims")).toBeUndefined();
    expect(resolveInitialField("not-a-column")).toBeUndefined();
  });
});
