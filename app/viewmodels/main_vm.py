"""ViewModel for grouping and sorting photo records loaded from a manifest."""

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
        sorter: SortService | None = None,
        default_sort: list[tuple[str, bool]] | None = None,
    ) -> None:
        self._sorter = sorter or SortService()
        self._default_sort = default_sort or []
        self.groups: list[PhotoGroup] = []
        # Accumulates paths the user has marked "remove from list" during
        # this manifest session. Lives on the VM (not the dialog) so the
        # Execute Action dialog can pick up paths that were removed via
        # the main window's right-click flow before opening, and so a
        # manifest reload starts with a clean slate.
        self.removed_from_list_paths: list[str] = []

    def load_from_repo(self, repo, path: str) -> None:
        """Load from a repository (e.g. ManifestRepository).

        Args:
            repo: Repository with a `load(path)` method.
            path: Path to the manifest file.
        """
        self._manifest_path = path
        items: list[PhotoRecord] = list(repo.load(path))
        self._group_records(items)
        # Reset removed-from-list bookkeeping on every load — carrying
        # the previous session's removals into a freshly-loaded manifest
        # would silently filter rows the user hasn't seen yet.
        self.removed_from_list_paths = []

    def _group_records(self, items: list[PhotoRecord]) -> None:
        grouped: dict[int, list[PhotoRecord]] = defaultdict(list)
        for item in items:
            grouped[item.group_number].append(item)
        self.groups = [PhotoGroup(group_number=k, items=v) for k, v in sorted(grouped.items())]
        # Compose the within-group sort: score-DESC is the design default
        # for #187 ("highest-quality copy at the top of each group"). User-
        # configured ``sorting.defaults`` from settings.json act as
        # secondary tiebreakers. If the user has explicitly set ``score``
        # as one of their default sorts (any direction), respect that
        # choice and skip the implicit prepend.
        sort_keys: list[tuple[str, bool]] = list(self._default_sort)
        if not any(field == "score" for field, _ in sort_keys):
            sort_keys = [("score", False)] + sort_keys
        if sort_keys:
            self._sorter.sort(self.groups, sort_keys)

    def remove_deleted_and_prune(
        self, deleted_paths: list[str], prune_singles: bool = True
    ) -> None:
        """Remove deleted items and optionally drop groups with exactly 1 item left.

        prune_singles=True  — pairs collapse to nothing after one file is deleted.
        prune_singles=False — single-item groups persist (KEEP / UNDATED / "").
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

    @property
    def pending_decision_count(self) -> int:
        """Number of records with a non-default ``user_decision``.

        Used by the re-scan confirmation flow (#142) to detect when a
        user has acted on the loaded manifest and should be warned
        before a re-scan replaces it. ``user_decision`` is empty string
        by default (see ``core.models.PhotoRecord``); any non-empty
        value (``delete`` / ``keep`` / etc.) means the user has acted.
        """
        return sum(
            1
            for group in self.groups
            for record in group.items
            if record.user_decision
        )
