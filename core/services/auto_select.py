"""Auto-select helper used by the scan pipeline (photo-manager#212).

When the scan's "Auto select after scan" option is on, the pipeline calls
:func:`top_score_path_per_group` immediately after scoring and before
writing the manifest. The set it returns is the keepers — one row per
duplicate group, picked by highest :attr:`score`.

The helper is shape-agnostic: it duck-types on ``group_id`` /
``source_path`` / ``score``, so the same function would work on
``PhotoRecord`` (where the corresponding attributes are
``group_id`` / ``file_path`` / ``score``) if a future caller adapts the
attribute names. The current production caller is
``core.app_service.scan_runner.run_pipeline`` operating on
``scanner.dedup.ManifestRow``.

Tie-break and None-handling match ``select_paths_top_n`` in
``core.app_service.action_resolve`` so behaviour is consistent between
the manual "Top 1 by score" rule the user triggers from the selection
UI and the automatic version triggered here:

* ``score is None`` rows are excluded from ranking entirely.
  Isolated rows (``group_id is None``) AND Live Photo MOV passengers
  both have ``score=None`` per the scoring rules.
* Ties break by ``source_path`` ascending so the keeper is stable
  across re-runs of the same scan.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import groupby
from pathlib import Path
from typing import Iterable

from core.services.auto_select_rules import LEGACY_RULES, RuleToggles
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
    *,
    lock_keepers: bool = True,
) -> tuple[dict[str, str], dict[str, bool]]:
    """Build the ``(decisions, lock_states)`` write-sets for the post-scan
    auto-select pass. Pure — no I/O, no schema access.

    #651 — so ``scanner.manifest.write_manifest`` can fold the SAME
    write-set into the manifest's own tmp connection (closing the
    incoherence window where a crash between the manifest write and a
    separate auto-select write left keepers unlocked). The separate
    post-write apply was removed in A5. ``action_service.apply_best_copy`` also
    builds a keeper set for its own write; this function defines the shape.

    The canonical stored value for "keep" is the **empty string** —
    matches what ``settable_decisions()`` returns and what the right-
    click "Set Action → keep" path writes. Earlier versions wrote the
    literal ``"keep"`` string which then leaked into the tree's Action
    column as raw text instead of an empty cell (#425). The lock badge
    in COL_LOCK is what signals the user that a row was locked as the
    keeper; the scan path no longer locks (PR-3), so it passes
    ``lock_keepers=False``.

    Args:
        keepers: Paths of the per-group top-scored rows from
            :func:`top_score_path_per_group`. Each receives
            ``user_decision=""``, and ``is_locked=1`` when
            ``lock_keepers`` is true.
        non_keepers_for_delete: Paths to receive
            ``user_decision='delete'``. ``None`` (the default) leaves
            non-keepers' decision untouched — that's the non-aggressive
            behaviour. Pass an empty set or ``None`` interchangeably;
            both skip the delete writes.
        lock_keepers: When false, ``lock_states`` is empty and no
            keeper is locked (PR-3: the scan path stamps without a
            lock). Default true keeps the user-invoked best-copy lock.

    Returns:
        ``(decisions, lock_states)`` — ``decisions`` maps
        ``source_path -> user_decision``; ``lock_states`` maps
        ``source_path -> True`` for every keeper (empty when
        ``lock_keepers`` is false).
    """
    # #425 — was {p: "keep" ...} which leaked as raw "keep" text in the
    # tree's Action column. "" is the canonical keep state per
    # settable_decisions(); the lock badge in COL_LOCK is the user-
    # visible signal that the row was locked as the keeper.
    decisions: dict[str, str] = {p: "" for p in keepers}
    if non_keepers_for_delete:
        decisions.update({p: "delete" for p in non_keepers_for_delete})
    lock_states: dict[str, bool] = (
        {p: True for p in keepers} if lock_keepers else {}
    )
    return decisions, lock_states


def top_n_paths(
    ranked: Iterable[tuple[float, str]], n: int, order: str
) -> list[str]:
    """Return the paths of the top (or bottom) ``n`` of ONE group's
    ``(value, path)`` pairs.

    #778 — the single home of the top-N-by-score ranking rule. Both
    surfaces that pick "the best copy in this group" call it:

    * :func:`top_score_path_per_group` (``n=1``, ``order="desc"``) — the
      post-scan auto-select and ``POST /api/action/apply-best-copy``.
    * ``core.app_service.action_resolve.select_paths_top_n`` — the
      ``__top_n__:`` pattern behind ``POST /api/action/bulk-decide``.

    Previously each had its own copy of the rule, so a scoring-semantics
    change (e.g. the #187 two-tier scorer) had to be applied twice and a
    miss produced a DIFFERENT keeper on one surface than the other.
    ``tests/test_topn_keeper_parity.py`` pins the two against each other.

    The sort key is total: ties on ``value`` break on ``path``, and paths
    within a group are unique, so the result never depends on input order
    (#792 — a nondeterministic tie-break in dedup; do not reintroduce one).

    Args:
        ranked: ``(value, path)`` pairs for a single group. Callers filter
            out unrankable records (``value is None``) BEFORE calling —
            what counts as unrankable differs per surface.
        n: How many to take. ``n <= 0`` yields ``[]``. A group with fewer
            than ``n`` pairs yields all of them.
        order: ``"desc"`` takes the LARGEST values (the keepers),
            ``"asc"`` the smallest (the deletables). Any other value
            yields ``[]``.

    Returns:
        Paths in selection order — value-ranked, then path-ascending
        within each equal-value bucket.
    """
    if n <= 0 or order not in ("asc", "desc"):
        return []
    # Sort ascending by (value, path), then for desc reverse and restore the
    # ascending path tiebreak within each equal-value bucket. Equivalent to a
    # (-value, path) key for every real score, but kept in this exact form
    # because it is the ordering users' saved "Top N" rules were built
    # against — see select_paths_top_n in core.app_service.action_resolve.
    ordered = sorted(ranked, key=lambda t: (t[0], t[1]))
    if order == "desc":
        ordered.reverse()
        fixed: list[tuple[float, str]] = []
        for _val, grp in groupby(ordered, key=lambda t: t[0]):
            fixed.extend(sorted(grp, key=lambda t: t[1]))
        ordered = fixed
    return [path for _val, path in ordered[:n]]


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
        # #778 — one shared ranking rule (highest score, ties by earliest
        # path) instead of a second private copy of it.
        keepers.update(top_n_paths(ranked, 1, "desc"))
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

# PR-3 — the confidence threshold ("confirm below"). The setting names the
# lowest level auto-select decides on alone: a group with any row below it
# is "needs your look" and gets no keeper stamp. ``"high"`` is the default
# (the safe direction for deletes, #1047).
CONFIRM_BELOW_CHOICES: tuple[str, ...] = ("high", "medium")
DEFAULT_CONFIRM_BELOW = "high"
_LEVEL_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def resolve_confirm_below(value: object) -> str:
    """Return ``value`` when it is a known threshold, else the default.

    An unset or junk stored value resolves to ``"high"`` so a bad settings
    file can never silently widen what auto-select decides on.
    """
    if isinstance(value, str) and value in CONFIRM_BELOW_CHOICES:
        return value
    return DEFAULT_CONFIRM_BELOW


def row_confidence_level(row: object) -> str | None:
    """Return the decision-confidence level of one manifest row.

    Duck-typed on ``action`` / ``match_confidence``; checked in order:

    * ``match_confidence == "low"`` -> ``"low"``
    * ``action == "REVIEW_DUPLICATE"`` -> ``"medium"`` (pixels differ, so
      the keeper pick is metadata-only)
    * ``action == "EXACT"`` or ``match_confidence == "high"`` -> ``"high"``
    * anything else (Ref-tier passenger, RAW+lossy pair, old shapes) ->
      ``None``, which is ignored by the threshold.
    """
    match_confidence = getattr(row, "match_confidence", None)
    action = getattr(row, "action", "")
    if match_confidence == "low":
        return "low"
    if action == "REVIEW_DUPLICATE":
        return "medium"
    if action == "EXACT" or match_confidence == "high":
        return "high"
    return None


def below_threshold(row: object, confirm_below: str) -> bool:
    """True when the row's level ranks under the threshold.

    A row with no level (``None``) is never below: it is ignored, not
    flagged. Ranks are low < medium < high.
    """
    level = row_confidence_level(row)
    if level is None:
        return False
    return _LEVEL_RANK[level] < _LEVEL_RANK[confirm_below]


def groups_needing_look(rows: Iterable, confirm_below: str) -> set[str]:
    """Return the ``group_id`` of every group holding a below-threshold row."""
    return {
        row.group_id
        for row in rows
        if row.group_id is not None and below_threshold(row, confirm_below)
    }


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


def _delete_blocker(
    row: object,
    keepers: set[str],
    raw_protected: set[str],
    *,
    confirm_below: str,
    rules: RuleToggles,
) -> str | None:
    """PR-4a — the first reason a row may not be auto-deleted, or ``None``.

    The checks run in a fixed order, and the first hit wins. Under
    :data:`LEGACY_RULES` the chain is exactly the pre-PR-4a filter. The rule
    blockers ("B4", "B3", "B8") can only remove a row from the delete set.
    """
    if row.group_id is None or row.score is None:
        return "unranked"
    if row.source_path in keepers:
        return "keeper"
    action = getattr(row, "action", "")
    if action not in _DUPLICATE_ACTIONS:
        return "action"
    if getattr(row, "match_confidence", None) == "low":
        return "low"
    if below_threshold(row, confirm_below):
        return "threshold"
    if rules.b4 and row.source_path in raw_protected:
        return "B4"
    if not rules.b3 and action == "EXACT":
        return "B3"
    if rules.b8 and action != "EXACT":
        return "B8"
    return None


def non_keepers_for_aggressive_delete(
    rows: Iterable,
    keepers: set[str],
    *,
    confirm_below: str = "medium",
    rules: RuleToggles = LEGACY_RULES,
) -> set[str]:
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

    #1047 / PR-3 — a row BELOW ``confirm_below`` (see
    :func:`below_threshold`) is EXCLUDED too. At ``"high"`` a REVIEW_DUPLICATE
    row (same pHash, pixels differ) is a near-duplicate, not a copy, and must
    not be deleted as one; an EXACT copy in the same group stays eligible. At
    ``"medium"`` the set is exactly the #517 set. The default ``"medium"``
    keeps existing callers on #517 semantics.

    #824 — when a group's keeper is a RAW, the best non-RAW row is protected
    alongside it (:func:`_raw_displaced_keepers`). A RAW joining a component
    via the new exact-tier complementary edge must not demote the lossy row it
    outscored into the delete set; without this, the review-time
    ``apply_best_copy`` path wrote ``decision='delete'`` onto a JPEG export
    that nothing had asked to delete before.

    PR-4a — ``rules`` (see :mod:`core.services.auto_select_rules`) can only
    narrow this set. B4 off drops the #824 protection above. B3 off spares
    every EXACT row. B8 on keeps only EXACT rows, at either threshold. The
    default :data:`LEGACY_RULES` gives the pre-PR-4a set, so existing callers
    are unchanged. The chain lives in :func:`_delete_blocker`.

    Args:
        rows: Iterable of ``ManifestRow``-shaped objects (``group_id``,
            ``source_path``, ``score``, ``action``, ``match_confidence``).
        keepers: The per-group keepers from :func:`top_score_path_per_group`.
        confirm_below: ``"high"`` or ``"medium"``; the threshold.
        rules: The rule toggles; defaults to the legacy behaviour.

    Returns:
        Set of ``source_path`` strings eligible for aggressive auto-delete.
    """
    rows = list(rows)
    raw_protected = _raw_displaced_keepers(rows, keepers)
    return {
        row.source_path for row in rows
        if _delete_blocker(
            row, keepers, raw_protected, confirm_below=confirm_below, rules=rules
        ) is None
    }


def plan_auto_select(
    rows: Iterable,
    *,
    aggressive: bool,
    confirm_below: str,
    rules: RuleToggles = LEGACY_RULES,
) -> tuple[set[str], set[str]]:
    """Return ``(stamped, deletes)`` for the post-scan auto-select pass.

    PR-3 — a group is decided only when every one of its rows is at or above
    ``confirm_below``. Such a group gets its top scorer stamped KEEP. A group
    with a below-threshold row gets no stamp; it is shown under "Needs your
    look" instead.

    Delete protection uses every group's top scorer, stamped or not, so the
    unstamped keeper of an uncertain group is never a delete candidate. In
    aggressive mode, deletes are the eligible rows of :func:`non_keepers_for_
    aggressive_delete` (the threshold excludes below rows). No mode sets a
    lock. Pure: no I/O.

    Args:
        rows: ``ManifestRow``-shaped objects.
        aggressive: Whether the aggressive delete set is wanted.
        confirm_below: ``"high"`` or ``"medium"``; the threshold.

    Returns:
        ``(stamped, deletes)`` — ``stamped`` are the keeper paths to stamp
        KEEP; ``deletes`` are paths to mark for delete (empty when not
        aggressive).
    """
    rows = list(rows)
    keepers = top_score_path_per_group(rows)
    needs_look = groups_needing_look(rows, confirm_below)
    stamped = {
        row.source_path
        for row in rows
        if row.source_path in keepers and row.group_id not in needs_look
    }
    deletes: set[str] = set()
    if aggressive:
        deletes = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below=confirm_below, rules=rules
        )
    return stamped, deletes


def rule_trace(
    rows: Iterable,
    stamped: set[str],
    *,
    aggressive: bool,
    confirm_below: str,
    rules: RuleToggles,
) -> dict[str, str]:
    """PR-4a — map each decided path to the rule that decided it.

    Values: ``"B1:keep"`` for a stamped keeper; and, only when ``aggressive``,
    ``"B3:delete"`` / ``"B1:delete"`` for a delete (EXACT vs. the rest) or
    ``"B3:spared"`` / ``"B4:spared"`` / ``"B8:spared"`` when a rule blocked a
    row that would otherwise be deleted. Rows held back by the threshold or
    the keeper pick have no entry, because no toggle is responsible for them.
    Pure: the trace is for the scan log and the decision log (PR-5).
    """
    rows = list(rows)
    keepers = top_score_path_per_group(rows)
    trace = {path: "B1:keep" for path in stamped}
    if not aggressive:
        return trace
    raw_protected = _raw_displaced_keepers(rows, keepers)
    for row in rows:
        blocker = _delete_blocker(
            row, keepers, raw_protected, confirm_below=confirm_below, rules=rules
        )
        if blocker is None:
            trace[row.source_path] = (
                "B3:delete" if row.action == "EXACT" else "B1:delete"
            )
        elif blocker in ("B3", "B4", "B8"):
            trace[row.source_path] = f"{blocker}:spared"
    return trace
