from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool
from loguru import logger


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
        except Exception as ex:  # pragma: no cover - GUI background task
            logger.error("Image task failed: {}", ex)
            img = None
        try:
            # Forward to main receiver (MainWindow) to keep the signal path
            self._receiver.imageLoaded.emit(self._token, self._path, img)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - best effort
            pass


class ImageTaskRunner:
    """Dispatches image load tasks to the global thread pool.

    Tokens must keep the original format:
    - Single preview: "single|{path}|{side}"
    - Grid thumbnail: "grid|{path}|{thumb_side}"
    """

    def __init__(self, *, service: Any, receiver: QObject) -> None:
        self._service = service
        self._receiver = receiver
        self._pool = QThreadPool.globalInstance()

    def request_single_preview(self, path: str) -> str:
        """Request a single-image preview. Returns the token string."""
        side = 0
        token = f"single|{path}|{side}"
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
        token = f"grid|{path}|{thumb_side}"
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
