"""After a test-set extension scan: where did the newly added groups land under the current grouping?

Usage: python -I analyze_extension.py <selection-shapes.csv> <new-manifest.sqlite> [detail.local.txt]

Read-only. For every selected source group (group_id column of the selection) it looks up the new scan's
rows for those files and reports: intact (one new group holding exactly those files), merged (new group also
holds other files), split (members in several new groups), dissolved (every member group_id NULL), partial
(some members NULL). Then a per-shape table of the outcomes and the action/confidence mix of the new rows.
Counts to stdout; path-bearing detail to the optional file.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from collections import Counter, defaultdict


def main(argv: list[str]) -> int:
    selection, manifest = argv[1], argv[2]
    detail_path = argv[3] if len(argv) > 3 else None
    sel = list(csv.DictReader(open(selection, encoding="utf-8")))
    by_src: dict = defaultdict(list)
    shape_of: dict = {}
    for r in sel:
        by_src[r["group_id"]].append(r["source_path"].lower())
        shape_of[r["group_id"]] = r["why"]
    con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)  # never create a file on a mistyped path
    con.execute("PRAGMA query_only = 1")
    new = {p.lower(): (g, a or "", c or "", rs or "") for p, g, a, c, rs in con.execute(
        "select source_path, group_id, action, match_confidence, reason from migration_manifest")}
    members_of_new_group: dict = defaultdict(set)
    for p, row in new.items():
        if row[0]:
            members_of_new_group[row[0]].add(p)

    def match(path: str) -> str | None:
        # the scan stores the test copy as <ROOT>\<drive letter>\<rest>; match on "\<drive>\<rest>"
        tail = "\\" + path[0] + path[2:]
        hits = [q for q in new if q.endswith(tail)]
        return hits[0] if len(hits) == 1 else None

    outcome: dict = {}
    detail = []
    mix = Counter()
    for src_gid, paths in by_src.items():
        found = [match(p) for p in paths]
        if any(f is None for f in found):
            outcome[src_gid] = "unmatched-path"
            detail.append(f"UNMATCHED {src_gid}: {[p for p, f in zip(paths, found) if f is None][:2]}")
            continue
        gids = {new[f][0] for f in found}
        for f in found:
            mix[(new[f][1] or "undec", new[f][2] or "-")] += 1
        if gids == {None}:
            outcome[src_gid] = "dissolved"
        elif None in gids:
            outcome[src_gid] = "partial-null"
        elif len(gids) > 1:
            outcome[src_gid] = "split"
        else:
            g = next(iter(gids))
            others = members_of_new_group[g] - set(found)
            outcome[src_gid] = "merged" if others else "intact"
            if others:
                detail.append(f"MERGED {src_gid} + {len(others)} other file(s)")
        if outcome[src_gid] not in ("intact",):
            detail.append(f"{outcome[src_gid].upper()} {shape_of[src_gid]} :: {src_gid}")
            detail.extend(f"    {f}  {new[f][1] or 'undec'} {new[f][2] or '-'}  {new[f][3][:70]}" for f in found)
    print("selected groups:", len(by_src), dict(Counter(outcome.values()).most_common()))
    per_shape: dict = defaultdict(Counter)
    for gid, o in outcome.items():
        per_shape[shape_of[gid]][o] += 1
    print("shapes with a non-intact outcome:")
    for shp, c in sorted(per_shape.items(), key=lambda kv: -sum(v for k, v in kv[1].items() if k != "intact")):
        if set(c) != {"intact"}:
            print(f"   {dict(c)}  {shp}")
    print("new rows action/confidence mix:", mix.most_common())
    if detail_path:
        with open(detail_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(detail) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
