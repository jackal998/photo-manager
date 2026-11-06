"""Read-only: reachability of the human-labelled groups by validated class (counts only).

labels folder = argv[1] or $PM_TESTDATA_LABELS."""
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["PM_TESTDATA_LABELS"])

with open(ROOT / "visual-gt.csv", encoding="utf-8", newline="") as fh:
    lines = [ln for ln in fh if not ln.startswith("#")]
members = defaultdict(set)
tags = {}
for r in csv.DictReader(lines):
    members[r["group_key"]].add(r["path"])
    tags[r["group_key"]] = r["case_tags"]
full = {k for k, ps in members.items() if all(os.path.exists(p) for p in ps)}

cal = json.load(open(ROOT / "human-calibration.json", encoding="utf-8"))
rows = cal["validation"]["rows"]
print("validation row keys", sorted(rows[0]))
cls_key = next((k for k in ("validated_class", "class", "final_class", "analyst_class") if k in rows[0]), None)
print("class field", cls_key)
if cls_key:
    allc = Counter(r[cls_key] for r in rows)
    reach = Counter(r[cls_key] for r in rows if r["key"] in full)
    for c in sorted(allc):
        print(f"  {c}: {reach[c]}/{allc[c]} reachable")
tv = cal["tables_validated_classes"]
strict = tv["c_clear_winner_overclaim"]["human_single_preferred_strict_keys"]
ext = tv["c_clear_winner_overclaim"]["human_single_preferred_extended_keys"]
print("strict-pick keys (clear-winner strata)", len(strict), "reachable", sum(k in full for k in strict))
print("extended-pick keys", len(ext), "reachable", sum(k in full for k in ext))
print("f_pick_accuracy", json.dumps(tv["f_pick_accuracy"])[:600])
print("policy_counts", json.dumps(tv["policy_counts"])[:600])
gp = [k for k, t in tags.items() if "group portrait" in t]
print("group-portrait groups", len(gp), "reachable", sum(k in full for k in gp))
print("all case tags", Counter(t for v in tags.values() for t in v.split("|") if not t.startswith("labeller")))
