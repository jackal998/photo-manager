"""Read-only: reachable counts for the human-label slices the PRD's acceptance criteria use.

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
for r in csv.DictReader(lines):
    members[r["group_key"]].add(r["path"])
full = {k for k, ps in members.items() if all(os.path.exists(p) for p in ps)}

cal = json.load(open(ROOT / "human-calibration.json", encoding="utf-8"))
rows = cal["validation"]["rows"]
tv = cal["tables_validated_classes"]
strict_rows = tv["f_pick_accuracy"]["strict_single_frame_named_in_note"]["rows"]
ext_rows = tv["f_pick_accuracy"]["extended_incl_implied_and_radio"]["rows"]
print("strict picks", len(strict_rows), "reachable", sum(r["key"] in full for r in strict_rows))
print("extended picks", len(ext_rows), "reachable", sum(r["key"] in full for r in ext_rows))
print("human_pick values", Counter(str(r["human_pick"])[:12] for r in rows))
for val in ("all", "none", "undecided"):
    ks = [r["key"] for r in rows if str(r["human_pick"]) == val]
    print(f"human_pick={val}", len(ks), "reachable", sum(k in full for k in ks))
pp = [r["key"] for r in rows if r.get("subject_people_or_pet")]
print("subject_people_or_pet true", len(pp), "reachable", sum(k in full for k in pp))
pol = tv["policy_counts"]
for name in ("people_or_pets_never_deleted", "identical_metadata_rule", "edit_relation_flagged"):
    ks = pol[name]
    print(name, len(ks), "reachable", sum(k in full for k in ks))
gc = [r["key"] for r in rows if r.get("grouping_complaint")]
print("grouping complaints", len(gc), "reachable", sum(k in full for k in gc))
