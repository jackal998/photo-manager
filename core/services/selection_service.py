"""Regex-based selection service decoupled from any UI toolkit.

The service operates on a simple accessor protocol so that views can
provide adapters for their model types (e.g., Qt or others).
"""

from __future__ import annotations

import re
from typing import Protocol


class _ModelAccessor(Protocol):
    """Abstracts the traversal of the UI model.

    Implementations should provide iteration over groups and children and
    read/write accessors for the fields and check-state.
    """

    def iter_groups(self) -> list[object]:
        """Return top-level group objects in view order."""
        raise NotImplementedError

    def iter_children(self, group: object) -> list[object]:
        """Return child row identifiers for the given group."""
        raise NotImplementedError

    def get_field_text(self, group: object, child: object | None, field_name: str) -> str:
        """Return display text for the given field at (group, child)."""
        raise NotImplementedError

    def set_checked(self, group: object, child: object, checked: bool) -> None:
        """Set the check state for (group, child)."""
        raise NotImplementedError


class RegexSelectionService:
    """Apply selection/unselection on a model via regular expressions.

    This mirrors the existing UI behavior without coupling to Qt types.
    """

    def __init__(self, accessor: _ModelAccessor) -> None:
        self._acc = accessor

    def apply(self, field: str, regex: str, select: bool) -> None:
        """Apply selection for rows whose target field matches `regex`.

        Args:
            field: Field name to inspect (e.g., "File Name").
            regex: Regular expression to match.
            select: If True, set to checked; otherwise uncheck.
        """
        rx = re.compile(regex)

        for group in self._acc.iter_groups():
            if field == "Group":
                group_text = self._acc.get_field_text(group, None, field)
                if rx.search(group_text or ""):
                    for child in self._acc.iter_children(group):
                        self._acc.set_checked(group, child, select)
                continue

            for child in self._acc.iter_children(group):
                target_text = self._acc.get_field_text(group, child, field) or ""
                if target_text and rx.search(target_text):
                    self._acc.set_checked(group, child, select)
