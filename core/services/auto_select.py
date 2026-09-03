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
from pathlib import Path
from typing import Iterable

from scanner.media import RAW_EXTENSIONS

# #824 — extensions the scanner types as ``raw``. This is
# ``scanner.media.RAW_EXTENSIONS`` PLUS ``.tif`` / ``.tiff``, which
# ``scanner.media.get_file_type``'s table maps to ``"raw"`` even though
# ``LOSSY_EXTENSIONS`` also lists them; the dedup engine's ``file_type`` is
# what decides complementary-vs-format-duplicate, so match that table.
# Extension-only on purpose: neither ``ManifestRow`` nor ``PhotoRecord``
# carries ``file_type``, and a magic-byte-retyped file (a Takeout JPEG named
# ``.DNG``) only makes this guard MORE conservative — the safe direction for
# something that gates auto-DELETE.
_RAW_SUFFIXES = RAW_EXTENSIONS | {".tif", ".tiff"}


def _is_raw_path(source_path: str) -> bool:
    return Path(source_path).suffix.lower() in _RAW_SUFFIXES


def build_auto_select_writes(
    keepers: set[str],
    non_keepers_for_delete: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, bool]]:
    """Build the ``(decisions, lock_states)`` write-sets for the post-scan
    auto-select pass. Pure — no I/O, no schema access.

    #651 — extracted from :func:`apply_auto_select_decisions` so
    ``scanner.manifest.write_manifest`` can fold the SAME write-set
    into the manifest's own tmp connection (closing the incoherence
    window where a crash between the manifest write and a separate
    auto-select write left keepers unlocked), while
    ``apply_auto_select_decisions`` keeps working standalone for any
    other caller.

    The canonical stored value for "keep" is the **empty string** —
    matches what ``settable_decisions()`` returns and what the right-
    click "Set Action → keep" path writes. Earlier versions wrote the
    literal ``"keep"`` string which then leaked into the tree's Action
    column as raw text instead of an empty cell (#425). The lock badge
    in COL_LOCK is what signals the user that a row was auto-selected
    as the keeper.

    Args:
        keepers: Paths of the per-group top-scored rows from
            :func:`top_score_path_per_group`. Each receives
            ``user_decision=""`` AND ``is_locked=1``.
        non_keepers_for_delete: Paths to receive
            ``user_decision='delete'``. ``None`` (the default) leaves
            non-keepers' decision untouched — that's the non-aggressive
            behaviour. Pass an empty set or ``None`` interchangeably;
            both skip the delete writes.

    Returns:
        ``(decisions, lock_states)`` — ``decisions`` maps
        ``source_path -> user_decision``; ``lock_states`` maps
        ``source_path -> True`` for every keeper.
    """
    # #425 — was {p: "keep" ...} which leaked as raw "keep" text in the
    # tree's Action column. "" is the canonical keep state per
    # settable_decisions(); the lock badge in COL_LOCK is the user-
    # visible signal that the row was auto-selected.
    decisions: dict[str, str] = {p: "" for p in keepers}
    if non_keepers_for_delete:
        decisions.update({p: "delete" for p in non_keepers_for_delete})
    lock_states: dict[str, bool] = {p: True for p in keepers}
    return decisions, lock_states


