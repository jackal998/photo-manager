"""Pure-logic helpers extracted from :mod:`app.views.main_window`.

Extracted so the load-bearing logic in MainWindow is unit-testable
against plain Python / a plain ``QStandardItemModel`` without
cascade-importing the full QMainWindow view stack (which would drag
in :mod:`app.views.preview_pane`, ``image_tasks``, ``widgets/*`` —
the heavy Qt widgets that legitimately belong to layer 3). Same
pattern previously used by ``action_handlers.py`` (#182),
``status_reporter_impl.py`` (#138, #140), and ``empty_state.py``
(#137).

What lives here:

* :func:`find_path_in_model` — single-target tree walk for
  ``MainWindow._reselect_by_path``.
* :func:`find_paths_in_model` — multi-target tree walk for
  ``MainWindow._select_rows_by_paths`` (#239 auto-select highlight).
* :func:`extract_keeper_paths` — pulls ``rec.action == "KEEP"`` file
  paths from a VM ``groups`` list (#239 scan-complete handler).
* :func:`count_isolated_rows` — SQLite query for un-grouped manifest
  rows; used by the status-bar baseline text after a manifest load
  (#138, #140).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from app.views.constants import COL_GROUP, COL_NAME, PATH_ROLE


def find_path_in_model(model: Any, target_path: str) -> Any:
    """Return the ``QModelIndex`` of the file row whose ``PATH_ROLE``
    matches ``target_path``, or ``None`` if no match.

    Walks the standard two-level tree shape used throughout the app:
    top-level rows are groups (the COL_GROUP cell holds the group
    number), and each group's children are files (the COL_NAME cell
    carries the full path on ``PATH_ROLE``).

    A ``None`` ``model`` returns ``None`` — callers may invoke this
    before the tree has been populated.
    """
    if model is None:
        return None
    for group_row in range(model.rowCount()):
        group_idx = model.index(group_row, COL_GROUP)
        for child_row in range(model.rowCount(group_idx)):
            name_idx = model.index(child_row, COL_NAME, group_idx)
            if model.data(name_idx, PATH_ROLE) == target_path:
                return name_idx
    return None


def find_paths_in_model(model: Any, target_paths: Iterable[str]) -> list:
    """Multi-target version of :func:`find_path_in_model`. Returns a
    list of ``QModelIndex`` for every file row whose ``PATH_ROLE`` is
    in ``target_paths``, in tree-walk order (top-down, group-by-group).

    Used by ``MainWindow._select_rows_by_paths`` for the #239
    auto-select-after-scan highlight — the worker pre-decides which
    rows to keep, the window paints that decision visibly. Returns an
    empty list when there are no targets, when the model is ``None``,
    or when no row matches.
    """
    targets = set(target_paths)
    if not targets or model is None:
        return []
    matches: list = []
    for group_row in range(model.rowCount()):
        group_idx = model.index(group_row, COL_GROUP)
        for child_row in range(model.rowCount(group_idx)):
            name_idx = model.index(child_row, COL_NAME, group_idx)
            if model.data(name_idx, PATH_ROLE) in targets:
                matches.append(name_idx)
    return matches


def extract_keeper_paths(groups: Iterable[Any]) -> set[str]:
    """Return the set of ``file_path`` values for every record whose
    ``action`` is ``"KEEP"`` across all groups. Empty paths are
    stripped.

    Pulled from ``MainWindow._load_manifest_after_scan``. The scan
    worker's auto-select stamps ``action="KEEP"`` on the chosen rows
    before writing the manifest; the window then asks
    ``_select_rows_by_paths`` to highlight them.

    Uses ``getattr`` so the function tolerates partial / mock records
    in tests without raising AttributeError on missing fields.
    """
    paths = {
        getattr(rec, "file_path", "")
        for group in groups
        for rec in getattr(group, "items", [])
        if getattr(rec, "action", "") == "KEEP"
    }
    paths.discard("")
    return paths


def extract_first_selected_file_path(items: Iterable[Any]) -> str | None:
    """Return the ``path`` of the first item in ``items`` whose
    ``type`` is ``"file"`` and whose ``path`` is truthy. Returns
    ``None`` if no file row is present.

    Pulled from ``MainWindow._capture_relocalize_state``. The tree
    controller's selected-items list mixes group-row entries
    (``type == "group"``) and file-row entries; the relocalize
    snapshot only needs the file path to re-select after the live
    language switch (#22 territory). Filtering this correctly is the
    difference between the user's selection surviving relocalize and
    "I lost my row" — which is the bug class the test catches.
    """
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "file" and item.get("path"):
            return item["path"]
    return None


def count_isolated_rows(manifest_path: str, grouped_count: int) -> int:
    """Return ``max(0, total_manifest_rows - grouped_count)`` for the
    manifest at ``manifest_path``.

    Used by ``MainWindow._load_manifest_from_path`` to surface
    isolated (un-grouped) files in the status-bar baseline so users
    whose scan produced zero near-duplicate groups don't stare at an
    empty review pane with no explanation (#138 / #140 baseline).

    Best-effort: any SQLite or OS error returns ``0`` — the worst
    case is the status bar omits the isolated count, which is the
    same as the pre-feature behavior and never user-blocking.
    """
    try:
        with sqlite3.connect(manifest_path) as conn:
            total = (
                conn.execute("SELECT COUNT(*) FROM migration_manifest").fetchone()[0]
                or 0
            )
        return max(0, total - grouped_count)
    except (sqlite3.Error, OSError):
        return 0
