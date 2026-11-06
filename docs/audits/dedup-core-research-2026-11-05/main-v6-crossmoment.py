"""Cross-moment groups, judged the way the classifier judges time (read-only).

A pair "disagrees" when its shot dates differ by more than 2 s and the gap is not a whole
half-hour multiple of at most 14 h (timezone-shifted copies agree). A group is a cross-moment
group when some pair of its HIGH-confidence rows (EXACT / REVIEW high / keeper) disagrees —
low rows and hint leaves are allowed to disagree by design.
Usage: python main-v6-crossmoment.py <a.sqlite> <b.sqlite>
"""
from __future__ import annotations

import collections
import itertools
import sqlite3
import sys
from datetime import datetime


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    return con


def parse(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    return None


def disagree(a: datetime, b: datetime) -> bool:
    gap = abs((a - b).total_seconds())
    if gap <= 2:
        return False
    if gap % 1800 == 0 and gap <= 14 * 3600:
        return False
    return True


def analyse(path: str) -> dict:
    rows = connect(path).execute("select * from migration_manifest").fetchall()
    groups: dict[str, list] = collections.defaultdict(list)
    for r in rows:
        if r["group_id"]:
            groups[r["group_id"]].append(r)
    out = collections.Counter()
    worst: list[tuple[int, str]] = []
    for gid, v in groups.items():
        high = [r for r in v if r["match_confidence"] != "low"]
        dated_high = [(parse(r["shot_date"]), r) for r in high]
        dated_high = [(d, r) for d, r in dated_high if d]
        dated_all = [d for d in (parse(r["shot_date"]) for r in v) if d]
        any_dis = any(disagree(a, b) for a, b in itertools.combinations(dated_all, 2))
        high_dis = any(disagree(a, b) for (a, _), (b, _) in itertools.combinations(dated_high, 2))
        out["groups"] += 1
        if any_dis:
            out["span_any"] += 1
        if high_dis:
            out["span_high_only"] += 1
            # distinct sha256 among the disagreeing high rows: byte copies cannot disagree genuinely
            shas = {r["source_hash"] for _, r in dated_high}
            if len(shas) >= 2:
                out["span_high_distinct_sha"] += 1
                worst.append((len(v), gid))
    worst.sort(reverse=True)
    out["worst"] = worst[:5]
    return out


def main(a: str, b: str) -> None:
    for label, p in (("before", a), ("after", b)):
        o = analyse(p)
        print(f"{label}: groups={o['groups']}  any-pair disagree={o['span_any']}  "
              f"high-rows disagree={o['span_high_only']}  high+distinct sha={o['span_high_distinct_sha']}")
        print("   largest high-disagree groups (size, group_id):", [(n, g[-50:]) for n, g in o["worst"]])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
