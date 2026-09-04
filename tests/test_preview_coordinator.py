"""Layer-1 tests for :mod:`app.views.preview_coordinator` (#622 Phase 2).

Every test drives the coordinator with REAL threads and fake decodes gated by
``threading.Event``, so the assertions are about what actually ran and in what
order — not about how many times a mock was called. No Qt: the coordinator is
deliberately Qt-free so this file does not drag the GUI stack into the
coverage report (see the import-cascade note in ``pyproject.toml``).

The contracts pinned here, each with the user-visible failure it prevents:

* per-device serialisation — N concurrent SMB reads on one NAS box, each
  slowing the others, when the user clicks through a group quickly;
* cancel-before-start — decoding images for groups the user has already left;
* no-delivery-after-cancel — a late image painting over the one the user is
  now looking at;
* "D waits for A" — four clicks costing four decodes instead of two;
* prefetch never starves a user request — the speculative read for the next
  group delaying the image actually being waited on;
* cross-device concurrency — a slow NAS decode blocking a local-SSD preview
  that shares nothing with it.
"""

from __future__ import annotations

import threading

import pytest

from app.views.preview_coordinator import (
    KIND_BATCH,
    KIND_PREFETCH,
    KIND_SINGLE,
    PreviewRequestCoordinator,
)

# Generous: these waits only ever elapse when the code under test is wrong.
_TIMEOUT = 5.0


def _fixed_device(mapping: dict[str, str]):
    """device_key stub: path → device, so tests don't depend on real drives."""
    return lambda path: mapping.get(path, "DEV")


