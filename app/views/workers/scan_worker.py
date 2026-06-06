"""Background QThread that runs the deduplication scan pipeline."""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from loguru import logger

# #424 — rolling throughput sampling window used to compute files/sec.
# 5s matches the issue's acceptance criterion ("ETA appears once ≥ 5s of
# throughput samples are available"). Wide enough that an SMB blip
# doesn't crash the rate to zero, narrow enough that a real stall
# surfaces within ~5s instead of getting smoothed out over a minute.
_THROUGHPUT_WINDOW_SECONDS = 5.0

# Minimum interval between two stage_progress emits inside a streaming
# loop. Per-second cadence keeps the UI feeling live without burning
# Qt event loop on every single file in a 100k-file scan.
_STAGE_EMIT_INTERVAL_SECONDS = 1.0

# Canonical stage names (#424). Receiver localises for display via
# translations[scan_dialog.stage_<name_lower>]; raw string passes
# through the Qt signal so the worker stays UI-agnostic.
STAGE_WALK = "WALK"
STAGE_HASH = "HASH"
STAGE_EXIFTOOL = "EXIFTOOL"
STAGE_CLASSIFY = "CLASSIFY"
STAGE_SCORE = "SCORE"
STAGE_WRITE = "WRITE"

# #486-PR3 — auto-calibration sample sizing for ``scan.hash_pool="auto"``.
# The worker times this many of the real scan's records through both
# executors before the full HASH stage, projects each to the real file
# count, and runs the faster one. Below the floor the measurement is too
# noisy to trust, so calibration is skipped and "thread" wins by default.
_CALIBRATION_SAMPLE = 96
_CALIBRATION_MIN = 24

# #526 — grouping-stage calibration. The near-dup candidate search has two
# strategies (brute O(N²) vs BK-tree); the crossover floor is measured per
# machine from two content-independent micro-rates rather than baked as a
# constant. ``_GROUP_CALIBRATION_SAMPLE`` synthetic 64-bit hashes (clustered so
# BK queries return realistic neighbour sets) are timed once; the rates are
# cached under the same #486 fingerprint and the derived floor is clamped to
# ``[_GROUP_FLOOR_MIN, _GROUP_FLOOR_MAX]``. PR1 (#529) proved BK wins from N≈10
# up, so the floor lands near the min on today's recipe — the value of
# measuring it is that the calibration *self-tunes* if a future hash recipe
# (longer hashes, slower metric) shifts the economics.
_GROUP_CALIBRATION_SAMPLE = 256
_GROUP_CALIBRATION_PAIRS = 4000
_GROUP_FLOOR_MIN = 8
_GROUP_FLOOR_MAX = 256


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


_RATE_KEYS = ("thread_per_file", "process_per_file", "spawn")


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


