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
        items: list[PhotoRecord] = list(self._repo.load(path))
        self._source_csv_path = path
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

    def remove_deleted_and_prune(self, deleted_paths: list[str]) -> None:
        """Remove deleted items and drop groups with <= 1 item remaining."""
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

        # Log before removal
        groups_before = len(self.groups)
        group_numbers_before = [g.group_number for g in self.groups]
        logger.info(
            "Before removal - Total groups: {}, Group numbers: {}",
            groups_before,
            group_numbers_before,
        )
        logger.info("Attempting to remove group: {}", group_number)

        # Find and log the group being removed
        group_to_remove = None
        for g in self.groups:
            if g.group_number == group_number:
                group_to_remove = g
                break

        if group_to_remove:
            logger.info(
                "Found group {} with {} files to remove",
                group_number,
                len(group_to_remove.items),
            )
        else:
            logger.warning("Group {} not found in current groups", group_number)

        # Perform removal
        self.groups = [g for g in self.groups if g.group_number != group_number]

        # Log after removal
        groups_after = len(self.groups)
        group_numbers_after = [g.group_number for g in self.groups]
        logger.info(
            "After removal - Total groups: {}, Group numbers: {}",
            groups_after,
            group_numbers_after,
        )

    def get_highlighted_items(self) -> list[str]:
        """Get file paths of currently highlighted (selected) items in the UI."""
        # This will be called from the UI to get currently selected items
        return []

    @property
    def group_count(self) -> int:
        """Number of groups currently loaded."""
        return len(self.groups)
