"""Pure-logic helpers extracted from
:class:`app.views.handlers.dialog_handler.DialogHandler`.

Extracted so the load-bearing decision logic (clicked-column →
initial-field lookup, canonical dialog field list, per-row column
unpacking, safe records-provider invocation) is unit-testable
against plain Python without cascade-importing the Qt dialog
stack.

Same extraction pattern previously used by ``action_handlers.py``
(#182), ``status_reporter_impl.py`` (#138, #140), ``empty_state.py``
(#137), ``main_window_helpers.py`` (#185 / #283),
``group_media_controller_helpers.py`` (#185 / #285), and
``preview_pane_helpers.py`` (#185 / #289).

What lives here:

* :data:`COL_TO_FIELD` — tree column index → dialog field name.
* :func:`resolve_initial_field` — wraps the dict lookup with
  ``None``-safe entry.
* :func:`default_action_dialog_fields` — the canonical fields tuple
  the ``ActionDialog`` dropdown shows. Keeping it here means the
  list isn't rebuilt at every dialog open and is unit-testable
  for drift against the result-tree columns (paired with
  ``test_probe_select_dialog_exposes_every_filterable_tree_column``).
* :data:`CHILD_ROW_FIELDS` / :data:`GROUP_ROW_FIELDS` /
  :data:`TOP_ROW_FIELDS` — the (label, column) pairs the highlighted-
  row dict is assembled from. Three tables, three contexts:
  child row pulls cells from the child's own row + the parent group;
  top-level row only has the group cells.
* :func:`dict_from_pairs` — assemble a label→value dict by calling
  a ``data_getter`` callable per column. The whole point of the
  extraction is that this function knows nothing about
  ``QModelIndex`` — the caller closes over the model walk.
* :func:`safe_call_records_provider` — invoke the
  ``records_provider`` callback, returning ``[]`` on any failure or
  ``None`` return. The records-provider plumbing is the load-bearing
  contract that #237 closed (numeric-condition panel reachability);
  this helper pins the "any provider error → no groups, dialog still
  opens" guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP,
    COL_GROUP_COUNT,
    COL_NAME,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
)


# Maps tree column index → dialog field name. Used to pre-select
# the dropdown when the user opens the dialog by right-clicking a
# specific column header.
COL_TO_FIELD: dict[int, str] = {
    COL_GROUP:         "Similarity",
    COL_ACTION:        "Action",
    COL_NAME:          "File Name",
    COL_FOLDER:        "Folder",
    COL_SIZE_BYTES:    "Size (Bytes)",
    COL_GROUP_COUNT:   "Group Count",
    COL_CREATION_DATE: "Creation Date",
    COL_SHOT_DATE:     "Shot Date",
}


# (label, column-index) pairs assembled into the values dict the
# ActionDialog reads to pre-populate the field combo. Three tables:
#   * CHILD_ROW_FIELDS — fields the user can edit on a single file.
#     Pulled from the child row itself.
#   * GROUP_ROW_FIELDS — fields that describe the parent group;
#     pulled from the parent index regardless of which child the
#     user clicked.
#   * TOP_ROW_FIELDS — when a top-level (group) row is highlighted,
#     only group-level fields make sense.
CHILD_ROW_FIELDS: tuple[tuple[str, int], ...] = (
    ("Action",        COL_ACTION),
    ("File Name",     COL_NAME),
    ("Folder",        COL_FOLDER),
    ("Size (Bytes)",  COL_SIZE_BYTES),
    ("Creation Date", COL_CREATION_DATE),
    ("Shot Date",     COL_SHOT_DATE),
)

GROUP_ROW_FIELDS: tuple[tuple[str, int], ...] = (
    ("Similarity",  COL_GROUP),
    ("Group Count", COL_GROUP_COUNT),
)

TOP_ROW_FIELDS: tuple[tuple[str, int], ...] = (
    ("Similarity",  COL_GROUP),
    ("Group Count", COL_GROUP_COUNT),
)


def resolve_initial_field(clicked_col: int | None) -> str | None:
    """Return the dialog field name that corresponds to ``clicked_col``,
    or ``None`` if no column was clicked or the column has no
    matching field.

    Failure mode: a refactor that drops the ``None``-check would
    raise ``KeyError`` on the menu route (no clicked column) — the
    dialog wouldn't open at all.
    """
    if clicked_col is None:
        return None
    return COL_TO_FIELD.get(clicked_col)


def default_action_dialog_fields() -> tuple[str, ...]:
    """Canonical field list the ``ActionDialog`` dropdown shows.

    Order matters: matches the result-tree column order so the
    dropdown reads top-to-bottom the same way the user scans the
    tree. The probe
    ``test_probe_select_dialog_exposes_every_filterable_tree_column``
    pins the cross-source invariant (every filterable column has
    a matching field here).
    """
    return (
        "Similarity",
        "Action",
        "Score",
        "Lock",
        "File Name",
        "Folder",
        "Size (Bytes)",
        "Group Count",
        "Creation Date",
        "Shot Date",
        "Resolution",
    )


def dict_from_pairs(
    pairs: tuple[tuple[str, int], ...],
    data_getter: Callable[[int], str],
) -> dict[str, str]:
    """Assemble a ``{label: value}`` dict by calling ``data_getter``
    for each column in ``pairs``.

    Pure: no Qt imports. The caller closes over the
    ``QModelIndex`` walk and passes a small callable.

    Failure mode: a refactor that swapped ``key`` and ``col`` in the
    pairs would produce ints as dict keys — silently breaks every
    downstream ``values["Action"]`` lookup.
    """
    return {label: data_getter(col) for label, col in pairs}


def safe_call_records_provider(
    provider: Callable[[], Any] | None,
) -> list:
    """Invoke ``provider``, returning ``[]`` on any failure or
    ``None`` return.

    Why: the records provider may not be wired (test callers pass
    ``None``) or may fail mid-call (records being rebuilt by a
    concurrent scan). Dialog open must never depend on a clean
    return — the user opening the dialog without groups gets the
    regex panel; the panel is what was broken in #237 when the
    callsite forgot to pass ``groups`` through.

    Failure mode: a refactor that lets the provider exception
    propagate would crash the menu click — instead of "open the
    dialog with no live preview", the user sees nothing.
    """
    if provider is None:
        return []
    try:
        result = provider()
    except Exception:
        return []
    return result or []
