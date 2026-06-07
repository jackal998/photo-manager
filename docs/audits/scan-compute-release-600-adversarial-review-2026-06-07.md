# Scan HASH compute-RELEASE lever (#600) — measurement + adversarial-review decision record

**Date:** 2026-06-07 **Status:** DO NOT BUILD (verdict held under adversarial-review,
with free-threaded CPython scoped out by a packaging-feasibility check)
**Method:** 4 decoupled measurements + a 2-peer (Opus+Sonnet) adversarial-review with a LEAD judge.
**Sibling:** [`scan-readbound-599-adversarial-review-2026-06-07.md`](scan-readbound-599-adversarial-review-2026-06-07.md) (#599, the diagnosis this follows up).

## TL;DR

#600 asked whether the HASH-stage **compute-RELEASE rate** is a worthwhile throughput
lever, via any of: (1) `compute_pool` sizing below `os.cpu_count()`, (2) cheaper
per-file compute, (3) ProcessPool compute. **Answer: build none of them.** The GIL does
serialize compute-release (a real ~10% serial residual caps thread scaling at ~4-5×),
but every *buildable* mechanism fails its cost/benefit or contract gate. Free-threaded
CPython — the one lever that would target the residual without the ProcessPool tax — is
**infeasible today** (PySide6 has no free-threaded support; importing it on a
free-threaded build silently re-enables the GIL) and is tracked as a separate
forward-looking issue. This is the 3rd measured no-op in the #577 → #599 → #600 arc.

## Context

#599 established (converged adversarial-review) that the HASH stage is read-supply +
GIL-serialized-compute-RELEASE bound, and that per-device compute **fairness** is dead
(work-conservation). #600 is the follow-up: is the compute-release **rate itself** a
lever? The user's machine is a DEV/validation CHECKPOINT (12-core, D: = HDD), not the
optimization target — the goal is GLOBAL optimization across all users' hardware. This
HDD rig is read-bound, so full-scan wall-clock here MASKS any compute lever; the decisive
signal is the **decoupled compute-release rate** on an in-RAM source (a valid proxy for
NVMe — both deliver bytes faster than the ~800 MB/s the compute consumes; a SATA-SSD
~500 MB/s user stays read-bound). Load-bearing quantity = **files/s**, never
bytes/s / CPU% / occupancy (the #599 scar).

## Measurements (scratch harnesses in `.claude/tmp-monitor/bench_e*.py`, read-only)

### E1 — decoupled compute-release rate per file-class (DECISIVE)
ThreadPool sweep over in-RAM blobs, `compute_from_bytes`, 3 repeats/point:

| arm | seq ms/file | files/s @8 | @12 | @16 | eff@12 | knee | f@4→@12 |
|---|---|---|---|---|---|---|---|
| DNG-big (104 MB) | 557 | 7.33 | 7.06 | 7.93 | 0.33 | N=8 | 0.119→0.187 |
| DNG-typ (18 MB) | 116 | 33.1 | 34.0 | 35.3 | 0.33 | N=8 | 0.114→0.186 |
| JPEG (2 MB) | 19 | 291 | 346 | 351 | 0.56 | N=10 | 0.033→0.071 |

A lever *exists* (eff < 0.80) but the headroom is a **saturation plateau, not 1/f**: the
GIL-held fraction `f` RISES with N (contention, not a fixed Amdahl serial fraction), so
throughput flattens by N≈8-12 and a thread pool cannot exceed it (DNG even wobbles DOWN
8→12). Knees diverge by class: DNG saturates at 8; **JPEG (88% of a real library) keeps
climbing — capping at 8 costs JPEG −16%** (291 vs 346 files/s).

### E2 + control — Lever 2a (drop the redundant 2nd `rawpy.imread` for `raw.sizes`)
- 210 size-diverse DNGs: **0/210 straddle the `min(w,h)=128` grouping gate**
  (`dedup._phash_dimension_ok`); 36/210 fail rawpy `imread` and fall to the path loader
  (orthogonal to #75).
- Cost control on the 16 biggest DNGs (102-125 MB): full 2nd open = **median 0.5 ms** —
  `rawpy.imread` parses only the RAW *header* (no decode/unpack), `raw.sizes` is a header
  field. The design-map's cited "~86 ms" was **unverified** (no spike output log existed).
- **Verdict: 2a DEAD** — saves ~0.5 ms/file, far below the 30 ms build threshold.

### E4b — ProcessPool vs ThreadPool, decoupled + pickle + RAM (Lever 3)
ProcessPool fed pre-read bytes (the only GIL-escape variant that doesn't re-read disk):

| arm | Thread@12 | Process@12 | ratio | pickle ms/file | peak ΔRAM |
|---|---|---|---|---|---|
| DNG-big | 7.17 | 4.70 | **0.66×** | 29 | +2.77 GB |
| DNG-typ | 35.0 | 21.7 | **0.62×** | 5 | +1.33 GB |
| JPEG | 317 | 195 | **0.61×** | 1 | +0.52 GB |

**Verdict: Lever 3 DEAD** — ProcessPool is ~0.6× (SLOWER) on every class; the pickle+IPC
dispatch tax exceeds the GIL-escape benefit (DNG compute is mostly GIL-released decode;
small-JPEG is dispatch-overhead-bound). The +2.77 GB spike is **untracked IPC copies that
breach the #587/#598 OOM bound** (verified in `byte_budget.py` — `_inflight` is a
parent-side int the child copy never touches — and `scan_worker.py:1426-1432`). It also
breaks the per-device byte-budget release contract and the #594 cancel teardown.
Generalizes to SSD (the tax is CPU/RAM-bound, not disk).

### E4a — full-scan thread-vs-process A/B (PARTIAL; killed as impractical)
Real ScanWorker on D: (13,686 files). Both passes hit the 40-min/pass cap (would need
~5.5 hr/pass) and the determinism assertion never ran (crashed before it). Partial rates:
thread ≈ **2.5 files/s**, process ≈ **1.65 files/s** (process slower — the legacy path
re-reads from disk; its arm also suffered 2 ExiftoolTimeout crashes, so it is a confounded
comparison — it cannot cleanly isolate GIL effects). Both are ≪ decoupled compute
(7-317 files/s). What it does show, robustly: the real HASH stage is **read+EXIF-bound**,
so no compute lever can move wall-clock on this rig, and **process is not faster than
thread** on the real pipeline (no contradiction of #599). The clean GIL-escape signal is
E4b, not E4a.

## The GIL mechanism (corrected — judge's direct probe)

The artifact's first phrasing — "DNG compute is already GIL-released decode, little held
to recover" — was **too strong**, and peer A's "43% held" decomposition was **too high**.
A direct probe (`.claude/tmp-monitor/judge_gil_probe.py`, on a real 124.8 MB DNG)
measured a busy counter-thread's starvation during each op: contention ratios
**0.91-0.96**, i.e. each op (`sha256` / rawpy decode / `phash` / `dhash`) **releases the
GIL ~90% of its wall-time per call**, but a **~10% serial residual** Amdahl-caps thread
scaling at ~4-5× — exactly matching E1's eff@12 = 0.327. So: the thread pool already
extracts the full plateau the residual allows; there is no THREAD or PROCESS lever that
recovers more (ProcessPool pays a pickle/IPC tax that dwarfs the ~10%).

## `dread.log` reconciliation (a presentation gap the review flagged)

The #599 sibling discriminator (`dread.log`) shows D: cold-read conc=8 = 3.61 DNG/s vs an
in-scan device rate of 2.45 DNG/s — i.e. **compute-RELEASE *is* the active gate on D:**
(reads can supply faster than the coupled pipeline consumes). This is consistent with
#600's premise; the original artifact failed to cite it. The corrected reading: the gate
exists, but **no profitable lever relieves it** (E1 plateau + E4b 0.6×). Caveat for future
use: `dread_discriminator.py`'s cold-disjoint claim is confounded — `SKIP_HEAD=4000` >
1024 candidates, so it sampled the warm alphabetic head; treat 3.61 as indicative, not
authoritative, until re-run with `SKIP_HEAD` scaled to corpus size.

## Adversarial-review

2 independent peers (Opus + Sonnet, `adversarial-peer`, file access to verify every claim)
attacked the DO-NOT-BUILD verdict across design/implementation/experiment/inference, round 1
blind + round 2 cross; LEAD judge adjudicated each objection against the data/code.
**Tally: 14 raised / 4 load-bearing / 1 unrebutted. Converged = false; verdict_holds = true.**

The verdict held for the three *tested* levers (all refuted on independently-verified
evidence). The 1 unrebutted load-bearing objection was **completeness**: free-threaded
CPython was never evaluated though #600 is scoped to "ANY compute-release lever." Three
real but non-flipping accuracy defects were fixed in this record (the GIL-mechanism
wording, the `dread.log` reconciliation + confound, the 2.5-vs-3.1 files/s arithmetic).

## Free-threaded CPython (3.13t / 3.14t) — packaging-feasibility scope-out

The judge's minimum bar to close #600 as "any compute-release lever": a packaging
feasibility check. Result — **infeasible today**:
- Free-threaded is an opt-in, non-default build (5-10% single-thread overhead at 3.14).
  Any C extension not updated for free-threading **silently re-enables the GIL for the
  whole process on import**.
- **PySide6 has no free-threaded support.** The scanner runs *in* the Qt process, so
  importing PySide6 on a free-threaded build re-enables the GIL → the no-GIL benefit is
  nullified. This alone is disqualifying.
- **pillow-heif** ships `abi3` wheels, which are **incompatible with free-threaded builds**
  (they need the still-draft PEP 803 `abi3t` or a dedicated `cp31Xt` wheel); **rawpy**
  (libraw Cython) likewise has no free-threaded wheels.

Free-threaded CPython is therefore an **ecosystem-gated, whole-app runtime migration**, not
a scan-pipeline lever we can build — tracked as a separate forward-looking issue, revisited
when the PySide6 + rawpy + pillow-heif stack ships free-threaded-safe wheels.

## Per-lever verdict

| Lever | Verdict | Reason |
|---|---|---|
| 1 — `compute_pool` sizing ↓ | do-not-build | file-class-dependent; `os.cpu_count()` is near-optimal for the 88%-JPEG common case (capping at 8 regresses JPEG −16%) |
| 2a — drop 2nd `rawpy.imread` | do-not-build | saves 0.5 ms/file (header-only; the "86 ms" was unverified) |
| 2b — smaller phash thumbnail | do-not-build | #569 took the safe win; ≤15% JPEG-only upside behind a full #526/#538 re-validation |
| 3 — ProcessPool compute | do-not-build | 0.6× (slower) under measured pickle tax + breaches #587/#598 OOM + #598 release + #594 cancel |
| free-threaded CPython | scoped out → tracked | infeasible today (PySide6 no FT support → silent GIL re-enable; pillow-heif abi3 / rawpy lack FT wheels) |

## Decision & follow-ups

- **#600 → close as a measured no-op** for all buildable compute-release levers, with this
  record as the trace.
- **File:** free-threaded-CPython forward-tracking issue (revisit when the stack supports it).
- **File:** exiftool-stage parallelism (>2 processes) — the real DNG full-scan rate is
  read+EXIF-bound and EXIF throughput is an untested, distinct lever, out of #600's scope.
- If a ProcessPool path is ever revived (it is dead on throughput + OOM today), the E4a
  determinism acceptance criterion (bit-identical `source_hash`/`phash`/`group_id`,
  #526/#538) must be executed first — it never ran here.
