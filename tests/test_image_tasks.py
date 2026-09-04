"""Layer-1 tests for :mod:`app.views.image_tasks` (#293).

Closes the image_tasks portion of #293 (cascade-omit follow-up
to #185). Pure-logic token format is extracted to
``image_tasks_helpers.py``; this file covers the dispatch surface:

* :class:`_ImageTask` — service dispatch + signal emit.
* :class:`ImageTaskRunner` — service-None fast path + ``QThreadPool``
  ``start(task)`` invocation.

The actual ``QThreadPool.globalInstance()`` is replaced with a
fake pool in the runner tests; we don't want to enqueue real
work into the global pool from a unit test.

Post-PR-C' (web-port Phase 0): ImageService returns JPEG bytes; _ImageTask.run()
converts bytes → QImage via _bytes_to_qimage before emitting. Tests that inspect
the emitted ``img`` argument now see a QImage object rather than the raw service
return value. On service failure the task emits a null QImage() rather than None.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image as PILImage
from PySide6.QtGui import QImage

from app.views.image_tasks import ImageTaskRunner, _ImageTask


def _make_jpeg(w: int = 4, h: int = 4) -> bytes:
    """Return minimal valid JPEG bytes."""
    pil = PILImage.new("RGB", (w, h), color=(100, 100, 100))
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ── _ImageTask.run — the dispatch + emit logic ───────────────────────────


class TestImageTaskRunPreview:
    """is_preview=True → service.get_preview(path, side)."""

    def test_calls_get_preview_with_path_and_side(self):
        service = MagicMock()
        service.get_preview.return_value = "PREVIEW_IMG"
        receiver = MagicMock()
        task = _ImageTask(
            path="a.jpg", side=512, is_preview=True,
            service=service, receiver=receiver, token="single|a.jpg|512",
        )

        task.run()

        service.get_preview.assert_called_once_with("a.jpg", 512)
        service.get_thumbnail.assert_not_called()

    def test_emits_imageLoaded_with_token_path_and_image(self):
        """The signal carries (token, path, img:QImage). Failure mode: a
        refactor that reordered the emit args would break the
        slot's ``(token, path, img)`` unpacking and every preview
        would render at the wrong path.

        Post-PR-C': service returns JPEG bytes; _ImageTask wraps via
        _bytes_to_qimage before emit, so the third arg is a QImage.
        """
        service = MagicMock()
        service.get_preview.return_value = _make_jpeg()
        receiver = MagicMock()
        task = _ImageTask(
            path="a.jpg", side=0, is_preview=True,
            service=service, receiver=receiver, token="single|a.jpg|0",
        )

        task.run()

        receiver.imageLoaded.emit.assert_called_once()
        token_arg, path_arg, img_arg = receiver.imageLoaded.emit.call_args.args
        assert token_arg == "single|a.jpg|0"
        assert path_arg == "a.jpg"
        assert isinstance(img_arg, QImage), f"Expected QImage, got {type(img_arg)}"
        assert not img_arg.isNull(), "QImage from valid JPEG bytes must not be null"


class TestImageTaskRunThumbnail:
    """is_preview=False → service.get_thumbnail(path, side)."""

    def test_calls_get_thumbnail(self):
        service = MagicMock()
        service.get_thumbnail.return_value = "THUMB"
        receiver = MagicMock()
        task = _ImageTask(
            path="p.jpg", side=128, is_preview=False,
            service=service, receiver=receiver, token="grid|p.jpg|128",
        )

        task.run()

        service.get_thumbnail.assert_called_once_with("p.jpg", 128)
        service.get_preview.assert_not_called()

    def test_emits_thumbnail_payload(self):
        """Post-PR-C': service returns JPEG bytes; emitted img is a QImage."""
        service = MagicMock()
        service.get_thumbnail.return_value = _make_jpeg()
        receiver = MagicMock()
        task = _ImageTask(
            path="p.jpg", side=128, is_preview=False,
            service=service, receiver=receiver, token="grid|p.jpg|128",
        )

        task.run()

        receiver.imageLoaded.emit.assert_called_once()
        token_arg, path_arg, img_arg = receiver.imageLoaded.emit.call_args.args
        assert token_arg == "grid|p.jpg|128"
        assert path_arg == "p.jpg"
        assert isinstance(img_arg, QImage)


