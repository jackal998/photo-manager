"""v5 → v6 (grouping 6) group anatomy: where did the mega-group go, what are the big groups now,
how many hint rows exist. Read-only. Usage: python main-v6-groups.py <v5.sqlite> <v6.sqlite>"""
from __future__ import annotations

import collections
import os
import sqlite3
import sys


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    return con


def groups_of(rows) -> dict[str, list]:
    g: dict[str, list] = collections.defaultdict(list)
    for r in rows:
        if r["group_id"]:
            g[r["group_id"]].append(r)
    return g


def describe(v, limit=4):
    dirs = collections.Counter(os.path.dirname(r["source_path"]) for r in v).most_common(limit)
    exts = collections.Counter(os.path.splitext(r["source_path"])[1].lower() for r in v)
    hams = collections.Counter(r["hamming_distance"] for r in v)
    confs = collections.Counter((r["action"], r["match_confidence"] or "-") for r in v)
    return f"n={len(v)} ext={dict(exts)} ham={dict(hams)} conf={dict(confs)}\n      dirs={[(os.path.basename(d) or d, n) for d, n in dirs]}"


def main(v5p: str, v6p: str) -> None:
    v5, v6 = connect(v5p), connect(v6p)
    r5 = v5.execute("select * from migration_manifest").fetchall()
    r6 = v6.execute("select * from migration_manifest").fetchall()
    g5, g6 = groups_of(r5), groups_of(r6)
    gid6_by_hash = {r["source_hash"]: r["group_id"] for r in r6 if r["source_hash"]}

    print("== 1. v5's largest groups → where their members sit in v6 ==")
    for gid, v in sorted(g5.items(), key=lambda kv: -len(kv[1]))[:3]:
        now = collections.Counter(gid6_by_hash.get(r["source_hash"]) for r in v)
        sizes = sorted((n for g, n in now.items() if g is not None), reverse=True)
        print(f"  v5 group n={len(v)} → v6: {len([g for g in now if g])} groups, sizes {sizes[:8]}{'…' if len(sizes) > 8 else ''}, isolated={now.get(None, 0)}")
        print("    v5 " + describe(v))

    print("\n== 2. v6's largest groups ==")
    for gid, v in sorted(g6.items(), key=lambda kv: -len(kv[1]))[:5]:
        print("  " + describe(v))

    print("\n== 3. group size distribution ==")
    for label, g in (("v5", g5), ("v6", g6)):
        sizes = collections.Counter(min(len(v), 10) for v in g.values())
        print(f"  {label}: " + ", ".join(f"{k if k < 10 else '10+'}:{sizes[k]}" for k in sorted(sizes)))

    print("\n== 4. hint rows in v6 ==")
    not_grouped = [r for r in r6 if (r["reason"] or "").startswith("hint only (not grouped)")]
    taken = [r for r in r6 if "; hint:" in (r["reason"] or "")]
    print(f"  hint only (not grouped): {len(not_grouped)}  (group_id None: {sum(1 for r in not_grouped if not r['group_id'])}, actions {dict(collections.Counter(r['action'] for r in not_grouped))})")
    print(f"  taken hints ('; hint:'): {len(taken)}  (all grouped: {all(r['group_id'] for r in taken)})")
    print("  not-grouped reasons sample:", [ (r["reason"] or "")[:90] for r in not_grouped[:3]])

    print("\n== 5. cross-moment check: groups mixing shot dates > 2 h apart (v5 vs v6) ==")
    from datetime import datetime
    def parse(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S"):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                pass
        return None
    for label, g in (("v5", g5), ("v6", g6)):
        mixed = 0
        for v in g.values():
            dates = [d for d in (parse(r["shot_date"]) for r in v) if d]
            if len(dates) >= 2 and (max(dates) - min(dates)).total_seconds() > 7200:
                mixed += 1
        print(f"  {label}: groups whose dated members span > 2 h: {mixed} of {len(g)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
