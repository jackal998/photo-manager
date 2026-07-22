"""Qt-free scan pipeline — run_pipeline().

Extracted from app.views.workers.ScanWorker._run_pipeline so the
pipeline logic is reachable without importing PySide6.  The original
file retains a thin Qt adapter (ScanWorker.run()) that builds a
ScanConfig + _QtBus and delegates here.

No Qt import lives in this module — enforced by the T6 AST probe in
tests/test_ui_probes.py.
"""
from __future__ import annotations

import time
from collections import deque

from loguru import logger

from core.app_service.cancel_token import _CancelToken
from core.app_service.dtos import ScanConfig
from core.app_service.events import ScanProgressBus

# ---------------------------------------------------------------------------
# Stage name constants (mirrored from scan_worker.py — kept in sync manually;
# the T6 probe asserts no Qt call sites in this file).
# ---------------------------------------------------------------------------
STAGE_WALK = "WALK"
STAGE_HASH = "HASH"
STAGE_EXIFTOOL = "EXIFTOOL"
STAGE_CLASSIFY = "CLASSIFY"
STAGE_SCORE = "SCORE"
STAGE_WRITE = "WRITE"

# Throughput / throttle constants — same values as scan_worker.py.
_THROUGHPUT_WINDOW_SECONDS = 5.0
_STAGE_EMIT_INTERVAL_SECONDS = 1.0

# Hash-pool calibration sample sizing (#486-PR3).
_CALIBRATION_SAMPLE = 96
_CALIBRATION_MIN = 24

# Grouping-stage calibration (#526).
_GROUP_CALIBRATION_SAMPLE = 256
_GROUP_CALIBRATION_PAIRS = 4000
_GROUP_FLOOR_MIN = 8
_GROUP_FLOOR_MAX = 256

# Required keys for a valid hash-pool calibration cache entry.
_RATE_KEYS = ("thread_per_file", "process_per_file", "spawn")


# ---------------------------------------------------------------------------
# Hash-pool calibration helpers — single source of truth (scan_worker.py
# re-exports these for backward-compat with scan_dialog and test imports).
# ---------------------------------------------------------------------------


def _time_hash_executor(executor_cls, sample: list, max_workers: int) -> float:
    """Hash ``sample`` through one executor and return the elapsed seconds.

    Results are discarded — this measures only wall-clock for the
    ``hash_pool="auto"`` calibration. Kept module-level (not a closure over
    pipeline state) so it stays trivially unit-testable and picklable-safe.
    """
    from concurrent.futures import as_completed
    from scanner.hasher import run_hash_for_record

    start = time.perf_counter()
    with executor_cls(max_workers=max_workers) as pool:
        futures = [
            pool.submit(run_hash_for_record, i, r) for i, r in enumerate(sample)
        ]
        for fut in as_completed(futures):
            fut.result()
    return time.perf_counter() - start


