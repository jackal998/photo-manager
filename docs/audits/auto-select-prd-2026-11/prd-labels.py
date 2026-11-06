"""Read-only census of the visual-autoselect label files (prints shapes and counts, never paths).

labels folder = argv[1] or $PM_TESTDATA_LABELS."""
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["PM_TESTDATA_LABELS"])


def shape(obj, depth=0, max_depth=2):
    pad = "  " * depth
    if isinstance(obj, dict):
        print(f"{pad}dict[{len(obj)}] keys={list(obj)[:12]}")
        if depth < max_depth:
            for k in list(obj)[:6]:
                print(f"{pad} .{k}:")
                shape(obj[k], depth + 1, max_depth)
    elif isinstance(obj, list):
        print(f"{pad}list[{len(obj)}]")
        if obj and depth < max_depth:
            shape(obj[0], depth + 1, max_depth)
    else:
        s = str(obj)
        if ":\\" in s or "/" in s:
            s = "<path-like elided>"
        print(f"{pad}{type(obj).__name__}: {s[:60]}")


for name in [
    "human-verdicts-30.json",
    "human-verdicts-30-recoded.json",
    "spotcheck-30.json",
    "human-calibration.json",
    "group-index-map.json",
    "visual-gt-groups.json",
]:
    print(f"=== {name}")
    with open(ROOT / name, encoding="utf-8") as fh:
        shape(json.load(fh))

print("=== visual-gt.csv")
rows = []
with open(ROOT / "visual-gt.csv", encoding="utf-8", newline="") as fh:
    lines = [ln for ln in fh if not ln.startswith("#")]
for r in csv.DictReader(lines):
    rows.append(r)
print("rows", len(rows))
print("groups", len({r["group_key"] for r in rows}))
print("source", Counter(r["source"] for r in rows))
by_group_conf = {r["group_key"]: r["confidence"] for r in rows}
print("confidence per group", Counter(by_group_conf.values()))
paths = {r["path"] for r in rows}
print("distinct paths", len(paths))
recycle = sum(1 for p in paths if "$RECYCLE.BIN" in p)
print("paths under a recycle-bin folder", recycle)
if "--exists" in sys.argv:
    exist = sum(1 for p in paths if os.path.exists(p))
    print("paths that exist on disk now", exist)
