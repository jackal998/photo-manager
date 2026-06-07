# Scan-pipeline OOM — adversarial-review decision record (2026-06-06)

**Status:** CONVERGED (0 unrebutted load-bearing objections). Root cause verified
against ground truth. Awaiting fix.

**Method:** [`adversarial-review`](../../.claude/skills/adversarial-review/SKILL.md) —
two independent peers (Opus + Sonnet) attacked a frozen artifact along
design / implementation / experiment / inference, round 1 blind, round 2 cross-attack;
LEAD judged each objection on evidence. Transcript: workflow `wf_65bfc362-700`.

---

## The artifact under review

The thread-branch HASH stage of the scan pipeline
(`app/views/workers/scan_worker.py`, the `else:` branch ~L1145–1438) and its claim
that the #564/#566/#570 bounded queues keep RAM "near one read-window worth of bytes".

## The incident

User report: "記憶體滿了閃退" (memory filled → hard crash) on a 3-source scan.
Machine: 12 logical CPUs, 31.8 GB RAM.

- Sources: `D:\Takeout-0508` (HDD, 1 reader), `H:\Photos\MobileBackup` + `J:\圖片`
  (both NAS `\\LINXIAOYUN`, 8 readers, ramping). `hash_pool=auto→thread`,
  `autotune_read_knee=ON`, `exif_workers=2` (→ exif_queue cap 2000).
- `app_20260606.log`: died at **~1,000 / 39,721 files (~2.7%)**, mid-HASH. Rate
  collapsed 14 files/s → 0.48 files/s around file 700–800 (700→800 took 209 s).
  Log then stops; next line is an app relaunch — **no graceful `MemoryError`** ⇒
  OS-level kill, not a caught Python exception.
- Library: hundreds of **100–130 MB iPhone ProRAW DNGs**, folder-clustered
  (verified on disk: H: 691 DNGs, 278 in `iPhone/2024/06`; D: 1024 DNGs,
  450+444 in two dirs).

---

## Verified root cause

Three compounding mechanisms (all evidence-grounded), triggered by the clustered
100–130 MB DNGs:

1. **Unbounded `reader_futures` retention (DOMINANT — missed by the initial
   hypothesis).** `_read_drain` pre-submits **all 39,721** reads into a
   `reader_futures` list (`scan_worker.py:1279-1289`) and iterates via
   `as_completed`. `read_for_record` returns `(idx, record, data=bytes)`
   (`hasher.py:310`). CPython does **not** clear `Future._result` after
   `.result()`, and the list holds every future until `_read_drain` returns at
   scan end — so **every completed read's bytes stay alive for the whole scan**,
   even after consumption into `hash_in_q`. The per-device read `Semaphore` caps
   *concurrency*, released in `finally` at `scan_worker.py:1273` **before**
   consumption, so it does **not** cap retained bytes. Reading one 278-DNG folder
   ⇒ ~27 GB retained ⇒ OOM.
   *Independently re-verified (LEAD):* with the futures list held, 6/6 consumed
   payloads stayed alive after `del` + `gc.collect()`; freed only when the list
   was cleared.

2. **Count-based in-flight ceiling, not byte-based.** `hash_in_q` maxsize=128
   (`:879-880`) + `compute_inflight=Semaphore(128)` (`:1238`) + 1 `c_data` pinned
   during the acquire wait (`:1349-1368`) = **257 global buffers** sharing one
   `_HASH_QUEUE_MAXSIZE`. Tuned for ~5 MB photos (~1.3 GB); on 120 MB DNGs the
   measured walk-order window peaks at **17.5 GB**.

3. **DNG folder-clustering (the trigger).** `walker.py` is depth-first,
   per-directory contiguous into `records`, so the 8-reader NAS pool ingests
   hundreds of consecutive 100–130 MB files. (Uniform 0.85 % DNG rate would give
   ~2 slots / 264 MB — nowhere near OOM; clustering is load-bearing and confirmed.)

**Regression.** #566 (read/compute split, `0c58950`) + #570 (`f5b3aed`), **both
shipped 2026-06-05 (the day before the crash).** Pre-#566 the fused `_hash_one`
returned `(idx, HashResult)` with **no bytes** in `_result`; peak live RAW bytes =
the **9** concurrently-executing workers (NAS 8 + HDD 1) ≈ 1 GB. Both the 257
ceiling AND the `reader_futures` retention are new. Multiplier ≈ **256/9 ≈ 28×**.
This is why it broke right after the update.

## Refuted over-reaches (kept out of the conclusion)

- **48 GB decode-inflation** (full RAW postprocess numpy/PIL on top of the bytes):
  REFUTED — the installed **rawpy 0.26.1 has no `open_buffer`** (verified
  `hasattr=False`), so the from-bytes preview path returns `None` and never runs
  `postprocess`. The byte ceiling is `data`-only.
- **`out_q` as a second byte leak:** REFUTED — `HashResult` (`dedup.py:59-70`)
  carries no bytes field; `out_q` is tens of MB of small objects. It explains the
  *throughput* stall (blocking `exif_queue.put` in `_route_outcome`) but not the OOM.
- **`autotune_read_knee` as cause:** red herring for the fix — it caps read
  concurrency, not queue/backlog/retention depth. It only shifts crash *timing*.

## Side finding (non-blocking)

rawpy 0.26.1's missing `open_buffer` means every RAW is **read from disk 3×**
(1 `read_bytes` + 2 path-based rawpy fallbacks at `hasher.py:132,141`), over SMB
for the NAS — an I/O amplifier feeding the rate collapse. The `hasher.py` header
("single `path.read_bytes()`… #446 eliminated the double-read") and the #453
footprint comment (points to the #449 workers spinner as the RAM lever) are **false
on this build / post-#566**.

---

## Fix direction (converged — both parts required; either alone still OOMs)

1. **Stop retaining read bytes after consumption.** Don't pre-submit all 39,721
   reads — submit in bounded batches sized to the in-flight budget, **or** drop
   each completed future's reference as it is drained so `_result` can be freed.
2. **Byte-budget the read→compute window** (replace the count cap): a budget
   semaphore acquired per read sized to `len(data)`, capped at a fraction of RAM
   (~2–4 GB), released when compute finishes. Keeps the queue hundreds deep for
   5 MB photos (no small-file regression), ~16–33 for 120 MB DNGs. Include an
   **"admit one over-budget file alone"** guard (largest image ~130 MB, videos
   are `data=None`, so a budget ≥256 MB never deadlocks — belt-and-suspenders,
   mirroring the #551 `_gated_read` finally-release).

A flat lower `_HASH_QUEUE_MAXSIZE` (~`2*sum(device_workers)`) is a valid emergency
stop-gap that cuts both shared bounds **but leaves the `reader_futures` leak
unaddressed** — not a complete fix.

## Convergence gate

Objections raised: **22** · load-bearing: **11** · unrebutted: **0** ⇒ converged.
Independence: Opus vs Sonnet (different base models). LEAD re-verified the two
decisive new claims (rawpy `open_buffer`, `Future._result` retention) and the
regression (`git show 0c58950^`) against ground truth before accepting.
