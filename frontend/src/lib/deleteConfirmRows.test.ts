// deleteConfirmRows — folder bucketing + the per-file reason vocabulary (#917).
//
// The bugs these catch are the ones a user would see in the confirm modal:
// a delete row filed under the wrong folder, a subtotal that does not add up
// to what the pinned total claims, a reason line naming the wrong keeper, and
// "null%" / "of null" reaching the screen when a percentage or a group Ref is
// missing from the manifest.

import { describe, it, expect } from "vitest";

import type { FileRow, Group, Similarity } from "@/api/types";
import { deleteFolderBuckets, deleteReasonText } from "./deleteConfirmRows";

// The real catalog is asserted by tests/test_web_i18n*.py; here the English
// fallback IS the string under test, so t() is the identity-with-params form
// the component's useT() degrades to before a catalog loads.
const t = (
  _key: string,
  fallback: string,
  params?: Record<string, string | number>
): string =>
  params === undefined
    ? fallback
    : fallback.replace(/\{(\w+)\}/g, (m, k: string) =>
        params[k] !== undefined ? String(params[k]) : m
      );

function mkRow(
  basename: string,
  folder: string,
  decision: FileRow["user_decision"],
  bytes: number,
  similarity: Similarity = { kind: "percent", percent: 100 }
): FileRow {
  return {
    file_path: `${folder}/${basename}`,
    basename,
    folder,
    action: "",
    user_decision: decision,
    is_locked: false,
    is_ref_winner: similarity.kind === "ref",
    similarity,
    score: null,
    file_size_bytes: bytes,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: null,
    thumbnail_url: "",
  };
}

function mkGroup(n: number, items: FileRow[]): Group {
  return { group_number: n, member_count: items.length, items };
}

describe("deleteFolderBuckets", () => {
  it("buckets delete rows by folder with a per-folder count and byte subtotal", () => {
    const buckets = deleteFolderBuckets([
      mkGroup(1, [
        mkRow("ref.jpg", "/photos/trip", "", 5_000, { kind: "ref", percent: null }),
        mkRow("a.jpg", "/photos/trip", "delete", 1_000),
        mkRow("b.jpg", "/photos/keepers", "delete", 2_000),
      ]),
      mkGroup(2, [
        mkRow("ref2.jpg", "/photos/trip", "", 9_000, { kind: "ref", percent: null }),
        mkRow("c.jpg", "/photos/trip", "delete", 3_000),
      ]),
    ]);
    expect(buckets.map((b) => b.folder)).toEqual([
      "/photos/keepers",
      "/photos/trip",
    ]);
    expect(buckets[0]).toMatchObject({ count: 1, bytes: 2_000 });
    expect(buckets[1]).toMatchObject({ count: 2, bytes: 4_000 });
    // The subtotals must add up to what the pinned total will claim.
    const total = buckets.reduce((acc, b) => acc + b.bytes, 0);
    expect(total).toBe(6_000);
  });

  it("ignores rows that are not marked delete", () => {
    const buckets = deleteFolderBuckets([
      mkGroup(1, [
        mkRow("keep.jpg", "/photos", "", 4_000),
        mkRow("skip.jpg", "/photos", "ignore", 8_000),
        mkRow("gone.jpg", "/photos", "delete", 1_500),
      ]),
    ]);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]).toMatchObject({ count: 1, bytes: 1_500 });
    expect(buckets[0].rows.map((r) => r.basename)).toEqual(["gone.jpg"]);
  });

  it("attaches the group's Ref basename to every non-Ref row", () => {
    const buckets = deleteFolderBuckets([
      mkGroup(1, [
        mkRow("winner.jpg", "/photos", "", 5_000, { kind: "ref", percent: null }),
        mkRow("dup.jpg", "/photos", "delete", 1_000),
      ]),
    ]);
    expect(buckets[0].rows[0].refBasename).toBe("winner.jpg");
  });

  it("gives a deleted Ref row no refBasename (it is not a duplicate of itself)", () => {
    const buckets = deleteFolderBuckets([
      mkGroup(1, [
        mkRow("winner.jpg", "/photos", "delete", 5_000, {
          kind: "ref",
          percent: null,
        }),
        mkRow("dup.jpg", "/photos", "delete", 1_000),
      ]),
    ]);
    const ref = buckets[0].rows.find((r) => r.basename === "winner.jpg");
    expect(ref?.refBasename).toBeNull();
  });

  // The scope argument is the reviewer-found HIGH on PR #921: "Execute (only
  // selected)" sends scope_paths=selection, but the dialog was listing every
  // delete row in the group — naming files that would NOT be deleted and
  // pinning a total the server would contradict.
  describe("execute scope", () => {
    const threeDeletes = (): Group[] => [
      mkGroup(1, [
        mkRow("ref.jpg", "/photos", "", 9_000, { kind: "ref", percent: null }),
        mkRow("a.jpg", "/photos", "delete", 1_000),
        mkRow("b.jpg", "/photos", "delete", 2_000),
        mkRow("c.jpg", "/photos", "delete", 4_000),
      ]),
    ];

    it("lists only the selected row when the scope is a selection of 1 of 3", () => {
      const buckets = deleteFolderBuckets(threeDeletes(), ["/photos/b.jpg"]);
      expect(buckets).toHaveLength(1);
      expect(buckets[0]).toMatchObject({ count: 1, bytes: 2_000 });
      expect(buckets[0].rows.map((r) => r.basename)).toEqual(["b.jpg"]);
    });

    it("lists only the visible rows when the scope is a filtered subset", () => {
      const buckets = deleteFolderBuckets(threeDeletes(), [
        "/photos/a.jpg",
        "/photos/c.jpg",
      ]);
      expect(buckets[0]).toMatchObject({ count: 2, bytes: 5_000 });
      expect(buckets[0].rows.map((r) => r.basename)).toEqual(["a.jpg", "c.jpg"]);
    });

    it("lists every delete row when the scope is null (unscoped commit)", () => {
      const buckets = deleteFolderBuckets(threeDeletes(), null);
      expect(buckets[0]).toMatchObject({ count: 3, bytes: 7_000 });
      // …and omitting the argument entirely behaves the same.
      expect(deleteFolderBuckets(threeDeletes())[0].count).toBe(3);
    });

    it("keeps naming the group's Ref even when the Ref is out of scope", () => {
      // The keeper a row duplicates does not stop existing because it was not
      // selected — a scope-narrowed Ref lookup would blank every reason line.
      const buckets = deleteFolderBuckets(threeDeletes(), ["/photos/a.jpg"]);
      expect(buckets[0].rows[0].refBasename).toBe("ref.jpg");
    });

    it("drops a folder whose every delete row is out of scope", () => {
      const buckets = deleteFolderBuckets(
        [
          mkGroup(1, [
            mkRow("keep.jpg", "/photos/trip", "delete", 1_000),
            mkRow("gone.jpg", "/photos/other", "delete", 1_000),
          ]),
        ],
        ["/photos/other/gone.jpg"]
      );
      expect(buckets.map((b) => b.folder)).toEqual(["/photos/other"]);
    });
  });

  it("counts a row with a non-finite size as 0 bytes, not NaN", () => {
    const buckets = deleteFolderBuckets([
      mkGroup(1, [mkRow("bad.jpg", "/photos", "delete", Number.NaN)]),
    ]);
    expect(buckets[0].bytes).toBe(0);
    expect(buckets[0].count).toBe(1);
  });
});

