"""Sorting service for `PhotoGroup` collections.

The service performs multi-key sorting across records, handling None values and
per-key ascending/descending ordering without mutating original values.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.models import PhotoGroup


def _sort_value(value: Any, numeric: bool) -> Any:
    """Comparable sort key for one field value.

    None is substituted with the type-appropriate zero (numeric → 0,
    string/other → "") so a field with mixed None and non-None values sorts
    without a float-vs-tuple ``TypeError``. Direction is applied by the
    caller via ``list.sort(reverse=...)`` — this returns the natural-order
    key only.
    """
    if value is None:
        return 0 if numeric else ""
    return value if numeric else str(value)


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
            # Stable multi-key sort: apply the keys from lowest to highest
            # priority (right to left), each in its own direction. Python's
            # sort is stable, so a higher-priority key applied later preserves
            # the order a lower-priority key already established within its
            # ties — yielding the same lexicographic result as one combined
            # key. This replaces the old decorated-tuple hack, which could not
            # invert a STRING field for a descending sort: it embedded a
            # constant leading flag and left the string comparison ascending,
            # so descending-on-a-string silently produced ascending (#791).
            items = list(group.items)
            for field_name, ascending in reversed(sort_keys):
                numeric = field_is_numeric[field_name]
                items.sort(
                    key=lambda it, fn=field_name, num=numeric: _sort_value(
                        getattr(it, fn, None), num
                    ),
                    reverse=not ascending,
                )
            group.items = items
