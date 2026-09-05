"""Anti-drift pin (#778): the two surfaces that pick the top-scoring keeper
must always pick the SAME row.

Two entry points select "the best copy in this duplicate group":

* ``core.app_service.action_resolve.select_paths_top_n(groups, "Score", 1,
  "desc")`` — what ``POST /api/action/bulk-decide`` resolves a
  ``__top_n__:1:desc`` pattern with (via ``resolve_matched_paths``).
* ``core.services.auto_select.top_score_path_per_group(rows)`` — what
  ``POST /api/action/apply-best-copy`` and the post-scan auto-select in
  ``scan_worker`` use.

Until #778 those were two independent implementations of one rule. They now
share ``auto_select.top_n_paths``; these tests are what makes a future
divergence fail loudly instead of silently marking a different file as the
keeper on one surface than on the other. **They pass on the pre-#778 code
too** — that is deliberate: the pin asserts an equivalence that already held,
so it can be trusted as a regression net rather than as a description of the
new implementation.

The user-visible bug each case guards: a user runs "apply best copy" on a
group and gets one keeper, then runs the same intent through the Action
dialog's Top-1-by-score and gets a DIFFERENT keeper — the other file having
meanwhile been marked ``delete``.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _f
from types import SimpleNamespace

from core.app_service.action_resolve import select_paths_top_n
from core.services.auto_select import top_score_path_per_group


# ---------------------------------------------------------------------------
# Duck-typed fixture shapes
# ---------------------------------------------------------------------------

@dataclass
class _Rec:
    """PhotoRecord stand-in: the attributes both implementations read."""

    file_path: str
    score: float | None = None
    is_locked: bool = False
    action: str = "REVIEW_DUPLICATE"


@dataclass
class _Group:
    items: list = _f(default_factory=list)
    group_number: int = 0


def _adapt(group: _Group, group_id: str = "g") -> list[SimpleNamespace]:
    """Adapt a group's records to the ManifestRow shape auto_select
    duck-types on — the same adapter ``action_service.apply_best_copy``
    builds before calling ``top_score_path_per_group``."""
    return [
        SimpleNamespace(
            group_id=group_id,
            source_path=rec.file_path,
            score=rec.score,
            action=rec.action,
        )
        for rec in group.items
    ]


def _both_picks(group: _Group) -> tuple[list[str], list[str]]:
    """Return (bulk-decide pick, apply-best-copy pick) for one group."""
    return (
        select_paths_top_n([group], "Score", 1, "desc"),
        sorted(top_score_path_per_group(_adapt(group))),
    )


# ---------------------------------------------------------------------------
# The shared fixture: ties, locked rows, and a top-N boundary on equal scores
# ---------------------------------------------------------------------------

_TIE_AT_TOP = _Group([
    _Rec("g/b.jpg", 0.90), _Rec("g/a.jpg", 0.90), _Rec("g/c.jpg", 0.10),
], 1)

_ALL_TIED = _Group([
    _Rec("g/zebra.jpg", 0.75), _Rec("g/apple.jpg", 0.75),
    _Rec("g/mango.jpg", 0.75),
], 2)

_LOCKED_TOP_SCORER = _Group([
    _Rec("g/locked.jpg", 0.95, is_locked=True), _Rec("g/free.jpg", 0.20),
], 3)

_LOCKED_TIED_WITH_UNLOCKED = _Group([
    _Rec("g/a_locked.jpg", 0.60, is_locked=True), _Rec("g/b_free.jpg", 0.60),
], 4)

_PASSENGER = _Group([
    _Rec("g/photo.heic", 0.80), _Rec("g/photo.mov", None),
    _Rec("g/dup.heic", 0.50),
], 5)

_BOUNDARY_ON_EQUAL_SCORES = _Group([
    _Rec("g/x.jpg", 0.90), _Rec("g/n.jpg", 0.50),
    _Rec("g/m.jpg", 0.50), _Rec("g/z.jpg", 0.50),
], 6)

_DISTINCT = _Group([
    _Rec("g/a.jpg", 0.40), _Rec("g/b.jpg", 0.90), _Rec("g/c.jpg", 0.60),
], 7)

_SINGLE = _Group([_Rec("g/solo.jpg", 0.55)], 8)

_ALL_FIXTURES = [
    ("distinct_scores", _DISTINCT),
    ("tie_at_top", _TIE_AT_TOP),
    ("all_tied", _ALL_TIED),
    ("locked_top_scorer", _LOCKED_TOP_SCORER),
    ("locked_tied_with_unlocked", _LOCKED_TIED_WITH_UNLOCKED),
    ("none_score_passenger", _PASSENGER),
    ("boundary_on_equal_scores", _BOUNDARY_ON_EQUAL_SCORES),
    ("single_row", _SINGLE),
]


class TestKeeperSelectionParity:
    def test_every_fixture_group_yields_the_same_keeper(self):
        """Catches: the two surfaces disagree on ANY group shape. A
        disagreement means bulk-decide's Top-1-by-score marks file X as the
        keeper while apply-best-copy marks Y — and whichever ran second has
        already marked the other for deletion."""
        disagreements = []
        for name, group in _ALL_FIXTURES:
            top_n, auto = _both_picks(group)
            if top_n != auto:
                disagreements.append((name, top_n, auto))
        assert disagreements == []

    def test_tie_is_broken_to_the_same_path_on_both_surfaces(self):
        """Catches: a tie-break that is nondeterministic or differs between
        the surfaces. #792 fixed exactly this class of bug in dedup — two
        equal-score siblings must not coin-flip, and both surfaces must
        land on the alphabetically-first path."""
        top_n, auto = _both_picks(_ALL_TIED)
        assert top_n == ["g/apple.jpg"]
        assert auto == ["g/apple.jpg"]

        top_n, auto = _both_picks(_TIE_AT_TOP)
        assert top_n == ["g/a.jpg"]
        assert auto == ["g/a.jpg"]

    def test_locked_rows_do_not_change_either_surfaces_pick(self):
        """Catches: one surface starting to skip locked rows while the
        other does not. Neither ranker may filter on is_locked — the lock
        gate lives in the CALLER (bulk_decide's 409 / apply_best_copy's
        skip_locked), so a locked top scorer is still the keeper on both."""
        top_n, auto = _both_picks(_LOCKED_TOP_SCORER)
        assert top_n == ["g/locked.jpg"]
        assert auto == ["g/locked.jpg"]

        # Locked row tied with an unlocked one: the tie-break is the path,
        # not the lock state, on both surfaces.
        top_n, auto = _both_picks(_LOCKED_TIED_WITH_UNLOCKED)
        assert top_n == ["g/a_locked.jpg"]
        assert auto == ["g/a_locked.jpg"]

    def test_top_n_boundary_landing_inside_a_tie_bucket_is_deterministic(self):
        """Catches: a non-total sort key. When the N cut falls inside a
        bucket of equal scores, WHICH of the tied rows makes the cut must be
        decided by the path, not by input order — otherwise re-running the
        same Top-2 selects a different second row and the user's delete set
        silently changes between runs."""
        # 0.90 then the 0.50 bucket {m, n, z}: top-2 takes x plus the
        # alphabetically-first of the tied bucket.
        assert select_paths_top_n(
            [_BOUNDARY_ON_EQUAL_SCORES], "Score", 2, "desc"
        ) == ["g/x.jpg", "g/m.jpg"]

        # Same fixture, input order shuffled — the result must not move.
        shuffled = _Group(list(reversed(_BOUNDARY_ON_EQUAL_SCORES.items)), 6)
        assert select_paths_top_n(
            [shuffled], "Score", 2, "desc"
        ) == ["g/x.jpg", "g/m.jpg"]

    def test_multi_group_run_agrees_group_for_group(self):
        """Catches: cross-group bleed in one surface only (e.g. one picks a
        global top). Both are run over ALL fixture groups at once, the way
        production does — bulk_decide resolves the whole manifest in one
        call and the scan worker passes every row at once."""
        groups = [g for _name, g in _ALL_FIXTURES]
        from_top_n = sorted(select_paths_top_n(groups, "Score", 1, "desc"))

        rows = []
        for name, group in _ALL_FIXTURES:
            rows.extend(_adapt(group, group_id=name))
        from_auto = sorted(top_score_path_per_group(rows))

        assert from_top_n == from_auto
