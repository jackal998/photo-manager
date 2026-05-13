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

        # Make ``groups`` materialisable: callers pass a list, but the
        # ``Iterable`` annotation allows generators which we'd otherwise
        # exhaust during type detection and have nothing left to sort.
        groups_list = list(groups)

        # Detect each sort field's type from the first non-None value seen
        # across all items. None values are then substituted with the
        # type-appropriate zero (numeric → 0, string → "") so a field with
        # mixed None and non-None values (e.g. ``score`` on Live Photo
        # MOV passengers + scored rows in the same group) sorts without
        # the float-vs-tuple TypeError that the previous implementation
        # produced. See #187 PR 5.
        field_is_numeric: dict[str, bool] = {}
        for field_name, _ in sort_keys:
            for group in groups_list:
                for item in group.items:
                    v = getattr(item, field_name, None)
                    if v is not None:
                        field_is_numeric[field_name] = isinstance(v, (int, float))
                        break
                if field_name in field_is_numeric:
                    break
            # All values None for this field → treat as numeric (0
            # default keeps the sort deterministic).
            field_is_numeric.setdefault(field_name, True)

        for group in groups_list:
            # Build a decorated list with adjusted values for per-key order
            decorated: list[tuple[tuple[Any, ...], PhotoRecord]] = []
            for item in group.items:
                row: list[Any] = []
                for field_name, ascending in sort_keys:
                    value = getattr(item, field_name, None)
                    if value is None:
                        value = 0 if field_is_numeric[field_name] else ""
                    if isinstance(value, (int, float)):
                        row.append(value if ascending else -value)
                    else:
                        # For strings/others, embed a leading flag to control order
                        row.append((0, str(value)) if ascending else (1, str(value)))
                decorated.append((tuple(row), item))

            decorated.sort(key=lambda x: x[0])
            group.items = [it for _, it in decorated]
