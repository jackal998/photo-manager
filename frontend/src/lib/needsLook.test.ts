import { describe, it, expect } from "vitest";

import { countNeedsLook, groupNeedsLook, rowNeedsLook } from "./needsLook";
import type { FileRow, Group } from "@/api/types";

function row(confidence_level: FileRow["confidence_level"]): FileRow {
  return { basename: "x.jpg", folder: "/p", confidence_level } as FileRow;
}

function group(n: number, levels: FileRow["confidence_level"][]): Group {
  return {
    group_number: n,
    member_count: levels.length,
    items: levels.map((l) => row(l)),
  };
}

describe("needsLook", () => {
  it("medium needs a look below high", () => {
    expect(rowNeedsLook(row("medium"), "high")).toBe(true);
  });

  it("medium needs no look below medium", () => {
    expect(rowNeedsLook(row("medium"), "medium")).toBe(false);
  });

  it("an unrated row never needs a look", () => {
    expect(rowNeedsLook(row(null), "high")).toBe(false);
  });

  it("an unknown level needs a look", () => {
    const odd = { confidence_level: "weird" } as unknown as FileRow;
    expect(rowNeedsLook(odd, "medium")).toBe(true);
  });

  it("countNeedsLook counts groups, not rows", () => {
    const groups = [
      group(1, ["medium", "medium", "high"]),
      group(2, ["high", "high"]),
      group(3, [null, "medium"]),
    ];
    expect(countNeedsLook(groups, "high")).toBe(2);
    expect(groupNeedsLook(groups[1], "high")).toBe(false);
  });
});
