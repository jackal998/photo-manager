import { describe, it, expect } from "vitest";

import { scanSourceName } from "./scanSummary";
import type { FileRow, Group } from "@/api/types";

function mkRow(folder: string, basename = "a.jpg"): FileRow {
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
    file_size_bytes: 10,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: "",
  } as FileRow;
}

function grp(n: number, folders: string[]): Group {
  return {
    group_number: n,
    member_count: folders.length,
    items: folders.map((f, i) => mkRow(f, `f${i}.jpg`)),
  };
}

describe("scanSourceName", () => {
  it("names the deepest folder every row shares", () => {
    const groups = [
      grp(1, ["/photos/Pictures Library/2019", "/photos/Pictures Library/2020"]),
      grp(2, ["/photos/Pictures Library/2021"]),
    ];
    expect(scanSourceName(groups, "/m/trip.db")).toBe("Pictures Library");
  });

  it("names the folder itself when every row sits in one", () => {
    const groups = [grp(1, ["/photos/near-duplicates", "/photos/near-duplicates"])];
    expect(scanSourceName(groups, "/m/trip.db")).toBe("near-duplicates");
  });

  it("handles Windows separators — a Windows manifest reaches this UI", () => {
    const groups = [
      grp(1, ["D:\\Pictures\\2019\\Taiwan", "D:\\Pictures\\2019\\Japan"]),
    ];
    expect(scanSourceName(groups, "D:\\m\\trip.db")).toBe("2019");
  });

  it("falls back to the manifest's own name when the rows share no root", () => {
    // Nothing honest to call this review — two unrelated drives — so the file
    // the user opened is the identity that is left.
    const groups = [grp(1, ["D:\\Pictures\\a", "/mnt/nas/photos/b"])];
    expect(scanSourceName(groups, "/manifests/2021-trip.db")).toBe("2021-trip");
  });

  it("falls back to the manifest name when no row carries a folder", () => {
    const groups = [grp(1, ["", ""])];
    expect(scanSourceName(groups, "/manifests/scan.db")).toBe("scan");
  });

  it("is null before a manifest is loaded", () => {
    expect(scanSourceName([], null)).toBeNull();
  });
});
