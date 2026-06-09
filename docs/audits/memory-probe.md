# Memory Probe — Developer Tool

Regression-guard harness for detecting Python heap + Qt heap leaks
across manifest load cycles (issues #614, #619, #624).

---

## Quick start

```powershell
# Generate the 13k-row fixture once (idempotent, ~0.3s):
.venv\Scripts\python.exe scripts\generate_probe_fixture.py

# Run a 3-reload measurement session:
$env:PHOTO_MANAGER_MEMORY_PROBE = '1'
$env:PHOTO_MANAGER_MEMORY_PROBE_TAG = 'regression-guard-baseline'
$env:PHOTO_MANAGER_PROBE_RELOAD_COUNT = '3'
.venv\Scripts\python.exe main.py --manifest "$env:USERPROFILE\AppData\Local\PhotoManager\probe-fixtures\probe_manifest.sqlite"
```

The app opens, loads the fixture 3 times with 7-second gaps (allowing
the Point 5 idle snapshot to fire between loads), then waits for you to
close it.

---

## Env vars

| Variable | Default | Meaning |
|---|---|---|
| `PHOTO_MANAGER_MEMORY_PROBE` | `""` | Set to `"1"` to activate. Off by default — zero overhead when unset. |
| `PHOTO_MANAGER_MEMORY_PROBE_TAG` | `"untagged"` | Free-text label written to every row — use for A/B or commit SHA. |
| `PHOTO_MANAGER_PROBE_MANIFEST` | `""` | Alternative to `--manifest` CLI arg. |
| `PHOTO_MANAGER_PROBE_RELOAD_COUNT` | `"1"` | Number of sequential reload cycles (7s gap between each). |
| `PHOTO_MANAGER_MEMORY_PROBE_TRIM` | `""` | Set to `"1"` to call `SetProcessWorkingSetSize(-1,-1)` after Point 5 and capture the delta as Point 6. |
| `PHOTO_MANAGER_MEMORY_PROBE_REFERRERS` | `""` | Comma-separated type names (e.g. `"QStandardItem,PhotoGroup"`). After Point 5, dumps `gc.get_referrers()` samples to a sibling JSONL. |

---

## Artifact location

```
~/AppData/Local/PhotoManager/logs/memory_probe_<RUN_ID>.jsonl
~/AppData/Local/PhotoManager/logs/referrers_<type>_<RUN_ID>.jsonl  # Stage 2 only
```

Each run gets a fresh UUID `RUN_ID` even across app restarts.

---

## 5 measurement points

| Point | Label | Where fired | What it measures |
|---|---|---|---|
| 1 | `mainwindow_init_done` | End of `MainWindow.__init__` | Baseline before any manifest load |
| 2 | `worker_post_fetchall` | `ManifestLoadWorker._load` after `list(repo.load())` | SQLite fetch + `PhotoRecord` allocation; `extras.n_records` shows row count |
| 3 | `vm_groups_assigned` | `FileOperationsHandler._on_manifest_loaded` after `vm.groups = groups` | All `PhotoGroup`/`PhotoRecord` objects in memory; `extras.n_groups`, `extras.n_items` |
| 4 | `after_refresh_model` | Same, after `ui_updater.refresh_tree()` returns | Qt `QStandardItemModel` + `QSortFilterProxyModel` live; all `QStandardItem` children created |
| 5 | `idle_5s` | `QTimer.singleShot(5000)` from Point 4 | 5 seconds after tree rebuild — transients should be freed by now |
| 6 | `after_trim` | Immediately after `SetProcessWorkingSetSize` | Windows working-set pressure delta (`rss_before_trim` vs `rss_after_trim`) |

---

## JSONL row schema

```json
{
  "ts": <float unix timestamp>,
  "iso": "<UTC ISO-8601>",
  "run_id": "<hex UUID>",
  "tag": "<tag>",
  "point": <1-6>,
  "label": "<label>",
  "thread": "<thread name>",
  "tracemalloc_total_bytes": <int>,
  "tracemalloc_peak_bytes": <int>,
  "tracemalloc_overhead_bytes": <int>,
  "top30": [{"file": str, "lineno": int, "size_bytes": int, "count": int}, ...],
  "rss_bytes": <int>,
  "vms_bytes": <int>,
  "private_bytes": <int>,
  "system_avail_bytes": <int>,
  "gc_count": [g0, g1, g2],
  "typed_counts": {
    "QStandardItem": int, "PhotoRecord": int, "PhotoGroup": int,
    "QImage": int, "QPixmap": int, "QThread": int, "ManifestLoadWorker": int
  },
  "qt_counter_qstandarditem": <int>,
  "qt_counter_qimage": <int>,
  "extras": {}
}
```

**`tracemalloc_total_bytes`** tracks Python-managed memory only.
**`rss_bytes`** includes the Qt C++ heap + pymalloc arenas + Python heap.
The gap `rss - tracemalloc_total` is dominated by Qt's C++ heap — mostly
`QStandardItem` and `QImage` objects. The `qt_counter_*` fields give
the direct count of live tracked Qt objects.

---

## Quick pandas analysis

```python
import json, pandas as pd
from pathlib import Path

rows = [json.loads(l) for l in Path("memory_probe_<RUN_ID>.jsonl").read_text().splitlines()]
df = pd.DataFrame(rows)

# RSS slope across reloads (Point 4 = after tree rebuild)
p4 = df[df.point == 4][["label", "rss_bytes", "qt_counter_qstandarditem"]].copy()
p4["rss_mb"] = p4.rss_bytes / 1e6
print(p4)

# QStandardItem leak check: should be FLAT across reloads
print("qt_counter_qstandarditem:", df[df.point == 4].qt_counter_qstandarditem.tolist())
```

---

## 4 hypothesis bands

The gap between successive Point 4 snapshots maps to one of four hypotheses:

| Band | Signal | Decision |
|---|---|---|
| **H1 Live retention** | `typed_counts.QStandardItem` or `typed_counts.PhotoRecord` grows monotonically across reloads | Model or PhotoRecord objects are retained by a Python reference (stale signal, listener, old `vm.groups` alias). Root cause: `gc.get_referrers()` via `REFERRERS` env var. |
| **H2 Allocator hoarding** | `tracemalloc_total` is flat but `rss` grows | Python's pymalloc arena pool is not returning pages to the OS. Usually benign. Confirm via TRIM (Point 6 delta < 5 MB = harmless). |
| **H3 Qt heap** | `tracemalloc` flat, `rss` grows, `qt_counter_qstandarditem` grows | `QStandardItem`s from the previous model are not freed — `setSourceModel(None)` + `deleteLater()` was not called. Fixed in #619. |
| **H4 Windows working-set** | All counters flat but RSS grows | Windows is opportunistically growing the working set into free RAM. TRIM Point 6 collapses RSS to near-baseline = harmless. |

**The #619 fix holds** when `qt_counter_qstandarditem` at Point 4 is flat
across all N reloads (not +163,680 per reload as it was before #619).

**The #624 byte-budget LRU holds** when `qt_counter_qimage` at Point 5
is bounded (does not grow proportional to reload count).

---

## Fixture generator

```powershell
.venv\Scripts\python.exe scripts\generate_probe_fixture.py
```

Generates `~/AppData/Local/PhotoManager/probe-fixtures/probe_manifest.sqlite`
with ~13,000 rows across ~2,500 groups (near-dup distribution, NAS-style paths,
realistic EXIF metadata). Fixed PRNG seed 42 — re-running produces an identical
file. The fixture schema mirrors `scanner/manifest.py` CREATE TABLE exactly.
