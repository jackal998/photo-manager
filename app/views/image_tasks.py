from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool
from loguru import logger

from app.views.image_tasks_helpers import make_grid_token, make_single_token

# Cached viewport cap — computed once on first call, then reused.
_VIEWPORT_CAP: int | None = None


def _compute_viewport_cap() -> int:
    """Return the viewport cap for single-image previews.

    Bounded to min(2048, primary-screen width). Falls back to 2048 when no
    screen is available (e.g. headless test runs). Cached at module level after
    the first successful probe so repeated requests are O(1).
    """
    global _VIEWPORT_CAP
    if _VIEWPORT_CAP is not None:
        return _VIEWPORT_CAP
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            _VIEWPORT_CAP = min(2048, screen.geometry().width())
            return _VIEWPORT_CAP
    except Exception:
        pass
    _VIEWPORT_CAP = 2048
    return _VIEWPORT_CAP


class _ImageTask(QRunnable):
    """QRunnable for background image loading.

    Emits `receiver.imageLoaded(token, path, image)` upon completion. The
    receiver is expected to own a Qt `Signal(str, str, object)` named
    `imageLoaded`.
    """

    def __init__(
        self, *, path: str, side: int, is_preview: bool, service: Any, receiver: QObject, token: str
    ) -> None:
        super().__init__()
        self._path = path
        self._side = side
        self._is_preview = is_preview
        self._service = service
        self._receiver = receiver
        self._token = token

    def run(self) -> None:  # type: ignore[override]
        try:
            if self._is_preview:
                img = self._service.get_preview(self._path, self._side)
            else:
                img = self._service.get_thumbnail(self._path, self._side)
        except Exception as ex:
            logger.error("Image task failed: {}", ex)
            img = None
        try:
            # Forward to main receiver (MainWindow) to keep the signal path
            self._receiver.imageLoaded.emit(self._token, self._path, img)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - best effort
            pass


class _ResolutionTask(QRunnable):
    """QRunnable for off-thread image resolution reads (#622 Phase 1).

    Emits ``receiver.resolutionLoaded(path, resolution_str)`` upon completion.
    The receiver is expected to own a Qt ``Signal(str, str)`` named
    ``resolutionLoaded``. Failed reads emit an empty string rather than
    raising — matches the pre-async ``_read_resolution`` returning None,
    so the info-label silently stays without a resolution row rather than
    surfacing a parser failure to the user.

    The ``_read_resolution`` import is lazy because ``preview_pane`` already
    imports ``image_tasks`` at module load; a top-level import here would
    cycle. Inside ``run()`` the cycle is harmless — both modules are fully
    loaded by the time any QRunnable executes.
    """

    def __init__(self, *, path: str, receiver: QObject) -> None:
        super().__init__()
        self._path = path
        self._receiver = receiver

    def run(self) -> None:  # type: ignore[override]
        try:
            from app.views.media_utils import normalize_windows_path
            from app.views.preview_pane import _read_resolution
            res = _read_resolution(normalize_windows_path(self._path))
        except Exception as ex:
            logger.error("Resolution task failed: {}", ex)
            res = None
        try:
            self._receiver.resolutionLoaded.emit(  # type: ignore[attr-defined]
                self._path, res or ""
            )
        except Exception:  # pragma: no cover - best effort
            pass


class ImageTaskRunner:
    """Dispatches image load tasks to the global thread pool.

    Tokens use the canonical format defined in
    :mod:`app.views.image_tasks_helpers`. Their classification on the
    receiver side lives in :func:`app.views.preview_pane_helpers.classify_image_token`.
    """

    def __init__(self, *, service: Any, receiver: QObject) -> None:
        self._service = service
        self._receiver = receiver
        # Receiver for ``_ResolutionTask`` results. Set later via
        # ``set_resolution_receiver`` — PreviewPane is constructed AFTER
        # the runner, so it self-registers in its ``__init__``.
        self._resolution_receiver: QObject | None = None
        self._pool = QThreadPool.globalInstance()

    def set_resolution_receiver(self, receiver: QObject) -> None:
        """Register the receiver for off-thread resolution reads.

        PreviewPane owns the ``resolutionLoaded(str, str)`` signal that
        ``_ResolutionTask`` emits on; the runner has no reason to relay
        through ``self._receiver`` (MainWindow) since resolution updates
        are pane-local. Idempotent — last registration wins.
        """
        self._resolution_receiver = receiver

    def request_resolution(self, path: str) -> None:
        """Submit an off-thread resolution read.

        Delivery happens via the registered receiver's ``resolutionLoaded``
        signal. Silent no-op when no resolution receiver is wired —
        constructor-time test paths exercise the runner standalone, and
        the pre-PreviewPane init window has no receiver yet.
        """
        recv = self._resolution_receiver
        if recv is None:
            return
        task = _ResolutionTask(path=path, receiver=recv)
        self._pool.start(task)

    def request_single_preview(self, path: str) -> str:
        """Request a single-image preview. Returns the token string."""
        side = _compute_viewport_cap()
        token = make_single_token(path, side)
        if self._service is None:
            return token
        task = _ImageTask(
            path=path,
            side=side,
            is_preview=True,
            service=self._service,
            receiver=self._receiver,
            token=token,
        )
        self._pool.start(task)
        return token

    def request_grid_thumbnail(self, path: str, thumb_side: int) -> str:
        """Request a grid thumbnail for `path` with given `thumb_side`. Returns token."""
        token = make_grid_token(path, thumb_side)
        if self._service is None:
            return token
        task = _ImageTask(
            path=path,
            side=thumb_side,
            is_preview=False,
            service=self._service,
            receiver=self._receiver,
            token=token,
        )
        self._pool.start(task)
        return token