class TestImageTaskRunServiceFailure:
    """When the service raises, the task still emits — with img=null QImage.

    Post-PR-C': on exception the task emits a null QImage() (empty, not None).
    Downstream slots that check ``img.isNull()`` or ``img is None`` should handle
    both; the signal type annotation (object) accepts QImage.
    """

    def test_exception_in_get_preview_emits_null_qimage(self):
        """The named real failure mode: a corrupt JPEG (or PIL chokes
        on a HEIC variant) raises during decode. The task must still
        fire the signal with a null QImage so the preview pane can
        render its "unavailable" placeholder. Otherwise the user sees
        a stale image (or nothing) and doesn't know the load failed.
        """
        service = MagicMock()
        service.get_preview.side_effect = RuntimeError("PIL.UnidentifiedImageError")
        receiver = MagicMock()
        task = _ImageTask(
            path="bad.jpg", side=0, is_preview=True,
            service=service, receiver=receiver, token="single|bad.jpg|0",
        )

        task.run()

        # The signal still fires with a null QImage on failure
        receiver.imageLoaded.emit.assert_called_once()
        token_arg, path_arg, img_arg = receiver.imageLoaded.emit.call_args.args
        assert token_arg == "single|bad.jpg|0"
        assert path_arg == "bad.jpg"
        assert isinstance(img_arg, QImage)
        assert img_arg.isNull(), "On service failure, emitted QImage must be null"

    def test_exception_in_get_thumbnail_emits_null_qimage(self):
        """Same contract for the thumbnail branch."""
        service = MagicMock()
        service.get_thumbnail.side_effect = OSError("file truncated")
        receiver = MagicMock()
        task = _ImageTask(
            path="bad.jpg", side=128, is_preview=False,
            service=service, receiver=receiver, token="grid|bad.jpg|128",
        )

        task.run()

        receiver.imageLoaded.emit.assert_called_once()
        token_arg, path_arg, img_arg = receiver.imageLoaded.emit.call_args.args
        assert token_arg == "grid|bad.jpg|128"
        assert path_arg == "bad.jpg"
        assert isinstance(img_arg, QImage)
        assert img_arg.isNull(), "On service failure, emitted QImage must be null"


# ── ImageTaskRunner — pool dispatch ──────────────────────────────────────


class TestImageTaskRunnerInit:
    """Constructor stores service + receiver + global pool handle."""

    def test_stores_service_and_receiver(self):
        service = object()
        receiver = MagicMock()
        runner = ImageTaskRunner(service=service, receiver=receiver)
        assert runner._service is service
        assert runner._receiver is receiver
        # _pool is the global QThreadPool instance (a QObject — just
        # check it's not None / not the placeholder service).
        assert runner._pool is not None


