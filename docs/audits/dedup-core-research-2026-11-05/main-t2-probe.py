"""T2 burst probe, read-only. 242 selection mirrors main-v6-crossmoment.py:36-69.

Usage: python main-t2-probe.py <manifest.sqlite>
A group is a BURST candidate when its dated, pHash-bearing, non-low rows span > 2 s and no consecutive
gap exceeds 2 s. Variant A: core = match_confidence != 'low' (the manifest cannot tell a REVIEW-low
frame from a hint leaf, so ALL_rule_* counts are a lower bound).
"""
from __future__ import annotations

import collections
import itertools
import sqlite3
import sys
from datetime import datetime
from pathlib import PureWindowsPath


def parse(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    return None


def disagree(a, b):
    g = abs((a - b).total_seconds())
    return not (g <= 2 or (g % 1800 == 0 and g <= 14 * 3600))


def tz_like(g):
    return 2 < g <= 14 * 3600 + 2 and abs(g - round(g / 1800) * 1800) <= 2


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def main(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    groups = collections.defaultdict(list)
    for r in con.execute("SELECT source_path, action, match_confidence, shot_date, source_hash, "
                         "phash, group_id FROM migration_manifest WHERE group_id IS NOT NULL"):
        groups[r["group_id"]].append(r)
    c, maxgaps, spans, pools = collections.Counter(), [], [], {"BURST": [], "NOT-BURST": []}
    for gid in sorted(groups):
        v = groups[gid]
        core = [r for r in v if r["match_confidence"] != "low"]
        frames = sorted(((parse(r["shot_date"]), r) for r in core if r["phash"] and parse(r["shot_date"])),
                        key=lambda t: (t[0], t[1]["source_path"]))
        times = [d for d, _ in frames]
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        span = (times[-1] - times[0]).total_seconds() if times else 0.0
        maxgap = max(gaps, default=0.0)
        rule = span > 2 and maxgap <= 2
        if rule:
            sha_n = collections.Counter(r["source_hash"] for _, r in frames)
            c["ALL_rule_groups"] += 1
            c["ALL_rule_review_relabel"] += sum(r["action"] == "REVIEW_DUPLICATE" for _, r in frames)
            c["ALL_rule_exact_formatdup_relabel"] += sum(
                r["action"] == "EXACT" and sha_n[r["source_hash"]] == 1 for _, r in frames)
            c["ALL_rule_exact_bytecopy_kept"] += sum(
                r["action"] == "EXACT" and sha_n[r["source_hash"]] > 1 for _, r in frames)
        dh = [(parse(r["shot_date"]), r) for r in core if parse(r["shot_date"])]
        if not any(disagree(a, b) for (a, _), (b, _) in itertools.combinations(dh, 2)):
            continue
        if len({r["source_hash"] for _, r in dh}) < 2:
            continue
        c["n242"] += 1
        maxgaps.append(maxgap)
        spans.append(span)
        for g in (2, 3, 5, 10):
            c[f"n242_all_gaps_le_{g:02d}s_and_span_gt_2"] += span > 2 and maxgap <= g
        c["n242_photo_span_le_2s"] += span <= 2
        c["n242_has_undated_member"] += any(r["shot_date"] is None for r in v)
        c["n242_has_undated_core"] += any(r["shot_date"] is None for r in core)
        c["n242_has_video_core"] += any(not r["phash"] for r in core)
        c["n242_has_tz_gap"] += any(tz_like(x) for x in gaps)
        pools["BURST" if rule else "NOT-BURST"].append((len(v), gid, maxgap, span, v))
    for k in sorted(c):
        print(f"{k}: {c[k]}")
    for name, xs in (("maxgap_s", maxgaps), ("span_s", spans)):
        print(f"{name} p10={pct(xs, .1)} p50={pct(xs, .5)} p90={pct(xs, .9)} max={max(xs, default=None)}")
    for label, pool in pools.items():
        print(f"\n== 5 largest {label} of the 242 ==")
        for n, gid, mg, sp, v in sorted(pool, key=lambda t: (-t[0], t[1]))[:5]:
            print(f"n={n} maxgap={mg:.0f}s span={sp:.0f}s dir={PureWindowsPath(gid).parent.name}")
            for r in sorted(v, key=lambda r: (r["shot_date"] or "", r["source_path"]))[:15]:
                print(f"   {r['shot_date'] or '-':<19} {r['action'] or '-':<17} "
                      f"{r['match_confidence'] or '-':<5} {PureWindowsPath(r['source_path']).name}")


if __name__ == "__main__":
    main(sys.argv[1])
