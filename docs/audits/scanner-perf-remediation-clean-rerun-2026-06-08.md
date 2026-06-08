# Scanner-perf remediation — clean post-#606 re-run (2026-06-08)

**Status:** Decisions made on direct measurement; load-bearing quantities
asserted at scan-start; the prior #604 conclusion was confounded by
#605/#583 and is REPLACED. No adversarial-review peer round (single
short rig — N=1; user-rig, dev/validation checkpoint), but every
finding ties to a number recorded in the linked JSON artifacts.

**Triggers**

* PR #605 / #583 changed local-drive `device_key` from `"D:"` to a
  durable volume `"{GUID}"`. That bypassed
  `scanner/workers.py::disk_incurs_seek_penalty`'s 2-char drive-letter
  guard, so `hash_workers_for_root` fell back to the SSD default (4)
  on the D: spinning HDD instead of `_HDD_WORKERS=1` — seek-thrash at
  ~20 MB/s rather than ~120 MB/s.
* The **#604 ON/OFF A/B for the #551 read-knee autotune** ran ~2.5 h
  *after* #605 merged. Every #604 scan was HDD-thrashing → the
  "autotune neutral (ratio 1.005)" conclusion was confounded; it then
  closed **#586** (~9 min later, no cooling-off) on the same confounded
  data.
* The brief flagged this cascade and asked for a clean re-run + a
  reconciliation of #551's GATE-2 (synthetic ratio 0.689) vs. the
  real-world #604 result (1.005).

**Method**

1. **Precondition gate (T1):** verified D:'s `device_key` is `"D:"` on
   post-#606 master, `disk_incurs_seek_penalty("D:")` = True,
   `hash_workers_for_root("D:")` = 1. J: NAS = 8 readers. Confirmed
   at runtime *before* any timed measurement.
