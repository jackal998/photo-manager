"""Auto-select helper used by the scan worker (photo-manager#212).

When ``ScanDialog``'s "Auto select after scan" option is on, the worker
calls :func:`top_score_path_per_group` immediately after scoring and
before writing the manifest. The set it returns is the keepers — one
row per duplicate group, picked by highest :attr:`score`.

The helper is shape-agnostic: it duck-types on ``group_id`` /
``source_path`` / ``score``, so the same function would work on
``PhotoRecord`` (where the corresponding attributes are
``group_id`` / ``file_path`` / ``score``) if a future caller adapts the
attribute names. The current production caller is
``app.views.workers.scan_worker.ScanWorker`` operating on
``scanner.dedup.ManifestRow``.

Tie-break and None-handling match ``select_paths_top_n`` in
``app/views/dialogs/select_dialog.py`` so behaviour is consistent
between the manual "Top 1 by score" rule the user triggers from the
Selection dialog and the automatic version triggered here:

* ``score is None`` rows are excluded from ranking entirely.
  Isolated rows (``group_id is None``) AND Live Photo MOV passengers
  both have ``score=None`` per the scoring rules.
* Ties break by ``source_path`` ascending so the keeper is stable
  across re-runs of the same scan.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def top_score_path_per_group(rows: Iterable) -> set[str]:
    """Return source_paths of the top-scoring row in each duplicate group.

    For each distinct non-None ``group_id``, picks the row with the
    highest ``score`` and adds its ``source_path`` to the returned set.
    Rows with ``score is None`` are excluded from ranking; if a group
    has no scored rows at all, no path is contributed for that group.
    Isolated rows (``group_id is None``) are ignored — auto-select only
    operates on grouped duplicates.

    Args:
        rows: Iterable of objects exposing ``group_id`` (``str | None``),
            ``source_path`` (``str``), and ``score`` (``float | None``).
            ``scanner.dedup.ManifestRow`` is the production shape.

    Returns:
        Set of ``source_path`` strings. Empty if no group had any scored
        row.
    """
    by_group: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for row in rows:
        if row.group_id is None or row.score is None:
            continue
        by_group[row.group_id].append((row.score, row.source_path))

    keepers: set[str] = set()
    for ranked in by_group.values():
        # Sort by (score, source_path) ascending — taking the last entry
        # gives the highest score, with ties broken by lexicographically-
        # latest path. To match select_paths_top_n's "ascending path
        # within a tied score bucket" rule, sort by (-score, path) so the
        # first entry is the highest score with the earliest path.
        ranked.sort(key=lambda t: (-t[0], t[1]))
        keepers.add(ranked[0][1])
    return keepers
