"""Byte-budget semaphore for the HASH-stage compute dispatch (#587 OOM fix).

Pure logic — no Qt, no I/O. The pipeline wiring lives in
``app/views/workers/scan_worker.py``; this module only holds the byte-budget
math so it is fully unit-testable at layer 1.

The problem it solves (#587):

* The compute pool's ``ThreadPoolExecutor.submit()`` never blocks.  Without a
  byte budget, every DNG read (~30-130 MB) is submitted and sits in the pool's
  internal work queue until a worker picks it up.  A 10 000-file DNG library
  submits ~10 000 × 130 MB = 1.3 TB of retained bytes before any compute
  worker drains them.  OOM.

* The existing ``compute_inflight = threading.Semaphore(_HASH_QUEUE_MAXSIZE)``
  (PR #570) caps count (128 tasks), but 128 × 130 MB is still 16 GB.  Count
  alone cannot bound the byte footprint on a heterogeneous library.

``ByteBudget`` replaces the count semaphore with a cooperative byte-budget
gate: a task may only be submitted to the compute pool once its bytes fit
under the budget ceiling (or it is the sole in-flight task — the
"admit-one-over-budget" rule prevents a file larger than the whole budget
from deadlocking).  The budget is released in the compute done-callback so
the next waiting dispatch can proceed.
"""

from __future__ import annotations

import os
import threading
from typing import Callable


class ByteBudget:
    """Cooperative byte-budget gate for bounded in-flight compute tasks.

    Usage pattern (replaces the #570 compute_inflight Semaphore):

    1. In the READER worker, after a read completes and ``n_bytes`` is known,
       call ``acquire(n_bytes)``.  It blocks until the budget has room (or the
       scan is cancelled), back-pressuring the reader pool so completed-read
       bytes can't accumulate faster than compute drains them.
    2. In the compute done-callback, call ``release(n_bytes)`` — symmetric,
       never raises.

    Thread-safety: all state is guarded by a single ``threading.Condition``
    so ``acquire`` and ``release`` are safe to call from any thread.
    """

    def __init__(self, budget_bytes: int, cancel_check: Callable[[], bool]) -> None:
        """Initialise a ByteBudget.

        Args:
            budget_bytes: Maximum bytes allowed in flight simultaneously.
                          Must be > 0.  The "admit-one-over-budget" rule
                          allows a single file larger than the budget to be
                          admitted (without deadlocking) as long as nothing
                          else is in flight.
            cancel_check: Zero-argument callable returning True when the scan
                          is cancelling.  Passed as ``cancel_flag.is_set`` by
                          the wiring in scan_worker.py.  Called on each
                          wake-cycle inside ``acquire`` so the dispatch thread
                          can exit promptly without a long timeout sleep.
        """
        self._budget = budget_bytes
        self._cancel_check = cancel_check
        self._lock = threading.Condition(threading.Lock())
        self._inflight = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, n_bytes: int, timeout: float = 0.05) -> bool:
        """Attempt to reserve ``n_bytes`` of budget.

        Returns True when admitted, False if the scan was cancelled before
        admission.

        Special cases:
        * ``n_bytes <= 0``: no accounting (video / None / ReadFailure payload
          that has no byte cost) — return True immediately.
        * Single over-budget file: if ``n_bytes > self._budget``, wait only
          until ``_inflight == 0`` so the file can never deadlock.  It is
          admitted and its size is tracked so ``release`` is symmetric.
        * Normal: cooperative loop — wait until ``_inflight + n_bytes <=
          budget`` or cancelled.
        """
        if n_bytes <= 0:
            return True

        over_budget = n_bytes > self._budget

        with self._lock:
            while True:
                if self._cancel_check():
                    return False
                if over_budget:
                    # Admit alone: wait until nothing else is in flight.
                    if self._inflight == 0:
                        self._inflight += n_bytes
                        return True
                else:
                    if self._inflight + n_bytes <= self._budget:
                        self._inflight += n_bytes
                        return True
                # Wait for a release() notification or timeout (to re-check
                # cancel).  timeout is short so cancel is responsive.
                self._lock.wait(timeout)

    def release(self, n_bytes: int) -> None:
        """Release ``n_bytes`` of budget held by a completed compute task.

        Symmetric to ``acquire``.  ``n_bytes <= 0``: no-op.  Never raises
        so a raise in the compute done-callback can't skip ``out_q.put`` and
        hang the parent drain loop.
        """
        if n_bytes <= 0:
            return
        try:
            with self._lock:
                self._inflight = max(0, self._inflight - n_bytes)
                self._lock.notify_all()
        except Exception:  # pylint: disable=broad-exception-caught
            # Belt-and-suspenders: release must never propagate an exception
            # out of a done-callback.  The only realistic failure is a
            # recursive acquire on a broken Condition, which can't happen here.
            pass


