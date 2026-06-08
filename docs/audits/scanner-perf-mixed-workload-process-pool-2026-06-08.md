# Mixed-workload (D+J) hash-pool inversion — decision record (2026-06-08)

**Status:** Direction-of-fix backed by direct per-second instrumentation
on the user's real rig. Single rig (N=1, dev/validation checkpoint), no
adversarial-review peer round on the inversion itself, but every claim
ties to a row in `probe_*_dj_*.json` / `probe_d_*.json` produced by
`scripts/probe_pipeline_timeline.py`.

## Trigger

User pushed back on the post-#608 conclusion that the rig was "near
physical limit": **"NAS 根本沒動，CPU 也沒滿，你根本沒有用到物理極限"**.
The framing I'd been using (max(D_wall, J_wall) with thread mode + per-device
read pools) implied the slower device dictates wall-time but couldn't
explain why **both** NAS and CPU sat idle for stretches of the scan.

## Method

Wrote `scripts/probe_pipeline_timeline.py` — monkeypatches
`scanner.hasher.read_for_record` + `scanner.hasher.compute_from_bytes` +
`scanner.byte_budget.per_device_budgets` to timestamp every read,
compute, and budget acquire/release. Buckets events into 1-second
windows; outputs JSON timeline. Ran with the user's actual default
settings (`hash_pool="auto"`, `autotune_read_knee=True`, `workers=8`,
`exif_workers=2`) on real disks.

## What the timeline showed (probe_timeline_dj.json — thread, cold)

D+J scan, `limit=1700` per source = 3400 files total, `per-scan-timeout=600`.

