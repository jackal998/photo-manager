from __future__ import annotations

from typing import Protocol, Callable, Optional
import re


class _ModelAccessor(Protocol):
    """Abstracts the traversal of the UI model.

    Implementations should provide iteration over groups and children and
    read/write accessors for the fields and check-state.
    """

    def iter_groups(self) -> list[object]:
        ...

    def iter_children(self, group: object) -> list[object]:
        ...

    def get_field_text(self, group: object, child: Optional[object], field_name: str) -> str:
        ...

    def set_checked(self, group: object, child: object, checked: bool) -> None:
        ...


class RegexSelectionService:
    """UI-agnostic selection applier that relies on a model accessor.

    This mirrors the existing UI behavior without coupling to Qt types.
    """

    def __init__(self, accessor: _ModelAccessor) -> None:
        self._acc = accessor

    def apply(self, field: str, regex: str, select: bool) -> None:
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


