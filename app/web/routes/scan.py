"""POST /api/scan, GET /api/scan/{id}/events, POST /api/scan/{id}/cancel."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.web.models import ScanTask, WebScanRequest
from app.web.registry import registry
from core.app_service.cancel_token import _CancelToken
from core.app_service.scan_runner import run_pipeline

router = APIRouter()

# ---------------------------------------------------------------------------
# Settings helpers — mirrors how scan_dialog.py locates settings.json
# ---------------------------------------------------------------------------

def _load_settings():
    """Return a JsonSettings pointed at the same settings.json the Qt app uses.

    Resolution order matches main.py:
    1. PHOTO_MANAGER_HOME env var (relative to repo root)
    2. Repository root (BASE_DIR = directory of main.py, two levels above app/web/)
    """
    from infrastructure.settings import JsonSettings

    home_env = os.environ.get("PHOTO_MANAGER_HOME")
    # app/web/routes/scan.py → app/web/ → app/ → repo root
    repo_root = Path(__file__).parent.parent.parent.parent
    if home_env:
        config_home = (repo_root / home_env).resolve()
    else:
        config_home = repo_root
    return JsonSettings(config_home / "settings.json")


# ---------------------------------------------------------------------------
# SseScanBus — implements ScanProgressBus over SSE
# ---------------------------------------------------------------------------

class SseScanBus:
    """Implements ScanProgressBus for SSE delivery.

    Constructed once per scan with the ScanTask and the asyncio event loop
    captured inside the async POST handler (asyncio.get_running_loop()).

    Each method:
    - Appends (event_id, name, payload) to task.event_buffer under buffer_lock.
    - Fans out to every subscriber asyncio.Queue via loop.call_soon_threadsafe
      under a snapshot of subscriber_queues (subscriber_lock guards the list
      itself; call_soon_threadsafe is lock-free per asyncio semantics).

    Terminal methods (finished / failed / completed_empty) additionally:
    - Set task.status and task.terminal_event.
    - Send a None sentinel to every subscriber queue so SSE generators exit.
    """

    def __init__(self, task: ScanTask, loop: asyncio.AbstractEventLoop) -> None:
        self._task = task
        self._loop = loop

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_and_fanout(self, name: str, payload: dict) -> None:
        """Append event to buffer and deliver to all subscribers."""
        task = self._task

        with task.buffer_lock:
            task.last_event_id += 1
            event_id = task.last_event_id
            task.event_buffer.append((event_id, name, payload))

        # Fan out under a list snapshot so we don't hold subscriber_lock
        # across the (non-blocking) call_soon_threadsafe calls.
        with task.subscriber_lock:
            queues = list(task.subscriber_queues)

        for q in queues:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, (event_id, name, payload))
            except RuntimeError:
                # Loop closed (server shutdown) — bus is fire-and-forget by contract.
                pass

    def _set_terminal(self, status: str, name: str, payload: dict) -> None:
        """Set task status + terminal_event, append to buffer, send sentinels."""
        import time as _time

        task = self._task

        with task.buffer_lock:
            task.last_event_id += 1
            event_id = task.last_event_id
            task.event_buffer.append((event_id, name, payload))
            task.status = status
            task.terminal_event = {"id": event_id, "name": name, "payload": payload}
            # FIX 2b: stamp terminal time so the reaper can enforce TTL.
            task._terminal_at = _time.monotonic()

        with task.subscriber_lock:
            queues = list(task.subscriber_queues)

        for q in queues:
            # Deliver the terminal event then the sentinel.
            # FIX 3: guard against closed loop on server shutdown.
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, (event_id, name, payload))
            except RuntimeError:
                pass
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, None)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # ScanProgressBus implementation — method names match the Protocol exactly
    # ------------------------------------------------------------------

    def log(self, msg: str) -> None:
        self._append_and_fanout("log", {"msg": msg})

    def stage(
        self,
        stage_name: str,
        completed: int,
        total: int,
        files_per_sec: float,
    ) -> None:
        self._append_and_fanout(
            "stage",
            {
                "stage_name": stage_name,
                "completed": completed,
                "total": total,
                "files_per_sec": files_per_sec,
            },
        )

    def failed(self, msg: str) -> None:
        # Classify by message, matching how scan_dialog distinguishes clean cancel
        # from red-modal errors.  The canonical clean-cancel payload is exactly
        # "Scan cancelled." — any other string is a genuine pipeline error.
        # (Classifying by cancel_token.is_set() races: a pipeline error that
        # arrives while a cancel is in flight would be mislabelled 'cancelled'.)
        status = "cancelled" if msg == "Scan cancelled." else "failed"
        self._set_terminal(status, "failed", {"msg": msg})

    def finished(self, output_path: str) -> None:
        self._set_terminal("finished", "finished", {"output_path": output_path})

    def completed_empty(self) -> None:
        self._set_terminal("finished", "completed_empty", {})

    def hash_pool_measured(self, rates: dict) -> None:
        """Persist calibration rates to settings.json and emit event."""
        self._append_and_fanout("hash_pool_measured", rates)
        try:
            from core.app_service.scan_runner import (
                hash_pool_fingerprint,
                store_hash_pool_rates,
            )
            import os as _os
            settings = _load_settings()
            config = self._task._config if hasattr(self._task, "_config") else None
            if config is not None:
                fp = hash_pool_fingerprint(
                    config.sources, config.recursive_map, _os.cpu_count() or 4
                )
                store_hash_pool_rates(settings, fp, rates)
        except Exception:  # pylint: disable=broad-exception-caught
            # Best-effort persist — a settings write failure must not crash
            # the scan (the event has already been appended to the bus).
            pass

    def read_knee_measured(self, summary: dict) -> None:
        """Persist read-knee measurement to settings.json and emit event."""
        self._append_and_fanout("read_knee_measured", summary)
        device = summary.get("device")
        knee = summary.get("knee")
        if device is not None and isinstance(knee, int):
            try:
                from scanner.autotune import store_read_knee
                settings = _load_settings()
                store_read_knee(settings, device, knee)
            except Exception:  # pylint: disable=broad-exception-caught
                pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/scan", status_code=201)
async def start_scan(req: WebScanRequest) -> JSONResponse:
    """Start a scan pipeline in a background threading.Thread.

    Returns 201 {task_id} on success.
    Returns 400 if sources is empty.
    Returns 409 if a scan is already running.
    """
    if not req.sources:
        raise HTTPException(status_code=400, detail="sources must not be empty")

    task_id = registry.new_task_id()
    cancel_token = _CancelToken()
    task = ScanTask(
        task_id=task_id,
        status="running",
        cancel_token=cancel_token,
    )

    # Capture the running loop before we hand anything to a thread.
    loop = asyncio.get_running_loop()
    task.loop = loop

    # Store config on task so hash_pool_measured can compute the fingerprint.
    config = req.to_config()
    task._config = config  # type: ignore[attr-defined]

    try:
        registry.create(task)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    bus = SseScanBus(task, loop)

    def _run() -> None:
        try:
            run_pipeline(config, cancel_token, bus)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            bus.failed(str(exc))

    thread = threading.Thread(target=_run, name=f"scan-{task_id[:8]}", daemon=True)
    task.thread = thread
    thread.start()

    return JSONResponse({"task_id": task_id}, status_code=201)


@router.get("/api/scan/{task_id}/events")
async def scan_events(task_id: str, request: Request) -> EventSourceResponse:
    """SSE stream for scan progress events.

    Supports Last-Event-ID resume: replays buffered events since the requested
    id.  If the requested id has aged out of the 500-event ring buffer, injects
    the terminal event (if any) so the client learns the final state.
    """
    task = registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    # Parse Last-Event-ID header for resume support.
    last_id_header = request.headers.get("last-event-id")
    resume_from: int | None = None
    if last_id_header is not None:
        try:
            resume_from = int(last_id_header)
        except ValueError:
            resume_from = None

    async def _generate() -> AsyncGenerator[dict, None]:
        q: asyncio.Queue = asyncio.Queue()

        # Register subscriber and snapshot the buffer atomically.
        with task.subscriber_lock:
            task.subscriber_queues.append(q)

        # Snapshot the buffer under buffer_lock — never iterate the live deque.
        with task.buffer_lock:
            buffer_snapshot = list(task.event_buffer)
            terminal = task.terminal_event

        # FIX 1: consumer-side dedup guard.
        # The registration (subscriber_lock) and the snapshot (buffer_lock) are
        # separate sections, so _append_and_fanout can deliver an event to the
        # queue between those two operations — that event would then appear both
        # in buffer_snapshot (replay) and in the live queue.  Skipping any live
        # item whose id ≤ max_replayed_id makes delivery exactly-once for all
        # interleavings without requiring nested locks.
        max_replayed_id = max((e[0] for e in buffer_snapshot), default=0)

        try:
            # Replay buffered events the client hasn't seen yet.
            replayed_any = False
            if resume_from is not None:
                # Find whether the requested id is still in the ring.
                ids_in_buffer = {evt[0] for evt in buffer_snapshot}
                if resume_from not in ids_in_buffer and resume_from > 0:
                    # Requested id aged out — fast-forward: inject terminal if
                    # available so the client learns the final state.
                    if terminal is not None:
                        yield {
                            "event": terminal["name"],
                            "data": json.dumps(terminal["payload"]),
                            "id": str(terminal["id"]),
                        }
                        return
                    # Terminal not yet set — fall through to live stream.
                else:
                    for event_id, name, payload in buffer_snapshot:
                        if event_id > resume_from:
                            yield {
                                "event": name,
                                "data": json.dumps(payload),
                                "id": str(event_id),
                            }
                            replayed_any = True
            else:
                # No resume header — replay everything buffered.
                for event_id, name, payload in buffer_snapshot:
                    yield {
                        "event": name,
                        "data": json.dumps(payload),
                        "id": str(event_id),
                    }
                    replayed_any = True

            # If the task is already terminal (replay included terminal event),
            # check whether the terminal event was replayed; if so, stop.
            if terminal is not None:
                # The terminal event was either just replayed or arrived via the
                # buffer snapshot. Either way the scan is done; no live stream.
                # Drain the queue of the sentinel the bus already put there.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                return

            # Live stream: await queue until sentinel (None) or disconnect.
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Keepalive comment — sse_starlette also sends its own
                    # keep-alive pings, but we send a comment here for
                    # explicit control on long-idle scans.
                    yield {"comment": "keepalive"}
                    continue
                except asyncio.CancelledError:
                    # Client disconnected — do NOT cancel the scan.
                    return

                if item is None:
                    # Sentinel: scan finished / failed / cancelled.
                    return

                event_id, name, payload = item
                # FIX 1: skip events already delivered via replay.
                if event_id <= max_replayed_id:
                    continue
                yield {
                    "event": name,
                    "data": json.dumps(payload),
                    "id": str(event_id),
                }

        except asyncio.CancelledError:
            # Client disconnected mid-stream — fire-and-forget; scan continues.
            return
        finally:
            # Always deregister this queue.
            with task.subscriber_lock:
                try:
                    task.subscriber_queues.remove(q)
                except ValueError:
                    pass

    return EventSourceResponse(_generate())


@router.post("/api/scan/{task_id}/cancel")
async def cancel_scan(task_id: str) -> dict:
    """Request cooperative cancellation of a running scan.

    Returns 200 {status: 'cancel_requested'} on success.
    Returns 404 if the task is unknown.
    Returns 409 if the task is already in a terminal state.
    """
    task = registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    if task.status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id!r} is already in terminal state {task.status!r}",
        )
    task.cancel_token.request()
    return {"status": "cancel_requested"}