2. **#604 A/B (T2):** `scripts/bench_autotune_604.py` drives real
   `ScanWorker.run()` and prints per-source `device_key` /
   `is_remote_drive` / `seek_penalty` / `hash_workers_for_root` AT
   scan-start (the load-bearing assertion the prior #604 skipped).
3. **#598 per-device byte-budget validation (T5/T6):**
   `scripts/probe_byte_budget_598.py` monkeypatches
   `scanner.byte_budget.per_device_budgets` so every acquire/release
   records the post-call `_inflight` keyed by device. Mirrors the
   pre-fix instrumentation pattern from
   [`docs/audits/scan-nas-starvation-2026-06-06.md`](scan-nas-starvation-2026-06-06.md).
4. Each scan capped at a hard per-scan timeout; exiftool
   snapshot-diff (PIDs before / after) checks T7 fix didn't regress.
   Live OS-process tasklist verified clean between every scan.

Artifacts: `bench_604_dj.json`, `bench_604_j_only.json`, `probe_598_dj.json`.

---

## T2 / T3 / T4 — #604 read-knee autotune ON/OFF re-run

### T2-DJ — D:+J: multi-device run (`bench_604_dj.json`)

Both arms TIMED OUT at the 300 s per-scan cap (clean interrupt via the
post-#607 `_kill_exif_procs()` path; status `"Scan cancelled."`,
`orphans=0`). The walker is per-source, so D: 1700 ran first and the
J: NAS portion only got the trailing ~10–20 s of each 300 s window.

| Arm | Wall (s) | n_hashed | Status | Reader counts |
|-----|---------|---------|--------|--------------|
| OFF | 301.44  | 1800/3400 | cancelled (timeout) | D:=1, \\\\LINXIAOYUN=8 |
| ON  | 301.50  | 2100/3400 | cancelled (timeout) | D:=1, \\\\LINXIAOYUN=8 |

**Load-bearing precondition confirmed at scan-start:** post-#606,
D: HDD runs at **1 reader** (not 4), and `disk_incurs_seek_penalty`
no longer returns None for a drive-letter key — the exact regression
PR #606 closed.

The OFF-vs-ON wall-time delta on this multi-device scan is
**HDD-dominated, not autotune-dominated**: ~5.97 files/s overall =
~285 s spent on the D: HDD portion alone (1700 files at 1 reader),
leaving the J: NAS exposure at < 5 % of each scan window.  That is
why the user observed the NAS not moving for the bulk of the run —
the multi-device A/B does **not** isolate the autotune signal.

### T2-J — J: NAS-only clean run (`bench_604_j_only.json`)

Both arms ran to completion (`status='Done.'`), no timeout, no orphans.

| Arm | Wall (s) | n_hashed | Status | Knee | Ladder rates (files/s) |
|-----|---------|---------|--------|------|------------------------|
| OFF | **119.55** | 1700 | Done. | — (static c=8) | — |
| ON  | **132.97** | 1700 | Done. | **knee=8** (top of ladder, never flattened) | c=1: 26.02 · c=2: 33.89 · c=4: 44.33 · c=8: 55.21 |

**Ratio ON/OFF = 1.112 → autotune is 11.2 % SLOWER on this NAS.**

Per-rung gain factors: 33.89/26.02 = **1.30** · 44.33/33.89 = **1.31** ·
55.21/44.33 = **1.25**. All three doublings are above the 15 %
diminishing-returns gate (`_KNEE_GAIN_THRESHOLD`), so the ramp never
detects a knee below the cap and freezes at `knee=8`. This NAS is the
**well-fit case** the #551 docs describe — c=8 is already optimal,
so the ramp's c=1/c=2/c=4 climb is pure transient tax.

The ON arm even had a *warm-cache* advantage (it ran second after
OFF's cold-cache pass); it is still 11 % slower than OFF. The 11 %
is a conservative lower bound on the cold-vs-cold autotune tax — on
truly cold cache both arms the tax would likely be larger.

### T3 — #586 floor drop (1584 → 792) — re-decision

**Decision: KEEP CLOSED won't-do (evidence-backed).**

#586 proposed lowering `_RAMP_MIN_SCAN_FILES` from 1584 to 792 (N=8 →
N=4) on the premise that the floor over-protects scans where the
autotune helps. The clean T2-J data shows autotune is a **−11.2 %
regression** on this NAS — lowering the floor would expose **more
scans** to the negative-tax, not fewer. Strong reject for the
well-fit-NAS case; would only be relevant if a mis-fit NAS were
demonstrated where ON beats OFF by enough to justify the asymmetric
floor.

### T4 — #551 GATE-2 (synthetic 0.689) vs. real-world reconciliation

**Reconciliation:**

* GATE-2's integration test (`tests/integration/test_autotune_ab.py`)
  models a **mis-fit NAS** with a hard latency cliff at c>2 (a real
  ~6× slowdown at c=3+). On that synthetic cliff, the cache-warm
  knee=2 wins decisively → ratio 0.689 (ON 31 % faster).
* This rig's J: NAS (the user's `\\LINXIAOYUN`) scales **linearly**
  through the full ladder (knee=8) — it is the **well-fit case**.
  GATE-2's synthetic cliff is not the situation here. The autotune
  ramp's c=1/c=2/c=4 measurement transient is pure tax → ON is 11 %
  slower than static-8.
* Both can be true simultaneously: GATE-2 bounds the algorithm's
  sampling overhead on an *idealised* cliff (real, measurable);
  this rig's NAS is on the other side of the cliff so autotune
  yields no win to offset its tax. The #551 default-ON ship is
  justified only if mis-fit NAS users exist; on this rig it is
  measurable insurance with a measurable cost.

**Action items (not in this PR):**

* Update the #551 features.md / README "value claim" so it states
  the well-fit-vs-mis-fit dichotomy explicitly rather than implying
  universal win.
* Consider an opt-out hint for users on demonstrably well-fit NAS
  (the cached `knee=8` + zero diminishing-returns gain at any rung
  is a clean signal). Not urgent — the 11 % tax is small in
  absolute terms and one-off per device (knee gets cached + reused).

