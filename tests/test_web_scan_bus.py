"""Unit tests for SseScanBus — the thread→asyncio bridge.

Tests here verify the REAL-BUG scenarios:
- Buffer + subscriber delivery on bg thread
- Deque-iteration race under concurrent load (the snapshot guard)
- Terminal event delivery and status transitions
- cancel_token.request() → status='cancelled' path
- hash_pool_measured → store_hash_pool_rates persistence
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from unittest.mock import MagicMock, call, patch

import pytest

from app.web.models import ScanTask
from app.web.routes.scan import SseScanBus
from core.app_service.cancel_token import _CancelToken


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(cancel_token: _CancelToken | None = None) -> ScanTask:
    return ScanTask(
        task_id="test-task-1",
        status="running",
        cancel_token=cancel_token or _CancelToken(),
    )


def _make_bus_with_loop(task: ScanTask) -> tuple[SseScanBus, asyncio.AbstractEventLoop]:
    """Create a real asyncio event loop + SseScanBus for threading tests."""
    loop = asyncio.new_event_loop()
    task.loop = loop
    bus = SseScanBus(task, loop)
    return bus, loop


# ---------------------------------------------------------------------------
# 1. log() on a background thread appends to buffer and reaches subscriber
# ---------------------------------------------------------------------------

class TestBusLog:
    def test_log_appends_to_buffer(self):
        task = _make_task()
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        bus.log("hello world")

        with task.buffer_lock:
            events = list(task.event_buffer)
        assert len(events) == 1
        event_id, name, payload = events[0]
        assert name == "log"
        assert payload == {"msg": "hello world"}
        assert event_id == 1

    def test_log_from_bg_thread_reaches_subscriber_queue(self):
        task = _make_task()
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)

        q: asyncio.Queue = asyncio.Queue()
        with task.subscriber_lock:
            task.subscriber_queues.append(q)

        received: list = []

        def _run_loop():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(asyncio.sleep(0.2))

        loop_thread = threading.Thread(target=_run_loop, daemon=True)
        loop_thread.start()

        # Fire from a different thread — the real use case.
        def _bg():
            bus.log("from background thread")

        bg = threading.Thread(target=_bg)
        bg.start()
        bg.join(timeout=1)

        loop_thread.join(timeout=0.5)

        # Drain synchronously from the queue — the loop has drained the
        # call_soon_threadsafe callbacks in its 0.2s window.
        async def _drain():
            item = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(item)

        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_drain())
        finally:
            loop.close()

        assert len(received) == 1
        event_id, name, payload = received[0]
        assert name == "log"
        assert payload["msg"] == "from background thread"


# ---------------------------------------------------------------------------
# 2. STRESS: 200 writer threads + 50 snapshot threads → no RuntimeError
# ---------------------------------------------------------------------------

class TestBusStressConcurrency:
    def test_concurrent_write_and_snapshot_no_runtime_error(self):
        """Real-bug guard: the deque must be snapshotted (list()) before iterating.

        Without the snapshot, iterating the live deque while another thread
        appends raises RuntimeError('deque mutated during iteration').

        200 threads write; 50 threads snapshot; all must complete without
        RuntimeError.  Snapshot length must be ≤ 200 (maxlen=500 so nothing
        is dropped but we wrote at most 200 events).
        """
        task = ScanTask(
            task_id="stress",
            status="running",
            cancel_token=_CancelToken(),
            event_buffer=deque(maxlen=500),
        )
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        errors: list[Exception] = []
        snapshot_lengths: list[int] = []
        write_done = threading.Event()

        def _write(i: int) -> None:
            try:
                bus.log(f"msg-{i}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(exc)

        def _snapshot() -> None:
            try:
                write_done.wait(timeout=2.0)
                # The snapshot must be taken under buffer_lock.
                with task.buffer_lock:
                    snap = list(task.event_buffer)
                snapshot_lengths.append(len(snap))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(exc)

        writers = [threading.Thread(target=_write, args=(i,)) for i in range(200)]
        snappers = [threading.Thread(target=_snapshot) for _ in range(50)]

        for t in writers + snappers:
            t.start()

        for t in writers:
            t.join(timeout=5)
        write_done.set()

        for t in snappers:
            t.join(timeout=5)

        assert errors == [], f"Errors during concurrent access: {errors}"
        assert all(l <= 200 for l in snapshot_lengths), snapshot_lengths


# ---------------------------------------------------------------------------
# 3. finished() → status='finished', terminal_event set, sentinel on queues
# ---------------------------------------------------------------------------

class TestBusFinished:
    def test_finished_sets_status_and_terminal_event(self):
        task = _make_task()
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        bus.finished("/out/scan.sqlite")

        assert task.status == "finished"
        assert task.terminal_event is not None
        assert task.terminal_event["name"] == "finished"
        assert task.terminal_event["payload"]["output_path"] == "/out/scan.sqlite"

    def test_finished_sends_sentinel_to_subscribers(self):
        task = _make_task()
        loop = asyncio.new_event_loop()

        delivered: list = []
        q: asyncio.Queue = asyncio.Queue()
        with task.subscriber_lock:
            task.subscriber_queues.append(q)

        bus = SseScanBus(task, loop)

        def _run():
            asyncio.set_event_loop(loop)

            async def _collect():
                # Expect: terminal event tuple, then None sentinel.
                item1 = await asyncio.wait_for(q.get(), timeout=1.0)
                delivered.append(item1)
                item2 = await asyncio.wait_for(q.get(), timeout=1.0)
                delivered.append(item2)

            loop.run_until_complete(_collect())

        loop_thread = threading.Thread(target=_run, daemon=True)
        loop_thread.start()

        # Give loop a moment to start.
        import time; time.sleep(0.05)

        bus.finished("/out.sqlite")
        loop_thread.join(timeout=2)
        loop.close()

        assert len(delivered) == 2
        # First item is the terminal event tuple.
        event_id, name, payload = delivered[0]
        assert name == "finished"
        # Second item is the sentinel.
        assert delivered[1] is None


# ---------------------------------------------------------------------------
# 4. cancel_token.request() then bus.failed() → status='cancelled'
# ---------------------------------------------------------------------------

class TestBusCancelledStatus:
    def test_failed_after_cancel_sets_cancelled_status(self):
        token = _CancelToken()
        task = _make_task(cancel_token=token)
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        token.request()  # signal cancel before pipeline calls bus.failed
        bus.failed("Scan cancelled.")

        assert task.status == "cancelled"
        assert task.terminal_event["name"] == "failed"
        assert task.terminal_event["payload"]["msg"] == "Scan cancelled."

    def test_failed_without_cancel_sets_failed_status(self):
        task = _make_task()
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        bus.failed("disk I/O error")

        assert task.status == "failed"


# ---------------------------------------------------------------------------
# 5. hash_pool_measured → store_hash_pool_rates called on mock settings
# ---------------------------------------------------------------------------

class TestBusHashPoolMeasured:
    def test_hash_pool_measured_calls_store_on_settings(self, tmp_path):
        """hash_pool_measured must emit the event AND persist to settings."""
        from core.app_service.dtos import ScanConfig
        from pathlib import Path

        token = _CancelToken()
        task = _make_task(cancel_token=token)
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        # Attach a mock config so hash_pool_fingerprint can run.
        task._config = ScanConfig(  # type: ignore[attr-defined]
            sources={"D": Path("/fake/d")},
            output_path=Path("/fake/out.sqlite"),
        )

        rates = {
            "thread_per_file": 0.001,
            "process_per_file": 0.0005,
            "spawn": 0.1,
            "group_per_pair": 0.0001,
            "group_bk_per_candidate": 0.0002,
        }

        mock_settings = MagicMock()
        mock_settings.get.return_value = {}

        with (
            patch("app.web.routes.scan._load_settings", return_value=mock_settings),
        ):
            bus.hash_pool_measured(rates)

        # Event must be in the buffer.
        with task.buffer_lock:
            events = list(task.event_buffer)
        assert any(name == "hash_pool_measured" for _, name, _ in events)

        # store_hash_pool_rates must have persisted via settings.
        mock_settings.set.assert_called_once()
        mock_settings.save.assert_called_once()


# ---------------------------------------------------------------------------
# FIX 4 regression guard: failed() classifies by message, not cancel_token
# ---------------------------------------------------------------------------

class TestBusFailedClassification:
    def test_genuine_error_with_cancel_set_stays_failed(self):
        """A pipeline error whose message is NOT 'Scan cancelled.' must set
        status='failed' even when the cancel_token is already set — the old
        cancel_token.is_set() check would have mislabelled it 'cancelled'.
        """
        token = _CancelToken()
        task = _make_task(cancel_token=token)
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        token.request()  # cancel flag is set — but the error is NOT a clean cancel
        bus.failed("disk I/O error")  # genuine error, not "Scan cancelled."

        assert task.status == "failed", (
            "A non-cancel error message must produce status='failed', "
            "not 'cancelled', even when the token is set"
        )

    def test_scan_cancelled_message_without_token_gives_cancelled(self):
        """The canonical 'Scan cancelled.' message always yields 'cancelled',
        regardless of token state — matches scan_dialog convention.
        """
        task = _make_task()  # token NOT set
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        bus.failed("Scan cancelled.")

        assert task.status == "cancelled"


# ---------------------------------------------------------------------------
# FIX 5: reaper test — must FAIL pre-fix, PASS post-fix
#
# Pre-fix bug: getattr(task, '_terminal_at', time.monotonic()) always returns
# 'now', so the comparison `now < cutoff` is False → tasks never reaped.
# Post-fix: ScanTask._terminal_at is set by _set_terminal; reaper uses
# float('inf') as the default so un-terminated tasks are never reaped.
# ---------------------------------------------------------------------------

class TestRegistryReaper:
    def test_reaper_removes_terminal_task_past_ttl(self):
        """_reap_once must drop a terminal task whose _terminal_at is past TTL.

        Proof-of-fix: with the old default=time.monotonic() the comparison
        `time.monotonic() < cutoff` (where cutoff = now - 300) was always False
        and the task was never removed.  With the new default=float('inf') and
        the real _terminal_at stamp the comparison is correct.
        """
        from app.web.registry import ScanRegistry, _TASK_TTL_SECONDS
        from app.web.models import ScanTask
        from core.app_service.cancel_token import _CancelToken
        import time

        reg = ScanRegistry()  # fresh registry (not the module singleton)

        token = _CancelToken()
        task = ScanTask(
            task_id="reap-me",
            status="finished",
            cancel_token=token,
        )
        # Stamp terminal time 400s in the past — well past the 300s TTL.
        task._terminal_at = time.monotonic() - (_TASK_TTL_SECONDS + 100)

        # Bypass create() (which rejects non-running tasks) and inject directly.
        with reg._lock:
            reg._tasks["reap-me"] = task

        reg._reap_once()

        with reg._lock:
            remaining = list(reg._tasks.keys())
        assert "reap-me" not in remaining, (
            "Task with _terminal_at past TTL must be removed by _reap_once"
        )

    def test_reaper_does_not_remove_running_task(self):
        """Running tasks must never be removed regardless of age."""
        from app.web.registry import ScanRegistry
        from app.web.models import ScanTask
        from core.app_service.cancel_token import _CancelToken
        import time

        reg = ScanRegistry()
        task = ScanTask(
            task_id="keep-me",
            status="running",
            cancel_token=_CancelToken(),
        )
        # No _terminal_at set → float('inf') default → never reaped.
        with reg._lock:
            reg._tasks["keep-me"] = task

        reg._reap_once()

        with reg._lock:
            remaining = list(reg._tasks.keys())
        assert "keep-me" in remaining

    def test_reaper_does_not_remove_terminal_task_within_ttl(self):
        """A recently-finished task (< 300s) must survive the reap."""
        from app.web.registry import ScanRegistry
        from app.web.models import ScanTask
        from core.app_service.cancel_token import _CancelToken
        import time

        reg = ScanRegistry()
        task = ScanTask(
            task_id="keep-recent",
            status="finished",
            cancel_token=_CancelToken(),
        )
        task._terminal_at = time.monotonic() - 10  # only 10s ago
        with reg._lock:
            reg._tasks["keep-recent"] = task

        reg._reap_once()

        with reg._lock:
            remaining = list(reg._tasks.keys())
        assert "keep-recent" in remaining


# ---------------------------------------------------------------------------
# FIX 6: duplicate-delivery interleave test — must FAIL pre-fix, PASS post-fix
#
# The SEED-1 race window: _append_and_fanout appends to the buffer, then
# in a separate lock section fans out to subscriber queues.  The SSE generator
# registers its queue, then in a separate section snapshots the buffer.
# If a bus.log() call lands BETWEEN those two generator sections, the event
# appears in BOTH buffer_snapshot (→ yielded by replay) AND the live queue
# (→ yielded again by the while-loop) — a duplicate.
#
# FIX: after replay compute max_replayed_id; in the live loop skip any item
# whose event_id ≤ max_replayed_id.  This makes delivery exactly-once.
# ---------------------------------------------------------------------------

class TestSseDuplicateDelivery:
    def test_interleave_produces_no_duplicates(self):
        """Deterministically hit the register→buffer_snapshot window.

        Sequence:
        1. Generator registers its queue (subscriber_lock).
        2. Bus thread fires bus.log() — event lands in buffer + live queue.
        3. Generator snapshots the buffer (buffer_lock).
        4. Generator replays buffer_snapshot (contains the event).
        5. Generator drains the live queue (also contains the event).
        Without FIX 1 the event appears twice; with FIX 1 it appears once.
        """
        task = _make_task()
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)

        # Barrier-controlled ordering: force bus.log() between register + snapshot.
        step1_registered = threading.Event()  # generator registered its queue
        step2_logged = threading.Event()       # bus thread has logged

        all_delivered: list[tuple] = []

        async def _run_generator():
            q: asyncio.Queue = asyncio.Queue()

            # Step 1: register subscriber.
            with task.subscriber_lock:
                task.subscriber_queues.append(q)
            step1_registered.set()

            # Step 2: wait for bus thread to log (event is now in queue).
            step2_logged.wait(timeout=2.0)

            # Compute max_replayed_id BEFORE snapshot (mirrors the generator logic).
            with task.buffer_lock:
                buffer_snapshot = list(task.event_buffer)
                terminal = task.terminal_event

            max_replayed_id = max((e[0] for e in buffer_snapshot), default=0)

            # Replay phase.
            for event_id, name, payload in buffer_snapshot:
                all_delivered.append((event_id, name))

            # Early-exit if terminal was already set (not the case here).
            if terminal is not None:
                return

            # Drain the live queue — apply the dedup guard.
            while not q.empty():
                item = q.get_nowait()
                if item is None:
                    break
                event_id, name, payload = item
                if event_id <= max_replayed_id:
                    continue  # FIX 1: skip duplicate
                all_delivered.append((event_id, name))

        def _bus_thread():
            step1_registered.wait(timeout=2.0)
            bus.log("interleaved-event")
            step2_logged.set()

        asyncio.set_event_loop(loop)

        bg = threading.Thread(target=_bus_thread, daemon=True)
        bg.start()

        loop.run_until_complete(_run_generator())
        bg.join(timeout=2)
        loop.close()

        # Each event_id must appear at most once.
        ids_seen = [eid for eid, _ in all_delivered]
        assert len(ids_seen) == len(set(ids_seen)), (
            f"Duplicate event_ids detected in delivery: {ids_seen}"
        )
        # The interleaved event must have been delivered exactly once.
        assert len(all_delivered) == 1
        assert all_delivered[0][1] == "log"
