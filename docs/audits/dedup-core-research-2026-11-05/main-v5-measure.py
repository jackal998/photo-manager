"""Before/after measurement: June manifest (recipe 4, grouping 3) vs the recipe-5 rescan.

Usage: python main-v5-measure.py <old.sqlite> <new.sqlite>
Read-only (query_only). Every number is stored-data tier.
"""
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


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[k]


def ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def groups_of(rows) -> dict[str, list]:
    g: dict[str, list] = collections.defaultdict(list)
    for r in rows:
        if r["group_id"]:
            g[r["group_id"]].append(r)
    return g


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main(old_path: str, new_path: str) -> None:
    old = connect(old_path)
    new = connect(new_path)
    orows = old.execute("select * from migration_manifest").fetchall()
    nrows = new.execute("select * from migration_manifest").fetchall()
    ncols = {r[1] for r in new.execute("pragma table_info(migration_manifest)")}
    print(f"old rows={len(orows)}  new rows={len(nrows)}")
    print("new has match_confidence:", "match_confidence" in ncols,
          " phash_quality:", "phash_quality" in ncols)

    # 1. phash_quality distribution
    section("1. phash_quality distribution (new)")
    q_all = [r["phash_quality"] for r in nrows if r["phash_quality"] is not None]
    q_none = sum(1 for r in nrows if r["phash_quality"] is None)
    print(f"with Q={len(q_all)}  without Q (videos / unhashed)={q_none}")
    if q_all:
        print(f"min={min(q_all):.1f} p1={pct(q_all,1):.1f} p5={pct(q_all,5):.1f} "
              f"p50={pct(q_all,50):.1f} p95={pct(q_all,95):.1f} max={max(q_all):.1f}")
    by_ext: dict[str, list[float]] = collections.defaultdict(list)
    for r in nrows:
        if r["phash_quality"] is not None:
            by_ext[ext(r["source_path"])].append(r["phash_quality"])
    print(f"{'ext':8}{'n':>7}{'<16':>7}{'p1':>7}{'p50':>7}")
    for e, vals in sorted(by_ext.items(), key=lambda kv: -len(kv[1]))[:10]:
        below = sum(1 for v in vals if v < 16.0)
        print(f"{e:8}{len(vals):7d}{below:7d}{pct(vals,1):7.1f}{pct(vals,50):7.1f}")

    # 2. files the Q gate excluded
    section("2. Q gate (phash_quality < 16): excluded files")
    gated = [r for r in nrows if r["phash_quality"] is not None and r["phash_quality"] < 16.0]
    print(f"gated files={len(gated)}  ({100*len(gated)/max(1,len(q_all)):.2f}% of hashed)")
    old_by_hash: dict[str, list] = collections.defaultdict(list)
    for r in orows:
        if r["source_hash"]:
            old_by_hash[r["source_hash"]].append(r)
    gated_old_grouped = sum(
        1 for r in gated if any(o["group_id"] for o in old_by_hash.get(r["source_hash"], []))
    )
    gated_new_grouped = sum(1 for r in gated if r["group_id"])
    print(f"gated files that were IN a June group={gated_old_grouped}  "
          f"still in a group now (via sha256 only)={gated_new_grouped}")
    print("gated examples (lowest Q):")
    for r in sorted(gated, key=lambda r: r["phash_quality"])[:8]:
        print(f"  Q={r['phash_quality']:5.2f}  {r['source_path']}")

    # 3. group / action / confidence breakdown
    section("3. groups, actions, confidence")
    og, ng = groups_of(orows), groups_of(nrows)
    print(f"groups: old={len(og)}  new={len(ng)}")
    print(f"grouped rows: old={sum(len(v) for v in og.values())}  new={sum(len(v) for v in ng.values())}")
    print("action old:", dict(collections.Counter(r["action"] for r in orows)))
    print("action new:", dict(collections.Counter(r["action"] for r in nrows)))
    if "match_confidence" in ncols:
        print("match_confidence new:", dict(collections.Counter(
            (r["match_confidence"] or "<null>") for r in nrows)))
        print("action x confidence (new, grouped rows):")
        cc = collections.Counter((r["action"], r["match_confidence"] or "<null>")
                                 for r in nrows if r["group_id"])
        for (a, c), n in sorted(cc.items()):
            print(f"  {a:18} {c:8} {n}")
    big_old = sorted((len(v) for v in og.values()), reverse=True)[:5]
    big_new = sorted((len(v) for v in ng.values()), reverse=True)[:5]
    print(f"largest groups old={big_old} new={big_new}")
    h0_old = sum(1 for v in og.values() if all((r["hamming_distance"] or 0) == 0 for r in v))
    h0_new = sum(1 for v in ng.values() if all((r["hamming_distance"] or 0) == 0 for r in v))
    print(f"pHash-0-only groups old={h0_old} new={h0_new}  near-dup groups old={len(og)-h0_old} new={len(ng)-h0_new}")

    # 4. per-file transitions joined on sha256
    section("4. transitions by sha256 (old action -> new action)")
    new_by_hash = {r["source_hash"]: r for r in nrows if r["source_hash"]}
    trans = collections.Counter()
    matched = 0
    for r in orows:
        n = new_by_hash.get(r["source_hash"])
        if n is None:
            trans[(r["action"], "<gone>")] += 1
            continue
        matched += 1
        trans[(r["action"], n["action"])] += 1
    print(f"old rows matched in new by sha256: {matched}/{len(orows)}")
    for (a, b), n in sorted(trans.items(), key=lambda kv: -kv[1])[:14]:
        print(f"  {a:18} -> {b:18} {n}")
    # June deletes: where do those files sit now?
    section("4b. the 556 June deletes, by new action/confidence")
    dels = [r for r in orows if (r["user_decision"] or "") == "delete"]
    dc = collections.Counter()
    for r in dels:
        n = new_by_hash.get(r["source_hash"])
        dc[(n["action"], n["match_confidence"]) if n else ("<gone>", None)] += 1
    for k, n in sorted(dc.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}")

    # 5. class A / B / C fixtures
    section("5. class A/B/C fixtures")
    fx = {
        "A eclipse 160439": "MVIMG_20200621_160439.jpg",
        "A eclipse 160730": "MVIMG_20200621_160730.jpg",
        "A lights IMG_8511": "IMG_8511.JPG",
        "B torii IMG_1391": "IMG_1391.DNG",
        "B torii IMG_1393": "IMG_1393.DNG",
        "B torii IMG_1395": "IMG_1395.DNG",
    }
    for label, name in fx.items():
        hits = [r for r in nrows if r["source_path"].lower().endswith(name.lower())]
        for r in hits:
            gsize = len(ng.get(r["group_id"], [])) if r["group_id"] else 0
            print(f"  {label:18} Q={r['phash_quality'] if r['phash_quality'] is not None else 'None':>6} "
                  f"action={r['action']:16} conf={r['match_confidence'] or '-':6} "
                  f"ham={r['hamming_distance']} group_size={gsize}  {r['source_path']}")
        if not hits:
            print(f"  {label:18} NOT IN NEW MANIFEST")
    old_cccc = [r for r in orows if r["phash"] == "cccc3333cccc3333"]
    print(f"  June cccc3333cccc3333 rows={len(old_cccc)}; now:",
          dict(collections.Counter(
              (new_by_hash[r["source_hash"]]["action"] if r["source_hash"] in new_by_hash else "<gone>")
              for r in old_cccc)))
    # class C: groups whose members are all PNG
    def png_only(gs: dict[str, list]) -> tuple[int, int]:
        g_all = [v for v in gs.values() if all(ext(r["source_path"]) == ".png" for r in v)]
        near = [v for v in g_all if any((r["hamming_distance"] or 0) > 0 for r in v)]
        return len(g_all), len(near)
    print(f"  class C PNG-only groups (all / near-dup): old={png_only(og)} new={png_only(ng)}")
    if "match_confidence" in ncols:
        png_low = sum(1 for v in ng.values()
                      if all(ext(r["source_path"]) == ".png" for r in v)
                      and all((r["match_confidence"] or "") == "low" for r in v if r["action"] != "KEEP"))
        print(f"  class C PNG-only groups whose non-KEEP members are all low confidence (new): {png_low}")

    # 6. decision_log
    section("6. decision_log")
    tbl = new.execute("select name from sqlite_master where type='table' and name='decision_log'").fetchone()
    trg = [r[0] for r in new.execute("select name from sqlite_master where type='trigger'")]
    print("table exists:", bool(tbl), " triggers:", trg)
    if tbl:
        n = new.execute("select count(*) from decision_log").fetchone()[0]
        print("rows:", n)
        if n:
            print(dict(collections.Counter(
                (r[0], r[1]) for r in new.execute("select verb, source from decision_log"))))
    old_tbl = old.execute("select 1 from sqlite_master where type='table' and name='decision_log'").fetchone()
    print("old manifest had decision_log:", bool(old_tbl))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
