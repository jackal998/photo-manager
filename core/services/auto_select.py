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


def apply_auto_select_decisions(
    manifest_path: str,
    keepers: set[str],
    non_keepers_for_delete: set[str] | None = None,
) -> None:
    """Write ``user_decision='keep'`` + ``is_locked=1`` on every keeper,
    and optionally ``user_decision='delete'`` on every non-keeper in
    ``non_keepers_for_delete`` (the aggressive #393 path).

    Composes ``ManifestRepository.batch_update_decisions`` and
    ``batch_update_lock_state`` so the post-scan auto-select state is
    durable on disk and visible in the tree via the lock badge. Both
    sets refer to ``source_path`` strings; non-keeper rows that aren't
    in a scored group (Live Photo MOV passengers, isolated files) are
    NOT included by callers — the caller filters before passing in.

    Args:
        manifest_path: Absolute path to the SQLite manifest just
            written by ``write_manifest``.
        keepers: Paths of the per-group top-scored rows from
            :func:`top_score_path_per_group`. Each receives
            ``user_decision='keep'`` AND ``is_locked=1``.
        non_keepers_for_delete: Paths to receive
            ``user_decision='delete'``. ``None`` (the default) leaves
            non-keepers' decision untouched — that's the non-aggressive
            behaviour. Pass an empty set or ``None`` interchangeably;
            both skip the delete writes.

    Returns:
        None. Writes are persisted by the time this returns. Caller's
        own ``progress`` / log emission is its responsibility.
    """
    from infrastructure.manifest_repository import ManifestRepository

    if not keepers:
        # No keepers → no writes. Empty input is a benign no-op so the
        # caller can invoke unconditionally without an outer guard.
        return

    decisions: dict[str, str] = {p: "keep" for p in keepers}
    if non_keepers_for_delete:
        decisions.update({p: "delete" for p in non_keepers_for_delete})

    repo = ManifestRepository()
    # Lazy-migrate the schema before writing — ``write_manifest`` uses
    # the original DDL, so post-scan / pre-first-load runs hit a
    # manifest without ``is_locked``. ``ensure_schema`` is idempotent
    # so the cost on already-migrated DBs is a couple of failed
    # ALTERs (caught and ignored).
    repo.ensure_schema(manifest_path)
    repo.batch_update_decisions(manifest_path, decisions)
    repo.batch_update_lock_state(
        manifest_path, {p: True for p in keepers}
    )


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
