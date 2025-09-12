"""Sorting service for `PhotoGroup` collections.

The service performs multi-key sorting across records, handling None values and
per-key ascending/descending ordering without mutating original values.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.models import PhotoGroup, PhotoRecord


class SortService:
    """Provides sorting utilities for `PhotoGroup` lists."""

    def sort(self, groups: Iterable[PhotoGroup], sort_keys: list[tuple[str, bool]]) -> None:
        """Sorts items in each group in-place based on provided keys.

        Args:
            groups: Iterable of groups to sort.
            sort_keys: List of tuples (field_name, ascending).
        """

        if not sort_keys:
            return

        for group in groups:
            # Build a decorated list with adjusted values for per-key order
            decorated: list[tuple[tuple[Any, ...], PhotoRecord]] = []
            for item in group.items:
                row: list[Any] = []
                for field_name, ascending in sort_keys:
                    value = getattr(item, field_name, None)
                    if value is None:
                        value = 0 if isinstance(value, (int, float)) else ""
                    if isinstance(value, (int, float)):
                        row.append(value if ascending else -value)
                    else:
                        # For strings/others, embed a leading flag to control order
                        row.append((0, str(value)) if ascending else (1, str(value)))
                decorated.append((tuple(row), item))

            decorated.sort(key=lambda x: x[0])
            group.items = [it for _, it in decorated]
