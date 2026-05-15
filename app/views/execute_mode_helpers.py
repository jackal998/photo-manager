"""Pure-logic helpers for the #165 Execute Mode prototype.

Kept in a sibling module (rather than inside ``main_window.py``) so
layer-1 tests can import the helpers without cascade-loading the
whole QMainWindow view stack — importing ``main_window`` transitively
pulls in ``PreviewPane``, ``ImageTaskRunner``, ``DialogHandler``,
``GroupMediaController``, and ``VideoPlayer``, which together carry
~1000 statements that have no layer-1 coverage and would drag the
global coverage gate below threshold.
"""

from __future__ import annotations


def complete_delete_group_numbers(groups: list) -> list[int]:
    """Return the group_numbers whose every file row is decided ``delete``.

    Lifted as a pure helper from
    ``ExecuteActionDialog._complete_delete_groups`` so the
    Execute-mode banner can recompute the same value without
    instantiating the dialog. Empty groups (no items) are skipped —
    they wouldn't trigger a destructive op anyway and would otherwise
    register as "all items deleted" against an empty set.
    """
    result: list[int] = []
    for group in groups or []:
        items = getattr(group, "items", [])
        if not items:
            continue
        if all(getattr(rec, "user_decision", "") == "delete" for rec in items):
            result.append(int(getattr(group, "group_number", 0)))
    return sorted(result)
