"""Read-only: how many labelled groups are still fully reachable on disk (counts only, no paths).

labels folder = argv[1] or $PM_TESTDATA_LABELS."""
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["PM_TESTDATA_LABELS"])

with open(ROOT / "visual-gt.csv", encoding="utf-8", newline="") as fh:
    lines = [ln for ln in fh if not ln.startswith("#")]
members = defaultdict(set)
for r in csv.DictReader(lines):
    members[r["group_key"]].add(r["path"])

exists = {p: os.path.exists(p) for ps in members.values() for p in ps}
full = [k for k, ps in members.items() if all(exists[p] for p in ps)]
partial = [k for k, ps in members.items() if any(exists[p] for p in ps) and k not in full]
none_ = [k for k, ps in members.items() if not any(exists[p] for p in ps)]
print("groups total", len(members), "fully reachable", len(full),
      "partly", len(partial), "none", len(none_))
ge2 = [k for k, ps in members.items() if sum(exists[p] for p in ps) >= 2]
print("groups with >=2 reachable members", len(ge2))

with open(ROOT / "human-verdicts-30.json", encoding="utf-8") as fh:
    human_keys = [v["key"] for v in json.load(fh)["verdicts"]]
hk = [k for k in human_keys if k in members]
print("human keys found in csv", len(hk), "of", len(human_keys))
print("human groups fully reachable", sum(1 for k in hk if k in full))
print("human groups with >=2 reachable", sum(1 for k in hk if k in ge2))

with open(ROOT / "spotcheck-30.json", encoding="utf-8") as fh:
    sc = json.load(fh)
e0 = sc["entries"][0]
print("spotcheck entry keys", sorted(e0)[:20])
print("verdict_values", sc.get("verdict_values"))
