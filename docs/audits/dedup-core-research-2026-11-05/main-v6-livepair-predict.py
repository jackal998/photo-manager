"""Predict the GROUPING "9" effect on a GROUPING 6-8 manifest (read-only): how many groups dissolve.

Usage: python main-v6-livepair-predict.py <manifest.sqlite>

Under GROUPING "9" an image<->video same-stem pair edge joins a group only when some member is already
grouped by a high edge or an accepted low hint. From a stored manifest the accepted edges are visible as
labels: EXACT rows, REVIEW_DUPLICATE rows whose target basename is in the same group, rows carrying a
"hint:" marker (accepted low hint) and burst-stack rows. A group with none of those is held together by
pair edges alone. If it contains a video and its image rows have distinct stems, it dissolves entirely.
If two images share a stem (RAW+JPG), their image<->image pair edge is "high", so the still is joined and
the video attaches to it: such a group is unchanged (confirmed on the test set g8 -> g9: 16 dissolved,
296 identical, the one RAW+JPG+video group kept its video). Prints counts only; no paths.
"""
from __future__ import annotations

import collections
import os
import re
import sqlite3
import sys

VIDEO = {".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mts", ".m2ts", ".mkv"}
TARGET_RE = re.compile(r"(?:duplicate|match with|same pHash as|stack) .*?(?:of |with |as )([^;(]+?)(?: \(| but|;|$)")


def basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def stem(path: str) -> str:
    return re.sub(r"\s*\(\d+\)$", "", os.path.splitext(basename(path))[0]).lower()


def main(manifest: str) -> None:
    con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)  # never create a file on a mistyped path
    con.execute("PRAGMA query_only = 1")
    groups: dict[str, list[tuple[str, str, str]]] = collections.defaultdict(list)
    for path, action, reason, gid in con.execute(
        "select source_path, action, reason, group_id from migration_manifest where group_id is not null"
    ):
        groups[gid].append((path, action or "", reason or ""))
    dissolve = unchanged_image_pair = anchored = 0
    dissolve_rows = 0
    for members in groups.values():
        names = {basename(p).lower() for p, _, _ in members}
        held = False
        for _, action, reason in members:
            if action == "EXACT" or "hint:" in reason or "burst stack" in reason:
                held = True
                break
            if action == "REVIEW_DUPLICATE":
                m = TARGET_RE.search(reason)
                if m and m.group(1).strip().lower() in names:
                    held = True
                    break
        if held:
            anchored += 1
            continue
        videos = [p for p, _, _ in members if os.path.splitext(p)[1].lower() in VIDEO]
        images = [p for p, _, _ in members if os.path.splitext(p)[1].lower() not in VIDEO]
        if not videos:
            anchored += 1  # image-only; cannot be pair-only under this rule (or an unparsed label)
            continue
        if len({stem(p) for p in images}) == len(images):
            dissolve += 1
            dissolve_rows += len(members)
        else:
            unchanged_image_pair += 1  # image<->image high edge joins the still; the video follows
    total = len(groups)
    print(f"groups {total}: anchored (unchanged) {anchored}; dissolve entirely {dissolve} "
          f"({dissolve_rows} rows -> group_id NULL); image-pair groups with a video, unchanged "
          f"{unchanged_image_pair}")
    print(f"predicted group count after GROUPING 9: {total - dissolve} (upper bound; hint-merges of two "
          f"pair-only groups subtract one more each)")


if __name__ == "__main__":
    main(sys.argv[1])