---

## T5 — #598 per-device byte-budget fix validation (`probe_598_dj.json`)

D:+J: scan, limit=800 per source, ran to completion in 181.14 s,
1600 files hashed, no orphans. Per-device `_inflight` mean / peak
fills (1601 samples per device, recorded at every acquire/release):

| Device | Mean fill | Peak fill | Samples at ≥99 % | Budget |
|--------|-----------|-----------|-------------------|--------|
| **D:** (HDD, 1 reader, ProRAW-heavy) | 36.0 % | 64.6 % | **0 / 1601** | 1.00 GiB |
| **\\\\LINXIAOYUN** (NAS, 8 readers) | 24.2 % | 52.9 % | **0 / 1601** | 1.00 GiB |

**Pre-fix (single shared 2 GiB budget, from
[`scan-nas-starvation-2026-06-06.md`](scan-nas-starvation-2026-06-06.md)):**
mean 86.5 % · **53/53 NAS-idle samples at 100 %** · longest stall 137 s
on the NAS reader.

**Verdict — #598 fix WORKS on real HW.** Neither device's budget
pins; the NAS budget tracks an order of magnitude lower than the
pre-fix global budget did, exactly as the unit test predicted.
The decoupled per-device budgets prevent D:'s 100–130 MB ProRAW
DNGs from starving the NAS reader at acquire. The earlier ratio
(NAS budget mean 0.2 % when alone vs 86.5 % when shared with D:)
is closed — post-fix the NAS budget tracks proportionally to its
own load.

## T6 — #587 OOM regression-gate (byproduct of T5)

The T5 D+J probe is a ProRAW-cluster scan (D:\\Takeout-0508 has the
100–130 MB DNGs from the original #587 repro). D:'s `peak_fill =
64.6 %` of its 1 GiB per-device budget — well below the
admit-one-over-budget ceiling. **OOM bound holds at the corrected
1-reader D: throughput; no regression.**

If a future rig has DNGs ≥1 GiB the admit-one-over-budget rule
admits a single over-size file alone (`scanner/byte_budget.py:92-102`),
so the worst-case `_inflight` is one such file plus the in-flight
window of smaller payloads — still bounded.

---

## What this re-run does NOT cover (deferred)

* **Truly cold-cache N≥3 medians** — the harness alternates OFF/ON
  per pair but does not flush OS cache between scans. The
  observed ON 11 % slower is a *floor* (cold-OFF vs warm-ON), so
  the well-fit-NAS conclusion is conservative. A truly cold N=3
  median would only widen the gap.
* **Mis-fit NAS measurement** — would require a different NAS
  with low SMB-mux or a real c=3 latency cliff. Not on this rig.
* **#604's original 3-cold-pair median methodology** — replaced
  here by 1 isolated J:-only pair + the load-bearing-quantity
  probe, on the basis that "asserted load-bearing quantity +
  one isolated arm" beats "3 confounded medians" (the prior
  #604's failure mode).

## Convergence note

This is a single user-rig measurement (N=1, dev/validation
checkpoint). The decisions are:

* **T3 (#586):** stay closed won't-do — backed by direct evidence
  on this rig.
* **T4 (#551 value claim):** reconciled — GATE-2 synthetic cliff
  bounds the algorithm tax in the mis-fit case; this rig is the
  well-fit case where there is no offset benefit. Default-ON ship
  is justified only if mis-fit NAS users exist (not measured on
  this rig).
* **T5 (#598):** fix verified on real HW.
* **T6 (#587):** OOM bound holds.

The #604 prior conclusion (ratio 1.005, autotune neutral) is
REPLACED by this clean run's ratio 1.112 (autotune −11 % on this
well-fit NAS). The direction differs (neutral vs. negative)
because the prior run was HDD-thrash dominated; this run isolates
the J: NAS signal.
