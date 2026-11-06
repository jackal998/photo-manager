"""Two checks on main-v5-followup.py's findings (read-only).

Usage: python main-v5-followup2.py <old.sqlite> <new.sqlite>
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


def main(old_path: str, new_path: str) -> None:
    old, new = connect(old_path), connect(new_path)
    orows = old.execute("select * from migration_manifest").fetchall()
    nrows = new.execute("select * from migration_manifest").fetchall()
    new_by_hash = {r["source_hash"]: r for r in nrows if r["source_hash"]}
    hash_count = collections.Counter(r["source_hash"] for r in nrows if r["source_hash"])

    print("== 1. the 1,555 EXACT->UNDATED JPGs: are they byte-identical copies? ==")
    moved_old = [r for r in orows if r["action"] == "EXACT" and r["source_hash"] in new_by_hash
                 and new_by_hash[r["source_hash"]]["action"] == "UNDATED"]
    moved_new = [new_by_hash[r["source_hash"]] for r in moved_old]
    print("sha256 shared with >=2 rows in new:", sum(1 for r in moved_new if hash_count[r["source_hash"]] >= 2))
    print("in a group now:", sum(1 for r in moved_new if r["group_id"]),
          " hamming 0:", sum(1 for r in moved_new if r["hamming_distance"] == 0))
    print("June reason samples:", collections.Counter((r["reason"] or "")[:50] for r in moved_old).most_common(4))
    print("new reason samples:", collections.Counter((r["reason"] or "")[:50] for r in moved_new).most_common(4))
    print("June shot_date / creation_date / mtime present:",
          sum(1 for r in moved_old if r["shot_date"]), sum(1 for r in moved_old if r["creation_date"]),
          sum(1 for r in moved_old if r["mtime"]))
    print("new shot_date / creation_date / mtime present:",
          sum(1 for r in moved_new if r["shot_date"]), sum(1 for r in moved_new if r["creation_date"]),
          sum(1 for r in moved_new if r["mtime"]))

    print("\n== 2. PNG-only pHash-0 groups still 'high': byte-identical? ==")
    groups: dict[str, list] = collections.defaultdict(list)
    for r in nrows:
        if r["group_id"]:
            groups[r["group_id"]].append(r)
    png0_high = [v for v in groups.values()
                 if all(os.path.splitext(r["source_path"])[1].lower() == ".png" for r in v)
                 and not any((r["hamming_distance"] or 0) > 0 for r in v)
                 and {(r["match_confidence"] or "-") for r in v if r["action"]} == {"high"}]
    one_sha = sum(1 for v in png0_high if len({r["source_hash"] for r in v}) == 1)
    print("groups:", len(png0_high), " with a single sha256 (true byte copies):", one_sha)
    multi = [v for v in png0_high if len({r["source_hash"] for r in v}) > 1]
    for v in multi[:3]:
        print("  multi-sha example:", [(os.path.basename(r["source_path"]), r["action"], r["match_confidence"]) for r in v][:4])

    print("\n== 3. the one exif-missing batch: rows with exif_tag_count NULL/0 but a date in June ==")
    old_dates = {r["source_hash"]: r["shot_date"] for r in orows if r["source_hash"]}
    lost = [r for r in nrows if not r["shot_date"] and old_dates.get(r["source_hash"])]
    print("files dated in June, undated now:", len(lost),
          " ext:", collections.Counter(os.path.splitext(r["source_path"])[1].lower() for r in lost).most_common(4))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