class TestRequestSinglePreview:
    """The single-preview dispatch."""

    def test_returns_token_in_canonical_format(self):
        """Side is viewport-derived (not 0) for single preview.

        The viewport cap is min(2048, screen_width). The token must embed
        whatever side _compute_viewport_cap() returns so the cache key
        matches the loaded image's actual pixel dimensions — a mismatch
        would serve stale images from a previous viewport size.
        """
        from app.views.image_tasks import _compute_viewport_cap

        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        token = runner.request_single_preview("photos/a.jpg")
        expected_side = _compute_viewport_cap()

        assert token == f"single|photos/a.jpg|{expected_side}"

    def test_side_is_positive_viewport_cap(self):
        """The single-preview side must be > 0 (viewport-bounded, not full-res).

        Failure mode: reverting to side=0 sends a full-resolution decode
        request to the image service for every single-view update — causes
        OOM on large DNG libraries (the #622 regression this change prevents).
        """
        from app.views.image_tasks import _compute_viewport_cap

        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_single_preview("a.jpg")
        task = runner._pool.start.call_args.args[0]
        assert task._side == _compute_viewport_cap()
        assert task._side > 0

    def test_service_none_returns_token_without_starting_task(self):
        """The empty-state path: no service wired yet (e.g. during
        construction before the image service is plugged in). The
        runner returns the token so the caller's bookkeeping
        (``_current_single_token`` in PreviewPane) still works; no
        task is queued because there's nothing to run.

        Failure mode: a refactor that dropped the ``None`` guard
        would raise ``AttributeError`` on ``service.get_preview``
        on every preview attempt during the empty-state."""
        from app.views.image_tasks import _compute_viewport_cap

        runner = ImageTaskRunner(service=None, receiver=MagicMock())
        runner._pool = MagicMock()

        token = runner.request_single_preview("a.jpg")
        expected_side = _compute_viewport_cap()

        assert token == f"single|a.jpg|{expected_side}"
        runner._pool.start.assert_not_called()

    def test_dispatches_task_to_pool_when_service_present(self):
        """Happy path: a task is created and enqueued."""
        from app.views.image_tasks import _compute_viewport_cap

        service = MagicMock()
        receiver = MagicMock()
        runner = ImageTaskRunner(service=service, receiver=receiver)
        runner._pool = MagicMock()

        runner.request_single_preview("a.jpg")

        runner._pool.start.assert_called_once()
        task = runner._pool.start.call_args.args[0]
        expected_side = _compute_viewport_cap()
        assert isinstance(task, _ImageTask)
        assert task._path == "a.jpg"
        assert task._side == expected_side
        assert task._is_preview is True
        assert task._service is service
        assert task._receiver is receiver
        assert task._token == f"single|a.jpg|{expected_side}"


class TestRequestGridThumbnail:
    """The grid-thumbnail dispatch."""

    def test_returns_token_with_thumb_side(self):
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        token = runner.request_grid_thumbnail("a.jpg", 256)

        assert token == "grid|a.jpg|256"

    def test_service_none_returns_token_without_starting_task(self):
        runner = ImageTaskRunner(service=None, receiver=MagicMock())
        runner._pool = MagicMock()

        token = runner.request_grid_thumbnail("a.jpg", 128)

        assert token == "grid|a.jpg|128"
        runner._pool.start.assert_not_called()

    def test_dispatches_thumbnail_task_with_is_preview_false(self):
        """Failure mode: a refactor that flipped ``is_preview=True``
        here would route every grid thumbnail through ``get_preview``
        — slower (preview decodes are higher-res) and would consume
        the preview cache. Visible as a noticeable scroll lag in
        the result tree's grid view."""
        service = MagicMock()
        receiver = MagicMock()
        runner = ImageTaskRunner(service=service, receiver=receiver)
        runner._pool = MagicMock()

        runner.request_grid_thumbnail("a.jpg", 128)

        task = runner._pool.start.call_args.args[0]
        assert task._is_preview is False
        assert task._side == 128


# ── _ResolutionTask + request_resolution (#622 Phase 1) ──────────────────


