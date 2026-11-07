"""Shape census: which group shapes exist in the full-library manifest but not in the dev test set?

Usage: python -I extend_testset_shapes.py <full.sqlite> <testset.sqlite> <selection.csv> <outdir> [per_shape]

Read-only over both manifests. A group's "shape" abstracts what the classifier saw: extension classes
(counts capped at 3), one stem or many, one folder or many, the set of actions / confidences, and flags
(burst stack, accepted hint, format duplicate, pixels-differ, screenshot pair, a REVIEW label whose target is
outside the group, a video with no same-stem image in the group). Shapes present in the full library and
absent from the test set are listed by frequency, and up to `per_shape` (default 2) small example groups per
missing shape are written to <outdir>/selection-shapes.csv in the selection.csv column layout, skipping paths
already selected, so copy_testset.py can add them to the NAS copy. Counts go to stdout; the path-bearing
detail goes to <outdir>/shape-census-output.local.txt (gitignored).
"""
from __future__ import annotations

import csv
import os
import random
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from select_testset import CAP_BYTES, COLUMNS, SMALL_VIDEO_BYTES, Row, ext_class, load_rows, stem  # noqa: E402

VIDEO_CLASSES = {"mov_mp4"}
TARGET_RE = re.compile(r"(?:of|with|as) ([^;(]+?\.[A-Za-z0-9]{2,5})(?: \(| but|;|$)")


def shape(members: list[Row]) -> tuple:
    classes = Counter(ext_class(r.ext) for r in members)
    exts = tuple(sorted((c, min(n, 3)) for c, n in classes.items()))
    stems = "1stem" if len({stem(r.name) for r in members}) == 1 else "multi-stem"
    dirs = "1dir" if len({r.folder for r in members}) == 1 else "xdir"
    actions = tuple(sorted({r.action or "undec" for r in members}))
    confs = tuple(sorted({r.confidence or "-" for r in members}))
    names = {r.name for r in members}
    image_stems = {stem(r.name) for r in members if ext_class(r.ext) not in VIDEO_CLASSES}
    flags = []
    if any("burst stack" in r.reason for r in members):
        flags.append("burst")
    if any("hint:" in r.reason for r in members):
        flags.append("hint")
    if any("format duplicate" in r.reason for r in members):
        flags.append("fmt-dup")
    if any("pixels differ" in r.reason for r in members):
        flags.append("pix-differ")
    if any("screenshots" in r.reason for r in members):
        flags.append("screenshot")
    for r in members:
        if r.action == "REVIEW_DUPLICATE":
            m = TARGET_RE.search(r.reason)
            if m and m.group(1).strip().lower() not in names:
                flags.append("cross-group-label")
                break
    if any(ext_class(r.ext) in VIDEO_CLASSES and stem(r.name) not in image_stems for r in members):
        flags.append("video-without-still")
    return (exts, stems, dirs, actions, confs, tuple(flags))


def group_shapes(rows: list[Row]) -> dict:
    by_gid: dict = defaultdict(list)
    for r in rows:
        if r.group_id:
            by_gid[r.group_id].append(r)
    shapes: dict = defaultdict(list)
    for gid, members in by_gid.items():
        shapes[shape(members)].append(members)
    return shapes


def fits(members: list[Row]) -> bool:
    total = sum(r.size for r in members)
    big_video = any(ext_class(r.ext) in VIDEO_CLASSES and r.size > SMALL_VIDEO_BYTES for r in members)
    return total <= CAP_BYTES and not big_video


def fmt(key: tuple) -> str:
    exts, stems, dirs, actions, confs, flags = key
    ext_s = "+".join(f"{c}x{n}" if n > 1 else c for c, n in exts)
    return f"{ext_s} {stems} {dirs} actions={'/'.join(actions)} conf={'/'.join(confs)} flags={','.join(flags) or '-'}"


def main(argv: list[str]) -> int:
    full, testset, selection, outdir = argv[1:5]
    per_shape = int(argv[5]) if len(argv) > 5 else 2
    already = {row["source_path"].lower() for row in csv.DictReader(open(selection, encoding="utf-8"))}
    full_shapes = group_shapes(load_rows(full))
    test_shapes = group_shapes(load_rows(testset))
    missing = {k: v for k, v in full_shapes.items() if k not in test_shapes}
    print(f"shapes: full {len(full_shapes)}, test set {len(test_shapes)}, missing from test set {len(missing)} "
          f"covering {sum(len(v) for v in missing.values())} full-library groups")
    rng = random.Random(20261107)
    picked: list = []
    detail = []
    for key, groups_ in sorted(missing.items(), key=lambda kv: -len(kv[1])):
        candidates = [g for g in groups_ if fits(g) and not any(r.path.lower() in already for r in g)]
        rng.shuffle(candidates)
        chosen = sorted(candidates, key=lambda g: sum(r.size for r in g))[:per_shape]
        print(f"{len(groups_):>5}  {fmt(key)}  -> picked {len(chosen)}")
        detail.append(f"{len(groups_)} {fmt(key)}")
        for g in chosen:
            detail.extend(f"    {r.path}  {r.action or 'undec'}  {r.reason[:60]}" for r in g)
            picked.extend((fmt(key), g[0].group_id, r) for r in g)
    os.makedirs(outdir, exist_ok=True)
    out_csv = os.path.join(outdir, "selection-shapes.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for why, gid, r in picked:
            writer.writerow(["shape", why, gid or "", r.path, r.source_hash or "", r.size, r.action,
                             r.confidence or ""])
    with open(os.path.join(outdir, "shape-census-output.local.txt"), "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(detail) + "\n")
    total = sum(r.size for _, _, r in picked)
    print(f"selection-shapes.csv: {len(picked)} files in {len({g for _, g, _ in picked})} groups, "
          f"{total / 1024**2:.0f} MB -> {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
