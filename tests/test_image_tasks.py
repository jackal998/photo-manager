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
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.views.image_tasks import ImageTaskRunner, _ImageTask


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
        """The signal carries (token, path, img). Failure mode: a
        refactor that reordered the emit args would break the
        slot's ``(token, path, img)`` unpacking and every preview
        would render at the wrong path."""
        service = MagicMock()
        service.get_preview.return_value = "IMG"
        receiver = MagicMock()
        task = _ImageTask(
            path="a.jpg", side=0, is_preview=True,
            service=service, receiver=receiver, token="single|a.jpg|0",
        )

        task.run()

        receiver.imageLoaded.emit.assert_called_once_with(
            "single|a.jpg|0", "a.jpg", "IMG"
        )


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
        service = MagicMock()
        service.get_thumbnail.return_value = "T"
        receiver = MagicMock()
        task = _ImageTask(
            path="p.jpg", side=128, is_preview=False,
            service=service, receiver=receiver, token="grid|p.jpg|128",
        )

        task.run()

        receiver.imageLoaded.emit.assert_called_once_with(
            "grid|p.jpg|128", "p.jpg", "T"
        )


class TestImageTaskRunServiceFailure:
    """When the service raises, the task still emits — with img=None."""

    def test_exception_in_get_preview_emits_none(self):
        """The named real failure mode: a corrupt JPEG (or PIL chokes
        on a HEIC variant) raises during decode. The task must still
        fire the signal with ``img=None`` so the preview pane can
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

        # The signal still fires with img=None
        receiver.imageLoaded.emit.assert_called_once_with(
            "single|bad.jpg|0", "bad.jpg", None
        )

    def test_exception_in_get_thumbnail_emits_none(self):
        """Same contract for the thumbnail branch."""
        service = MagicMock()
        service.get_thumbnail.side_effect = OSError("file truncated")
        receiver = MagicMock()
        task = _ImageTask(
            path="bad.jpg", side=128, is_preview=False,
            service=service, receiver=receiver, token="grid|bad.jpg|128",
        )

        task.run()

        receiver.imageLoaded.emit.assert_called_once_with(
            "grid|bad.jpg|128", "bad.jpg", None
        )


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
