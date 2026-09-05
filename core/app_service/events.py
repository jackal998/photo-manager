"""ScanProgressBus — Qt-free protocol for scan pipeline event dispatch.

The scan pipeline (scan_runner.run_pipeline) emits all progress and
lifecycle events through this interface so the pipeline code has zero
Qt dependency.  ScanWorker provides a concrete implementation that
forwards each call to the matching Qt signal; a future web-API layer
can implement the same interface over SSE without touching the pipeline.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScanProgressBus(Protocol):
    """Event bus the scan pipeline dispatches into.

    All methods are fire-and-forget from the pipeline's perspective —
    the implementation decides whether to queue (Qt signal), stream
    (SSE), or buffer (test spy).
    """

    def log(self, msg: str) -> None:
        """One-line status text for the progress log (replaces ``progress`` signal)."""
        ...

    def stage(
        self,
        stage_name: str,
        completed: int,
        total: int,
        files_per_sec: float,
    ) -> None:
        """Typed per-stage progress (replaces ``stage_progress`` signal).

        ``total == 0`` marks an atomic / indeterminate stage.
        ``files_per_sec == 0`` means insufficient samples — ETA hides.
        """
        ...

    def failed(self, msg: str) -> None:
        """Pipeline error or clean cancel (replaces ``failed`` signal).

        The string ``"Scan cancelled."`` is the canonical clean-cancel
        payload; scan_dialog distinguishes it from red-modal errors.
        """
        ...

    def finished(self, output_path: str) -> None:
        """Successful completion — manifest written (replaces ``finished`` signal)."""
        ...

    def completed_empty(self) -> None:
        """Scan ran cleanly but found zero media files (replaces ``completed_empty``)."""
        ...

    def hash_pool_measured(self, rates: dict) -> None:
        """Fresh hash-pool calibration rates (replaces ``hash_pool_measured`` signal).

        Payload keys: ``thread_per_file``, ``process_per_file``, ``spawn``,
        ``group_per_pair``, ``group_bk_per_candidate``.  The dialog caches
        this under a machine+sources fingerprint so re-scans skip
        re-measurement.
        """
        ...

    def read_knee_measured(self, summary: dict) -> None:
        """Per-device read-knee ramp result (replaces ``read_knee_measured`` signal).

        Payload: ``ReadKneeRamp.summary()`` augmented with ``device`` and
        ``sole_ramping``.  The dialog persists it via
        ``scanner.autotune.store_read_knee`` keyed by ``device_key``.
        """
        ...
