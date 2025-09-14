"""Core service interfaces and shared data structures.

This module defines simple dataclasses that represent delete planning
and results used across the infrastructure and UI layers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeleteResult:
    """Outcome of a delete operation.

    Attributes:
        success_paths: Paths successfully deleted.
        failed: Tuples of (path, reason) for failures.
        log_path: Optional path to a detailed log file.
    """

    success_paths: list[str]
    failed: list[tuple[str, str]]
    log_path: str | None = None


@dataclass
class DeletePlanGroupSummary:
    """Summary of delete intent for a single group.

    Attributes:
        group_number: Identifier of the group.
        selected_count: Number of selected items in the group.
        total_count: Total items in the group.
        is_full_delete: Whether all items in the group are selected.
    """

    group_number: int
    selected_count: int
    total_count: int
    is_full_delete: bool


@dataclass
class DeletePlan:
    """Planned delete operation with per-group summaries.

    Attributes:
        delete_paths: Paths chosen for deletion (already filtered to skip locked).
        group_summaries: Group-level summaries for confirmation UI.
    """

    delete_paths: list[str]
    group_summaries: list[DeletePlanGroupSummary]


@dataclass
class RemoveResult:
    """Outcome of a remove operation.

    Attributes:
        success_paths: Paths successfully removed from the list.
        failed: Tuples of (path, reason) for failures.
    """

    success_paths: list[str]
    failed: list[tuple[str, str]]


class IListService:
    """Interface for list management services."""

    def remove_from_list(self, paths: list[str]) -> RemoveResult:
        """Remove specified paths from the list without deleting actual files."""
        raise NotImplementedError
