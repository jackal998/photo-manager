// Table tests for the two derivations the group header is allowed to make
// about a group (#878 slice G, open questions Q4).

import { describe, it, expect } from "vitest";

import { groupFolderSuffix, groupSizeTotal } from "./groupSummary";
import type { FileRow } from "../api/types";

function item(folder: string, bytes: number, name = "a.jpg"): FileRow {
  return {
    file_path: `${folder}/${name}`,
    basename: name,
    folder,
    action: "REVIEW_DUPLICATE",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "percent", percent: 95 },
    score: 71,
    file_size_bytes: bytes,
    pixel_width: 1920,
    pixel_height: 1080,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: null,
    thumbnail_url: "",
  };
}

describe("groupSizeTotal", () => {
  it("sums every member's bytes", () => {
    expect(
      groupSizeTotal([
        item("/p", 1_048_576),
        item("/p", 3_145_728, "b.jpg"),
        item("/p", 512, "c.jpg"),
      ])
    ).toBe(4_194_816);
  });

  it("is 0 for an empty group", () => {
    expect(groupSizeTotal([])).toBe(0);
  });
});

describe("groupFolderSuffix", () => {
  it("returns the shared folder's leaf when every member shares a folder", () => {
    expect(
      groupFolderSuffix([
        item("/fake/photos/2021-trip", 1),
        item("/fake/photos/2021-trip", 2, "b.jpg"),
      ])
    ).toEqual({ kind: "leaf", leaf: "2021-trip" });
  });

  it("counts folders when members span more than one", () => {
    expect(
      groupFolderSuffix([
        item("/fake/photos/2021-trip", 1),
        item("/fake/backup", 2, "b.jpg"),
        item("/fake/backup", 3, "c.jpg"),
      ])
    ).toEqual({ kind: "folders", count: 2 });
  });

  it("reads a Windows path's leaf — the manifest carries whatever the OS wrote", () => {
    expect(
      groupFolderSuffix([item("D:\\Pictures\\2021-trip", 1)])
    ).toEqual({ kind: "leaf", leaf: "2021-trip" });
  });

  it("ignores a trailing separator rather than yielding an empty leaf", () => {
    expect(groupFolderSuffix([item("/fake/photos/2021-trip/", 1)])).toEqual({
      kind: "leaf",
      leaf: "2021-trip",
    });
  });

  it("returns null when there is nothing honest to say", () => {
    expect(groupFolderSuffix([])).toBeNull();
    expect(groupFolderSuffix([item("", 1)])).toBeNull();
  });
});
