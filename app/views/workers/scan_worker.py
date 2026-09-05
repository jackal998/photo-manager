"""Background QThread that runs the deduplication scan pipeline."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from loguru import logger

from core.app_service.cancel_token import _CancelToken

# ---------------------------------------------------------------------------
# Re-export only what external callers consume from this module path.
# Canonical definitions live in core.app_service.scan_runner.
#
# Consumers:
#   scan_dialog.py               → hash_pool_fingerprint, store_hash_pool_rates
#   test_scan_worker_progress.py → _StageTracker, _THROUGHPUT_WINDOW_SECONDS,
#                                   _STAGE_EMIT_INTERVAL_SECONDS, STAGE_HASH
#   test_scan_worker.py          → hash_pool_fingerprint, store_hash_pool_rates,
#                                   _valid_hash_pool_rates, _derive_bktree_floor,
#                                   _stratified_sample, _GROUP_FLOOR_MIN/MAX
# ---------------------------------------------------------------------------
from core.app_service.scan_runner import (  # noqa: F401  (re-exports)
    STAGE_HASH,
    _THROUGHPUT_WINDOW_SECONDS,
    _STAGE_EMIT_INTERVAL_SECONDS,
    _StageTracker,
    _GROUP_FLOOR_MIN,
    _GROUP_FLOOR_MAX,
    _derive_bktree_floor,
    _stratified_sample,
    _valid_hash_pool_rates,
    hash_pool_fingerprint,
    store_hash_pool_rates,
)


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
        # Single cooperative cancellation token — the sole signal that flows
        # through the pipeline.  Replaces the dual isInterruptionRequested()/
        # cancel_flag scheme: requestInterruption() writes to it,
        # isInterruptionRequested() reads from it, and all intra-pipeline
        # cancel checks use it directly.
        self._cancel_token = _CancelToken()

    def requestInterruption(self) -> None:
        """Set the cooperative _cancel_token in addition to the Qt flag.

        This is the ONLY entry point for cancellation: ScanDialog.closeEvent
        and the main-window guard both call worker.requestInterruption(), so
        a single override here is sufficient to propagate the signal.
        """
        super().requestInterruption()
        self._cancel_token.request()

    def isInterruptionRequested(self) -> bool:
        """Delegate to _cancel_token so the single flag is the source of truth.

        Keeping this method means existing call sites (self.isInterruptionRequested())
        and test monkeypatches both continue to work without change.
        """
        return self._cancel_token.is_set()

    def run(self) -> None:
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        config = ScanConfig(
            sources=self.sources,
            output_path=self.output_path,
            recursive_map=self.recursive_map,
            source_priority=self.source_priority,
            threshold=self.threshold,
            mean_color_threshold=self.mean_color_threshold,
            dhash_threshold=self.dhash_threshold,
            limit=self.limit,
            workers=self.workers,
            exif_workers=self.exif_workers,
            hash_pool=self.hash_pool,
            hash_pool_rates=self.hash_pool_rates,
            auto_select_enabled=self.auto_select_enabled,
            auto_select_aggressive_delete=self.auto_select_aggressive_delete,
            autotune_read_knee=self._autotune_read_knee,
            autotune_knees=self.autotune_knees,
        )
        bus = _QtBus(self)
        try:
            run_pipeline(config, self._cancel_token, bus)
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


class _QtBus:
    """Qt-signal implementation of ScanProgressBus.

    Wraps the ScanWorker's signals so ``run_pipeline()`` (Qt-free) can
    dispatch events without importing PySide6 itself.  One instance is
    created per ``ScanWorker.run()`` call and discarded afterwards.
    """

    def __init__(self, worker: "ScanWorker") -> None:
        self._worker = worker

    def log(self, msg: str) -> None:
        self._worker._emit(msg)

    def stage(
        self,
        stage_name: str,
        completed: int,
        total: int,
        files_per_sec: float,
    ) -> None:
        self._worker.stage_progress.emit(stage_name, completed, total, files_per_sec)

    def failed(self, msg: str) -> None:
        self._worker.failed.emit(msg)

    def finished(self, output_path: str) -> None:
        self._worker.finished.emit(output_path)

    def completed_empty(self) -> None:
        self._worker.completed_empty.emit()

    def hash_pool_measured(self, rates: dict) -> None:
        self._worker.hash_pool_measured.emit(rates)

    def read_knee_measured(self, summary: dict) -> None:
        self._worker.read_knee_measured.emit(summary)
