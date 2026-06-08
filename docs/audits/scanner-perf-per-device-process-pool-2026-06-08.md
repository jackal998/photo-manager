# Per-device process pools — decision record (2026-06-08, post-#609)

**Status:** Implement-then-verify. Pre-fix monitor showed real regression
(50% idle CPU, 900s timeout on the user's actual D+H+J workload). Fix
applied, **same monitor probe re-run on same args**, post-fix data
recorded below. No claims pre-measurement; this record exists because
the measurement reproduced the predicted improvement.

## Trigger

User pushed back after the #609 merge: "我現在實際跑起來 NAS 跟 CPU 就是在發呆"
(NAS and CPU are both idle when I actually run it now). The previous
"physical limit" framing didn't hold up. I had measured only D+J in the
#609 analysis — the user's persistent sources are **D:\\Takeout-0508 +
H:\\Photos\\MobileBackup + J:\\圖片** (per the #598 audit). H: was the
gap in my prior testing.

## Method

`scripts/probe_process_monitor.py` — wraps a real `ScanWorker.run()`
on the user's real sources and samples **every python.exe process'**
CPU + memory + count every 2 seconds via `wmic process get
kernelmodetime+usermodetime+workingsetsize`. This is the only way to
see what process-pool spawn workers are doing; monkeypatches don't
reach spawn subprocesses.

Same args pre- and post-fix:
* sources = D: + H: + J:
* limit = 1500 per source (= 4500 total)
* hash_pool = auto (post-#609 picks "process" for multi-device+NAS)
* autotune_read_knee = True
* exif_workers = 2
* workers = 8 (NAS default)
* per-scan-timeout = 900s

## Pre-fix diagnosis (flat `ProcessPoolExecutor(max_workers=8)`)

`monitor_dhj_process.json`:

| Metric | Value |
|---|---|
| Wall | **900s timeout** |
| Hashed | **2800 / 4500 (62%)** |
| Status | `Scan cancelled.` (harness interrupt at timeout) |
| Mean worker CPU | 111% (of 1200% nominal — 12 cores) |
| Max worker CPU | 709% |
| Samples idle (<50% CPU) | **195 / 387 = 50%** |
| Hash rate | ~3.4 files/s |

**Root cause** — the original `pool = ProcessPoolExecutor(max_workers=self.workers)`
at the punted TODO ("TODO(#548 follow-on): per-device process pools")
became load-bearing once #609 routed multi-device+NAS scans through
this branch. With a **flat single pool** and **all records submitted
in walk order**, 8 worker processes all grab D: records first and
**simultaneously read from the same HDD spindle** — exactly the
seek-thrash bug #605/#606 fixed for thread mode, now reproduced in
the compute path. The H+J NAS portion also has 8 workers contending
on the same `\\\\LINXIAOYUN` server.

## Fix

`app/views/workers/scan_worker.py:1113` — split the flat
`ProcessPoolExecutor` into one per device, sized by
`hash_workers_for_root(dev)` (HDD=1, NAS=8, SSD/unknown=
`min(4, cpu_count)`). Mirrors the thread branch's per-device
ThreadPoolExecutor shape that was already there. Each pool's workers
go into the #460 `KILL_ON_JOB_CLOSE` job individually via
`_assign_process_pool_to_kill_job`. Cancel teardown shuts down each
pool with `wait=False, cancel_futures=True`.

```python
proc_device_records = OrderedDict()
for idx, r in enumerate(records):
    proc_device_records.setdefault(device_key(r.path), []).append((idx, r))
proc_device_workers = {
    dev: hash_workers_for_root(dev) for dev in proc_device_records
}
process_pools = {
    dev: ProcessPoolExecutor(max_workers=proc_device_workers[dev])
    for dev in proc_device_records
}
# Submit each record to its device's pool.
# as_completed loops over all futures across all pools.
```

## Post-fix verification (same args, same probe)

`monitor_dhj_postfix.json`:

| Metric | **Post-fix** | Pre-fix | Δ |
|---|---|---|---|
| Wall | **513.2 s** | 900s timeout | **~2.6× faster** |
| Hashed | **4500 / 4500** ✓ | 2800 / 4500 | completes naturally |
| Status | **`Done.`** | `Scan cancelled.` | no timeout |
| Mean worker CPU | **215%** | 111% | **1.93× utilization** |
| Max worker CPU | 769% | 709% | + |
| Samples idle (<50% CPU) | **56 / 220 = 25%** | 195 / 387 = 50% | **half the idle** |
| n_workers | 9 (1 D: + 8 NAS) | 8 (flat) | + 1 |

Per-minute post-fix worker-CPU shape:

| Minute | mean% | max% | idle% |
|---|---|---|---|
| 0 | 0 | 0 | 100% (walk + spawn) |
| 1 | 266 | 768 | 28% |
| 2 | 365 | 690 | **0%** |
| 3 | 352 | 657 | 4% |
| 4 | 271 | 374 | 0% |
| 5 | 296 | 576 | 0% |
| 6 | 116 | 256 | 32% |
| 7 | 111 | 203 | 27% |
| 8 | 71 | 187 | 53% (tail / EXIF post-drain) |

Minutes 2-5 the pipeline sustained ~300% CPU with **zero idle samples** —
workers are continuously busy. The walk + spawn at minute 0 is
unchanged (NAS SMB stat overhead, not addressable by this fix).

## Cache caveat

Both runs ran with **partial-warm OS cache** (multiple D: + NAS scans
across the session). The absolute wall-time on a **cold-first scan**
will be slower in both pre- and post-fix versions because HDD physical
read of the 33 GB D: DNG content takes ~5 min at sustained 100 MB/s
regardless of pool shape. What this measurement establishes is the
**shape of the improvement on this workload**: pre-fix the
seek-thrash kept CPU under-utilised even when cache was helping; post-
fix workers stay near saturation.

For the user's full ~24k file library cold-from-power-on, projecting
from 4500 files in 513s (warm) is unreliable — disk read dominates
that scale. What is safe to project: **the pipeline pathology is gone**
(no more "everything idle for 70 s" stretches, no more flat-pool
contention).

## Single-rig N=1

No adversarial-review peer round. The fix follows directly from the
existing per-device thread shape; the data shows the predicted
improvement; the escape hatch
`settings.json[scan.hash_pool] = "thread"` still works.

## Artifacts

- `monitor_dhj_process.json` — pre-fix raw samples (387 × 2-second)
- `monitor_dhj_postfix.json` — post-fix raw samples (220 × 2-second)
- `scripts/probe_process_monitor.py` — the harness (added in this PR)

## Related

- #605/#606 — the HDD seek-thrash bug in thread-mode reader pool (this
  fix is the compute-path twin)
- #609 — flipped multi-device+NAS to process pool; exposed the punted
  per-device-pool TODO at scan_worker.py:1113-1118
- #548 — original per-device thread-pool work; this PR is the "follow-on"
  the comment promised