class TestResolutionTask:
    """The off-thread resolution read dispatch.

    Replaces the synchronous ``_read_resolution(path)`` call in
    ``PreviewPane.show_single`` (which blocked the UI thread on NAS DNG
    rawpy reads for 3-5 s) with a QRunnable-dispatched async path.
    """

    def test_emits_resolution_loaded_with_resolved_string(self):
        """Happy path: ``_read_resolution`` returns "4000×3000" → receiver
        gets ``resolutionLoaded.emit(path, "4000×3000")``.

        Failure mode: an emit-with-None or shape-changed payload would
        break the slot's unpacking, leaving the info label permanently
        without a resolution row even when the read succeeded.
        """
        from unittest.mock import patch as _patch

        from app.views.image_tasks import _ResolutionTask

        receiver = MagicMock()
        task = _ResolutionTask(path="/photos/raw.dng", receiver=receiver)

        with _patch("app.views.preview_pane._read_resolution", return_value="4000×3000"), \
             _patch("app.views.media_utils.normalize_windows_path", side_effect=lambda p: p):
            task.run()

        receiver.resolutionLoaded.emit.assert_called_once_with(
            "/photos/raw.dng", "4000×3000"
        )

    def test_emits_empty_string_on_read_failure(self):
        """Read failure (None return) → emit empty string, not None.

        Failure mode: emitting None would surface as a Qt signal-arg type
        error (Signal(str, str) rejects None), suppressing the slot call
        entirely instead of letting it cleanly no-op.
        """
        from unittest.mock import patch as _patch

        from app.views.image_tasks import _ResolutionTask

        receiver = MagicMock()
        task = _ResolutionTask(path="/photos/corrupt.jpg", receiver=receiver)

        with _patch("app.views.preview_pane._read_resolution", return_value=None), \
             _patch("app.views.media_utils.normalize_windows_path", side_effect=lambda p: p):
            task.run()

        receiver.resolutionLoaded.emit.assert_called_once_with(
            "/photos/corrupt.jpg", ""
        )

    def test_swallows_read_exception_and_emits_empty(self):
        """An exception during the read (e.g. PIL choke on a truncated TIFF)
        is logged but does not propagate; the slot still gets an empty string.

        Failure mode: a raise would crash the QThreadPool worker thread,
        leaving the runner pool in an undefined state — and the info label
        would never update from "loading" because the slot is never called.
        """
        from unittest.mock import patch as _patch

        from app.views.image_tasks import _ResolutionTask

        receiver = MagicMock()
        task = _ResolutionTask(path="/bad.jpg", receiver=receiver)

        with _patch(
            "app.views.preview_pane._read_resolution",
            side_effect=RuntimeError("PIL.UnidentifiedImageError"),
        ), _patch("app.views.media_utils.normalize_windows_path", side_effect=lambda p: p):
            task.run()  # must not raise

        receiver.resolutionLoaded.emit.assert_called_once_with("/bad.jpg", "")


class TestRequestResolution:
    """ImageTaskRunner.request_resolution — dispatch + receiver-absent guard."""

    def test_no_receiver_is_silent_no_op(self):
        """Constructed runner has no resolution receiver until ``set_resolution_receiver``
        is called. Calling ``request_resolution`` before then must be a silent
        no-op.

        Failure mode: a missing guard would raise AttributeError on every
        early-init preview, breaking the show_single path during the brief
        window before PreviewPane self-registers.
        """
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_resolution("/a.jpg")

        runner._pool.start.assert_not_called()

    def test_dispatches_resolution_task_when_receiver_set(self):
        """Happy path: receiver registered → task created and started.

        Verifies the task is a ``_ResolutionTask`` with the correct path
        and receiver — not the path-unrelated dispatch shape from
        ``_ImageTask``.
        """
        from app.views.image_tasks import _ResolutionTask

        recv = MagicMock()
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()
        runner.set_resolution_receiver(recv)

        runner.request_resolution("/photos/x.dng")

        runner._pool.start.assert_called_once()
        task = runner._pool.start.call_args.args[0]
        assert isinstance(task, _ResolutionTask)
        assert task._path == "/photos/x.dng"
        assert task._receiver is recv

    def test_set_resolution_receiver_idempotent_last_wins(self):
        """Repeated set_resolution_receiver calls overwrite — last wins.

        Failure mode: the runner is a singleton-per-MainWindow, but the
        PreviewPane may be rebuilt on a live-language switch (see #520).
        A first-wins setter would leak the old receiver and never deliver
        resolutions to the new pane.
        """
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        first = MagicMock()
        second = MagicMock()

        runner.set_resolution_receiver(first)
        runner.set_resolution_receiver(second)

        assert runner._resolution_receiver is second


