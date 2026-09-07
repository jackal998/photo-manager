"""ScanRegistry — in-process map of task_id → ScanTask with TTL reaping."""

from __future__ import annotations

import threading
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.web.models import ScanTask

# Terminal tasks are dropped from the registry after this many seconds so
# SSE clients that never reconnect don't hold references indefinitely.
_TASK_TTL_SECONDS = 300
_REAP_INTERVAL_SECONDS = 60


class ScanRegistry:
    """Thread-safe map of running and recently completed scan tasks.

    Policy: **reject-if-running** — create() raises ValueError (→ 409) if any
    task currently has status=='running'.  This mirrors the Qt app's
    one-scan-at-a-time constraint; it is the right default for a localhost
    single-user deployment.

    The multiprocessing.Process upgrade path (Phase 2+) would relax this by
    making the bus serialisable, enabling concurrent scans.  For now, the
    threading.Thread implementation keeps the SSE bus in-process.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, "ScanTask"] = {}
        self._lock = threading.Lock()
        self._reaper_thread = threading.Thread(
            target=self._reap_loop,
            name="scan-registry-reaper",
            daemon=True,
        )
        self._reaper_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self, task: "ScanTask") -> None:
        """Register ``task``.

        Raises ``ValueError`` if any task is currently running (→ 409).
        """
        with self._lock:
            for existing in self._tasks.values():
                if existing.status == "running":
                    raise ValueError(
                        f"A scan is already running (task_id={existing.task_id})"
                    )
            self._tasks[task.task_id] = task

    def get(self, task_id: str) -> "ScanTask | None":
        """Return the task or None if not found."""
        with self._lock:
            return self._tasks.get(task_id)

    def running_count(self) -> int:
        """Number of tasks currently in 'running' state."""
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == "running")

    @staticmethod
    def new_task_id() -> str:
        return str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Background reaper
    # ------------------------------------------------------------------

    def _reap_loop(self) -> None:
        """Drop terminal tasks older than _TASK_TTL_SECONDS every minute."""
        while True:
            time.sleep(_REAP_INTERVAL_SECONDS)
            self._reap_once()

    def _reap_once(self) -> None:
        cutoff = time.monotonic() - _TASK_TTL_SECONDS
        with self._lock:
            to_drop = [
                tid
                for tid, task in self._tasks.items()
                if task.status in ("finished", "failed", "cancelled")
                and getattr(task, "_terminal_at", float("inf")) < cutoff
            ]
            for tid in to_drop:
                del self._tasks[tid]


# Module-level singleton so all routes share one registry.
registry = ScanRegistry()
