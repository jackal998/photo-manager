"""ViewModel for orchestrating CSV IO and grouping/sorting logic."""

from __future__ import annotations

from collections import defaultdict

from loguru import logger

from core.models import PhotoGroup, PhotoRecord
from core.services.sort_service import SortService


class MainVM:
    """Main application view-model.

    Mediates between a repository providing `PhotoRecord` rows and UI models.
    """

    def __init__(
        self,
        repo,
        sorter: SortService | None = None,
        default_sort: list[tuple[str, bool]] | None = None,
    ) -> None:
        """Create a MainVM.

        Args:
            repo: Repository with `load(path)` and `save(path, groups)` methods.
            sorter: Sorting service (defaults to `SortService`).
            default_sort: List of (field_name, ascending) used after load.
        """
        self._repo = repo
        self._sorter = sorter or SortService()
        self._default_sort = default_sort or []
        self.groups: list[PhotoGroup] = []
        self._source_csv_path: str | None = None

    def load_csv(self, path: str) -> None:
        """Load CSV `path`, group records, and apply `default_sort` if provided."""
        self._manifest_path = None
        items: list[PhotoRecord] = list(self._repo.load(path))
        self._source_csv_path = path
        self._group_records(items)

    def load_from_repo(self, repo, path: str) -> None:
        """Load from an arbitrary repository (e.g. ManifestRepository).

        Use this when the source is not a CSV (e.g. migration_manifest.sqlite).
        """
        self._source_csv_path = None
        self._manifest_path = path
        items: list[PhotoRecord] = list(repo.load(path))
        self._group_records(items)

    def _group_records(self, items: list[PhotoRecord]) -> None:
        grouped: dict[int, list[PhotoRecord]] = defaultdict(list)
        for item in items:
            grouped[item.group_number].append(item)
        self.groups = [PhotoGroup(group_number=k, items=v) for k, v in sorted(grouped.items())]
        if self._default_sort:
            self._sorter.sort(self.groups, self._default_sort)

    def export_csv(self, path: str) -> None:
        """Export current groups to CSV at `path`."""
        self._repo.save(path, self.groups)

    def get_source_csv_path(self) -> str | None:
        """Return the last-loaded CSV path, if available."""
        return self._source_csv_path

    def remove_deleted_and_prune(
        self, deleted_paths: list[str], prune_singles: bool = True
    ) -> None:
        """Remove deleted items and optionally drop groups with exactly 1 item left.

        prune_singles=True  (default) — CSV workflow: pairs collapse to nothing.
        prune_singles=False — manifest workflow: single-item groups must persist
                              (KEEP / UNDATED / MOVE rows are single-item by design).
        """
        if not deleted_paths:
            return
        removed = set(deleted_paths)
        new_groups: list[PhotoGroup] = []
        for g in self.groups:
            kept_items = [it for it in g.items if it.file_path not in removed]
            if not kept_items:
                continue
            if prune_singles and len(kept_items) == 1:
                continue
            new_groups.append(
                PhotoGroup(group_number=g.group_number, items=kept_items, is_expanded=g.is_expanded)
            )
        self.groups = new_groups

    def remove_from_list(self, paths_to_remove: list[str]) -> None:
        """Remove specified items from the list without deleting actual files."""
        if not paths_to_remove:
            return
        removed = set(paths_to_remove)
        new_groups: list[PhotoGroup] = []
        for g in self.groups:
            kept_items = [it for it in g.items if it.file_path not in removed]
            if kept_items:
                new_groups.append(
                    PhotoGroup(
                        group_number=g.group_number, items=kept_items, is_expanded=g.is_expanded
                    )
                )
        self.groups = new_groups

    def remove_group_from_list(self, group_number: int) -> None:
        """Remove an entire group from the list without deleting actual files."""
        self.groups = [g for g in self.groups if g.group_number != group_number]

    def get_highlighted_items(self) -> list[str]:
        """Get file paths of currently highlighted (selected) items in the UI."""
        # This will be called from the UI to get currently selected items
        return []

    def update_marks_from_checked_paths(self, checked_paths: list[str]) -> None:
        """Set `is_mark` on records based on currently checked file paths.

        Args:
            checked_paths: List of file paths that are checked in the UI
        """
        checked: set[str] = set(checked_paths or [])
        for group in self.groups:
            for rec in group.items:
                rec.is_mark = rec.file_path in checked

    @property
    def group_count(self) -> int:
        """Number of groups currently loaded."""
        return len(self.groups)
