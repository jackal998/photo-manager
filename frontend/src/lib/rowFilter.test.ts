import { describe, it, expect } from "vitest";

import { filterGroups, visibleSelectedPaths } from "./rowFilter";
import type { FileRow, Group } from "@/api/types";

function mkRow(folder: string, basename: string): FileRow {
  return {
    file_path: `${folder}/${basename}`,
    basename,
    folder,
    action: "KEEP",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "near_dup", percent: 98 },
    score: null,
    file_size_bytes: 1000,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: "",
  } as FileRow;
}

const GROUPS: Group[] = [
  {
    group_number: 1,
    member_count: 2,
    items: [
      mkRow("/photos/2019/Taiwan", "IMG_0001.JPG"),
      mkRow("/photos/2019/Taiwan", "sunset.jpg"),
    ],
  },
  {
    group_number: 2,
    member_count: 2,
    items: [
      mkRow("/photos/2021/Japan", "IMG_9000.jpg"),
      mkRow("/photos/2021/Japan", "IMG_9001.jpg"),
    ],
  },
];

describe("filterGroups", () => {
  it("returns the store's array BY IDENTITY when the box is empty", () => {
    // Identity, not just equality: every memo in ResultTree keys off this
    // array, so a fresh copy per render would re-sort and re-flatten the whole
    // tree on each keystroke anywhere else in the app.
    expect(filterGroups(GROUPS, "")).toBe(GROUPS);
    expect(filterGroups(GROUPS, "   ")).toBe(GROUPS);
  });

  it("matches a substring of the basename, case-insensitively", () => {
    const out = filterGroups(GROUPS, "sunset");
    expect(out).toHaveLength(1);
    expect(out[0].items.map((r) => r.basename)).toEqual(["sunset.jpg"]);

    // The same query in the other case must find the same row — filenames off a
    // camera are upper case and what the user types is not.
    expect(filterGroups(GROUPS, "img_9000")[0].items[0].basename).toBe(
      "IMG_9000.jpg"
    );
  });

  it("matches the FOLDER too, which is how a real query is phrased", () => {
    const out = filterGroups(GROUPS, "taiwan");
    expect(out).toHaveLength(1);
    expect(out[0].group_number).toBe(1);
    expect(out[0].items).toHaveLength(2);
  });

  it("hides a group whose rows all fail the filter", () => {
    const out = filterGroups(GROUPS, "japan");
    expect(out.map((g) => g.group_number)).toEqual([2]);
  });

  it("recomputes member_count from the surviving rows", () => {
    // The group header renders this number and derives its size total from the
    // same items; leaving the original count would promise rows the filter has
    // removed from under it.
    const out = filterGroups(GROUPS, "0001");
    expect(out).toHaveLength(1);
    expect(out[0].member_count).toBe(1);
    expect(out[0].items).toHaveLength(1);
  });

  it("returns no groups when nothing matches, and everything when cleared", () => {
    expect(filterGroups(GROUPS, "nothing-matches-this")).toEqual([]);
    expect(filterGroups(GROUPS, "")).toBe(GROUPS);
  });

  it("does not mutate the input groups", () => {
    const before = JSON.stringify(GROUPS);
    filterGroups(GROUPS, "sunset");
    expect(JSON.stringify(GROUPS)).toBe(before);
  });
});

describe("visibleSelectedPaths", () => {
  const ALL = [
    "/photos/2019/Taiwan/IMG_0001.JPG",
    "/photos/2019/Taiwan/sunset.jpg",
    "/photos/2021/Japan/IMG_9000.jpg",
    "/photos/2021/Japan/IMG_9001.jpg",
  ];

  it("returns the selection by identity when no filter is active", () => {
    expect(visibleSelectedPaths(GROUPS, "", ALL)).toBe(ALL);
    expect(visibleSelectedPaths(GROUPS, "  ", ALL)).toBe(ALL);
  });

  it("drops selected rows the filter is hiding", () => {
    // The bug this closes: the selection SURVIVES the filter (typing must not
    // destroy the user's picks), so without this the toolbar counted 4 and the
    // verb wrote to 4 while only 2 were on screen — a bulk write whose extent
    // the user cannot see.
    expect(visibleSelectedPaths(GROUPS, "japan", ALL)).toEqual([
      "/photos/2021/Japan/IMG_9000.jpg",
      "/photos/2021/Japan/IMG_9001.jpg",
    ]);
  });

  it("keeps the SELECTION's order, not the tree's", () => {
    const reversed = [...ALL].reverse();
    expect(visibleSelectedPaths(GROUPS, "japan", reversed)).toEqual([
      "/photos/2021/Japan/IMG_9001.jpg",
      "/photos/2021/Japan/IMG_9000.jpg",
    ]);
  });

  it("is empty when the filter hides every selected row", () => {
    expect(
      visibleSelectedPaths(GROUPS, "japan", ["/photos/2019/Taiwan/sunset.jpg"])
    ).toEqual([]);
  });

  it("gives the hidden picks back when the filter is cleared", () => {
    expect(visibleSelectedPaths(GROUPS, "japan", ALL)).toHaveLength(2);
    expect(visibleSelectedPaths(GROUPS, "", ALL)).toHaveLength(4);
  });
});