class _Decoder:
    """Fake decode worker: runs on a real thread and blocks until released.

    ``start`` is what the coordinator invokes when a request wins its device
    slot, so ``started`` is the honest record of which decodes actually
    happened — a request that is cancelled before it starts never appears.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started: list[str] = []
        self.completed: list[str] = []
        self._gates: dict[str, threading.Event] = {}
        self._started_events: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []

    def _gate(self, path: str) -> threading.Event:
        with self._lock:
            return self._gates.setdefault(path, threading.Event())

    def _started_event(self, path: str) -> threading.Event:
        with self._lock:
            return self._started_events.setdefault(path, threading.Event())

    def start(self, handle) -> None:
        thread = threading.Thread(target=self._run, args=(handle,), daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _run(self, handle) -> None:
        try:
            with self._lock:
                self.started.append(handle.path)
            self._started_event(handle.path).set()
            self._gate(handle.path).wait(timeout=_TIMEOUT)
            with self._lock:
                self.completed.append(handle.path)
        finally:
            handle.finish()

    # -- test controls --------------------------------------------------
    def release(self, path: str) -> None:
        """Let the decode of ``path`` finish."""
        self._gate(path).set()

    def wait_started(self, path: str) -> bool:
        return self._started_event(path).wait(timeout=_TIMEOUT)

    def has_started(self, path: str) -> bool:
        return self._started_event(path).is_set()

    def join(self) -> None:
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=_TIMEOUT)


@pytest.fixture
def decoder():
    dec = _Decoder()
    yield dec
    # Never leave a blocked worker thread behind for the next test.
    for path in list(dec._gates):
        dec.release(path)
    dec.join()


class TestPerDeviceSerialization:
    def test_serializes_same_device(self, decoder):
        """Two requests on ONE device never decode at the same time.

        Real failure mode: without this, every rapid click adds another
        concurrent SMB read to the same NAS box; they contend for one pipe and
        all of them get slower, including the one the user is waiting for.
        """
        coord = PreviewRequestCoordinator(
            device_key_fn=_fixed_device({"a.jpg": "J", "b.jpg": "J"})
        )

        coord.submit(path="a.jpg", start=decoder.start, kind=KIND_BATCH)
        assert decoder.wait_started("a.jpg")
        coord.submit(path="b.jpg", start=decoder.start, kind=KIND_BATCH)

        assert not decoder.has_started("b.jpg"), (
            "b.jpg decoded while a.jpg was still in flight on the same device"
        )
        decoder.release("a.jpg")
        assert decoder.wait_started("b.jpg"), "b.jpg never started after a.jpg finished"
        decoder.release("b.jpg")
        decoder.join()
        assert decoder.completed == ["a.jpg", "b.jpg"]

    def test_different_devices_run_concurrently(self, decoder):
        """Requests on different devices are NOT serialised against each other.

        Real failure mode: a 3 s NAS DNG decode must not hold up the preview
        of a local-SSD file. One global lock would make the whole pane as slow
        as its slowest device.
        """
        coord = PreviewRequestCoordinator(
            device_key_fn=_fixed_device({"nas.dng": "J", "ssd.jpg": "C"})
        )

        coord.submit(path="nas.dng", start=decoder.start, kind=KIND_SINGLE)
        coord.submit(path="ssd.jpg", start=decoder.start, kind=KIND_SINGLE)

        assert decoder.wait_started("nas.dng")
        assert decoder.wait_started("ssd.jpg"), (
            "the local-SSD decode waited behind the NAS decode — the "
            "per-device buckets collapsed into one global queue"
        )

    def test_batch_requests_all_run(self, decoder):
        """Every tile of one grid decodes — a batch is a set, not a race.

        Real failure mode this guards: if grid thumbnails used the
        supersede-the-pending rule that single previews use, selecting a group
        of five would cancel four tiles and the user would see one image and
        four "Loading…" labels forever.
        """
        paths = [f"t{i}.jpg" for i in range(5)]
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        for path in paths:
            coord.submit(path=path, start=decoder.start, kind=KIND_BATCH)
        for path in paths:
            assert decoder.wait_started(path), f"{path} never decoded"
            decoder.release(path)

        decoder.join()
        assert decoder.completed == paths, "grid tiles decoded out of order"


class TestCancellation:
    def test_superseded_pending_never_runs(self, decoder):
        """A superseded, not-yet-started request does not decode at all.

        Real failure mode: the user clicks past a file. Its decode is still
        queued; running it spends the NAS read budget on an image nobody will
        look at, delaying the one they are waiting for.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="a.jpg", start=decoder.start, kind=KIND_SINGLE)
        assert decoder.wait_started("a.jpg")
        superseded = coord.submit(path="b.jpg", start=decoder.start, kind=KIND_SINGLE)
        coord.submit(path="c.jpg", start=decoder.start, kind=KIND_SINGLE)

        assert superseded.is_cancelled()
        decoder.release("a.jpg")
        assert decoder.wait_started("c.jpg")
        decoder.release("c.jpg")
        decoder.join()

        assert "b.jpg" not in decoder.started, (
            "a superseded request decoded anyway — cancel-before-start is not "
            "actually preventing the work"
        )

    def test_cancelled_in_flight_does_not_deliver(self, decoder):
        """A request cancelled mid-decode reports cancelled to its worker.

        The decode itself cannot be interrupted (rawpy / WIC are
        uninterruptible — issue #622's acknowledged limit), so the contract is
        that the worker asks before delivering. Real failure mode if it did
        not: the stale image paints over the file the user has since selected.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        handle = coord.submit(path="a.jpg", start=decoder.start, kind=KIND_SINGLE)
        assert decoder.wait_started("a.jpg")
        assert not handle.is_cancelled()

        handle.cancel()

        assert handle.is_cancelled()
        assert handle.has_started(), "the request under test was never in flight"

    def test_explicitly_cancelled_pending_request_never_starts(self, decoder):
        """``handle.cancel()`` on a QUEUED request means it never decodes.

        Distinct from supersession, which unlinks the request from the queue:
        here the handle is still queued and only marked. The coordinator must
        check the mark when the device frees up, otherwise a caller that
        cancels its own request (the handle is public API — ``submit``
        returns it) still pays for the read it just cancelled.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH)
        assert decoder.wait_started("busy.jpg")
        doomed = coord.submit(path="doomed.jpg", start=decoder.start, kind=KIND_BATCH)
        coord.submit(path="wanted.jpg", start=decoder.start, kind=KIND_BATCH)

        doomed.cancel()
        decoder.release("busy.jpg")

        assert decoder.wait_started("wanted.jpg")
        decoder.release("wanted.jpg")
        decoder.join()
        assert "doomed.jpg" not in decoder.started, (
            "a request cancelled while queued was started anyway"
        )

    def test_cancelled_in_flight_still_releases_the_device(self, decoder):
        """Cancelling an in-flight request must not wedge its device.

        Real failure mode: if the cancelled decode's slot were never freed,
        that drive's preview pane would stop updating for the rest of the
        session — every later request would queue behind a ghost.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        handle = coord.submit(path="a.jpg", start=decoder.start, kind=KIND_SINGLE)
        assert decoder.wait_started("a.jpg")
        handle.cancel()
        coord.submit(path="b.jpg", start=decoder.start, kind=KIND_SINGLE)
        decoder.release("a.jpg")

        assert decoder.wait_started("b.jpg"), (
            "the device stayed busy after a cancelled decode finished"
        )

    def test_rapid_clicks_only_first_and_last(self, decoder):
        """A, B, C, D on one device → exactly A and D decode, in that order.

        This is the "D waits for A" contract from issue #622 item 8, and the
        whole point of the coordinator: four fast clicks cost two NAS reads,
        not four, and the image the user ends on is the one that gets decoded.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="A", start=decoder.start, kind=KIND_SINGLE)
        assert decoder.wait_started("A")
        for path in ("B", "C", "D"):
            coord.begin_selection()
            coord.submit(path=path, start=decoder.start, kind=KIND_SINGLE)

        decoder.release("A")
        assert decoder.wait_started("D")
        decoder.release("D")
        decoder.join()

        assert decoder.started == ["A", "D"], (
            f"expected exactly A then D to decode, got {decoder.started}"
        )

    def test_begin_selection_cancels_pending_batch(self, decoder):
        """Switching groups drops the previous group's undecoded tiles.

        Real failure mode: the user clicks group 1 then immediately group 2.
        Without this, group 1's four remaining thumbnails still read from the
        NAS ahead of group 2's, so the group actually on screen fills in last.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="g1-a", start=decoder.start, kind=KIND_BATCH)
        assert decoder.wait_started("g1-a")
        stale = [
            coord.submit(path=f"g1-{n}", start=decoder.start, kind=KIND_BATCH)
            for n in ("b", "c")
        ]

        coord.begin_selection()
        coord.submit(path="g2-a", start=decoder.start, kind=KIND_BATCH)
        decoder.release("g1-a")

        assert decoder.wait_started("g2-a")
        decoder.release("g2-a")
        decoder.join()
        assert all(handle.is_cancelled() for handle in stale)
        assert decoder.started == ["g1-a", "g2-a"], (
            f"stale group-1 tiles decoded anyway: {decoder.started}"
        )

    def test_begin_selection_only_cancels_its_own_owners_requests(self, decoder):
        """One pane's new selection must not cancel another pane's queue.

        Real failure mode, and the reason the owner scope exists: the main
        window and the Execute Action dialog share ONE ImageTaskRunner
        (#409). Unscoped, opening that dialog and previewing a row would
        cancel the main pane's still-queued thumbnails — and nothing
        re-requests them, so those tiles sit on "Loading…" until the user
        re-selects the group.
        """
        main_pane = object()
        dialog_pane = object()
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH,
                     owner=main_pane)
        assert decoder.wait_started("busy.jpg")
        mine = coord.submit(path="main-tile.jpg", start=decoder.start,
                            kind=KIND_BATCH, owner=main_pane)

        coord.begin_selection(dialog_pane)

        assert not mine.is_cancelled(), (
            "the dialog's selection cancelled the main pane's queued tile"
        )
        decoder.release("busy.jpg")
        assert decoder.wait_started("main-tile.jpg")
        decoder.release("main-tile.jpg")

    def test_begin_selection_without_an_owner_cancels_everything(self, decoder):
        """An unscoped reset still drops every queued request."""
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH,
                     owner=object())
        assert decoder.wait_started("busy.jpg")
        queued = coord.submit(path="queued.jpg", start=decoder.start,
                              kind=KIND_BATCH, owner=object())

        coord.begin_selection()

        assert queued.is_cancelled()
        decoder.release("busy.jpg")

    def test_finish_is_idempotent(self, decoder):
        """A double ``finish()`` must not release the slot twice.

        Real failure mode: a worker that both returns early and hits its
        ``finally`` would free the device twice, letting two decodes run
        concurrently on the drive the coordinator exists to protect.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))
        handle = coord.submit(path="a.jpg", start=decoder.start, kind=KIND_SINGLE)
        assert decoder.wait_started("a.jpg")

        coord.submit(path="b.jpg", start=decoder.start, kind=KIND_BATCH)
        coord.submit(path="c.jpg", start=decoder.start, kind=KIND_BATCH)
        handle.finish()
        handle.finish()  # the second must be a no-op

        assert decoder.wait_started("b.jpg")
        assert not decoder.has_started("c.jpg"), (
            "a repeated finish() released the device twice — two decodes are "
            "now in flight on one device"
        )
        decoder.release("a.jpg")
        decoder.release("b.jpg")