# ------------------------------------------------------------------
# Default budget sizing
# ------------------------------------------------------------------


def default_budget_bytes() -> int:
    """Return a byte budget sized from the machine's total physical RAM.

    Formula: ``max(256 MiB, min(2 GiB, total_ram // 2))``.

    * Floor 256 MiB: safe even on a 512 MiB VM; fits ~2 DNG files in flight.
    * Cap 2 GiB: prevents a 64 GiB workstation from holding 32 GB of raw
      bytes in the compute queue, leaving nothing for the OS and the app.
    * Half-RAM: leaves the other half for the OS, the Qt UI, and the
      compute workers' decoded image buffers.

    The Windows probe uses ``GlobalMemoryStatusEx`` (same ctypes style as
    ``scanner/workers.py``).  The POSIX probe uses ``os.sysconf``.  Both
    paths fall open to the 4 GiB default on any failure — the budget is a
    soft ceiling, not a correctness invariant.
    """
    _256_MIB = 256 * 1024 * 1024
    _2_GIB = 2 * 1024 ** 3
    # Probe-failure fallback. Kept at 1 GiB (not the 2 GiB cap) so a RAM probe
    # that fails on a low-RAM box can't hand out a budget larger than the box —
    # 1 GiB is safe on a 2 GiB machine and still admits ~7 130 MB DNGs in flight.
    _FALLBACK = 1 * 1024 ** 3

    total_ram = _probe_total_ram()
    if total_ram is None:
        return _FALLBACK
    return max(_256_MIB, min(_2_GIB, total_ram // 2))


def per_device_budgets(
    total_bytes: int,
    device_keys: list[str],
    cancel_check: Callable[[], bool],
) -> dict[str, ByteBudget]:
    """Split ``total_bytes`` into one :class:`ByteBudget` per device (#596).

    A single GLOBAL ByteBudget shared across devices lets a slow, large-file
    device (e.g. an HDD full of 100-130 MB ProRAW DNGs) consume the whole ceiling
    and starve a fast device's reader at ``acquire`` — even though that device's
    own files are small and its link is idle. Giving each device its own slice
    isolates them so one device's in-flight bytes can't block another's reader.

    The split is EQUAL, which preserves #587's OOM bound: the sum of the
    per-device budgets never exceeds ``total_bytes`` (``n * (total // n) <=
    total``). A single device therefore keeps the full budget — byte-identical to
    the pre-#596 global behaviour. Each per-device ByteBudget keeps the
    admit-one-over-budget rule, so a file larger than its slice is still admitted
    alone rather than deadlocking.

    Args:
        total_bytes: the global ceiling (e.g. from ``default_budget_bytes``).
        device_keys: the active HASH-stage per-device bucket keys.
        cancel_check: forwarded to each ByteBudget (``cancel_flag.is_set``).

    Returns:
        ``{device_key: ByteBudget}`` — one bounded budget per device.
    """
    n = max(1, len(device_keys))
    per_device = max(1, total_bytes // n)
    return {key: ByteBudget(per_device, cancel_check) for key in device_keys}


def _probe_total_ram() -> int | None:
    """Return total physical RAM in bytes, or None on any failure.

    The real-OS code paths are excluded from unit-test coverage because
    ``ctypes.windll`` is unavailable on Linux CI and ``os.sysconf`` values
    are machine-specific; both paths are exercised by the unit test via
    monkeypatch of this function, and by Windows local runs.
    """
    import sys

    if sys.platform == "win32":
        return _probe_total_ram_windows()
    return _probe_total_ram_posix()


def _probe_total_ram_windows() -> int | None:  # pragma: no cover
    """Windows: GlobalMemoryStatusEx → ullTotalPhys.

    Excluded from coverage — ctypes.windll not available on Linux CI;
    exercised by monkeypatching _probe_total_ram in unit tests and by
    Windows local runs.
    """
    try:
        import ctypes
        import ctypes.wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.wintypes.DWORD),
                ("dwMemoryLoad", ctypes.wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys)
    except Exception:
        return None


def _probe_total_ram_posix() -> int | None:  # pragma: no cover
    """POSIX: sysconf SC_PAGE_SIZE × SC_PHYS_PAGES.

    Excluded from coverage — sysconf values are machine-specific and the
    CI Linux runner's reported RAM is a container ceiling; exercised by
    monkeypatching _probe_total_ram in unit tests and by POSIX local runs.
    """
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        if page > 0 and pages > 0:
            return page * pages
        return None
    except Exception:
        return None