describe("deleteReasonText", () => {
  const cases: Array<[string, Similarity, string | null, string]> = [
    [
      "exact",
      { kind: "percent", percent: 100 },
      "beach-01.jpg",
      "Exact duplicate of beach-01.jpg",
    ],
    ["near", { kind: "percent", percent: 92.4 }, "beach-01.jpg", "92% match — same group"],
    ["passenger", { kind: "passenger", percent: 88 }, "beach-01.jpg", "Linked match, 88%*"],
    [
      "near_dup",
      { kind: "near_dup", percent: null },
      "beach-01.jpg",
      "Near-duplicate of beach-01.jpg",
    ],
    ["none", { kind: "none", percent: null }, "beach-01.jpg", "No comparable image — same group"],
    [
      "ref",
      { kind: "ref", percent: null },
      null,
      "Group reference copy — the whole group is marked delete",
    ],
  ];

  for (const [name, similarity, refBasename, expected] of cases) {
    it(`renders the ${name} phrase`, () => {
      expect(deleteReasonText({ similarity, refBasename }, t)).toBe(expected);
    });
  }

  it("never prints 'of null' when the group has no Ref row", () => {
    expect(
      deleteReasonText(
        { similarity: { kind: "percent", percent: 100 }, refBasename: null },
        t
      )
    ).toBe("Exact duplicate — same group");
    expect(
      deleteReasonText(
        { similarity: { kind: "near_dup", percent: null }, refBasename: null },
        t
      )
    ).toBe("Near match — same group");
  });

  it("never prints 'null%' when the percentage is missing", () => {
    expect(
      deleteReasonText(
        { similarity: { kind: "percent", percent: null }, refBasename: "a.jpg" },
        t
      )
    ).toBe("Near match — same group");
    expect(
      deleteReasonText(
        { similarity: { kind: "passenger", percent: null }, refBasename: "a.jpg" },
        t
      )
    ).toBe("Linked match");
  });
});
