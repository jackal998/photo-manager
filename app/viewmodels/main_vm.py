from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from core.models import PhotoRecord, PhotoGroup
from core.services.interfaces import IPhotoRepository
from core.services.sort_service import SortService


class MainVM:
    def __init__(self, repo: IPhotoRepository, sorter: Optional[SortService] = None, default_sort: Optional[list[tuple[str, bool]]] = None) -> None:
        self._repo = repo
        self._sorter = sorter or SortService()
        self._default_sort = default_sort or []
        self.groups: list[PhotoGroup] = []

    def load_csv(self, path: str) -> None:
        items: list[PhotoRecord] = list(self._repo.load(path))
        grouped: dict[int, list[PhotoRecord]] = defaultdict(list)
        for item in items:
            grouped[item.group_number].append(item)
        self.groups = [PhotoGroup(group_number=k, items=v) for k, v in sorted(grouped.items())]
        if self._default_sort:
            self._sorter.sort(self.groups, self._default_sort)

    def export_csv(self, path: str) -> None:
        self._repo.save(path, self.groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)
