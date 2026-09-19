// The Execute confirm's auditable body (#917, design REPLY Q6).
//
// REPLY §Q6: «A number ("delete 23 files") can only be accepted or cancelled
// wholesale. A list with reasons can be *checked*, which is what turns the
// moment from a leap into a review.» Two things carry that:
//
//   - **folder buckets** with a per-folder count and size subtotal — «this is
//     how users notice "wait, why is anything from /fake/photos/keepers in
//     here"»;
//   - **one reason line per file**, reusing the Similarity badge vocabulary
//     verbatim so the sentence under a row says the same thing its badge does.
//
// Pure data + a pure string builder: the dialog renders, this module decides.
// Keeping the reason text here (rather than inline in the JSX) is what lets a
// unit test walk all five `SimilarityKind`s without mounting a modal.

import type { Group, Similarity } from "@/api/types";

/** One delete-marked row as the confirm dialog needs it. */
export interface DeleteConfirmRow {
  path: string;
  basename: string;
  bytes: number;
  similarity: Similarity;
  /**
   * Basename of this row's group keeper (`similarity.kind === "ref"`), which
   * the "Exact duplicate of X" / "Near-duplicate of X" phrasings name.
   * `null` when the group has no Ref row — the reason line then falls back to
   * a keeper-free phrasing rather than printing "of null".
   */
  refBasename: string | null;
}

/** Delete rows sharing one folder, with the subtotal Q6 asks for. */
export interface DeleteFolderBucket {
  folder: string;
  count: number;
  bytes: number;
  rows: DeleteConfirmRow[];
}

/** A row whose size never made it into the manifest contributes 0, not NaN —
 *  the same rule `deleteTotals` applies, so the subtotals add up to the total. */
function safeBytes(n: number): number {
  return Number.isFinite(n) ? n : 0;
}

/**
 * Bucket every delete-marked row in `groups` by its folder.
 *
 * Folders come back in ascending path order (deterministic across renders and
 * across a manifest reload that reorders groups); rows keep their manifest
 * order inside each bucket. Counting the WHOLE passed-in set — not a filtered
 * view — mirrors `deleteTotals`: this is what Execute will act on.
 */
export function deleteFolderBuckets(
  groups: readonly Group[]
): DeleteFolderBucket[] {
  const byFolder = new Map<string, DeleteFolderBucket>();
  for (const group of groups) {
    const ref = group.items.find((row) => row.similarity.kind === "ref");
    const refBasename = ref !== undefined ? ref.basename : null;
    for (const row of group.items) {
      if (row.user_decision !== "delete") continue;
      let bucket = byFolder.get(row.folder);
      if (bucket === undefined) {
        bucket = { folder: row.folder, count: 0, bytes: 0, rows: [] };
        byFolder.set(row.folder, bucket);
      }
      bucket.count += 1;
      bucket.bytes += safeBytes(row.file_size_bytes);
      bucket.rows.push({
        path: row.file_path,
        basename: row.basename,
        bytes: safeBytes(row.file_size_bytes),
        similarity: row.similarity,
        // A Ref row that is itself marked delete does not describe itself as a
        // duplicate "of" anything — it gets the `reason_ref` phrasing below.
        refBasename: row.similarity.kind === "ref" ? null : refBasename,
      });
    }
  }
  return [...byFolder.values()].sort((a, b) =>
    a.folder < b.folder ? -1 : a.folder > b.folder ? 1 : 0
  );
}

/**
 * The reason line for one row: why this file is on the delete list.
 *
 * The vocabulary is REPLY Q6's, verbatim ("Exact duplicate of X" / "N% match —
 * same group" / "Linked match, N%*"), extended to the two kinds Q6 did not
 * spell out (`near_dup`, `none`) and to the Ref row, which reaches this list
 * only inside an all-delete group.
 *
 * The mapping onto `SimilarityKind` is the badge's
 * (`lib/similarityBadge.ts::similarityBadgeState`): `percent`+100 is the exact
 * duplicate, `percent`<100 the direct near-match, `passenger` the indirect one.
 */
export function deleteReasonText(
  row: Pick<DeleteConfirmRow, "similarity" | "refBasename">,
  t: (key: string, fallback: string, params?: Record<string, string | number>) => string
): string {
  const { kind, percent } = row.similarity;
  const ref = row.refBasename;
  switch (kind) {
    case "ref":
      return t(
        "web.delete_confirm.reason_ref",
        "Group reference copy — the whole group is marked delete"
      );
    case "percent":
      if (percent === 100) {
        return ref !== null
          ? t("web.delete_confirm.reason_exact", "Exact duplicate of {ref}", {
              ref,
            })
          : t(
              "web.delete_confirm.reason_exact_generic",
              "Exact duplicate — same group"
            );
      }
      if (percent !== null) {
        return t("web.delete_confirm.reason_near", "{percent}% match — same group", {
          percent: Math.round(percent),
        });
      }
      return t("web.delete_confirm.reason_near_generic", "Near match — same group");
    case "near_dup":
      return ref !== null
        ? t("web.delete_confirm.reason_near_dup", "Near-duplicate of {ref}", {
            ref,
          })
        : t("web.delete_confirm.reason_near_generic", "Near match — same group");
    case "passenger":
      return percent !== null
        ? t("web.delete_confirm.reason_linked", "Linked match, {percent}%*", {
            percent: Math.round(percent),
          })
        : t("web.delete_confirm.reason_linked_generic", "Linked match");
    case "none":
      return t(
        "web.delete_confirm.reason_none",
        "No comparable image — same group"
      );
    default: {
      // Exhaustiveness guard, same shape as lib/format.ts and
      // lib/similarityBadge.ts: a new SimilarityKind is a compile error here.
      const exhaustive: never = kind;
      throw new Error(`Unhandled similarity kind: ${String(exhaustive)}`);
    }
  }
}
