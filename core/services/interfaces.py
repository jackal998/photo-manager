from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Any

from core.models import PhotoRecord, PhotoGroup


class IPhotoRepository(Protocol):
    def load(self, csv_path: str) -> Iterable[PhotoRecord]:
        ...

    def save(self, csv_path: str, groups: Iterable[PhotoGroup]) -> None:
        ...


class IImageService(Protocol):
    def get_thumbnail(self, path: str, size: int) -> Any:
        ...

    def get_preview(self, path: str, max_side: int) -> Any:
        ...


@dataclass
class DeleteResult:
    success_paths: list[str]
    failed: list[tuple[str, str]]  # (path, reason)


class IDeleteService(Protocol):
    def delete_to_recycle(self, paths: list[str]) -> DeleteResult:
        ...


class IRuleService(Protocol):
    def execute(self, groups: Iterable[PhotoGroup], rule: dict) -> Any:
        ...


class ISortService(Protocol):
    def sort(self, groups: Iterable[PhotoGroup], sort_keys: list[tuple[str, bool]]) -> None:
        ...


class ISettings(Protocol):
    def get(self, key: str, default: Any | None = None) -> Any:
        ...


class IUndoRedoService(Protocol):
    def push(self, command: Any) -> None:
        ...

    def undo(self) -> None:
        ...

    def redo(self) -> None:
        ...
