import { describe, it, expect } from "vitest";

import { deleteTotals } from "./deleteTotals";
import type { DecisionValue, FileRow, Group } from "@/api/types";

function mkRow(
  basename: string,
  decision: DecisionValue,
  bytes: number,
  locked = false
): FileRow {
  return {
    file_path: `/photos/${basename}`,
    basename,
    folder: "/photos",
    action: "REVIEW_DUPLICATE",
    user_decision: decision,
    is_locked: locked,
    is_ref_winner: false,
    similarity: { kind: "near_dup", percent: 98 },
    score: null,
    file_size_bytes: bytes,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: "",
  } as FileRow;
}

describe("deleteTotals (#906)", () => {
  it("counts and sums only the delete-marked rows, across every group", () => {
    const groups: Group[] = [
      {
        group_number: 1,
        member_count: 3,
        items: [
          mkRow("keep.jpg", "", 1_000_000),
          mkRow("dup1.jpg", "delete", 2_000_000),
          mkRow("skipped.jpg", "ignore", 4_000_000),
        ],
      },
      {
        group_number: 2,
        member_count: 2,
        items: [
          mkRow("dup2.jpg", "delete", 3_000_000),
          mkRow("other.jpg", "", 9_000_000),
        ],
      },
    ];
    expect(deleteTotals(groups)).toEqual({ count: 2, bytes: 5_000_000 });
  });

  it("counts a LOCKED delete row — the lock blocks writes, not the total", () => {
    // A locked row that is already marked `delete` WILL be deleted by Execute;
    // omitting it here would understate the danger CTA's number.
    const groups: Group[] = [
      {
        group_number: 1,
        member_count: 2,
        items: [
          mkRow("locked-dup.jpg", "delete", 500, true),
          mkRow("keep.jpg", "", 500),
        ],
      },
    ];
    expect(deleteTotals(groups)).toEqual({ count: 1, bytes: 500 });
  });

  it("is zero on an empty manifest", () => {
    expect(deleteTotals([])).toEqual({ count: 0, bytes: 0 });
  });

  it("treats a missing size as 0 rather than poisoning the sum with NaN", () => {
    // One row whose `file_size_bytes` never made it into the manifest must not
    // blank the whole reclaim figure — the user would read "reclaims NaN".
    const broken = mkRow("no-size.jpg", "delete", undefined as unknown as number);
    const groups: Group[] = [
      {
        group_number: 1,
        member_count: 2,
        items: [broken, mkRow("dup.jpg", "delete", 700)],
      },
    ];
    expect(deleteTotals(groups)).toEqual({ count: 2, bytes: 700 });
  });
});