def _profile_process_pool(
    executor_cls, sample: list, max_workers: int
) -> tuple[float, float]:
    """Return ``(spawn_seconds, per_file_seconds)`` for the process executor.

    Times two halves of ``sample`` on ONE pool: the first (cold) pass pays
    the one-time worker spawn + per-worker module re-import; the second
    (warm) pass is steady-state. Subtracting the warm per-file rate from the
    cold pass isolates the fixed spawn cost, so the caller can project both
    executors to the *real* file count rather than charging process's
    one-time startup against a tiny sample (which under-credits it on large
    scans — the bias #498's flat timing had).
    """
    from concurrent.futures import as_completed
    from scanner.hasher import run_hash_for_record

    half = max(1, len(sample) // 2)
    cold, warm = sample[:half], sample[half:] or sample[:half]

    def _drain(pool, batch) -> float:
        start = time.perf_counter()
        futures = [
            pool.submit(run_hash_for_record, i, r) for i, r in enumerate(batch)
        ]
        for fut in as_completed(futures):
            fut.result()
        return time.perf_counter() - start

    with executor_cls(max_workers=max_workers) as pool:
        cold_s = _drain(pool, cold)  # cold: pays pool spawn + module imports
        warm_s = _drain(pool, warm)  # warm: steady-state, workers already up

    per_file = warm_s / len(warm)
    # Clamp at 0: on a fast warm pass the cold pass can measure marginally
    # cheaper per-file (scheduler warmup noise), which would otherwise yield
    # a spurious negative spawn estimate.
    spawn = max(0.0, cold_s - per_file * len(cold))
    return spawn, per_file


def _profile_grouping() -> tuple[float, float]:
    """Return ``(brute_per_pair_s, bk_per_candidate_s)`` — the two grouping
    micro-rates the #486 calibration caches to derive the BK-tree floor (#526).

    * ``brute_per_pair_s`` — one ``imagehash`` Hamming subtraction, the cost of
      a single comparison in the pre-#526 O(N²) inner loop.
    * ``bk_per_candidate_s`` — amortised BK-tree build + query per indexed
      hash, the cost of the #526 candidate-generation path.

    Both depend on hash *width* (64-bit) and CPU speed, not on the library's
    actual content, so they're timed here on a synthetic clustered hash set at
    the existing pre-hash calibration moment — no need to sequence the
    measurement after the hash pass. Kept module-level (not a closure over
    pipeline state) so it stays trivially unit-testable.
    """
    import random

    import imagehash

    from scanner.dedup import _BKTree

    rng = random.Random(526)  # fixed seed → stable timing set (Math.random-free)
    n = _GROUP_CALIBRATION_SAMPLE
    ints: list[int] = []
    objs: list = []
    # Cluster centres + bit jitter so the BK query returns realistic neighbour
    # sets (pure-random 64-bit hashes sit ~32 apart and would never match).
    while len(ints) < n:
        centre = rng.getrandbits(64)
        for _ in range(rng.randint(1, 6)):
            if len(ints) >= n:
                break
            v = centre
            for _ in range(rng.randint(0, 7)):
                v ^= 1 << rng.randint(0, 63)
            ints.append(v)
            objs.append(imagehash.hex_to_hash(format(v, "016x")))

    pairs = _GROUP_CALIBRATION_PAIRS
    start = time.perf_counter()
    for k in range(pairs):
        _ = objs[k % n] - objs[(k + 1) % n]
    brute_per_pair = (time.perf_counter() - start) / pairs

    threshold = 10
    start = time.perf_counter()
    tree = _BKTree(ints[0], 0)
    for idx in range(1, n):
        tree.add(ints[idx], idx)
    for idx in range(n):
        tree.query(ints[idx], threshold)
    bk_per_candidate = (time.perf_counter() - start) / n

    return brute_per_pair, bk_per_candidate


def _derive_bktree_floor(brute_per_pair: float, bk_per_candidate: float) -> int:
    """Candidate count where BK-tree starts beating brute force (#526).

    Brute force costs ``brute_per_pair × N(N-1)/2``; the BK-tree costs roughly
    ``bk_per_candidate × N``. Equating them gives the crossover
    ``N ≈ 2 × bk_per_candidate / brute_per_pair + 1``. Clamped to
    ``[_GROUP_FLOOR_MIN, _GROUP_FLOOR_MAX]`` so a noisy micro-measurement can't
    push the floor to a silly value. On today's recipe BK is far cheaper per
    unit work, so this lands at the min — confirming, from measurement rather
    than a baked constant, that BK should engage on all but trivial inputs.
    """
    if brute_per_pair <= 0:
        return _GROUP_FLOOR_MAX
    crossover = round(2 * bk_per_candidate / brute_per_pair + 1)
    return max(_GROUP_FLOOR_MIN, min(_GROUP_FLOOR_MAX, crossover))


def _stratified_sample(records: list, n: int) -> list:
    """#548 — draw up to ``n`` records spread across physical devices.

    ``records`` is a source-order concatenation, so a naive ``records[:n]``
    slice samples only the first device (e.g. all D: files) and the
    thread-vs-process calibration then measures the slowest device alone.
    Round-robin across ``device_key`` buckets (in source-iteration order so
    the sample stays deterministic) so the pick is fair on a mixed scan.

    With a single device this returns ``records[:n]`` exactly — the common
    case is unchanged.
    """
    from collections import OrderedDict

    from scanner.workers import device_key

    buckets: "OrderedDict[str, list]" = OrderedDict()
    for r in records:
        # FileRecord exposes ``.path``; tolerate plain values (e.g. the
        # synthetic int lists the calibration unit tests pass) by keying
        # off the value itself — they all collapse into one bucket so the
        # function degrades to the records[:n] single-device path.
        key = device_key(getattr(r, "path", r))
        buckets.setdefault(key, []).append(r)
    if len(buckets) <= 1:
        return records[:n]
    sample: list = []
    cursors = {dev: 0 for dev in buckets}
    while len(sample) < n:
        progressed = False
        for dev, items in buckets.items():
            if len(sample) >= n:
                break
            cur = cursors[dev]
            if cur < len(items):
                sample.append(items[cur])
                cursors[dev] = cur + 1
                progressed = True
        if not progressed:
            break
    return sample


def _valid_hash_pool_rates(rates) -> bool:
    """True iff ``rates`` is a usable cached calibration.

    ``settings.json`` is hand-editable, so a corrupt or partial
    ``scan.hash_pool_cache`` entry must be treated as a cache miss
    (re-measure) rather than crashing the scan with a ``KeyError`` —
    boundary validation per the project's input-at-boundaries rule.
    """
    return isinstance(rates, dict) and all(
        isinstance(rates.get(k), (int, float)) for k in _RATE_KEYS
    )


def hash_pool_fingerprint(
    sources: dict, recursive_map: dict | None, cpu_count: int
) -> str:
    """Stable key for the hash-pool calibration cache (#486-PR3b).

    Captures what makes the thread-vs-process decision vary: the machine
    (``cpu_count`` — the main determinant of the GIL-escape benefit) and the
    source set (folder paths + recursive flags — the dataset shape). A new
    machine or a different folder set yields a different key → cache miss →
    re-measure. Returns a short hex digest so it stays a tidy settings key.

    #526 — also folds in the hash-recipe and grouping-strategy version tokens
    (``scanner.hasher.HASH_RECIPE_VERSION`` /
    ``scanner.dedup.GROUPING_STRATEGY_VERSION``). The cache entry now stores
    both the hash thread/process rates AND the grouping micro-rates, so a
    change to either recipe must invalidate the whole entry — otherwise a
    cached calibration measured under an old recipe would mis-project. Bumping
    either constant changes every fingerprint → universal cache miss →
    re-measure under the new recipe. (Closes the #517 breadcrumb: pre-#526 the
    key had no recipe component, so dHash being added to the 7-tuple never
    invalidated the cache.)
    """
    import hashlib

    from scanner.dedup import GROUPING_STRATEGY_VERSION
    from scanner.hasher import HASH_RECIPE_VERSION

    rec = recursive_map or {}
    parts = sorted(
        (str(path), bool(rec.get(label))) for label, path in (sources or {}).items()
    )
    canonical = repr(
        (int(cpu_count), parts, HASH_RECIPE_VERSION, GROUPING_STRATEGY_VERSION)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def store_hash_pool_rates(settings, fingerprint: str, rates: dict) -> None:
    """Persist a fresh calibration into ``scan.hash_pool_cache`` (#486-PR3b).

    Kept a plain function (not a dialog method) so the cache round-trip is
    unit-testable against a real ``JsonSettings`` without constructing a Qt
    dialog. ``settings`` is any object exposing ``get``/``set``/``save``.
    """
    cache = settings.get("scan.hash_pool_cache", {}) or {}
    cache[fingerprint] = rates
    settings.set("scan.hash_pool_cache", cache)
    settings.save()


def _assign_process_pool_to_kill_job(pool) -> int:
    """#549(a) — register a ``ProcessPoolExecutor``'s worker processes with the
    #460 ``KILL_ON_JOB_CLOSE`` job so an ungraceful parent exit (crash / Task
    Manager force-kill) reaps them too — the same guard exiftool already gets.
    Returns the number of workers assigned (0 = no-op).

    Process-mode hash workers read the source disks directly; without this a
    force-kill of the app orphans them mid-read and they keep spinning the disk
    after the user has "exited" (verified #549). The parent does the assignment
    (it holds the sole job handle, so the kill-on-last-handle-close semantics
    stay correct — identical to exiftool's).

    Best-effort + fail-open: off Windows, without pywin32, or if the pool's
    worker set isn't introspectable, this is a no-op and the pre-#549
    orphan-on-hard-exit behaviour applies (no worse than before). ``_processes``
    is a CPython implementation detail — read defensively via ``getattr`` so a
    future stdlib change degrades to the no-op rather than raising.
    """
    from scanner.exif import assign_pid_to_kill_job

    procs = getattr(pool, "_processes", None)
    if not procs:
        return 0
    assigned = 0
    for proc in list(procs.values()):
        pid = getattr(proc, "pid", None)
        if pid is not None and assign_pid_to_kill_job(pid):
            assigned += 1
    return assigned


# ---------------------------------------------------------------------------
# _StageTracker — worker-side throughput accumulator + per-second emit throttle.
# ---------------------------------------------------------------------------

class _StageTracker:
    """Worker-side throughput accumulator + per-second emit throttle.

    One instance per stage. Records ``(timestamp, completed_count)`` samples
    in a deque trimmed to the last :data:`_THROUGHPUT_WINDOW_SECONDS` on every
    update, then reports throughput as
    ``(latest_completed - oldest_completed) / (latest_ts - oldest_ts)`` —
    zero when the deque collapses to a single sample or the dt is too small.

    ``should_emit()`` returns True only on (a) the first call for a stage,
    (b) the boundary (completed == total), or (c) when
    ≥ ``_STAGE_EMIT_INTERVAL_SECONDS`` has elapsed since the last emit.
    """

    def __init__(self, stage_name: str) -> None:
        self.stage_name = stage_name
        self._samples: deque[tuple[float, int]] = deque()
        self._last_emit_at: float = 0.0
        self._first_emit_done = False

    def record(self, completed: int) -> None:
        now = time.monotonic()
        self._samples.append((now, completed))
        cutoff = now - _THROUGHPUT_WINDOW_SECONDS
        while len(self._samples) > 1 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def throughput(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        t0, c0 = self._samples[0]
        t1, c1 = self._samples[-1]
        dt = t1 - t0
        if dt < 0.1:
            return 0.0
        return max(0.0, (c1 - c0) / dt)

    def should_emit(self, completed: int, total: int) -> bool:
        now = time.monotonic()
        if not self._first_emit_done:
            self._first_emit_done = True
            self._last_emit_at = now
            return True
        if total > 0 and completed >= total:
            self._last_emit_at = now
            return True
        if now - self._last_emit_at >= _STAGE_EMIT_INTERVAL_SECONDS:
            self._last_emit_at = now
            return True
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _emit_stage(
    bus: ScanProgressBus,
    tracker: _StageTracker,
    completed: int,
    total: int,
    *,
    force: bool = False,
) -> None:
    """#424 — emit a stage_progress event with throttling.

    ``force`` bypasses the per-second throttle for stage boundaries
    (start / end) where the receiver must update the label even if
    the throttle hasn't elapsed.
    """
    tracker.record(completed)
    if force or tracker.should_emit(completed, total):
        bus.stage(tracker.stage_name, completed, total, tracker.throughput())


def _resolve_grouping_floor(
    bus: ScanProgressBus, rates: dict, n: int
) -> "int | None":
    """#526 — derive + log the BK-tree floor from grouping micro-rates.

    Returns the per-machine candidate floor (passed to ``classify`` as
    ``bktree_min_candidates``), or ``None`` when the cached rates lack
    valid grouping keys — in which case ``classify`` uses the module
    default and grouping still works, just without the measured crossover.
    """
    pp = rates.get("group_per_pair")
    bpc = rates.get("group_bk_per_candidate")
    if not isinstance(pp, (int, float)) or not isinstance(bpc, (int, float)):
        return None
    if pp <= 0 or bpc < 0:
        return None
    floor = _derive_bktree_floor(pp, bpc)
    brute_proj = pp * n * (n - 1) / 2
    bk_proj = bpc * n
    bus.log(
        f"  Grouping calibration → BK-tree floor {floor} candidates;"
        f" projected to ≤{n:,}: brute≈{brute_proj:.1f}s vs BK≈{bk_proj:.2f}s"
    )
    return floor


def _calibrate_hash_pool(
    bus: ScanProgressBus,
    config: ScanConfig,
    records: list,
    thread_cls,
    process_cls,
) -> "tuple[str, int | None]":
    """Resolve ``hash_pool='auto'`` to 'thread' or 'process'.

    Returns ``(pool_type, calibrated_bktree_floor)``.  The floor is
    ``None`` when grouping rates aren't available in the cached rates.

    Mirrors ScanWorker._calibrate_hash_pool exactly, with ``self`` refs
    replaced by ``bus`` / ``config``.
    """
    from scanner.workers import device_key, is_remote_drive

    calibrated_floor: "int | None" = None

    device_keys = {device_key(getattr(r, "path", r)) for r in records}
    if len(device_keys) >= 2 and any(
        is_remote_drive(dk) for dk in device_keys if dk
    ):
        bus.log(
            "  Multi-device + NAS scan → process pool"
            " (GIL escape on shared compute > per-device thread I/O overlap; #609)"
        )
        # #554 — still resolve grouping floor from cached rates if
        # available, so the BK-tree calibration isn't lost on the fast
        # path. When no cached rates exist the floor stays None and
        # classify() uses the module default.
        if _valid_hash_pool_rates(config.hash_pool_rates):
            calibrated_floor = _resolve_grouping_floor(
                bus, config.hash_pool_rates, len(records)
            )
        return "process", calibrated_floor

    n = len(records)
    rates = config.hash_pool_rates
    if not _valid_hash_pool_rates(rates):
        # #548 — stratify the sample across devices so the thread-vs-
        # process pick is measured fairly on a mixed scan instead of on
        # the first (source-order) device alone.
        sample = _stratified_sample(records, _CALIBRATION_SAMPLE)
        if len(sample) < _CALIBRATION_MIN:
            bus.log(
                f"  Hash-pool calibration skipped ({len(sample)} files;"
                f" need ≥{_CALIBRATION_MIN}) → pool=thread"
            )
            return "thread", calibrated_floor
        bus.log(f"  Calibrating hash pool on {len(sample)} files…")
        thread_s = _time_hash_executor(thread_cls, sample, config.workers)
        spawn_s, process_per_file = _profile_process_pool(
            process_cls, sample, config.workers
        )
        # #526 — measure the grouping micro-rates in the same calibration
        # pass and cache them alongside the hash rates under one fingerprint.
        group_per_pair, group_bk_per_candidate = _profile_grouping()
        rates = {
            "thread_per_file": thread_s / len(sample),
            "process_per_file": process_per_file,
            "spawn": spawn_s,
            "group_per_pair": group_per_pair,
            "group_bk_per_candidate": group_bk_per_candidate,
        }
        bus.hash_pool_measured(rates)
    else:
        bus.log("  Using cached hash-pool calibration (fingerprint match)…")
    thread_proj = rates["thread_per_file"] * n
    process_proj = rates["spawn"] + rates["process_per_file"] * n
    winner = "process" if process_proj < thread_proj else "thread"
    bus.log(
        f"  Hash-pool calibration → projected to {n:,}:"
        f" thread≈{thread_proj:.1f}s process≈{process_proj:.1f}s"
        f" (spawn {rates['spawn'] * 1000:.0f}ms +"
        f" {rates['process_per_file'] * 1000:.1f}ms/file) → pool={winner}"
    )
    # #526 — fold the GROUPING stage into the same calibration: derive the
    # per-machine BK-tree floor from the cached/measured micro-rates and log
    # the projected brute-vs-BK grouping cost at this file count.
    calibrated_floor = _resolve_grouping_floor(bus, rates, n)
    return winner, calibrated_floor


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    config: ScanConfig,
    cancel_token: _CancelToken,
    bus: ScanProgressBus,
) -> None:
    """Run the full scan pipeline — Qt-free.

    All progress and lifecycle events are dispatched through ``bus``.
    The pipeline exits (returns) when it succeeds, fails, or is cancelled;
    it calls ``bus.failed("Scan cancelled.")`` on clean cancel.

    ``cancel_token`` is polled cooperatively at every stage boundary and
    inside every hot loop.  Callers cancel by calling
    ``cancel_token.request()``.
    """
    import threading
    from concurrent.futures import (
        ProcessPoolExecutor,
        ThreadPoolExecutor,
        as_completed,
    )
    from collections import OrderedDict
    from scanner.walker import scan_sources
    from scanner.hasher import (
        HashFailure,
        run_hash_for_record,
        read_for_record,
        compute_from_bytes,
    )
    import os
    from scanner.exif import ExiftoolProcess, batch_read_extracts
    from scanner.dedup import HashResult, classify
    from scanner.manifest import write_manifest, print_summary
    from scanner.scoring import apply_scoring_to_rows
    from scanner.workers import device_key, hash_workers_for_root
    from scanner.autotune import (
        ReadKneeRamp,
        _RAMP_MIN_SCAN_FILES,
        _valid_read_knee,
    )
    from scanner.byte_budget import default_budget_bytes, per_device_budgets
    import io
    from contextlib import redirect_stdout

    # #425 — was "no files will be moved or deleted"; reworded to
    # match scan_dialog.notice and stop implying a file operation
    # the read-only scan never performs.
    bus.log("Read-only scan — no files on disk are changed.")
    bus.log("")

    # --- 1. Walk sources ---
    # #448 — WALK reports a running per-file count via an
    # indeterminate bar (``total=0``); the counter advances live
    # through the walker's ``progress_callback`` hook so a
    # single-source NAS scan no longer sits silent for minutes.
    #
    # #452 — when more than one source is configured, walks run
    # in parallel via a ``ThreadPoolExecutor`` so each source
    # saturates its own SMB / disk pipe independently. The
    # walker is read-only so there's no shared mutable state to
    # protect on the walker side; the only cross-thread
    # contention is the shared file counter, which is guarded
    # by a small lock. Order-stability of ``records`` is
    # preserved by collecting per-source-label results into a
    # dict and concatenating in source-iteration order at the
    # end, not by appending as walks complete.
    bus.log(f"Scanning {len(config.sources)} source(s)…")
    records: list = []
    walk_tracker = _StageTracker(STAGE_WALK)
    walk_files_seen = 0
    walk_counter_lock = threading.Lock()
    _emit_stage(bus, walk_tracker, 0, 0, force=True)

    def _on_walk_file_seen() -> None:
        nonlocal walk_files_seen
        with walk_counter_lock:
            walk_files_seen += 1
            snapshot = walk_files_seen
        # The tracker's should_emit throttles to 1Hz so a million
        # rglob hits on a fast SSD don't spam the event bus.
        _emit_stage(bus, walk_tracker, snapshot, 0)

    def _walk_one_source(label: str, root) -> tuple:
        mode = "flat" if config.recursive_map.get(label) is False else "recursive"
        bus.log(f"  Walking {label} ({mode}): {root} …")
        # #491 — pass the cancel token's callable straight through
        # as the walker's cancel-check. A title-bar X / Cancel during
        # the WALK stage now lands within one rglob tick instead of
        # waiting for ``rglob`` to exhaust.
        partial = scan_sources(
            {label: root},
            limit=config.limit,
            recursive_map={label: config.recursive_map.get(label, True)},
            progress_callback=_on_walk_file_seen,
            cancel_check=cancel_token,
        )
        bus.log(f"  → {len(partial):,} files")
        return label, partial

    if len(config.sources) > 1:
        # Parallel branch — one thread per source, capped to the
        # source count so a 100-source pathological case doesn't
        # spawn 100 threads.
        partials: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=len(config.sources)) as pool:
            futures = {
                pool.submit(_walk_one_source, label, root): label
                for label, root in config.sources.items()
            }
            for future in as_completed(futures):
                label, partial = future.result()
                partials[label] = partial
        for label in config.sources:
            records.extend(partials.get(label, []))
    else:
        for label, root in config.sources.items():
            _, partial = _walk_one_source(label, root)
            records.extend(partial)

    # Force a final emit so the stage bar reflects the true count
    # when scan_sources finishes faster than the 1Hz throttle.
    _emit_stage(bus, walk_tracker, walk_files_seen, 0, force=True)
    bus.log(f"  Total: {len(records):,} media files")

    # #491 — gate-out after WALK if the user cancelled.
    if cancel_token():
        logger.warning("Scan cancelled by user during walk")
        bus.failed("Scan cancelled.")
        return

    if not records:
        bus.log("Done. No media files found — nothing to scan.")
        bus.completed_empty()
        return

    # --- 2 + 3. Hash + EXIF (pipelined / overlapping) ---
    import queue as _queue

    chunk_size = 500
    skipped: list[tuple] = []  # (path, exc type, exc msg)

    # #561 — live ExiftoolProcess instances owned by the consumer threads.
    exif_procs: list = []
    exif_procs_lock = threading.Lock()

    def _kill_exif_procs() -> None:
        """#561 — hard-kill every live exiftool so a consumer wedged in a
        batch unblocks immediately (its execute() hits EOF and returns),
        letting the cancel join complete fast and leaving no orphan."""
        with exif_procs_lock:
            for _p in exif_procs:
                _p.kill()

    # #566 — bounded read→compute queue for the thread branch HASH stage.
    _HASH_QUEUE_MAXSIZE = 128
    hash_in_q: _queue.Queue = _queue.Queue(maxsize=_HASH_QUEUE_MAXSIZE)

    def _drain_queue_nowait(q: _queue.Queue) -> None:
        """Empty a queue without blocking (swallows ``queue.Empty``)."""
        while True:
            try:
                q.get_nowait()
            except _queue.Empty:
                break

    # #564 — bound the exif_queue so hash-stage producers can't grow it
    # unboundedly in RAM while a slow exiftool consumer falls behind.
    _EXIF_QUEUE_MAXSIZE = 2 * chunk_size * config.exif_workers
    exif_queue: _queue.Queue = _queue.Queue(maxsize=_EXIF_QUEUE_MAXSIZE)
    extracts: dict = {}
    exif_tracker = _StageTracker(STAGE_EXIFTOOL)
    exif_done = [0]
    exif_total = [0]  # grows as hash threads enqueue eligible records
    exif_done_lock = threading.Lock()
    exif_total_lock = threading.Lock()
    exiftool_missing = [False]

    def _route_outcome(record, outcome):
        """Route one compute outcome into the shared dispatch state.

        #486-PR2 — extracted so both executor paths share ONE routing
        implementation.  #564 — the exif_queue put is cancel-safe via a
        cooperative bounded-put loop.  #594 — also watches cancel_token
        so a dialog-close can't wedge the parent drain thread here.

        #786 — formats with in-memory scoring signals (``HashResult.
        inmemory_signals`` set, currently JPEG only) skip the exiftool
        queue entirely: their MediaExtract is written straight into
        ``extracts`` here instead. This runs in the single thread that
        drains ``out_q`` (same as the ``skipped.append`` above), so the
        plain dict write needs no lock — mirrors ``_flush_exif_batch``'s
        writes to the same dict from the exif consumer thread(s); the two
        never touch the same key (disjoint file sets).
        """
        if isinstance(outcome, HashFailure):
            skipped.append((record.path, outcome.exc_type, outcome.exc_msg))
            return None
        if outcome is None:
            return None
        if record.file_type != "skip":
            if getattr(outcome, "inmemory_signals", None) is not None:
                extracts[record.path] = outcome.to_media_extract()
                return outcome
            # #564 — cooperative bounded put.
            # #594 — ALSO break on cancel_token so a dialog-close unblocks
            # the parent before the teardown branch can reach it.
            while not cancel_token():
                try:
                    exif_queue.put(outcome, timeout=0.05)
                    break
                except _queue.Full:
                    pass
            with exif_total_lock:
                exif_total[0] += 1
        return outcome

    def _exif_consumer() -> None:
        """Drain ``exif_queue`` into 500-batches fed to one ExiftoolProcess.
        Sentinel = ``None``.  Exits early on cancel_token.
        """
        try:
            proc = ExiftoolProcess()
        except FileNotFoundError:
            exiftool_missing[0] = True
            while True:
                try:
                    item = exif_queue.get(timeout=0.5)
                except _queue.Empty:
                    if cancel_token.is_set():
                        return
                    continue
                if item is None:
                    return
            return
        # #561 — register so the HASH-cancel branch can kill this exiftool.
        with exif_procs_lock:
            exif_procs.append(proc)
        try:
            with proc as et:
                batch: list = []
                while True:
                    try:
                        item = exif_queue.get(timeout=0.5)
                    except _queue.Empty:
                        if cancel_token.is_set():
                            return
                        continue
                    if item is None:
                        if batch:
                            _flush_exif_batch(batch, et)
                        return
                    batch.append(item)
                    if len(batch) >= chunk_size:
                        _flush_exif_batch(batch, et)
                        batch = []
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("exiftool consumer crashed: {}", exc)

    def _flush_exif_batch(batch: list, et: "ExiftoolProcess") -> None:
        paths = [r.record.path for r in batch]
        chunk_extracts = batch_read_extracts(paths, et, chunk_size=chunk_size)
        extracts.update(chunk_extracts)
        with exif_done_lock:
            exif_done[0] += len(batch)
            done_snapshot = exif_done[0]
        with exif_total_lock:
            total_snapshot = exif_total[0]
        _emit_stage(bus, exif_tracker, done_snapshot, total_snapshot)

    # #486-PR3 — resolve "auto" to thread|process.
    resolved_pool = config.hash_pool
    calibrated_bktree_floor: "int | None" = None
    if resolved_pool == "auto":
        resolved_pool, calibrated_bktree_floor = _calibrate_hash_pool(
            bus, config, records, ThreadPoolExecutor, ProcessPoolExecutor
        )
    bus.log(
        f"Hashing {len(records):,} files (workers={config.workers},"
        f" pool={resolved_pool})…"
    )
    bus.log(
        f"EXIF + scoring signals via exiftool — pipelined,"
        f" {config.exif_workers} parallel process(es)…"
    )
    hash_results: list[HashResult] = [None] * len(records)  # type: ignore[list-item]
    done = 0
    hash_tracker = _StageTracker(STAGE_HASH)
    _emit_stage(bus, hash_tracker, 0, len(records), force=True)
    _emit_stage(bus, exif_tracker, 0, 0, force=True)

    # #451 — N consumer threads, each owning its own ExiftoolProcess.
    consumer_threads = [
        threading.Thread(
            target=_exif_consumer,
            name=f"exif-consumer-{i}",
            daemon=True,
        )
        for i in range(config.exif_workers)
    ]
    for t in consumer_threads:
        t.start()

    use_process = resolved_pool == "process"
    if use_process:
        # #610 — per-device process pools.
        from collections import OrderedDict as _OrderedDict
        from scanner.workers import device_key, hash_workers_for_root
        proc_device_records: "_OrderedDict[str, list[tuple[int, object]]]" = (
            _OrderedDict()
        )
        for idx, r in enumerate(records):
            proc_device_records.setdefault(device_key(r.path), []).append((idx, r))
        proc_device_workers = {
            dev: hash_workers_for_root(dev) for dev in proc_device_records
        }
        bus.log(
            f"Hashing {len(records):,} files across"
            f" {len(proc_device_records)} device(s) [process]: "
            + ", ".join(
                f"{dev or 'local'}={proc_device_workers[dev]}×{len(items)}f"
                for dev, items in proc_device_records.items()
            )
        )
        process_pools = {
            dev: ProcessPoolExecutor(max_workers=proc_device_workers[dev])
            for dev in proc_device_records
        }
        try:
            futures: dict = {}
            for dev, items in proc_device_records.items():
                pool = process_pools[dev]
                for _idx, _r in items:
                    futures[pool.submit(run_hash_for_record, _idx, _r)] = _idx
                # #549(a) — assign to KILL_ON_JOB_CLOSE job.
                _assign_process_pool_to_kill_job(pool)
            for future in as_completed(futures):
                if cancel_token():
                    # cancel_token already set; no separate flag needed.
                    for _p in process_pools.values():
                        _p.shutdown(wait=False, cancel_futures=True)
                    # #561 — kill exiftool first so a consumer wedged in a
                    # batch unblocks immediately.
                    _kill_exif_procs()
                    # #564 — drain the bounded exif_queue.
                    _drain_queue_nowait(exif_queue)
                    for _ in consumer_threads:
                        exif_queue.put(None)
                    for t in consumer_threads:
                        t.join(timeout=5)
                    logger.warning("Scan cancelled by user during hashing pass")
                    bus.failed("Scan cancelled.")
                    return
                idx, outcome = future.result()
                result = _route_outcome(records[idx], outcome)
                if result is not None:
                    hash_results[idx] = result
                done += 1
                if done % 100 == 0 or done == len(records):
                    bus.log(f"  Hashed {done:,}/{len(records):,}")
                _emit_stage(bus, hash_tracker, done, len(records))
        finally:
            for _p in process_pools.values():
                _p.shutdown(wait=False)
    else:
        # #566 — THREAD path: READ stage + COMPUTE stage joined by a
        # bounded queue.
        device_records: "OrderedDict[str, list[tuple[int, object]]]" = (
            OrderedDict()
        )
        for idx, r in enumerate(records):
            device_records.setdefault(device_key(r.path), []).append((idx, r))
        device_workers = {
            dev: hash_workers_for_root(dev) for dev in device_records
        }
        # #551 Phase 2 — read-knee ramp setup (default-OFF).
        read_permits: "dict | None" = None
        ramps: dict = {}
        _last_permits: dict = {}
        _knee_emitted: set = set()
        if config.autotune_read_knee:
            read_permits = {}
            for dev in device_records:
                max_c = device_workers[dev]
                cached = config.autotune_knees.get(dev)
                if _valid_read_knee(cached):
                    read_permits[dev] = threading.Semaphore(cached["knee"])
                    continue
                eligible = sum(
                    1
                    for _i, _r in device_records[dev]
                    if _r.file_type not in ("mp4", "mov", "gif", "skip")
                )
                if max_c <= 1 or eligible < _RAMP_MIN_SCAN_FILES:
                    read_permits[dev] = threading.Semaphore(max_c)
                    continue
                read_permits[dev] = threading.Semaphore(1)
                ramps[dev] = ReadKneeRamp(max_c)
                _last_permits[dev] = 1
        bus.log(
            f"Hashing {len(records):,} files across"
            f" {len(device_records)} device(s): "
            + ", ".join(
                f"{dev or 'local'}={device_workers[dev]}×{len(items)}f"
                for dev, items in device_records.items()
            )
        )

        reader_pools = {
            dev: ThreadPoolExecutor(max_workers=device_workers[dev])
            for dev in device_records
        }
        compute_pool = ThreadPoolExecutor(
            max_workers=os.cpu_count() or 4
        )
        # #587/#596 — per-device byte-budget gate.
        byte_budgets = per_device_budgets(
            default_budget_bytes(), list(device_records.keys()), cancel_token.is_set
        )
        out_q: _queue.Queue = _queue.Queue()

        def _gated_read(dev, idx, r):
            """#551 Phase 2 — reader worker wrapped with the per-device permit."""
            acquired = False
            while not cancel_token.is_set():
                if read_permits[dev].acquire(timeout=0.05):
                    acquired = True
                    break
            if not acquired:
                return None, dev, None, None
            ramp = ramps.get(dev)
            level_tag = ramp.current_permits() if ramp is not None else None
            try:
                read_result = read_for_record(idx, r)
            finally:
                read_permits[dev].release()
            t_end = time.monotonic()
            _data = read_result[2]
            byte_budgets[dev].acquire(len(_data) if isinstance(_data, bytes) else 0)
            return read_result, dev, level_tag, t_end

        def _budgeted_read(dev, idx, r):
            """#587 — ungated reader with byte-budget backpressure."""
            read_result = read_for_record(idx, r)
            _data = read_result[2]
            byte_budgets[dev].acquire(len(_data) if isinstance(_data, bytes) else 0)
            return read_result, dev

        def _read_drain() -> None:
            """Drive read futures into hash_in_q; put a None sentinel when done."""
            gated = read_permits is not None
            reader_futures: set = set()
            for dev, items in device_records.items():
                for _idx, _r in items:
                    if gated:
                        reader_futures.add(
                            reader_pools[dev].submit(_gated_read, dev, _idx, _r)
                        )
                    else:
                        reader_futures.add(
                            reader_pools[dev].submit(_budgeted_read, dev, _idx, _r)
                        )
            for fut in as_completed(reader_futures):
                if cancel_token.is_set():
                    break
                try:
                    if gated:
                        read_result, _dev, level_tag, t_end = fut.result()
                        reader_futures.discard(fut)
                        if read_result is None:
                            continue
                        ramp = ramps.get(_dev)
                        if ramp is not None:
                            _data = read_result[2]
                            _nbytes = len(_data) if isinstance(_data, bytes) else 0
                            ramp.record(_nbytes, t_end, level_tag=level_tag)
                            _new = ramp.advance_if_level_done()
                            if _new > _last_permits[_dev]:
                                read_permits[_dev].release(_new - _last_permits[_dev])
                                _last_permits[_dev] = _new
                            if (not ramp.is_ramping()) and _dev not in _knee_emitted:
                                _knee_emitted.add(_dev)
                                if len(ramps) == 1 and ramp.knee() is not None:
                                    _summary = ramp.summary()
                                    _summary["device"] = _dev
                                    _summary["sole_ramping"] = True
                                    bus.read_knee_measured(_summary)
                    else:
                        read_result, _dev = fut.result()
                        reader_futures.discard(fut)
                    while not cancel_token.is_set():
                        try:
                            hash_in_q.put((*read_result, _dev), timeout=0.05)
                            break
                        except _queue.Full:
                            pass
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            hash_in_q.put(None)  # sentinel — always sent, even on cancel

        def _compute_dispatch() -> None:
            """Pull from hash_in_q, submit compute_from_bytes futures."""
            while not cancel_token.is_set():
                try:
                    item = hash_in_q.get(timeout=0.05)
                except _queue.Empty:
                    continue
                if item is None:
                    break
                c_idx, c_record, c_data, c_dev = item
                n = len(c_data) if isinstance(c_data, bytes) else 0

                def _cb(f, _dev=c_dev, _n=n):
                    byte_budgets[_dev].release(_n)
                    out_q.put(f.result())

                try:
                    compute_pool.submit(
                        compute_from_bytes, c_idx, c_record, c_data
                    ).add_done_callback(_cb)
                except RuntimeError:
                    # #594 — shutdown race: cancel branch may have called
                    # compute_pool.shutdown(cancel_futures=True) between this
                    # loop's cancel_token check and the submit.
                    break

        import threading as _threading
        read_drain_thread = _threading.Thread(
            target=_read_drain, name="hash-read-drain", daemon=True
        )
        compute_dispatch_thread = _threading.Thread(
            target=_compute_dispatch,
            name="hash-compute-dispatch",
            daemon=True,
        )
        read_drain_thread.start()
        compute_dispatch_thread.start()

        cancelled = False
        try:
            for _ in range(len(records)):
                while True:
                    if cancel_token():
                        # cancel_token already set; no separate flag needed.
                        _drain_queue_nowait(hash_in_q)
                        for p in reader_pools.values():
                            p.shutdown(wait=False, cancel_futures=True)
                        compute_pool.shutdown(
                            wait=False, cancel_futures=True
                        )
                        _kill_exif_procs()
                        # #564 — drain the bounded exif_queue.
                        _drain_queue_nowait(exif_queue)
                        for _ in consumer_threads:
                            exif_queue.put(None)
                        for t in consumer_threads:
                            t.join(timeout=5)
                        logger.warning(
                            "Scan cancelled by user during hashing pass"
                        )
                        bus.failed("Scan cancelled.")
                        cancelled = True
                        return
                    try:
                        idx, outcome = out_q.get(timeout=0.5)
                        break
                    except _queue.Empty:
                        continue
                result = _route_outcome(records[idx], outcome)
                if result is not None:
                    hash_results[idx] = result
                done += 1
                if done % 100 == 0 or done == len(records):
                    bus.log(f"  Hashed {done:,}/{len(records):,}")
                _emit_stage(bus, hash_tracker, done, len(records))
        finally:
            if cancelled:
                _drain_queue_nowait(hash_in_q)
            read_drain_thread.join(timeout=5)
            compute_dispatch_thread.join(timeout=5)
            for p in reader_pools.values():
                p.shutdown(wait=False)
            compute_pool.shutdown(wait=False)

    # Signal each consumer that no more items are coming and wait
    # for them to drain whatever's still queued.
    for _ in consumer_threads:
        exif_queue.put(None)
    # #607 — cancel-aware post-HASH consumer join.
    while any(t.is_alive() for t in consumer_threads):
        if cancel_token():
            _kill_exif_procs()
            break
        for t in consumer_threads:
            if t.is_alive():
                t.join(timeout=0.5)
                break
    for t in consumer_threads:
        t.join(timeout=5.0)
    if cancel_token():
        logger.warning(
            "Scan cancelled by user during EXIF post-HASH drain"
        )
        bus.failed("Scan cancelled.")
        return

    hash_results = [r for r in hash_results if r is not None]

    if skipped:
        bus.log(f"  Skipped {len(skipped):,} unreadable file(s):")
        for p, exc_type, exc_msg in skipped[:10]:
            bus.log(f"    {p}  [{exc_type}: {exc_msg}]")
        if len(skipped) > 10:
            bus.log(f"    … and {len(skipped) - 10:,} more")

    # --- 3 (continued). EXIF post-processing ---
    et_records = [r for r in hash_results if r.record.file_type != "skip"]
    if exiftool_missing[0]:
        bus.log(
            "WARNING: exiftool not found on PATH — EXIF dates for HEIC/RAW/video"
            " and scoring signals (GPS, EXIF census, XMP provenance) unavailable"
            " for those formats. (JPEG scoring signals are extracted in-memory"
            " and are unaffected — #786.)\n"
            "Install from https://exiftool.org/ and add to PATH."
        )
    elif et_records:
        _emit_stage(bus, exif_tracker, exif_done[0], exif_total[0], force=True)
        found_dates = sum(1 for e in extracts.values() if e.exif_date is not None)
        with_gps = sum(1 for e in extracts.values() if e.gps_present)
        bus.log(
            f"  EXIF done — {len(extracts):,} files,"
            f" {found_dates:,} dates, {with_gps:,} with GPS"
        )

    # Backfill exif_date from the extracts dict for any record still missing
    # one. Runs REGARDLESS of exiftool availability: #786 populates `extracts`
    # for JPEG from the in-memory PIL pass independently of exiftool, so a JPEG
    # whose date lives only in XMP must not be left UNDATED in exiftool-missing
    # mode while apply_scoring_to_rows scores it WITH that date — an internally
    # inconsistent row (#793). For formats that DO need exiftool (HEIC/RAW/video)
    # there is no extract in exiftool-missing mode, so those are untouched.
    for r in et_records:
        if r.exif_date is None:
            extract = extracts.get(r.record.path)
            if extract is not None:
                r.exif_date = extract.exif_date

    # --- 4. Classify ---
    if cancel_token():
        logger.warning("Scan cancelled by user before classify pass")
        bus.failed("Scan cancelled.")
        return
    bus.log("Classifying…")
    classify_tracker = _StageTracker(STAGE_CLASSIFY)
    _emit_stage(bus, classify_tracker, 0, 0, force=True)
    rows = classify(
        hash_results,
        threshold=config.threshold,
        mean_color_threshold=config.mean_color_threshold,
        dhash_threshold=config.dhash_threshold,
        source_priority=config.source_priority,
        # #526 — per-machine BK-tree crossover (None → classify uses module default).
        bktree_min_candidates=calibrated_bktree_floor,
    )
    _emit_stage(bus, classify_tracker, 1, 1, force=True)

    # --- 4.5: score within each duplicate group (#187) ---
    if cancel_token():
        logger.warning("Scan cancelled by user before scoring pass")
        bus.failed("Scan cancelled.")
        return
    score_tracker = _StageTracker(STAGE_SCORE)
    _emit_stage(bus, score_tracker, 0, 0, force=True)
    apply_scoring_to_rows(rows, extracts)
    _emit_stage(bus, score_tracker, 1, 1, force=True)

    # --- 4.6: optional auto-select keepers (#212, #393) ---
    if cancel_token():
        logger.warning("Scan cancelled by user before auto-select pass")
        bus.failed("Scan cancelled.")
        return
    keepers: set[str] = set()
    non_keepers: "set[str] | None" = None
    if config.auto_select_enabled:
        from core.services.auto_select import top_score_path_per_group
        keepers = top_score_path_per_group(rows)
        if keepers:
            for row in rows:
                if row.source_path in keepers:
                    row.action = "KEEP"
            bus.log(f"Auto-select: marked {len(keepers):,} keeper(s) per group.")
            if config.auto_select_aggressive_delete:
                from core.services.auto_select import (
                    non_keepers_for_aggressive_delete,
                )
                non_keepers = non_keepers_for_aggressive_delete(rows, keepers)
                bus.log(
                    f"Auto-select aggressive: marked {len(non_keepers):,}"
                    f" non-keeper(s) for delete."
                )

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_summary(rows, skipped=len(skipped))
    for line in buf.getvalue().splitlines():
        bus.log(line)

    # --- 5. Write manifest (atomic incl. auto-select decisions — #651) ---
    if cancel_token():
        logger.warning("Scan cancelled by user before manifest write")
        bus.failed("Scan cancelled.")
        return
    bus.log(f"Writing manifest → {config.output_path}")
    write_tracker = _StageTracker(STAGE_WRITE)
    _emit_stage(bus, write_tracker, 0, 0, force=True)
    write_manifest(
        rows,
        config.output_path,
        keepers=keepers if keepers else None,
        non_keepers_for_delete=non_keepers,
    )
    _emit_stage(bus, write_tracker, 1, 1, force=True)
    if keepers:
        bus.log(
            f"Auto-select: locked {len(keepers):,} keeper(s);"
            f" decisions written."
        )

    bus.log("Done.")
    bus.finished(str(config.output_path))
