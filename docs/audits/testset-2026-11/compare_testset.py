"""Compare a test-set scan against the rows it was selected from (read-only).

Usage: python compare_testset.py <selection.csv> <ROOT> <testset_manifest.sqlite>
Joins test-set rows to the original library rows by original path (ROOT/<letter>/rel -> <letter>:\\rel),
then reports: coverage, grouping partition equality (same sets of files per group), and every
action / match_confidence change with the new reason prefix.
"""
from __future__ import annotations

import collections
import csv
import sqlite3
import sys
from pathlib import PureWindowsPath


def original_path(root: str, p: str) -> str | None:
    """Map ROOT/<letter>/rel back to <letter>:\\rel. The scanner stores the mapped drive as a UNC path
    (\\\\server\\share\\...), so match on the ROOT folder's name rather than on the full prefix."""
    marker = "\\" + PureWindowsPath(root).name.lower() + "\\"
    low = p.replace("/", "\\").lower()
    i = low.find(marker)
    if i < 0:
        return None
    rest = p.replace("/", "\\")[i + len(marker):]
    parts = rest.split("\\")
    if len(parts) < 2 or len(parts[0]) != 1:
        return None
    return f"{parts[0]}:\\" + "\\".join(parts[1:])


def partition(rows, key_path, key_group) -> dict[frozenset, str]:
    groups = collections.defaultdict(set)
    for r in rows:
        if r[key_group]:
            groups[r[key_group]].add(r[key_path])
    return {frozenset(v): k for k, v in groups.items()}


def main(selection: str, root: str, manifest: str) -> None:
    sel = list(csv.DictReader(open(selection, encoding="utf-8", newline="")))
    sel_by_path = {r["source_path"].lower(): r for r in sel}
    con = sqlite3.connect(manifest)
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    rows = con.execute("select source_path, action, match_confidence, group_id, reason, source_hash from migration_manifest").fetchall()
    new = []
    unmatched = 0
    for r in rows:
        op = original_path(root, r["source_path"])
        if op is None or op.lower() not in sel_by_path:
            unmatched += 1
            continue
        new.append({"orig": op.lower(), "action": r["action"] or "", "conf": r["match_confidence"] or "",
                    "group": r["group_id"], "reason": r["reason"] or "", "sha": r["source_hash"]})
    seen = {n["orig"] for n in new}
    print(f"selection rows {len(sel)}; manifest rows {len(rows)}; matched {len(new)}; "
          f"missing from scan {len(sel) - len(seen)}; manifest rows outside the selection {unmatched}")
    sha_ok = sum(1 for n in new if (sel_by_path[n['orig']]['source_hash'] or '').lower() == (n['sha'] or '').lower())
    print(f"sha256 equal to the v6 manifest: {sha_ok}/{len(new)}")

    old_part = partition([{"p": r["source_path"].lower(), "g": r["group_id"]} for r in sel], "p", "g")
    new_part = partition([{"p": n["orig"], "g": n["group"]} for n in new], "p", "g")
    same = len(set(old_part) & set(new_part))
    print(f"groups v6 {len(old_part)} / testset {len(new_part)}; identical member sets {same}; "
          f"only in v6 {len(set(old_part) - set(new_part))}; only in testset {len(set(new_part) - set(old_part))}")
    for members in sorted(set(old_part) - set(new_part), key=len, reverse=True)[:8]:
        now = collections.Counter(next((n["group"] for n in new if n["orig"] == m), None) for m in members)
        print(f"   v6 group of {len(members)} -> testset groups: {sorted((v for k, v in now.items() if k), reverse=True)} ungrouped={now.get(None, 0)}")

    changes = collections.Counter()
    burst_rows = 0
    for n in new:
        o = sel_by_path[n["orig"]]
        if n["reason"].startswith("burst stack"):
            burst_rows += 1
        key = (o["action"] or "", o["match_confidence"] or "", n["action"], n["conf"])
        if key[:2] != key[2:]:
            changes[key + (n["reason"].split(":")[0][:28],)] += 1
    print(f"rows with a 'burst stack' reason: {burst_rows}")
    print("action/confidence changes (old action, old conf -> new action, new conf, reason prefix): count")
    for k, v in changes.most_common():
        print(f"   {k}: {v}")
    if not changes:
        print("   none")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