class TestPrefetch:
    def test_prefetch_runs_when_device_is_idle(self, decoder):
        """With nothing else queued, the 1-ahead prefetch does run.

        Without this the feature would be inert — and inert speculative code
        looks identical to working speculative code from the outside.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))
        coord.submit(path="next.jpg", start=decoder.start, kind=KIND_PREFETCH)
        assert decoder.wait_started("next.jpg")

    def test_prefetch_never_starves_a_user_request(self, decoder):
        """A queued user request always decodes before a queued prefetch.

        Real failure mode: the speculative read for the next group holds the
        device while the user stares at "Loading…" for the group they just
        clicked. Speculation must never cost the user latency.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH)
        assert decoder.wait_started("busy.jpg")
        # Prefetch queued FIRST, user request second — priority, not arrival
        # order, must decide.
        coord.submit(path="ahead.jpg", start=decoder.start, kind=KIND_PREFETCH)
        coord.submit(path="clicked.jpg", start=decoder.start, kind=KIND_SINGLE)

        decoder.release("busy.jpg")
        assert decoder.wait_started("clicked.jpg")
        assert not decoder.has_started("ahead.jpg"), (
            "the prefetch jumped ahead of the image the user is waiting for"
        )
        decoder.release("clicked.jpg")

    def test_new_selection_drops_a_pending_prefetch(self, decoder):
        """A real selection pre-empts speculation that has not started.

        The prefetch was a guess about where the user was going; once they go
        somewhere else the guess is worthless, and running it would spend the
        device's one slot on an image nothing is waiting for.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH)
        assert decoder.wait_started("busy.jpg")
        ahead = coord.submit(path="ahead.jpg", start=decoder.start, kind=KIND_PREFETCH)
        coord.submit(path="clicked.jpg", start=decoder.start, kind=KIND_SINGLE)

        assert ahead.is_cancelled()
        decoder.release("busy.jpg")
        assert decoder.wait_started("clicked.jpg")
        decoder.release("clicked.jpg")
        decoder.join()
        assert "ahead.jpg" not in decoder.started

    def test_begin_selection_drops_a_pending_prefetch(self, decoder):
        """The pane's own new-selection signal drops the stale 1-ahead guess.

        Distinct from the ``submit``-time pre-emption: this is the path the
        real pane takes (``show_single`` / ``show_grid`` call
        ``begin_selection`` before requesting anything). Without it a
        prefetch queued for a device the user then navigated away from would
        still read from that device.
        """
        pane = object()
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH,
                     owner=pane)
        assert decoder.wait_started("busy.jpg")
        ahead = coord.submit(path="ahead.jpg", start=decoder.start,
                             kind=KIND_PREFETCH, owner=pane)

        coord.begin_selection(pane)

        assert ahead.is_cancelled()
        decoder.release("busy.jpg")
        decoder.join()
        assert "ahead.jpg" not in decoder.started

    def test_a_newer_prefetch_supersedes_the_older_one(self, decoder):
        """Only the most recent 1-ahead guess is kept.

        Real failure mode: scrolling through ten groups would otherwise queue
        ten speculative decodes, and the device would spend minutes reading
        images for groups the user passed long ago.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        coord.submit(path="busy.jpg", start=decoder.start, kind=KIND_BATCH)
        assert decoder.wait_started("busy.jpg")
        first = coord.submit(path="ahead1.jpg", start=decoder.start, kind=KIND_PREFETCH)
        coord.submit(path="ahead2.jpg", start=decoder.start, kind=KIND_PREFETCH)

        assert first.is_cancelled()
        assert coord.pending_count("busy.jpg") == 1
        decoder.release("busy.jpg")
        assert decoder.wait_started("ahead2.jpg")
        decoder.release("ahead2.jpg")


class TestStartFailure:
    def test_a_raising_start_does_not_wedge_the_device(self, decoder):
        """If handing the task to the pool raises, the device still recovers.

        Real failure mode: the worker never runs, so its ``finally`` never
        releases the slot — that drive's preview pane would silently stop
        updating for the rest of the session.
        """
        coord = PreviewRequestCoordinator(device_key_fn=_fixed_device({}))

        def _explode(handle):
            raise RuntimeError("QThreadPool.start failed")

        with pytest.raises(RuntimeError):
            coord.submit(path="boom.jpg", start=_explode, kind=KIND_SINGLE)

        coord.submit(path="after.jpg", start=decoder.start, kind=KIND_SINGLE)
        assert decoder.wait_started("after.jpg"), (
            "the device stayed permanently busy after a failed start"
        )
        decoder.release("after.jpg")
