"""ManifestLoadWorker — loads a migration_manifest.sqlite in a background QThread.

Signals:
    progress(str)            — human-readable status update (shown in status bar)
    finished(list)           — list[PhotoGroup] on success
    failed(str)              — error message on failure

Usage::

    worker = ManifestLoadWorker(path, default_sort)
    worker.progress.connect(status_bar.showMessage)
    worker.finished.connect(self._on_manifest_loaded)
    worker.failed.connect(self._on_manifest_failed)
    worker.start()
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QThread, Signal

from loguru import logger


class ManifestLoadWorker(QThread):
    """Loads all rows from a manifest SQLite in a background thread.

    Emits ``finished`` with a ``list[PhotoGroup]`` when loading succeeds, or
    ``failed`` with an error string if an exception occurs.
    """

    progress = Signal(str)      # one-line status for the status bar
    finished = Signal(list)     # list[PhotoGroup]
    failed = Signal(str)        # error message

    def __init__(
        self,
        path: str,
        default_sort: list,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._default_sort = default_sort

    def run(self) -> None:
        try:
            self._load()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("ManifestLoadWorker failed: {}", exc)
            self.failed.emit(str(exc))

    def _load(self) -> None:
        from infrastructure.manifest_repository import ManifestRepository
        from core.models import PhotoGroup, PhotoRecord
        from core.services.sort_service import SortService

        self.progress.emit("Loading manifest…")

        repo = ManifestRepository()
        items: list[PhotoRecord] = list(repo.load(self._path))

        self.progress.emit(f"Grouping {len(items):,} records…")

        grouped: dict[int, list[PhotoRecord]] = defaultdict(list)
        for item in items:
            grouped[item.group_number].append(item)
        groups = [
            PhotoGroup(group_number=k, items=v) for k, v in sorted(grouped.items())
        ]

        if self._default_sort:
            SortService().sort(groups, self._default_sort)

        self.progress.emit(f"Loaded {len(groups):,} group(s).")
        self.finished.emit(groups)
