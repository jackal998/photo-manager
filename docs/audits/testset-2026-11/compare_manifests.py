"""Diff two scans of the SAME file set (read-only): grouping partition and label changes.

Usage: python compare_manifests.py <before.sqlite> <after.sqlite>
Rows are joined on source_path. Reports: row coverage, whether the grouping partition (member sets per
group) is identical, and every (action, match_confidence) change with the new reason's prefix.
"""
from __future__ import annotations

import collections
import sqlite3
import sys


def load(path: str) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # never create a file on a mistyped path
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    rows = con.execute("select source_path, action, match_confidence, group_id, reason, source_hash "
                       "from migration_manifest").fetchall()
    return {r["source_path"].lower(): dict(r) for r in rows}


def partition(rows: dict[str, dict]) -> set[frozenset]:
    groups = collections.defaultdict(set)
    for p, r in rows.items():
        if r["group_id"]:
            groups[r["group_id"]].add(p)
    return {frozenset(v) for v in groups.values()}


def main(before: str, after: str) -> None:
    a, b = load(before), load(after)
    common = set(a) & set(b)
    print(f"rows before {len(a)} / after {len(b)} / common {len(common)}; only-before {len(set(a) - set(b))}; "
          f"only-after {len(set(b) - set(a))}")
    sha_same = sum(1 for p in common if (a[p]["source_hash"] or "") == (b[p]["source_hash"] or ""))
    print(f"sha256 unchanged: {sha_same}/{len(common)}")
    pa, pb = partition(a), partition(b)
    print(f"groups before {len(pa)} / after {len(pb)}; identical member sets {len(pa & pb)}; "
          f"only-before {len(pa - pb)}; only-after {len(pb - pa)}")
    changes = collections.Counter()
    examples = collections.defaultdict(list)
    for p in sorted(common):
        ka = (a[p]["action"] or "", a[p]["match_confidence"] or "")
        kb = (b[p]["action"] or "", b[p]["match_confidence"] or "")
        if ka != kb:
            key = ka + kb + ((b[p]["reason"] or "").split("(")[0].strip()[:48],)
            changes[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(p.rsplit("\\", 1)[-1])
    print("label changes (old action, old conf -> new action, new conf, new reason prefix): count  [examples]")
    for k, v in changes.most_common():
        print(f"   {k}: {v}  {examples[k]}")
    if not changes:
        print("   none")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