# ── #622 Phase 2 — coordinator handle wiring ─────────────────────────────


class _StubHandle:
    """Minimal stand-in for a coordinator RequestHandle."""

    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled
        self.finish_calls = 0

    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def finish(self) -> None:
        self.finish_calls += 1


class TestImageTaskCancelledHandle:
    """A cancelled request must deliver nothing and still free its device."""

    def test_cancelled_handle_does_not_emit(self):
        """The user navigated away mid-decode: do not paint the stale image.

        Real failure mode without this: the late image overwrites the preview
        of the file the user has since selected, so the pane shows one
        filename's metadata beside another photo's pixels.
        """
        service = MagicMock()
        service.get_preview.return_value = _make_jpeg()
        receiver = MagicMock()
        handle = _StubHandle()
        task = _ImageTask(
            path="a.jpg", side=512, is_preview=True, service=service,
            receiver=receiver, token="single|a.jpg|512", handle=handle,
        )

        handle.cancel()
        task.run()

        receiver.imageLoaded.emit.assert_not_called()
        assert handle.finish_calls == 1, "a cancelled task must still free its slot"

    def test_cancelled_during_the_decode_discards_the_finished_image(self):
        """Cancelled WHILE decoding → the finished image is thrown away.

        This is the branch that matters most, because it is the only one the
        coordinator cannot prevent: rawpy / Shell-WIC decodes are
        uninterruptible, so a click-away mid-decode always produces an image
        that nobody wants. Real failure mode if it were delivered: it paints
        over the file the user has since selected, so the pane shows one
        photo's pixels under another's metadata.

        The cancel fires from inside the service call — exactly when the real
        one arrives, rather than being staged before ``run()``.
        """
        handle = _StubHandle()
        service = MagicMock()

        def _decode_then_user_clicks_away(path, side):
            handle.cancel()
            return _make_jpeg()

        service.get_preview.side_effect = _decode_then_user_clicks_away
        receiver = MagicMock()
        task = _ImageTask(
            path="a.jpg", side=512, is_preview=True, service=service,
            receiver=receiver, token="single|a.jpg|512", handle=handle,
        )

        task.run()

        service.get_preview.assert_called_once()  # the decode did happen
        receiver.imageLoaded.emit.assert_not_called()  # but nothing was painted
        assert handle.finish_calls == 1

    def test_cancelled_before_start_skips_the_decode_entirely(self):
        """Cancelled before it ran → the service is never asked to decode.

        This is the read that the coordinator exists to avoid: on a NAS the
        decode IS the network transfer, so skipping the emit but doing the
        read would give up the whole benefit.
        """
        service = MagicMock()
        handle = _StubHandle(cancelled=True)
        task = _ImageTask(
            path="a.jpg", side=512, is_preview=True, service=service,
            receiver=MagicMock(), token="single|a.jpg|512", handle=handle,
        )

        task.run()

        service.get_preview.assert_not_called()
        assert handle.finish_calls == 1

    def test_handle_is_finished_even_when_the_decode_raises(self):
        """A decode that blows up must not wedge its device.

        Real failure mode: one corrupt file would stop every later preview on
        that drive, because its slot would never be released.
        """
        service = MagicMock()
        service.get_preview.side_effect = OSError("truncated file")
        handle = _StubHandle()
        task = _ImageTask(
            path="bad.jpg", side=512, is_preview=True, service=service,
            receiver=MagicMock(), token="single|bad.jpg|512", handle=handle,
        )

        task.run()

        assert handle.finish_calls == 1

    def test_prefetch_task_decodes_but_does_not_deliver(self):
        """``deliver=False`` warms the cache without painting anything.

        The 1-ahead prefetch has no widget waiting for it; emitting would push
        an image for a group that is not on screen through the pane's token
        routing for no reason.
        """
        service = MagicMock()
        service.get_thumbnail.return_value = _make_jpeg()
        receiver = MagicMock()
        handle = _StubHandle()
        task = _ImageTask(
            path="next.jpg", side=256, is_preview=False, service=service,
            receiver=receiver, token="grid|next.jpg|256", handle=handle,
            deliver=False,
        )

        task.run()

        service.get_thumbnail.assert_called_once_with("next.jpg", 256)
        receiver.imageLoaded.emit.assert_not_called()
        assert handle.finish_calls == 1