| Phase | t (s) | What happened |
|---|---|---|
| 1 — healthy parallel | 0..32 | NAS reading 14–358 MB/s, HDD 9–58 MB/s, compute 30–170 files/s |
| 2 — DNG cluster hits HDD | 33–34 | **HDD reads 5.8 GB in one second** (clustered 100-130 MB ProRAW DNGs); compute completions drop 61 → 0 |
| 3 — NAS portion finishes | 35–38 | J: completes its 1700 files in **38 seconds total** |
| 4 — compute GIL stall | 38..108 | **70 consecutive seconds with ZERO compute completions** while only big DNGs in flight. NAS idle (its work is done). HDD trickling. |
| 5 — D: byte_budget pinned | 108..385 | D: budget climbs to 100%, oscillates at 95-100%. HDD reader blocked at `acquire`. Compute trickles at 0–19 files/s. |
| 6 — read drain done | 385..545 | All reads complete; compute slowly chews remaining items (~5 f/s). |
| 7 — invisible to probe | 545..600 | Likely EXIF post-drain (exiftool batches; my probe doesn't instrument these). |
| 8 — harness timeout | 600 | Worker cleanly cancelled. 3400 reads done, **2878 computes done (522 short → cancelled at scope)** |

Net: thread mode runs `~5 files/s` end-to-end on the mixed workload —
not because NAS or HDD is slow, but because the shared `compute_pool`
becomes GIL-locked when big-DNG decode (PIL convert, imagehash phash/dhash,
getexif — all Python-level, hold GIL) interleaves with the NAS small-file
flood.

## Why "NAS not moving / CPU not full" — user observations explained

| User saw | Real reason |
|---|---|
| **NAS not moving** | J: finishes its 1700 files in **38 seconds**; the remaining ~9 minutes of the scan are D: HDD + compute drain. NAS has no more work. Not a physical limit — a workload-distribution artefact. |
| **CPU not full** | `compute_pool` ThreadPoolExecutor has 12 workers but PIL/imagehash hold the GIL, so effective parallelism is ~2-4 cores. 8 other cores idle. |
| **HDD usage continuous (no break)** | D: byte_budget pinned at 95-100% for ~5 minutes (Phase 5); HDD reader unblocking intermittently as compute releases bytes. Consistent with the curve the user saw. |
| **70-second freezes** | Phase 4 (real). Compute_pool can't drain the big-DNG queue because GIL contention pathology when 12 workers contend on PIL Python-level code paths. |

## The lever (measured both directions)

Process pool escapes the GIL by running compute in separate Python
processes. Tested same scan with `hash_pool="process"` (which the #554
shortcut had been blocking).

**Apples-to-apples D+J 3400 files, both warm cache:**

| Mode | Wall (s) | Status | Notes |
|---|---|---|---|
| **thread** | **601.4** (timeout) | 3013/3400 computes done (89%) | Projects to ~678s for full scan |
| **process** | **421.4** | ✓ complete | **1.61× faster** |

**Side-by-side D:-only 500 files (controls):**

| Mode | Wall (s) | Notes |
|---|---|---|
| thread cold | 241.8 | HDD physical limit; compute keeps up |
| thread warm | **9.8** | RAM-cache speed; thread wins decisively |
| process warm | 30.98 | Process spawn cost > GIL benefit on single-device |

The pattern: process pool **loses** on single-device (spawn cost
dominates without contention) but **wins** on mixed multi-device + NAS
(GIL contention erases per-device thread overlap). The #554 shortcut
was protecting the per-device overlap, but that overlap is downstream
of the GIL-bound compute pool — it's a fiction once you measure.

## Decision — invert #554 to force `process` on multi-device+NAS

`app/views/workers/scan_worker.py:601-617` — replace `return "thread"`
with `return "process"` on the multi-device + at-least-one-NAS path.
Calibration is still bypassed (we don't need to re-measure what's now a
known answer for this topology). Single-device and all-local paths are
unchanged. `settings.json[scan.hash_pool]` = "thread" still overrides
verbatim for power users.

Test: `tests/test_scan_worker.py::TestHashPoolCalibration::test_multi_device_with_remote_forces_process_pool`
(renamed and inverted from the prior `_skips_process_calibration`).
Single-device test unchanged. All 240+ scanner tests green.

## What this fix gets the user

- **1.6× faster D+J scans** (from this rig's measurement; magnitude on
  other rigs depends on file-size mix and CPU count).
- **No more 70-second compute-freeze episodes.** Process pool workers
  each hold their own GIL, so big-DNG decode runs truly in parallel
  across cores.
- **CPU will go to high utilization** during the compute-heavy phase —
  matching the user's expectation of "use the physical limit".
- **NAS usage pattern unchanged** — J: still finishes in ~38 seconds
  because that's its actual workload. The NAS-idle-for-most-of-scan
  reflects work distribution, not a pipeline pathology.

## What the fix does NOT change

- **Single-device or all-local scans** — calibration still runs there,
  spawn cost still matters, thread can still win.
- **HDD physical read speed** — D: cold reads are still HDD-sequential
  bound (~70 MB/s on this rig).
- **NAS gigabit link cap** — still ~125 MB/s for active reads.
- The per-device thread reader pools (1 HDD + 8 NAS) — those still run
  in the parallel READ stage; only the compute executor changes.
  Wait — actually process branch uses a flat ProcessPoolExecutor for
  both read and compute fused via `run_hash_for_record`, so there is no
  per-device reader pool in process mode. The measurement says this is
  net faster anyway on mixed workloads.

## What we did NOT measure (residual uncertainty)

- **Other rigs / NAS configurations.** Single rig (N=1). A user with a
  high-channel NAS and small CPU might see a different cost/benefit.
- **Spawn cost on slower machines.** Windows ProcessPoolExecutor spawn
  re-imports PIL/rawpy per worker (~150-300 ms each); on a 4-core
  laptop the GIL escape gain might not offset.
- **Adversarial-review peer round.** This is a single-author inversion
  of a previous defensive default; in a higher-stakes context I'd want
  two peers attacking the methodology + measurements before flipping a
  shipped default.

Mitigation: power-user override `settings.json[scan.hash_pool]` = "thread"
forces the old behaviour, so any user who hits a regression has an
immediate escape hatch.

## Artifacts

- `scripts/probe_pipeline_timeline.py` — the timeline harness (new).
- `scripts/bench_autotune_604.py` — extended with `--hash-pool` flag.
- `probe_timeline_dj.json` (local, gitignored) — the 600-second
  thread-mode timeline that pinned the GIL stall.
- `probe_dj_process.json` — process-mode 421-second complete scan.
- `probe_dj_thread_warm.json` — apples-to-apples thread comparison.
- `probe_d_thread.json` / `probe_d_thread_warm.json` /
  `probe_d_process.json` — D:-only controls.