def apply_auto_select_decisions(
    manifest_path: str,
    keepers: set[str],
    non_keepers_for_delete: set[str] | None = None,
) -> None:
    """Write ``user_decision=""`` (canonical keep state) + ``is_locked=1``
    on every keeper, and optionally ``user_decision='delete'`` on every
    non-keeper in ``non_keepers_for_delete`` (the aggressive #393 path).

    Composes ``ManifestRepository.batch_update_decisions`` and
    ``batch_update_lock_state`` so the post-scan auto-select state is
    durable on disk and visible in the tree via the lock badge. Both
    sets refer to ``source_path`` strings; non-keeper rows that aren't
    in a scored group (Live Photo MOV passengers, isolated files) are
    NOT included by callers — the caller filters before passing in.

    #651 — the scan pipeline itself no longer calls this function (it
    passes ``keepers`` / ``non_keepers_for_delete`` into
    ``scanner.manifest.write_manifest`` so the writes land atomically
    with the manifest). This function remains for any other caller
    that needs to apply auto-select decisions to an ALREADY-WRITTEN
    manifest as a separate, non-atomic step.

    Args:
        manifest_path: Absolute path to the SQLite manifest just
            written by ``write_manifest``.
        keepers: Paths of the per-group top-scored rows from
            :func:`top_score_path_per_group`. Each receives
            ``user_decision=""`` AND ``is_locked=1``.
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

    decisions, lock_states = build_auto_select_writes(
        keepers, non_keepers_for_delete
    )

    repo = ManifestRepository()
    # Lazy-migrate the schema before writing — ``write_manifest`` uses
    # the original DDL, so post-scan / pre-first-load runs hit a
    # manifest without ``is_locked``. ``ensure_schema`` is idempotent
    # so the cost on already-migrated DBs is a couple of failed
    # ALTERs (caught and ignored).
    repo.ensure_schema(manifest_path)
    # Single-transaction write — saves one connection-open + one fsync
    # vs the original split call pair. Microsecond gain on local SSD,
    # measurable over SMB/NAS.
    repo.batch_update_decisions_and_lock(manifest_path, decisions, lock_states)


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


# #536 — the only actions the classifier assigns when it has POSITIVELY
# identified a row as a duplicate. Aggressive auto-delete is restricted to
# these: a row carrying any OTHER (Ref-tier "" / KEEP / UNDATED) action was
# never asserted to be a duplicate of anything. Such a row can still land in a
# duplicate group via the unconditional, filename-based pair edge
# (`scanner/dedup.py::_collect_pair_edges` — RAW+JPG / HEIC+JPG same-stem, Live
# Photo) where it renders as the "—" passenger; it carries a real score but
# ``match_confidence=None``, so the #517 ``!= "low"`` guard alone let it through
# and a complementary ORIGINAL could be auto-marked for deletion. An allowlist
# (not a Ref-tier denylist) is deliberate: any future non-duplicate action is
# excluded by default — fail-safe.
_DUPLICATE_ACTIONS = frozenset({"EXACT", "REVIEW_DUPLICATE"})


def _raw_displaced_keepers(rows: Iterable, keepers: set[str]) -> set[str]:
    """#824 — paths a RAW keeper must not be allowed to demote into the
    aggressive-delete set.

    Since #824 an exact-pHash RAW+lossy bucket draws a ``group_id`` edge, so a
    RAW can now merge into a component it was previously absent from. If that
    RAW outscores the incumbent, it takes keepership — and the lossy row that
    WAS the keeper loses the only protection it had. #536's action allowlist
    does not cover it: that row is often a genuine ``EXACT`` (a byte-identical
    sibling exists), which is exactly what makes it eligible once it stops
    being the keeper. Measured on the reviewer's trio: base delete-set ``[]`` →
    ``['/p/A.JPG']``, and ``apply_best_copy`` wrote ``decision=delete`` on it.

    The rule is therefore the narrowest one that reproduces the pre-#824
    outcome: **when a group's keeper is a RAW, the row that would have been
    keeper among the group's non-RAW members stays protected too.** Same
    ``(-score, source_path)`` ordering :func:`top_score_path_per_group` uses,
    so "would have been keeper" is decided identically.

    Deliberately narrow — it does NOT spare every member of a RAW-containing
    group. A genuine duplicate that was never the keeper stays deletable, which
    is what ``tests/test_auto_select.py::TestNonKeepersForAggressiveDelete::
    test_ref_tier_passenger_excluded_from_aggressive_delete`` (#536, a RAW
    keeper with a real ``REVIEW_DUPLICATE`` peer) pins.
    """
    by_group: dict[str, list] = defaultdict(list)
    for row in rows:
        if row.group_id is None or row.score is None:
            continue
        by_group[row.group_id].append(row)

    protected: set[str] = set()
    for group_rows in by_group.values():
        keeper = next(
            (r for r in group_rows if r.source_path in keepers), None
        )
        if keeper is None or not _is_raw_path(keeper.source_path):
            continue
        lossy = [r for r in group_rows if not _is_raw_path(r.source_path)]
        if not lossy:
            continue
        lossy.sort(key=lambda r: (-r.score, r.source_path))
        protected.add(lossy[0].source_path)
    return protected


def non_keepers_for_aggressive_delete(rows: Iterable, keepers: set[str]) -> set[str]:
    """Return source_paths to auto-mark ``user_decision='delete'`` in the
    aggressive auto-select path (#393).

    A non-keeper qualifies only when it is a ranked peer in a scored group
    (has both ``group_id`` and ``score``) and is not itself the keeper.

    #536 — the row's ``action`` must be in :data:`_DUPLICATE_ACTIONS`
    (``EXACT`` / ``REVIEW_DUPLICATE``). A Ref-tier row (``""`` / ``KEEP`` /
    ``UNDATED``) pulled into a group by the ungated pair edge renders as a
    "—" passenger and is NOT a duplicate the engine asserted — auto-deleting
    it risks removing a complementary original (the same-stem RAW+JPG /
    Live-Photo case). Only positively-classified duplicates are eligible.

    #517 — rows whose ``match_confidence`` is ``"low"`` (a pHash-only
    near-duplicate match with no independent dHash agreement) are EXCLUDED,
    so a shaky match is never auto-deleted; the user confirms it manually.
    Rows lacking ``match_confidence`` (older shapes) are treated as not-low and
    remain eligible *provided* their action is a duplicate action.

    #824 — when a group's keeper is a RAW, the best non-RAW row is protected
    alongside it (:func:`_raw_displaced_keepers`). A RAW joining a component
    via the new exact-tier complementary edge must not demote the lossy row it
    outscored into the delete set; without this, the review-time
    ``apply_best_copy`` path wrote ``decision='delete'`` onto a JPEG export
    that nothing had asked to delete before.

    Args:
        rows: Iterable of ``ManifestRow``-shaped objects (``group_id``,
            ``source_path``, ``score``, ``action``, ``match_confidence``).
        keepers: The per-group keepers from :func:`top_score_path_per_group`.

    Returns:
        Set of ``source_path`` strings eligible for aggressive auto-delete.
    """
    rows = list(rows)
    protected = set(keepers) | _raw_displaced_keepers(rows, keepers)
    return {
        row.source_path for row in rows
        if row.group_id is not None
        and row.score is not None
        and row.source_path not in protected
        and getattr(row, "action", "") in _DUPLICATE_ACTIONS
        and getattr(row, "match_confidence", None) != "low"
    }