class TestRunnerCoordinatorRouting:
    """``ImageTaskRunner`` must route through the coordinator, not the pool."""

    def test_second_request_on_one_device_waits_for_the_first(self):
        """Two previews on the same device → only one task reaches the pool.

        Real failure mode this pins: a runner that called ``pool.start``
        directly would put both decodes on the NAS at once — the regression
        the coordinator exists to prevent, and one that no other test in this
        file would notice because both images would still eventually appear.
        """
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_grid_thumbnail("J:/nas/a.jpg", 256)
        runner.request_grid_thumbnail("J:/nas/b.jpg", 256)

        assert runner._pool.start.call_count == 1, (
            "both decodes were dispatched at once — per-device serialisation "
            "is not in the request path"
        )

    def test_finishing_the_first_releases_the_second(self):
        """The queued request starts once the first task finishes."""
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_grid_thumbnail("J:/nas/a.jpg", 256)
        runner.request_grid_thumbnail("J:/nas/b.jpg", 256)
        first_task = runner._pool.start.call_args.args[0]
        first_task._handle.finish()

        assert runner._pool.start.call_count == 2
        second_task = runner._pool.start.call_args.args[0]
        assert second_task._path == "J:/nas/b.jpg"

    def test_begin_selection_drops_the_queued_request(self):
        """A new selection cancels the previous one's queued decode."""
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_grid_thumbnail("J:/nas/a.jpg", 256)
        runner.request_grid_thumbnail("J:/nas/stale.jpg", 256)
        runner.begin_selection()
        runner.request_grid_thumbnail("J:/nas/fresh.jpg", 256)
        first_task = runner._pool.start.call_args.args[0]
        first_task._handle.finish()

        started = [
            call.args[0]._path for call in runner._pool.start.call_args_list
        ]
        assert started == ["J:/nas/a.jpg", "J:/nas/fresh.jpg"], (
            f"stale request decoded anyway: {started}"
        )

    def test_prefetch_is_suppressed_behind_a_user_request(self):
        """``request_prefetch`` never pre-empts a queued user request."""
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_grid_thumbnail("J:/nas/a.jpg", 256)
        runner.request_prefetch("J:/nas/next.jpg", 256)
        runner.request_single_preview("J:/nas/clicked.jpg")
        first_task = runner._pool.start.call_args.args[0]
        first_task._handle.finish()

        second_task = runner._pool.start.call_args.args[0]
        assert second_task._path == "J:/nas/clicked.jpg"
        assert second_task._deliver is True

    def test_prefetch_task_is_built_with_delivery_off(self):
        """A prefetch that does run must not deliver its image."""
        runner = ImageTaskRunner(service=MagicMock(), receiver=MagicMock())
        runner._pool = MagicMock()

        runner.request_prefetch("J:/nas/next.jpg", 256)

        task = runner._pool.start.call_args.args[0]
        assert task._deliver is False
        assert task._side == 256
        assert task._is_preview is False

    def test_service_none_prefetch_starts_nothing(self):
        runner = ImageTaskRunner(service=None, receiver=MagicMock())
        runner._pool = MagicMock()

        token = runner.request_prefetch("a.jpg", 128)

        assert token == "grid|a.jpg|128"
        runner._pool.start.assert_not_called()
