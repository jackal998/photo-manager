"""Per-device serialisation + cancellation for preview decodes (#622 Phase 2).

Why this exists
---------------
Every preview/thumbnail decode reads the source file. On a NAS the read
dominates. Before this coordinator, each click fired its decode straight at
the global ``QThreadPool``: clicking through five groups fast put five
concurrent SMB reads on one box, each slowing the others, and the user waited
for the image of a group they had already left.

The contract implemented here:

* **Per-device serialisation** — at most ONE in-flight decode per device key
  (:func:`infrastructure.device_key.device_key`, the same key the scanner's
  HASH stage buckets by). Requests for *different* devices are untouched and
  proceed concurrently.
* **Cancellation tokens** — a superseded request that has not started never
  runs at all; one that has already started still finishes its decode (see
  the limit below) but delivers nothing to the pane.
* **"D waits for A"** — clicks A, B, C, D on one device: A is already in
  flight, B and C are superseded before they start, D runs when A returns.
  Exactly two decodes, not four.
* **1-ahead prefetch** — the pane may enqueue the next group's first image at
  prefetch priority. A prefetch is only ever started when nothing the user is
  waiting for is pending, and any new selection drops a prefetch that has not
  started. It can never starve a real request.

Acknowledged limit (issue #622, "out-of-budget surfaces"): an in-flight
``rawpy`` / Shell-WIC decode is uninterruptible. Cancellation therefore means
"never starts" or "result discarded" — at most one already-running decode
burns through per device.

Design notes
------------
Plain Python + ``threading`` only: no Qt import, so the whole contract is
unit-testable with fake tasks gated by ``threading.Event`` and without
dragging the GUI stack into the layer-1 coverage report. Qt appears only at
the boundary — the caller passes a ``start`` callable that submits to
``QThreadPool``, and the task calls :meth:`RequestHandle.finish` when done.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

from infrastructure.device_key import device_key as _default_device_key

# Request kinds, in the order the coordinator will start them.
KIND_SINGLE = "single"      # the single-view preview: at most one pending
KIND_BATCH = "batch"        # one tile of a grid: FIFO, the whole set must render
KIND_PREFETCH = "prefetch"  # speculative 1-ahead: lowest priority, droppable


class RequestHandle:
    """Cancellation token + completion signal for one submitted request.

    The worker that performs the decode MUST call :meth:`finish` exactly once
    (in a ``finally``) — that is what releases the device slot for the next
    request. It should check :meth:`is_cancelled` before delivering its result.
    """

    __slots__ = ("_coordinator", "_device", "_path", "_kind", "_start",
                 "_owner", "_cancelled", "_started", "_finished", "_lock")

    def __init__(
        self,
        *,
        coordinator: "PreviewRequestCoordinator",
        device: str,
        path: str,
        kind: str,
        start: Callable[[], None],
        owner: object = None,
    ) -> None:
        self._coordinator = coordinator
        self._device = device
        self._path = path
        self._kind = kind
        self._start = start
        self._owner = owner
        self._cancelled = False
        self._started = False
        self._finished = False
        self._lock = threading.Lock()

    @property
    def device(self) -> str:
        return self._device

    @property
    def path(self) -> str:
        return self._path

    @property
    def owner(self) -> object:
        return self._owner

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def has_started(self) -> bool:
        with self._lock:
            return self._started

    def cancel(self) -> None:
        """Mark this request cancelled.

        Cheap and idempotent. If it has not started it will never start; if it
        has, the worker discards its result instead of delivering it.
        """
        with self._lock:
            self._cancelled = True

    def finish(self) -> None:
        """Release this request's device slot and start whatever is next.

        Idempotent — a second call is a no-op, so a worker that both returns
        early and hits its ``finally`` cannot double-release the slot (which
        would let two decodes run on one device at once).
        """
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._coordinator._release(self)

    def _begin(self) -> None:
        """Invoke the caller's ``start`` callable. Called outside the lock.

        ``start`` receives this handle, so the caller can build its task
        around the token in the same breath as starting it — there is no
        window in which a task is running without knowing its own handle.
        """
        with self._lock:
            self._started = True
        try:
            self._start(self)
        except Exception:
            # The worker never got as far as its own ``finally``, so nothing
            # else will release this device. Without this the whole device
            # wedges: every later preview for that drive queues behind a
            # request that is not running and never will.
            self.finish()
            raise


def _owned_by(handle: RequestHandle, owner: object) -> bool:
    """True when ``handle`` is in scope for a cancellation by ``owner``.

    Identity, not equality: an owner is a specific pane object, and ``==`` on
    a widget could run someone's ``__eq__`` and match a different instance.
    """
    return owner is None or handle.owner is owner


class _DeviceSlot:
    """The queue state for one physical device."""

    __slots__ = ("running", "pending_single", "pending_batch", "pending_prefetch")

    def __init__(self) -> None:
        self.running: RequestHandle | None = None
        self.pending_single: RequestHandle | None = None
        self.pending_batch: deque[RequestHandle] = deque()
        self.pending_prefetch: RequestHandle | None = None


class PreviewRequestCoordinator:
    """Serialises preview decodes per physical device.

    ``device_key_fn`` is injected so tests can pin bucketing without depending
    on the host's real drive letters; production uses the shared
    :func:`infrastructure.device_key.device_key`, so the preview layer and the
    scanner's HASH stage always agree on what "one device" means.
    """

    def __init__(self, *, device_key_fn: Callable[[str], str] | None = None) -> None:
        self._device_key = device_key_fn or _default_device_key
        self._lock = threading.Lock()
        self._devices: dict[str, _DeviceSlot] = {}

    def begin_selection(self, owner: object = None) -> None:
        """Mark a new user selection: drop requests that have not started.

        Called when a pane switches to a different file or group. Requests
        already in flight are left alone (uninterruptible), but everything
        still queued belongs to a selection the user has left, so running it
        would spend NAS bandwidth on an image nobody is going to look at.

        ``owner`` scopes the cancellation to requests submitted by that owner.
        This matters because ONE runner is shared between the main window's
        preview pane and the one embedded in the Execute Action dialog
        (#409): without the scope, opening that dialog would cancel the main
        pane's still-queued thumbnails, and those tiles would sit on
        "Loading…" until the user re-selected the group. ``None`` cancels
        everything queued, which is what a whole-runner reset wants.
        """
        with self._lock:
            stale: list[RequestHandle] = []
            for slot in self._devices.values():
                if slot.pending_single is not None and _owned_by(
                    slot.pending_single, owner
                ):
                    stale.append(slot.pending_single)
                    slot.pending_single = None
                kept = deque(
                    h for h in slot.pending_batch if not _owned_by(h, owner)
                )
                stale.extend(h for h in slot.pending_batch if _owned_by(h, owner))
                slot.pending_batch = kept
                if slot.pending_prefetch is not None and _owned_by(
                    slot.pending_prefetch, owner
                ):
                    stale.append(slot.pending_prefetch)
                    slot.pending_prefetch = None
        for handle in stale:
            handle.cancel()

    def submit(
        self,
        *,
        path: str,
        start: Callable[[], None],
        kind: str = KIND_SINGLE,
        owner: object = None,
    ) -> RequestHandle:
        """Queue one decode for ``path``; return its cancellation handle.

        ``start`` is invoked the moment this request wins its device slot —
        that is where the caller hands the real task to ``QThreadPool``. That
        may be inside this call (the device was idle) or later, on the worker
        thread of whichever request finishes ahead of it, so ``start`` must be
        safe to call from a non-GUI thread. It is never invoked at all for a
        request that gets cancelled first.
        """
        device = self._device_key(path)
        superseded: list[RequestHandle] = []
        with self._lock:
            slot = self._devices.get(device)
            if slot is None:
                slot = _DeviceSlot()
                self._devices[device] = slot
            handle = RequestHandle(
                coordinator=self, device=device, path=path, kind=kind,
                start=start, owner=owner,
            )
            if kind == KIND_PREFETCH:
                if slot.pending_prefetch is not None:
                    superseded.append(slot.pending_prefetch)
                slot.pending_prefetch = handle
            elif kind == KIND_BATCH:
                slot.pending_batch.append(handle)
            else:
                # A newer single-view request replaces the pending one: the
                # user has moved on, so the older click must not decode.
                if slot.pending_single is not None:
                    superseded.append(slot.pending_single)
                slot.pending_single = handle
                # A real selection pre-empts speculation.
                if slot.pending_prefetch is not None:
                    superseded.append(slot.pending_prefetch)
                    slot.pending_prefetch = None
            to_start = self._next_locked(slot)
        for stale in superseded:
            stale.cancel()
        if to_start is not None:
            to_start._begin()
        return handle

    def pending_count(self, path: str) -> int:
        """Number of not-yet-started requests queued for ``path``'s device.

        Read-only introspection for tests and diagnostics.
        """
        device = self._device_key(path)
        with self._lock:
            slot = self._devices.get(device)
            if slot is None:
                return 0
            return (
                (1 if slot.pending_single is not None else 0)
                + len(slot.pending_batch)
                + (1 if slot.pending_prefetch is not None else 0)
            )

    def _release(self, handle: RequestHandle) -> None:
        """Free ``handle``'s device slot and start the next queued request."""
        with self._lock:
            slot = self._devices.get(handle.device)
            if slot is None or slot.running is not handle:
                return
            slot.running = None
            to_start = self._next_locked(slot)
        if to_start is not None:
            to_start._begin()

    def _next_locked(self, slot: _DeviceSlot) -> RequestHandle | None:
        """Pick the next request to run for ``slot``. Caller holds the lock.

        Returns ``None`` when the device is busy or nothing is waiting. The
        caller must invoke ``_begin()`` OUTSIDE the lock — ``start`` may run
        the work synchronously and call back into ``finish``.
        """
        if slot.running is not None:
            return None
        while True:
            if slot.pending_single is not None:
                candidate = slot.pending_single
                slot.pending_single = None
            elif slot.pending_batch:
                candidate = slot.pending_batch.popleft()
            elif slot.pending_prefetch is not None:
                candidate = slot.pending_prefetch
                slot.pending_prefetch = None
            else:
                return None
            # A cancelled request never starts — skip it and look at the next.
            if not candidate.is_cancelled():
                slot.running = candidate
                return candidate
