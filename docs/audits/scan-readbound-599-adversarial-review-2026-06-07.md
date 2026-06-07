# #599 decision record — compute-fairness is moot; the HASH gate is per-device read-supply (adversarial-review converged 2026-06-07)

## Decision
**Do NOT build #599 (per-device compute fairness — round-robin dispatch or
per-device compute cap).** It cannot reduce HASH wall-clock. Re-scope #599 to
the actual binding constraint: **per-device READ SUPPLY** (D: HDD ~2.45 DNG/s)
plus the **GIL-serialized compute-*release* rate**, with a cheap compute-pool
sizing A/B as the first probe and a ProcessPool-vs-ThreadPool A/B as the
tie-breaker before any GIL-escape work.

## How we got here
Filed #599 after a post-#598 monitoring run showed the NAS still idle ~38%. A
design Workflow recommended round-robin dispatch (PR2). Before building, a
spike + a fresh instrumented run + a 2-peer adversarial-review (Opus + Sonnet,
LEAD judge) were run. The review converged on the decision but **refuted the
reasoning** the artifact reached it by.

## Measurement (the airtight run)
Harness `.claude/tmp-monitor/measure_compute.py` against a detached worktree on
**origin/master 0c5134a** (#595 + #598). Two zero-product-change monkeypatches:
live per-device `ByteBudget._inflight/_budget`; per-device count of threads
inside `compute_from_bytes` (= compute-pool occupancy). `hash_pool="thread"`
(the real multi-device path). Sampler @0.4 s, anchored to HASH start, 600 s of
HASH. Raw: `.claude/tmp-monitor/compute_occupancy.csv` (2348 rows / 1495 HASH).

Steady-state (last 1122 HASH samples):
- `compute_active` = **12.0/12** (mean=median=p10=max) — pool occupancy-saturated
- both budgets ~**99.6%** full → both readers blocked at acquire
- NAS-idle (NAS_bud ≥95%) = **1104/1122 = 98%** of steady-state
- during NAS-idle: D_act ~**10.1**, NAS_act ~**1.9** (D monopolises slots)

## What the review established (converged)
- **Claim B is correct** (independently re-derived): round-robin *submission*
  does NOT rebalance worker occupancy — occupancy = rate × duration, so the
  slow device dominates the slots regardless of dispatch order. Per-device
  occupancy converges to ~10.7 D / ~1.3 N at D:N duration 8:1 (≈3.5:1 measured).
- **The decision is correct and invariant** to the mechanism: reorder/cap
  cannot raise total compute work (compute-bound view) and neither device is
  compute-limited (read-bound view) → fairness cannot reduce wall-clock.

## What the review REFUTED in the artifact's reasoning (5 load-bearing, unrebutted)
1. **"compute-saturated / GIL-bound" is the WRONG throughput diagnosis.** The
   pool is *occupancy*-saturated but has ~**2.7× spare throughput**: D runs at
   **2.45 DNG/s** vs the spike's **6.63 DNG/s** 12-thread ceiling (37%), real
   in-call wall is **4.12 s** vs the spike's 1.81 s (2.3× inflation) at sub-half
   CPU, and **47% of saturated intervals complete zero files**. Occupancy-
   saturated is necessary-but-not-sufficient for "compute is the bottleneck."
   The byte-budget (~8.9 DNGs read-ahead) + maxsize-128 queue is the BUFFER that
   pins 12 slots nominally full while throughput tracks the read-supply rate.
2. **"CPU ~25%" was ungrounded** — the harness records `os.cpu_count()` in the
   `cpu` column (measure_compute.py:121,171); no utilization was ever measured.
   Only the *direction* (well under 100%, GIL-limited) is supported, via the
   spike's 3.73×-of-12.
3. **The "escape the GIL via process-pool compute" redirect is mis-aimed.** With
   D read-supply-bound, lifting the compute ceiling buys ~nothing; and a process
   pool breaks the per-device `ByteBudget` release contract
   (scan_worker.py:1426-1432, release keyed by `c_dev` in the parent done-
   callback) and re-introduces the byte copy the #587/#598 OOM fixes removed.
4. Provenance: the CSV is from `measure2.log`, not the cited `measure.log`.
5. The "prior 25-min run NAS-idle ~38% = same regime" cross-run claim is
   unreconciled vs this run's 98% (different threshold or source mix unknown).

Sonnet's pro-compute-bound rebuttals (never-idle ⇒ compute-saturated; process-
pool redirect sound) were **refuted on ground truth**: never-idle is explained
by the queue+budget buffer decoupling slot-fill from read rate; the redirect's
premise ("read supply can keep up") is contradicted by the measured 2.45/s.

## Convergence gate
raised 14, load-bearing 5, **unrebutted 5, converged=false** on the *reasoning*;
the *decision* (do-not-build) is unanimous. The single measurement that would
fully settle the redirect: an **A/B of ProcessPoolExecutor vs ThreadPoolExecutor
compute on the full 3-source scan to completion** — wall-clock unchanged ⇒ read-
supply is the binding floor and the GIL redirect is dead; wall-clock drops ⇒
compute-*release* (GIL-inflated) was the gate after all.

## Discriminator result (2026-06-07, post-review) — it's BOTH, bounded
Ran `.claude/tmp-monitor/dread_discriminator.py`: D: DNG raw read with
compute+budget DECOUPLED, cold disjoint sets, at the scan's reader concurrencies:
- conc=1: 3.86 DNG/s (107 MB/s); conc=4: 4.06 (144); conc=8: 3.61 (121)
- in-scan (coupled): 2.45 DNG/s

Findings: (1) **D's HDD ceiling ≈ 120-144 MB/s / ~4 DNG/s and does NOT scale with
reader count** (seek-bound — conc 1/4/8 ~flat) — a hard per-rig physical floor.
(2) **In-scan D (2.45) runs ~40% BELOW its own read ceiling (3.6-4)** because the
reader is blocked at the byte budget (99.6% full) waiting for GIL-serialized
compute to RELEASE. So the compute-release throttle is REAL but BOUNDED (~1.5×),
not the unbounded "escape-GIL = big win" the artifact implied (review was right to
refute the overstatement; the discriminator rescues a modest version). **Global
lens:** this HDD rig MASKS the lever (reads slow anyway); on an SSD/NVMe rig the
read ceiling is far higher, so GIL-serialized compute-release becomes the DOMINANT
bottleneck — the SW lever matters MORE for SSD users than for this dev checkpoint.

Refined conclusion: HASH throughput is **compute-release-gated** (12 compute
threads GIL-contending with ~18 other pipeline threads), holding reads ~1.5× below
their ceilings; upside from lifting the release rate is bounded by D's ~120 MB/s
HDD here, larger on SSD rigs. #599 fairness remains dead (it adds no throughput).

## Cheap-first probes (before any heavy work)
1. `compute_pool` is `ThreadPoolExecutor(os.cpu_count())` (scan_worker.py:1241),
   never questioned. A peer's bench suggested conc=8 gives ~92% of conc=12
   throughput at lower CPU-per-file — an A/B (max_workers 8 vs 12, full scan)
   is a low-cost probe that also frees cores for the concurrent EXIF stage.
2. The ProcessPool-vs-ThreadPool A/B above is the redirect tie-breaker.

## Provenance
- Artifact: `.claude/tmp-monitor/599-artifact.md`
- Workflow transcript (full 4-round + judge): run `wf_26a9dbe0-633`
- Raw data + harness + analyzer: `.claude/tmp-monitor/` (gitignored scratch)