class _StageTracker:
    """Worker-side throughput accumulator + per-second emit throttle.

    One instance per stage. Records `(timestamp, completed_count)`
    samples in a deque trimmed to the last :data:`_THROUGHPUT_WINDOW_SECONDS`
    on every update, then reports throughput as
    `(latest_completed - oldest_completed) / (latest_ts - oldest_ts)` —
    zero when the deque collapses to a single sample or the dt is
    too small for a stable rate.

    The throttle prevents per-file emits in the hot HASH / EXIFTOOL
    loops; ``should_emit()`` returns True only on (a) the first call
    for a stage, (b) the boundary (completed == total), or (c) when
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


class ScanWorker(QThread):
    """Runs scan.py pipeline in a background thread.

    Signals:
        progress(str)        — one-line status update for the UI log
        stage_progress(str, int, int, float)
                              — #424 typed per-stage progress: stage
                                name, completed-in-stage, total-in-stage,
                                files-per-second over the last
                                :data:`_THROUGHPUT_WINDOW_SECONDS`.
                                ``total == 0`` marks an atomic stage
                                (receiver should render indeterminate).
                                ``files_per_sec == 0`` indicates either
                                a stall or insufficient samples — ETA
                                hides until the rate stabilises.
        finished(str)        — emitted with manifest_path on success
        failed(str)          — emitted with error message on real failure
        completed_empty()    — scan ran cleanly but found 0 media files
                               (kept distinct from `failed` so the dialog
                               can avoid misclassifying a benign empty
                               input as an error)
    """

    progress = Signal(str)
    stage_progress = Signal(str, int, int, float)
    finished = Signal(str)
    failed = Signal(str)
    completed_empty = Signal()
    # #486-PR3b — emitted once after a FRESH hash-pool calibration (cache
    # miss) carrying the measured rates dict {thread_per_file, process_per_file,
    # spawn}. The dialog persists it keyed by a machine+sources fingerprint so
    # the next scan of the same library skips the ~2s re-measurement. Emitted
    # right after calibration (before the long hash pass) so the measurement
    # survives even if the user cancels the scan.
    hash_pool_measured = Signal(dict)
    # #551 Phase 2 — emitted once per device after its read-knee ramp freezes
    # with a clean (sole-ramping) measurement, carrying ReadKneeRamp.summary()
    # augmented with the device_key. The dialog (#551 Phase 3) persists it via
    # scanner.autotune.store_read_knee keyed by device_key, so the next scan of
    # that device starts at the cached knee with no ramp. No persistence slot is
    # wired in this PR — the signal exists and fires; the dialog connects it next.
    read_knee_measured = Signal(dict)

    def __init__(
        self,
        sources: dict[str, str],                    # label → path string
        output_path: str,
        recursive_map: dict[str, bool] | None = None,
        source_priority: dict[str, int] | None = None,
        threshold: int = 10,
        mean_color_threshold: int = 30,
        dhash_threshold: int = 10,
        limit: int | None = None,
        workers: int = 4,
        exif_workers: int = 2,
        hash_pool: str = "thread",
        hash_pool_rates: dict | None = None,
        auto_select_enabled: bool = False,
        auto_select_aggressive_delete: bool = False,
        autotune_read_knee: bool = False,
        autotune_knees: dict | None = None,
    ) -> None:
        super().__init__()
        self.sources = {k: Path(v) for k, v in sources.items() if v.strip()}
        self.output_path = Path(output_path)
        self.recursive_map = recursive_map or {}
        self.source_priority = source_priority   # None → auto-inferred in classify()
        self.threshold = threshold
        self.mean_color_threshold = mean_color_threshold
        self.dhash_threshold = dhash_threshold
        self.limit = limit
        self.workers = workers
        # #451 — number of parallel ExiftoolProcess instances spawned
        # by the exif consumer thread pool. Clamped at construction to
        # ``min(4, os.cpu_count() // 2)`` (with a floor of 1) so a
        # 100-core machine doesn't peg the box on exiftool spawn cost.
        # exiftool itself is single-threaded within one ``-stay_open``
        # instance; running N instances in parallel scales near-linearly
        # up to ~4 instances on a modern CPU.
        import os as _os
        cpu = _os.cpu_count() or 4
        cap = max(1, min(4, cpu // 2))
        self.exif_workers = max(1, min(exif_workers, cap))
        # #486 follow-up — HASH-stage executor selector:
        #   "thread"  (default) — in-process ThreadPoolExecutor
        #   "process" (PR2) — picklable run_hash_for_record across a
        #             ProcessPoolExecutor to escape the GIL on CPU-bound
        #             hashing (Windows spawn re-imports PIL/rawpy per
        #             worker, so it only pays off on large scans)
        #   "auto"    (PR3) — time a sample of the real scan data through
        #             both executors at scan start and run the faster
        # Unknown values fall back to "thread".
        self.hash_pool = (
            hash_pool if hash_pool in ("thread", "process", "auto") else "thread"
        )
        # #486-PR3b — pre-measured calibration rates from the dialog's
        # fingerprint cache. When present (and hash_pool="auto"), the worker
        # re-projects them to the current file count instead of re-measuring;
        # when None, "auto" measures fresh and emits hash_pool_measured so the
        # dialog can cache the result. Ignored unless hash_pool == "auto".
        self.hash_pool_rates = hash_pool_rates
        # #212 — when True, promote the top-scored row in each duplicate
        # group to action="KEEP" before writing the manifest. The scan
        # dialog persists the corresponding setting; defaults False so
        # callers that don't opt in get the pre-#212 behaviour.
        self.auto_select_enabled = auto_select_enabled
        # #393 — when True (and auto_select_enabled also True), set
        # user_decision='delete' on every non-keeper row in scored
        # groups so the user opens Execute Action with the full triage
        # pre-populated. Off by default because it's destructive-leaning;
        # the user still confirms via the standard ExecuteAction flow.
        self.auto_select_aggressive_delete = auto_select_aggressive_delete
        # #526 — per-machine BK-tree crossover floor for the grouping stage,
        # set by ``_calibrate_hash_pool`` when hash_pool="auto". ``None`` →
        # classify() uses the module default (thread/process scans, or auto
        # scans whose cached rates predate the grouping micro-rates).
        self._calibrated_bktree_floor: int | None = None
        # #551 Phase 2 — in-pipeline read-knee ramp. This ctor flag defaults False
        # (a library-safe default); the Scan dialog passes the explicit checkbox
        # state, which is ON by default since #551 Phase 4 (bounded first-scan ramp
        # tax — the conservative N=8 floor, _RAMP_MIN_SCAN_FILES). When False the
        # thread branch builds reader pools at the static hash_workers_for_root
        # count exactly as before (byte-identical path). When True, each device's
        # reader pool is still sized at that static MAX but a per-device Semaphore
        # caps active reads, ramped 1→2→4→8 to a measured files/s knee (or started
        # at a cached knee).
        self._autotune_read_knee = autotune_read_knee
        # Pre-cached per-device knees {device_key: {"knee": int, "recipe": str}}
        # read from scan.read_knee_cache by the dialog (#551 Phase 3). A valid
        # entry skips the ramp for that device — its Semaphore starts at the
        # cached knee. Empty by default; ignored unless _autotune_read_knee.
        self.autotune_knees = autotune_knees or {}

    def run(self) -> None:
        try:
            self._run_pipeline()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Log with traceback so the rotating app_<date>.log captures the
            # full forensic context — the dialog log box clears on close.
            logger.exception("Scan pipeline failed: {}", exc)
            self.failed.emit(str(exc))

    def _emit(self, msg: str) -> None:
        # Forward every progress line to loguru so the rotating app_<date>.log
        # has a persistent record for users reporting "the scan stopped" —
        # the dialog log box is transient and disappears on close.
        logger.info("scan: {}", msg)
        self.progress.emit(msg)

    def _emit_stage(
        self,
        tracker: _StageTracker,
        completed: int,
        total: int,
        *,
        force: bool = False,
    ) -> None:
        """#424 — emit a stage_progress signal with throttling.

        ``force`` bypasses the per-second throttle for stage boundaries
        (start / end) where the receiver must update the label even if
        the throttle hasn't elapsed. The throughput value rides on the
        tracker's rolling deque; samples are recorded unconditionally
        so a slow loop's rate stays accurate even when emits are
        throttled away.
        """
        tracker.record(completed)
        if force or tracker.should_emit(completed, total):
            self.stage_progress.emit(
                tracker.stage_name, completed, total, tracker.throughput()
            )

    def _calibrate_hash_pool(self, records: list, thread_cls, process_cls) -> str:
        """Resolve ``hash_pool="auto"`` to "thread" or "process".

        #486-PR3 — times a sample of the *real* scan data (in-situ, not a
        synthetic benchmark) and **projects each executor to the full file
        count** before comparing:

            thread_total  ≈ thread_per_file × N
            process_total ≈ spawn_cost + process_per_file × N

        Projecting (rather than comparing raw sample times) is what makes
        the pick correct across scales: process's one-time spawn cost
        dominates on a tiny scan (→ thread) but amortises to nothing on a
        large one (→ process). The flat per-sample timing in the first cut
        (#498) charged that spawn against the 96-file sample and so
        under-credited process on large scans.

        Below ``_CALIBRATION_MIN`` files the measurement is too noisy to
        trust, so we skip it and pick "thread". The projection components
        are logged so the decision stays visible.

        #486-PR3b — when ``self.hash_pool_rates`` is supplied (the dialog's
        fingerprint cache hit), the measurement is skipped entirely and the
        cached rates are re-projected to the current ``N`` — so a re-scan of
        the same library doesn't pay the ~2s calibration again, yet still
        adapts the pick if the file count changed. On a cache miss the fresh
        rates are emitted via ``hash_pool_measured`` for the dialog to store.

        #554 — multi-device + NAS guard: when the scan spans ≥2 physical
        devices AND at least one is a remote (NAS) drive, the flat
        thread-vs-process projection is skipped and "thread" is returned
        unconditionally. The flat calibration builds a FLAT
        ThreadPoolExecutor over the sample — it never learned that #550
        made thread mode PER-DEVICE. On a mixed HDD+NAS scan it measures
        "8 threads thrashing the HDD" (slow) and under-credits thread →
        picks process. But process runs the old flat single pool, bypassing
        the per-device I/O-overlap win entirely. The per-device overlap is
        exactly what the flat calibration cannot measure, and on an
        I/O-bound mixed scan it dominates. The single-device / all-local
        case is unaffected — calibration still runs there and process can
        legitimately win.
        """
        from scanner.workers import device_key, is_remote_drive

        device_keys = {device_key(getattr(r, "path", r)) for r in records}
        if len(device_keys) >= 2 and any(
            is_remote_drive(dk) for dk in device_keys if dk
        ):
            self._emit(
                "  Multi-device + NAS scan → per-device thread path"
                " (I/O overlap wins; skipping flat process calibration)"
            )
            # #554 — still resolve grouping floor from cached rates if
            # available, so the BK-tree calibration isn't lost on the
            # thread-fast path. When no cached rates exist the floor
            # stays None and classify() uses the module default.
            if _valid_hash_pool_rates(self.hash_pool_rates):
                self._calibrated_bktree_floor = self._resolve_grouping_floor(
                    self.hash_pool_rates, len(records)
                )
            return "thread"

        n = len(records)
        rates = self.hash_pool_rates
        if not _valid_hash_pool_rates(rates):
            # #548 — stratify the sample across devices so the thread-vs-
            # process pick is measured fairly on a mixed scan instead of on
            # the first (source-order) device alone. Single-device scans are
            # unchanged (the helper returns records[:n] verbatim).
            sample = _stratified_sample(records, _CALIBRATION_SAMPLE)
            if len(sample) < _CALIBRATION_MIN:
                self._emit(
                    f"  Hash-pool calibration skipped ({len(sample)} files;"
                    f" need ≥{_CALIBRATION_MIN}) → pool=thread"
                )
                return "thread"
            self._emit(f"  Calibrating hash pool on {len(sample)} files…")
            thread_s = _time_hash_executor(thread_cls, sample, self.workers)
            spawn_s, process_per_file = _profile_process_pool(
                process_cls, sample, self.workers
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
            self.hash_pool_measured.emit(rates)
        else:
            self._emit("  Using cached hash-pool calibration (fingerprint match)…")
        thread_proj = rates["thread_per_file"] * n
        process_proj = rates["spawn"] + rates["process_per_file"] * n
        winner = "process" if process_proj < thread_proj else "thread"
        self._emit(
            f"  Hash-pool calibration → projected to {n:,}:"
            f" thread≈{thread_proj:.1f}s process≈{process_proj:.1f}s"
            f" (spawn {rates['spawn'] * 1000:.0f}ms +"
            f" {rates['process_per_file'] * 1000:.1f}ms/file) → pool={winner}"
        )
        # #526 — fold the GROUPING stage into the same calibration: derive the
        # per-machine BK-tree floor from the cached/measured micro-rates and
        # log the projected brute-vs-BK grouping cost at this file count. Stored
        # on the worker so the classify() call passes the measured floor instead
        # of the baked module default. Defensive: a hand-edited cache missing
        # the group keys falls back to the default floor (None).
        self._calibrated_bktree_floor = self._resolve_grouping_floor(rates, n)
        return winner

    def _resolve_grouping_floor(self, rates: dict, n: int) -> int | None:
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
        self._emit(
            f"  Grouping calibration → BK-tree floor {floor} candidates;"
            f" projected to ≤{n:,}: brute≈{brute_proj:.1f}s vs BK≈{bk_proj:.2f}s"
        )
        return floor

    def _run_pipeline(self) -> None:
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
        from scanner.byte_budget import ByteBudget, default_budget_bytes
        import io
        from contextlib import redirect_stdout

        # #425 — was "no files will be moved or deleted"; reworded to
        # match scan_dialog.notice and stop implying a file operation
        # the read-only scan never performs.
        self._emit("Read-only scan — no files on disk are changed.")
        self._emit("")

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
        self._emit(f"Scanning {len(self.sources)} source(s)…")
        records: list = []
        walk_tracker = _StageTracker(STAGE_WALK)
        walk_files_seen = 0
        walk_counter_lock = threading.Lock()
        self._emit_stage(walk_tracker, 0, 0, force=True)

        def _on_walk_file_seen() -> None:
            nonlocal walk_files_seen
            with walk_counter_lock:
                walk_files_seen += 1
                snapshot = walk_files_seen
            # The tracker's should_emit throttles to 1Hz so a million
            # rglob hits on a fast SSD don't spam the Qt event loop.
            # Qt signal emission across threads is queued automatically
            # (the receiver lives in the main thread), so this is
            # safe to call from any walker thread.
            self._emit_stage(walk_tracker, snapshot, 0)

        def _walk_one_source(label: str, root: Path) -> tuple[str, list]:
            mode = "flat" if self.recursive_map.get(label) is False else "recursive"
            self._emit(f"  Walking {label} ({mode}): {root} …")
            # #491 — pass the QThread's interruption flag straight through
            # as the walker's cancel-check. A title-bar X / Cancel during
            # the WALK stage now lands within one rglob tick instead of
            # waiting for ``rglob`` to exhaust. For the multi-source
            # branch each parallel walker observes the same flag.
            partial = scan_sources(
                {label: root},
                limit=self.limit,
                recursive_map={label: self.recursive_map.get(label, True)},
                progress_callback=_on_walk_file_seen,
                cancel_check=self.isInterruptionRequested,
            )
            self._emit(f"  → {len(partial):,} files")
            return label, partial

        if len(self.sources) > 1:
            # Parallel branch — one thread per source, capped to the
            # source count so a 100-source pathological case doesn't
            # spawn 100 threads. Per-source results are collected by
            # label so we can rebuild the source-order list below.
            partials: dict[str, list] = {}
            with ThreadPoolExecutor(max_workers=len(self.sources)) as pool:
                futures = {
                    pool.submit(_walk_one_source, label, root): label
                    for label, root in self.sources.items()
                }
                for future in as_completed(futures):
                    label, partial = future.result()
                    partials[label] = partial
            for label in self.sources:
                records.extend(partials.get(label, []))
        else:
            for label, root in self.sources.items():
                _, partial = _walk_one_source(label, root)
                records.extend(partial)

        # Force a final emit so the stage bar reflects the true count
        # when scan_sources finishes faster than the 1Hz throttle.
        self._emit_stage(walk_tracker, walk_files_seen, 0, force=True)
        self._emit(f"  Total: {len(records):,} media files")

        # #491 — gate-out after WALK if the user cancelled. The walker's
        # cooperative check returns partial results without raising, so
        # the only way to distinguish "walked everything" from
        # "cancelled mid-walk with partial" is to re-check the QThread
        # interruption flag here. Symmetric with the HASH / CLASSIFY /
        # SCORE / WRITE stage gates further down — same ``"Scan
        # cancelled."`` failed-signal payload so scan_dialog distinguishes
        # the clean cancel from a red-modal error string.
        if self.isInterruptionRequested():
            logger.warning("Scan cancelled by user during walk")
            self.failed.emit("Scan cancelled.")
            return

        if not records:
            # Empty input is a benign success, not a failure: the user picked
            # folders that simply have no media. Log a neutral terminator and
            # signal the dialog to re-enable Start Scan without a red modal.
            self._emit("Done. No media files found — nothing to scan.")
            self.completed_empty.emit()
            return

        # --- 2 + 3. Hash + EXIF (pipelined / overlapping) ---
        # One file read per image: SHA-256, pHash, and EXIF date for JPEG/PNG
        # are extracted from the same in-memory buffer.
        #
        # #450 — hash and exif stages now overlap via a producer-consumer
        # queue: each hash worker pushes its HashResult onto ``exif_queue``
        # as soon as it finishes, and a single dedicated consumer thread
        # batches them into 500-path chunks fed to one ExiftoolProcess.
        # The previous strict-serial flow (all hashes done → all exif done)
        # left the CPU idle during whichever stage wasn't running; under
        # the new flow total wall time drops by ≈ min(hash_time, exif_time)
        # because exif fully overlaps the tail of hashing.
        #
        # Corrupt-image detection moved from a post-hash sweep INTO the
        # hash worker so we don't enqueue corrupt files to exiftool; the
        # skipped[] accumulator + the post-loop emit summary preserve the
        # pre-#450 user-visible behaviour.
        import queue as _queue

        chunk_size = 500
        cancel_flag = threading.Event()
        skipped: list[tuple[Path, str, str]] = []  # (path, exc type, exc msg)
        # #561 — live ExiftoolProcess instances owned by the consumer threads.
        # A consumer wedged inside a batch only checks cancel_flag between
        # queue gets, so on cancel we kill its exiftool directly to unblock it
        # (see _kill_exif_procs) instead of waiting out the join timeout and
        # orphaning the process. Guarded by a lock — consumers register from
        # their own threads while the HASH-cancel branch reads the list.
        exif_procs: list = []
        exif_procs_lock = threading.Lock()

        def _kill_exif_procs() -> None:
            """#561 — hard-kill every live exiftool so a consumer wedged in a
            batch unblocks immediately (its execute() hits EOF and returns),
            letting the cancel join complete fast and leaving no orphan."""
            with exif_procs_lock:
                for _p in exif_procs:
                    _p.kill()

        # Silent image-decode-failure detection lives in
        # scanner.hasher.run_hash_for_record now (#486 refactor). The
        # closure below only routes the HashFailure / HashResult outcomes
        # into ``skipped`` and ``exif_queue``; the compute and the
        # corrupt-image gate are inside run_hash_for_record so the same
        # pure function can be reused by a future ProcessPoolExecutor path.

        # #566 — bounded read→compute queue for the thread branch HASH stage.
        # Capacity of 128 provides ~2× the typical per-device pool size in
        # flight at once; when compute falls behind, back-pressure blocks the
        # reader threads so the RAM ceiling stays near one read-window worth of
        # bytes rather than the whole scan.  The process branch and calibration
        # timers keep calling the fused run_hash_for_record unchanged.
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
        # Capacity = 2 full batches per consumer keeps the #450 hash/exif
        # overlap intact (the producer never has to block under normal load)
        # while capping RAM to a predictable ceiling.
        _EXIF_QUEUE_MAXSIZE = 2 * chunk_size * self.exif_workers
        exif_queue: _queue.Queue = _queue.Queue(maxsize=_EXIF_QUEUE_MAXSIZE)
        extracts: dict = {}
        exif_tracker = _StageTracker(STAGE_EXIFTOOL)
        exif_done = [0]
        exif_total = [0]  # grows as hash threads enqueue eligible records
        # #451 — locks guard the shared counters under N parallel
        # consumer threads. CPython's GIL makes int ``+=`` atomic on
        # named bindings, but ``list[0] += k`` is __getitem__ then
        # __setitem__ — a tight race window with N consumers. The
        # extracts dict's ``.update`` is GIL-atomic per the CPython
        # dict implementation, so we don't lock around it.
        exif_done_lock = threading.Lock()
        exif_total_lock = threading.Lock()
        # Latched flag — ``True`` if any consumer thread aborted because
        # exiftool isn't installed. Surfaced as a one-line warning AFTER
        # all consumers join so the message stays adjacent to the EXIF
        # block in the log.
        exiftool_missing = [False]

        def _route_outcome(record, outcome):
            """Route one compute outcome into the shared dispatch state and
            return the ``HashResult`` to store (or ``None`` to skip).

            #486-PR2 — extracted so both executor paths share ONE routing
            implementation. Both the process path (parent drain loop) and
            the thread path (``out_q`` drain loop) call this in the single
            parent drain loop after the compute stage returns.
            ``skipped.append`` is GIL-atomic, ``exif_queue`` is thread-safe,
            and the ``exif_total`` bump is taken under ``exif_total_lock``
            for the consumers' cross-thread read.

            #564 — the put is cancel-safe: uses a cooperative bounded-put
            loop so a slow exiftool consumer with a full queue doesn't
            deadlock the producer on cancel.
            """
            if isinstance(outcome, HashFailure):
                # Both raised exceptions and silent decode failures
                # land here — the HashFailure carries a distinct
                # exc_type so the user-visible log line stays
                # distinguishable.
                skipped.append((record.path, outcome.exc_type, outcome.exc_msg))
                return None
            if outcome is None:
                return None
            # outcome is a HashResult — queue for exif unless this is
            # a skip-type record (the exiftool pass excludes "skip"
            # anyway pre-#450).
            if record.file_type != "skip":
                # #564 — cooperative bounded put: mirrors the _read_drain
                # pattern so a full exif_queue (slow exiftool consumer) can't
                # wedge the producer forever on cancel.
                while not cancel_flag.is_set():
                    try:
                        exif_queue.put(outcome, timeout=0.05)
                        break
                    except _queue.Full:
                        pass
                with exif_total_lock:
                    exif_total[0] += 1
            return outcome

        def _exif_consumer() -> None:
            """Drain ``exif_queue`` into 500-batches fed to one
            ExiftoolProcess. Sentinel = ``None``.

            Exits early on ``cancel_flag`` (between blocking gets via a
            short ``get(timeout=...)``) so a user-cancel during hashing
            tears down the exiftool process within ~0.5s. If exiftool
            isn't on PATH we drain the queue without processing and
            latch ``exiftool_missing[0]`` so the worker surfaces the
            "install exiftool" warning post-join.
            """
            try:
                proc = ExiftoolProcess()
            except FileNotFoundError:
                exiftool_missing[0] = True
                # Drain until sentinel/cancel so the producer's put()
                # calls don't pile up in memory for a 100k-file scan.
                while True:
                    try:
                        item = exif_queue.get(timeout=0.5)
                    except _queue.Empty:
                        if cancel_flag.is_set():
                            return
                        continue
                    if item is None:
                        return
                return
            # #561 — register so the HASH-cancel branch can kill this exiftool
            # if this consumer wedges inside a batch. Registered before the
            # batch loop; the lock guards the cross-thread read in _kill_exif_procs.
            with exif_procs_lock:
                exif_procs.append(proc)
            try:
                with proc as et:
                    batch: list = []
                    while True:
                        try:
                            item = exif_queue.get(timeout=0.5)
                        except _queue.Empty:
                            if cancel_flag.is_set():
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
                # Don't let an exiftool failure abort the scan — log and
                # carry on with whatever extracts we already collected.
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
            # exif_total is still growing while hashing runs; the dialog
            # renders this as (done / total) with the total ticking up.
            # Once hashing finishes the totals settle.
            self._emit_stage(exif_tracker, done_snapshot, total_snapshot)

        # #486-PR3 — resolve "auto" to thread|process by timing a sample
        # of the real scan data through both executors. Held in a local so
        # the log line and the executor branch below see thread|process,
        # never "auto" — self.hash_pool stays the user's literal setting.
        resolved_pool = self.hash_pool
        if resolved_pool == "auto":
            resolved_pool = self._calibrate_hash_pool(
                records, ThreadPoolExecutor, ProcessPoolExecutor
            )
        self._emit(
            f"Hashing {len(records):,} files (workers={self.workers},"
            f" pool={resolved_pool})…"
        )
        self._emit(
            f"EXIF + scoring signals via exiftool — pipelined,"
            f" {self.exif_workers} parallel process(es)…"
        )
        hash_results: list[HashResult] = [None] * len(records)  # type: ignore[list-item]
        done = 0
        hash_tracker = _StageTracker(STAGE_HASH)
        self._emit_stage(hash_tracker, 0, len(records), force=True)
        self._emit_stage(exif_tracker, 0, 0, force=True)

        # #451 — N consumer threads, each owning its own ExiftoolProcess.
        # All consumers pull from the same queue (Queue is thread-safe);
        # exiftool itself is single-threaded within one ``-stay_open``
        # instance, but N independent instances scale near-linearly up
        # to the CPU cap baked into self.exif_workers.
        consumer_threads = [
            # #472 — daemon=True so interpreter shutdown can't block on a
            # consumer thread that, due to a future bug, didn't receive its
            # sentinel. Normal cancel + happy paths still drain via the
            # sentinel-then-join contract below; daemon flag is the
            # emergency-only fallback.
            threading.Thread(
                target=_exif_consumer,
                name=f"exif-consumer-{i}",
                daemon=True,
            )
            for i in range(self.exif_workers)
        ]
        for t in consumer_threads:
            t.start()

        # #486-PR2 — executor selector. Process path submits the picklable
        # run_hash_for_record directly (the child can't touch the
        # thread-only cancel_flag / exif_queue); the parent routes each
        # outcome in the drain loop. Thread path (default) keeps the
        # in-worker routing via _hash_one — identical to pre-PR2.
        use_process = resolved_pool == "process"
        if use_process:
            # TODO(#548 follow-on): per-device process pools. The
            # seek-thrash problem #548 targets is I/O-bound, so the
            # per-device overlap win lives entirely in the thread branch.
            # Keep the single flat ProcessPoolExecutor here.
            #
            # #549(b) — explicit pool + try/finally, NOT ``with``. A
            # ``with ProcessPoolExecutor() as pool:`` runs shutdown(wait=True)
            # on __exit__, which on a user-cancel blocks until every in-flight
            # worker finishes its current read_bytes() — defeating the
            # requestInterruption()/wait(3000) teardown and leaving the disk
            # grinding after "exit". ``finally: shutdown(wait=False)`` lets the
            # QThread return promptly; #549(a)'s job assignment reaps any
            # still-running workers when the parent exits. Mirrors the thread
            # branch's teardown shape below.
            pool = ProcessPoolExecutor(max_workers=self.workers)
            try:
                futures = {
                    pool.submit(run_hash_for_record, i, r): i
                    for i, r in enumerate(records)
                }
                # #549(a) — workers are spawned by the submit() calls above;
                # assign them to the #460 KILL_ON_JOB_CLOSE job so a hard
                # parent-kill reaps them instead of leaving python.exe workers
                # reading the source disks. No-op off Windows / without pywin32.
                _assign_process_pool_to_kill_job(pool)
                for future in as_completed(futures):
                    if self.isInterruptionRequested():
                        cancel_flag.set()
                        pool.shutdown(wait=False, cancel_futures=True)
                        # #561 — kill exiftool first so a consumer wedged in a
                        # batch unblocks immediately (execute() hits EOF);
                        # otherwise the join below waits out the whole batch
                        # and the abandoned consumer orphans its process.
                        _kill_exif_procs()
                        # #564 — drain the bounded exif_queue so any producer
                        # wedged in a cooperative put() unblocks and can exit
                        # on the cancel_flag check (mirrors hash_in_q drain in
                        # the thread branch).
                        _drain_queue_nowait(exif_queue)
                        # Tell each consumer to stop — one sentinel per
                        # consumer so each gets exactly one ``None`` off the
                        # queue. With exiftool killed + the 0.5s get(timeout)
                        # the consumer picks up cancel within ~½s.
                        for _ in consumer_threads:
                            exif_queue.put(None)
                        for t in consumer_threads:
                            t.join(timeout=5)
                        logger.warning("Scan cancelled by user during hashing pass")
                        self.failed.emit("Scan cancelled.")
                        return
                    idx, outcome = future.result()
                    # Process path returns the raw outcome to route here
                    # in the parent (the child can't touch the thread-only
                    # cancel_flag / exif_queue).
                    result = _route_outcome(records[idx], outcome)
                    if result is not None:
                        hash_results[idx] = result
                    done += 1
                    if done % 100 == 0 or done == len(records):
                        self._emit(f"  Hashed {done:,}/{len(records):,}")
                    # #424 — per-second-throttled stage_progress emit
                    # alongside the existing per-100 log line.
                    self._emit_stage(hash_tracker, done, len(records))
            finally:
                # wait=False (NOT the ``with``-exit's wait=True) so cancel
                # doesn't block on in-flight reads; on normal completion every
                # future is already drained so there is nothing to wait for.
                pool.shutdown(wait=False)
        else:
            # #566 — THREAD path: READ stage + COMPUTE stage joined by a
            # bounded queue (#566).  The two-stage split lets I/O-bound reads
            # (per-device pools, one executor per physical device so NAS and
            # HDD overlap) and CPU-bound decoding (one flat compute pool at
            # os.cpu_count()) run concurrently instead of serialised in each
            # worker thread.  #570 — a ``compute_inflight`` semaphore bounds the
            # number of submitted-but-unfinished compute tasks, so the bytes they
            # hold can't pile up unboundedly behind a slow compute stage (a bare
            # ``ThreadPoolExecutor.submit()`` never blocks); together with the
            # bounded hash_in_q this keeps RAM within the #453 ceiling.
            #
            # Ordering invariant: idx is threaded through both hops so the
            # parent can do hash_results[idx]=result — completion-order never
            # reaches classify().  This is the hard constraint from #526 /
            # #538 (lex-min group_id determinism / rescore key).
            #
            # Cancellation contract (#492/#495/#507/#561 scars):
            #   1. cancel_flag.set() — stops new dispatcher submissions.
            #   2. _drain_queue_nowait(hash_in_q) — unblocks any reader wedged
            #      in a bounded put() so reader threads can exit.
            #   3. reader_pools + compute_pool.shutdown(wait=False) — abandon
            #      in-flight work; no blocking on reads.
            #   4. _kill_exif_procs() + sentinels — existing exif teardown.
            device_records: "OrderedDict[str, list[tuple[int, object]]]" = (
                OrderedDict()
            )
            for idx, r in enumerate(records):
                device_records.setdefault(device_key(r.path), []).append((idx, r))
            device_workers = {
                dev: hash_workers_for_root(dev) for dev in device_records
            }
            # #551 Phase 2 — read-knee ramp setup (default-OFF). reader pools are
            # ALWAYS sized at device_workers[dev] (the static MAX); the ramp only
            # caps *active* reads via a per-device Semaphore. When the flag is off
            # read_permits stays None and the reader path below is byte-identical
            # to the pre-#551 thread branch. Per device, on the flag-on path:
            #   • valid cached knee  → Semaphore(knee), no ramp (lifetime cache hit)
            #   • HDD (MAX 1) / too few image files → Semaphore(MAX), no ramp
            #   • otherwise          → Semaphore(1) + a ReadKneeRamp that widens to MAX
            read_permits: "dict | None" = None
            ramps: dict = {}
            _last_permits: dict = {}
            _knee_emitted: set = set()
            if self._autotune_read_knee:
                read_permits = {}
                for dev in device_records:
                    max_c = device_workers[dev]
                    cached = self.autotune_knees.get(dev)
                    if _valid_read_knee(cached):
                        read_permits[dev] = threading.Semaphore(cached["knee"])
                        continue
                    eligible = sum(
                        1
                        for _i, _r in device_records[dev]
                        if _r.file_type not in ("mp4", "mov", "gif", "skip")
                    )
                    if max_c <= 1 or eligible < _RAMP_MIN_SCAN_FILES:
                        # Single-rung HDD, or a scan too short to reach the ramp's
                        # minimum size (_RAMP_MIN_SCAN_FILES — bounds the worst-case
                        # sub-MAX read tax, #551 Phase 4) → static MAX, no ramp.
                        read_permits[dev] = threading.Semaphore(max_c)
                        continue
                    read_permits[dev] = threading.Semaphore(1)
                    ramps[dev] = ReadKneeRamp(max_c)
                    _last_permits[dev] = 1
            self._emit(
                f"Hashing {len(records):,} files across"
                f" {len(device_records)} device(s): "
                + ", ".join(
                    f"{dev or 'local'}={device_workers[dev]}×{len(items)}f"
                    for dev, items in device_records.items()
                )
            )

            # READER pools — one per device, same per-device worker counts
            # as the pre-#566 thread branch.  Each submits read_for_record
            # whose result flows into hash_in_q.
            reader_pools = {
                dev: ThreadPoolExecutor(max_workers=device_workers[dev])
                for dev in device_records
            }
            # COMPUTE pool — one flat pool for CPU-bound decode/hash work.
            compute_pool = ThreadPoolExecutor(
                max_workers=os.cpu_count() or 4
            )
            # #587 — replace the count-based compute_inflight Semaphore with a
            # byte-budget gate.  Count alone (128 × ~130 MB DNG = ~16 GB) can't
            # bound the RAM footprint on a large DNG library.  ByteBudget caps
            # in-flight *bytes* instead: the byte cost is acquired in the READER
            # worker (_gated_read / _budgeted_read) — which back-pressures the
            # reader pool so completed-read bytes can't accumulate in
            # Future._result — and released in the compute done-callback.
            # hash_in_q maxsize=_HASH_QUEUE_MAXSIZE remains a secondary
            # queue-DEPTH cap.
            byte_budget = ByteBudget(default_budget_bytes(), cancel_flag.is_set)
            # out_q carries (idx, outcome) tuples from compute callbacks
            # back to the parent drain loop.
            out_q: _queue.Queue = _queue.Queue()

            def _gated_read(dev, idx, r):
                """#551 Phase 2 — reader worker wrapped with the per-device permit.

                Cooperative acquire (mirrors the #570 compute_inflight loop): block
                on the device's Semaphore but wake on cancel within 50 ms. The
                ``acquired`` guard means a cancel-before-acquire path takes NO
                release (releasing an un-acquired permit would silently raise the
                active-read cap). The permit is released in a ``finally`` on ANY
                read_for_record return (bytes / None / ReadFailure) — without that
                release a Semaphore that only ever decreases would deadlock on the
                first read at concurrency 1.

                Returns ``(read_result, dev, level_tag, t_end)`` where read_result is
                the ``(idx, record, data)`` tuple _compute_dispatch expects (or None
                when cancelled before acquiring). ``level_tag`` is the permit budget
                captured at acquire time — the concurrency the read ran AT — for a
                ramping device, else None.
                """
                acquired = False
                while not cancel_flag.is_set():
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
                # Capture read-completion time BEFORE the byte-budget wait so the
                # autotune ramp measures pure read throughput, not back-pressure.
                t_end = time.monotonic()
                # #587 — reader-side byte-budget backpressure. Acquiring HERE (in the
                # reader-pool worker), NOT in _compute_dispatch, is what actually
                # bounds RAM: it makes the reader pool stop producing once `budget`
                # bytes are in flight, so completed-read bytes can't pile up in
                # Future._result faster than compute drains them — the dominant #587
                # leak. Released in the compute done-callback. n=0 (video / None /
                # ReadFailure) is a no-op; on cancel acquire returns False and we
                # still return the result — the drain loop drops it on its cancel check.
                _data = read_result[2]
                byte_budget.acquire(len(_data) if isinstance(_data, bytes) else 0)
                return read_result, dev, level_tag, t_end

            def _budgeted_read(idx, r):
                """#587 — ungated reader (autotune off) with byte-budget backpressure.

                Mirrors _gated_read's post-read acquire so the reader pool can't
                outrun compute and accumulate read bytes in Future._result. The
                budget is released in the compute done-callback.
                """
                read_result = read_for_record(idx, r)
                _data = read_result[2]
                byte_budget.acquire(len(_data) if isinstance(_data, bytes) else 0)
                return read_result

            def _read_drain() -> None:
                """Drive read futures into hash_in_q; put a None sentinel when done."""
                gated = read_permits is not None
                # #587 Part A — use a set so each future can be discarded
                # (reader_futures.discard(fut)) immediately after its result is
                # consumed.  CPython never clears Future._result after .result(),
                # so keeping every future in a list for the lifetime of the scan
                # would retain ALL read bytes (up to N × DNG size) until the list
                # is freed at scan end — triggering OOM on large DNG libraries.
                # as_completed() snapshots its input at call time, so mutating the
                # set inside the loop is safe.
                reader_futures: set = set()
                for dev, items in device_records.items():
                    for _idx, _r in items:
                        if gated:
                            reader_futures.add(
                                reader_pools[dev].submit(_gated_read, dev, _idx, _r)
                            )
                        else:
                            reader_futures.add(
                                reader_pools[dev].submit(_budgeted_read, _idx, _r)
                            )
                for fut in as_completed(reader_futures):
                    if cancel_flag.is_set():
                        break
                    try:
                        if gated:
                            read_result, _dev, level_tag, t_end = fut.result()
                            # Drop the strong reference to this future NOW so
                            # CPython can GC Future._result (the read bytes)
                            # immediately rather than holding them until the
                            # set is freed at scan end.
                            reader_futures.discard(fut)
                            if read_result is None:
                                continue  # cancelled before this read acquired
                            ramp = ramps.get(_dev)
                            if ramp is not None:
                                _data = read_result[2]
                                _nbytes = len(_data) if isinstance(_data, bytes) else 0
                                ramp.record(_nbytes, t_end, level_tag=level_tag)
                                _new = ramp.advance_if_level_done()
                                if _new > _last_permits[_dev]:
                                    # Widen the cap: hand out the delta permits the
                                    # level-close raised the budget by (monotone — we
                                    # never narrow a live Semaphore, see #551 Open
                                    # risk 7).
                                    read_permits[_dev].release(_new - _last_permits[_dev])
                                    _last_permits[_dev] = _new
                                if (not ramp.is_ramping()) and _dev not in _knee_emitted:
                                    _knee_emitted.add(_dev)
                                    # Knee-acceptance gate: only a sole-ramping
                                    # device's measurement is clean enough to cache.
                                    # len(ramps)==1 is exactly "sole actively-ramping"
                                    # for the dominant topologies (single device;
                                    # HDD+NAS where the HDD never ramps). The monotone
                                    # condition is implied by the gain rule, which only
                                    # freezes after strictly-rising rungs.
                                    if len(ramps) == 1 and ramp.knee() is not None:
                                        _summary = ramp.summary()
                                        _summary["device"] = _dev
                                        _summary["sole_ramping"] = True
                                        self.read_knee_measured.emit(_summary)
                        else:
                            read_result = fut.result()
                            # Drop the strong reference so the bytes can be GC'd.
                            reader_futures.discard(fut)
                        # Cooperative bounded put: retry with a short timeout so a
                        # cancel_flag check doesn't leave us stuck forever when
                        # compute is stopped and the queue is full.
                        while not cancel_flag.is_set():
                            try:
                                hash_in_q.put(read_result, timeout=0.05)
                                break
                            except _queue.Full:
                                pass
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                hash_in_q.put(None)  # sentinel — always sent, even on cancel

            def _compute_dispatch() -> None:
                """Pull from hash_in_q, submit compute_from_bytes futures."""
                while not cancel_flag.is_set():
                    try:
                        item = hash_in_q.get(timeout=0.05)
                    except _queue.Empty:
                        continue
                    if item is None:
                        break
                    c_idx, c_record, c_data = item

                    # #587 — the byte budget is acquired in the READER worker
                    # (_gated_read / _budgeted_read), so dispatch just submits.
                    # n is the byte cost to release once this compute completes;
                    # 0 for video / None / ReadFailure (acquire was a no-op there).
                    n = len(c_data) if isinstance(c_data, bytes) else 0

                    def _cb(f, _idx=c_idx, _n=n):
                        # release() never raises (#587 invariant) so out_q.put
                        # always runs; releasing here (compute done) is what wakes
                        # a reader blocked on the byte budget.
                        byte_budget.release(_n)
                        out_q.put(f.result())

                    compute_pool.submit(
                        compute_from_bytes, c_idx, c_record, c_data
                    ).add_done_callback(_cb)

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
                        if self.isInterruptionRequested():
                            cancel_flag.set()
                            # Unblock any reader wedged in a bounded put().
                            _drain_queue_nowait(hash_in_q)
                            for p in reader_pools.values():
                                p.shutdown(wait=False, cancel_futures=True)
                            compute_pool.shutdown(
                                wait=False, cancel_futures=True
                            )
                            _kill_exif_procs()
                            # #564 — drain the bounded exif_queue so any
                            # producer wedged in a cooperative put() unblocks
                            # and can exit on the cancel_flag check (mirrors
                            # hash_in_q drain above).
                            _drain_queue_nowait(exif_queue)
                            for _ in consumer_threads:
                                exif_queue.put(None)
                            for t in consumer_threads:
                                t.join(timeout=5)
                            logger.warning(
                                "Scan cancelled by user during hashing pass"
                            )
                            self.failed.emit("Scan cancelled.")
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
                        self._emit(f"  Hashed {done:,}/{len(records):,}")
                    # #424 — per-second-throttled stage_progress emit
                    # alongside the existing per-100 log line.
                    self._emit_stage(hash_tracker, done, len(records))
            finally:
                # On cancel the flag was already set in the inner loop;
                # on normal completion we don't set it (exif consumers
                # check cancel_flag and must keep running after hashing).
                if cancelled:
                    _drain_queue_nowait(hash_in_q)
                read_drain_thread.join(timeout=5)
                compute_dispatch_thread.join(timeout=5)
                for p in reader_pools.values():
                    p.shutdown(wait=False)
                compute_pool.shutdown(wait=False)

        # Signal each consumer that no more items are coming and wait
        # for them to drain whatever's still queued. The hash threads
        # may have produced records that the consumers haven't yet
        # batched — joining ensures extracts is fully populated before
        # the classify step reads it. One sentinel per consumer.
        for _ in consumer_threads:
            exif_queue.put(None)
        for t in consumer_threads:
            t.join()

        # Remove any None slots (cancelled futures that didn't run, or skipped files)
        hash_results = [r for r in hash_results if r is not None]

        if skipped:
            self._emit(f"  Skipped {len(skipped):,} unreadable file(s):")
            for p, exc_type, exc_msg in skipped[:10]:
                self._emit(f"    {p}  [{exc_type}: {exc_msg}]")
            if len(skipped) > 10:
                self._emit(f"    … and {len(skipped) - 10:,} more")

        # --- 3 (continued). EXIF post-processing ---
        # The consumer thread already populated ``extracts``. Now finalise
        # the stage: surface the missing-exiftool warning if it fired,
        # emit summary stats, and backfill exif_date onto records.
        et_records = [r for r in hash_results if r.record.file_type != "skip"]
        if exiftool_missing[0]:
            self._emit(
                "WARNING: exiftool not found on PATH — EXIF dates for HEIC/RAW/video"
                " and scoring signals (GPS, EXIF census, XMP provenance) unavailable.\n"
                "Install from https://exiftool.org/ and add to PATH."
            )
        elif et_records:
            # Force a final exif emit so the bar settles at 100% even if
            # the last batch finished within the 1Hz throttle window.
            self._emit_stage(exif_tracker, exif_done[0], exif_total[0], force=True)
            found_dates = sum(1 for e in extracts.values() if e.exif_date is not None)
            with_gps = sum(1 for e in extracts.values() if e.gps_present)
            self._emit(
                f"  EXIF done — {len(extracts):,} files,"
                f" {found_dates:,} dates, {with_gps:,} with GPS"
            )
            # Backfill exif_date for records where PIL didn't find one.
            for r in et_records:
                if r.exif_date is None:
                    extract = extracts.get(r.record.path)
                    if extract is not None:
                        r.exif_date = extract.exif_date

        # --- 4. Classify ---
        # #424: classify() is opaque from the worker's view (single
        # call into scanner/dedup.py). Surface start + end emits with
        # total=0 so the receiver renders the bar as indeterminate
        # ("CLASSIFY — working…") instead of stuck at 0%. Pattern
        # repeats for SCORE and WRITE below.
        # #463 — opaque stages (CLASSIFY/SCORE/AUTO-SELECT/WRITE) each
        # check isInterruptionRequested() at entry so a user-cancel
        # during the final 10-15s of a scan actually stops the pipeline
        # before write_manifest overwrites the output path. Mirrors the
        # HASH-loop cancel pattern above.
        if self.isInterruptionRequested():
            logger.warning("Scan cancelled by user before classify pass")
            self.failed.emit("Scan cancelled.")
            return
        self._emit("Classifying…")
        classify_tracker = _StageTracker(STAGE_CLASSIFY)
        self._emit_stage(classify_tracker, 0, 0, force=True)
        rows = classify(
            hash_results,
            threshold=self.threshold,
            mean_color_threshold=self.mean_color_threshold,
            dhash_threshold=self.dhash_threshold,
            source_priority=self.source_priority,
            # #526 — per-machine BK-tree crossover from the #486 calibration
            # (None on thread/process scans → classify uses the module default).
            bktree_min_candidates=self._calibrated_bktree_floor,
        )
        self._emit_stage(classify_tracker, 1, 1, force=True)

        # --- 4.5: score within each duplicate group (#187) ---
        # Mutates rows in place: copies exif_tag_count / gps_present /
        # xmp_derived from extracts into ManifestRow, then assigns
        # compute_score(...) per group. Isolated rows (group_id is None)
        # stay unscored — no peers to compete with.
        if self.isInterruptionRequested():
            logger.warning("Scan cancelled by user before scoring pass")
            self.failed.emit("Scan cancelled.")
            return
        score_tracker = _StageTracker(STAGE_SCORE)
        self._emit_stage(score_tracker, 0, 0, force=True)
        apply_scoring_to_rows(rows, extracts)
        self._emit_stage(score_tracker, 1, 1, force=True)

        # --- 4.6: optional auto-select keepers (#212, #393) ---
        # When enabled in the scan dialog, the top-scored row in each
        # duplicate group is promoted to action="KEEP" so the manifest
        # loads with keepers already chosen — the user does not have
        # to open the Selection dialog manually. Other duplicates keep
        # their classifier action (REVIEW_DUPLICATE / EXACT / "" undecided) so
        # the user still confirms deletions explicitly.
        #
        # #393 layered on top: keepers also receive user_decision=""
        # (canonical "keep" state — empty string, NOT the literal "keep")
        # AND is_locked=1 (written post-write_manifest via the
        # repo's batch_update_* methods, since ManifestRow has neither
        # field — those live on the DB and PhotoRecord). The lock gives
        # a visible tree badge; user_decision="" composes with #182
        # LockedRowsConfirmDialog if the user later applies bulk-regex.
        # #425 — Previously this wrote the literal "keep" string, which
        # then leaked into the tree's Action column as raw "keep" text
        # instead of an empty cell. Canonical convention everywhere else
        # in the codebase (settable_decisions, set_decision via right-
        # click) is empty string for keep; auto-select now matches.
        #
        # #393 (c) — optional aggressive mode: every non-keeper row in
        # a scored group gets user_decision='delete'. Off by default
        # (destructive-leaning); the user still confirms via the
        # standard ExecuteAction flow before any file moves.
        if self.isInterruptionRequested():
            logger.warning("Scan cancelled by user before auto-select pass")
            self.failed.emit("Scan cancelled.")
            return
        keepers: set[str] = set()
        if self.auto_select_enabled:
            from core.services.auto_select import top_score_path_per_group
            keepers = top_score_path_per_group(rows)
            if keepers:
                for row in rows:
                    if row.source_path in keepers:
                        row.action = "KEEP"
                self._emit(f"Auto-select: marked {len(keepers):,} keeper(s) per group.")

        # Capture print_summary output and re-emit as progress
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_summary(rows, skipped=len(skipped))
        for line in buf.getvalue().splitlines():
            self._emit(line)

        # --- 5. Write manifest ---
        # #463 — refuse to write the manifest on cancel; write_manifest
        # overwrites whatever sits at output_path so a late cancel would
        # otherwise destroy the previous scan's manifest with a partial.
        if self.isInterruptionRequested():
            logger.warning("Scan cancelled by user before manifest write")
            self.failed.emit("Scan cancelled.")
            return
        self._emit(f"Writing manifest → {self.output_path}")
        write_tracker = _StageTracker(STAGE_WRITE)
        self._emit_stage(write_tracker, 0, 0, force=True)
        write_manifest(rows, self.output_path)
        self._emit_stage(write_tracker, 1, 1, force=True)

        # --- 5.5: post-write keep+lock (and aggressive delete) (#393) ---
        # Runs only when auto_select_enabled fired and produced keepers.
        # The helper composes the repo's batch_update_* primitives — see
        # core/services/auto_select.py::apply_auto_select_decisions for
        # the write contract. Both writes are tiny (≤N rows per scan)
        # so the cost is negligible compared to the scan itself.
        if keepers:
            from core.services.auto_select import apply_auto_select_decisions
            non_keepers: set[str] | None = None
            if self.auto_select_aggressive_delete:
                # Non-keepers in scored groups: ranked peers (group_id AND
                # score) that aren't the keeper. ``score=None`` peers (Live
                # Photo MOV passengers, all-MOV groups) are excluded, and
                # #517 excludes low-confidence (pHash-only) near-dups so a
                # shaky match is never auto-deleted. See the helper for the
                # full contract.
                from core.services.auto_select import (
                    non_keepers_for_aggressive_delete,
                )
                non_keepers = non_keepers_for_aggressive_delete(rows, keepers)
                self._emit(
                    f"Auto-select aggressive: marked {len(non_keepers):,}"
                    f" non-keeper(s) for delete."
                )
            apply_auto_select_decisions(
                str(self.output_path), keepers, non_keepers
            )
            self._emit(
                f"Auto-select: locked {len(keepers):,} keeper(s);"
                f" decisions written."
            )

        self._emit("Done.")
        self.finished.emit(str(self.output_path))
