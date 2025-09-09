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
        self._source_csv_path: Optional[str] = None

    def load_csv(self, path: str) -> None:
        items: list[PhotoRecord] = list(self._repo.load(path))
        self._source_csv_path = path
        grouped: dict[int, list[PhotoRecord]] = defaultdict(list)
        for item in items:
            grouped[item.group_number].append(item)
        self.groups = [PhotoGroup(group_number=k, items=v) for k, v in sorted(grouped.items())]
        if self._default_sort:
            self._sorter.sort(self.groups, self._default_sort)

    def export_csv(self, path: str) -> None:
        self._repo.save(path, self.groups)

    def get_source_csv_path(self) -> Optional[str]:
        return self._source_csv_path

    def remove_deleted_and_prune(self, deleted_paths: list[str]) -> None:
        if not deleted_paths:
            return
        removed = set(deleted_paths)
        new_groups: list[PhotoGroup] = []
        for g in self.groups:
            kept_items = [it for it in g.items if it.file_path not in removed]
            if not kept_items:
                # Entire group removed -> drop
                continue
            # If group has only one file left, drop the group from the main list per request
            if len(kept_items) == 1:
                continue
            new_groups.append(PhotoGroup(group_number=g.group_number, items=kept_items, is_expanded=g.is_expanded))
        self.groups = new_groups

    @property
    def group_count(self) -> int:
        return len(self.groups)
