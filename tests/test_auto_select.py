"""Tests for core/services/auto_select.py (photo-manager#212, #393).

Each test catches a real user-visible bug — see the docstring on every
case below for the failure mode it pins. No defensive-branch padding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.services.auto_select import top_score_path_per_group


@dataclass
class _Row:
    """Minimal ManifestRow stand-in carrying the three attributes the
    helper reads. Using a dataclass instead of ManifestRow itself keeps
    the test independent of the scanner module — auto_select.py is
    deliberately shape-agnostic, and this proves it."""

    source_path: str
    group_id: Optional[str]
    score: Optional[float]
    match_confidence: Optional[str] = None  # #517
    # #536 — the classifier action. Defaults to "" (Ref-tier / undecided):
    # the conservative, safe default, since only EXACT / REVIEW_DUPLICATE rows
    # are eligible for aggressive auto-delete. Tests that model a deletable
    # duplicate set this explicitly.
    action: str = ""


class TestTopScorePathPerGroup:
    def test_picks_highest_score_in_group_of_three(self):
        """Catches: helper picks the wrong row (off-by-one in the sort,
        or asc-vs-desc inversion). This is the headline correctness
        case — if it ever fails, auto-select is marking the wrong file
        as KEEP and downstream the user deletes the keeper."""
        rows = [
            _Row("/a.jpg", "g1", 0.40),
            _Row("/b.jpg", "g1", 0.90),
            _Row("/c.jpg", "g1", 0.60),
        ]
        assert top_score_path_per_group(rows) == {"/b.jpg"}

    def test_tie_break_picks_alphabetically_first_path(self):
        """Catches: non-deterministic tie-break. Two rows with the same
        score must resolve to the same keeper every run; otherwise the
        same scan re-run flips which file is marked KEEP, which would
        manifest as flaky qa scenarios and surprised users."""
        rows = [
            _Row("/zebra.jpg", "g1", 0.75),
            _Row("/apple.jpg", "g1", 0.75),
            _Row("/mango.jpg", "g1", 0.75),
        ]
        # All three tied; helper must pick the lexicographically
        # earliest path. (Matches select_paths_top_n's tie-break.)
        assert top_score_path_per_group(rows) == {"/apple.jpg"}

    def test_none_scored_row_excluded_but_group_still_resolves(self):
        """Catches: helper picks the None row (Python sort of mixed
        types raises, OR the None gets treated as 'highest'). Live
        Photo MOV passengers have score=None and must NOT be selected
        as keepers — they inherit the HEIC's decision at action-execute
        time, but at auto-select they're a no-op."""
        rows = [
            _Row("/photo.heic", "g1", 0.80),
            _Row("/photo.mov", "g1", None),         # passenger
            _Row("/photo_dup.heic", "g1", 0.50),
        ]
        assert top_score_path_per_group(rows) == {"/photo.heic"}

    def test_group_with_all_none_scores_yields_no_keeper(self):
        """Catches: helper crashes on an empty ranked list, or invents
        a keeper from None rows. A group where every row is unscored
        (synthetic but possible — Live Photo cluster of all-MOV peers
        before the HEIC is added) must produce no keeper. The action
        column for those rows stays whatever the classifier set."""
        rows = [
            _Row("/a.mov", "g1", None),
            _Row("/b.mov", "g1", None),
        ]
        assert top_score_path_per_group(rows) == set()

    def test_isolated_rows_with_group_id_none_are_ignored(self):
        """Catches: helper auto-selects on isolated rows. Isolated
        files (group_id is None) have no peers to compete with — the
        user did not ask the system to make a choice. Marking them
        KEEP would silently flip every solitary file's action away
        from its undecided '' state (#433 — was MOVE)."""
        rows = [
            _Row("/lone1.jpg", None, 0.95),         # isolated
            _Row("/lone2.jpg", None, 0.10),         # isolated
            _Row("/grp_a.jpg", "g1", 0.40),
            _Row("/grp_b.jpg", "g1", 0.70),
        ]
        assert top_score_path_per_group(rows) == {"/grp_b.jpg"}

    def test_single_row_group_returns_that_row(self):
        """Catches: helper requires N≥2 to operate (a min-size guard
        that mistakenly excludes single-member groups). Two-row Live
        Photo pairs can drop to one scored row if the partner is a
        score=None video — the surviving scored row IS the keeper
        of the group, and the helper must return it."""
        rows = [
            _Row("/solo.jpg", "g1", 0.55),
        ]
        assert top_score_path_per_group(rows) == {"/solo.jpg"}

    def test_multiple_groups_pick_independent_top_each(self):
        """Catches: cross-group bleed (e.g. helper picks the global
        top instead of per-group tops). Every duplicate group must
        get its own keeper independently."""
        rows = [
            _Row("/g1_low.jpg", "g1", 0.20),
            _Row("/g1_high.jpg", "g1", 0.80),
            _Row("/g2_low.jpg", "g2", 0.30),
            _Row("/g2_high.jpg", "g2", 0.90),
            _Row("/g3_only.jpg", "g3", 0.10),
        ]
        assert top_score_path_per_group(rows) == {
            "/g1_high.jpg",
            "/g2_high.jpg",
            "/g3_only.jpg",
        }

    def test_empty_input_returns_empty_set(self):
        """Catches: helper crashes on empty input (e.g. an
        unconditional ``rows[0]`` access). Scans on a source with no
        duplicates produce zero grouped rows, and the helper must
        return cleanly so the worker's auto-select branch is a no-op
        rather than blowing up the whole pipeline."""
        assert top_score_path_per_group([]) == set()


class TestWorkerIntegrationShape:
    """The helper is duck-typed but the production caller passes
    scanner.dedup.ManifestRow. Pin the contract: ManifestRow has the
    three attributes the helper reads, so the helper works on real
    rows without an adapter. If this ever fails the helper or
    ManifestRow drifted apart and the worker integration is broken."""

    def test_helper_reads_real_manifestrow(self):
        """Catches: ManifestRow renames any of group_id / source_path /
        score, or the worker integration starts passing a different
        shape. The production code path between scan_worker._run_pipeline
        and top_score_path_per_group depends on this contract."""
        from scanner.dedup import ManifestRow

        rows = [
            ManifestRow(
                source_path="/grp/a.jpg",
                source_label="src",
                action="REVIEW_DUPLICATE",
                source_hash="h1",
                phash="p1",
                hamming_distance=2,
                duplicate_of="/grp/b.jpg",
                reason="near-dup",
                group_id="/grp/b.jpg",
                score=0.42,
            ),
            ManifestRow(
                source_path="/grp/b.jpg",
                source_label="src",
                action="",
                source_hash="h2",
                phash="p2",
                hamming_distance=None,
                duplicate_of=None,
                reason="unique",
                group_id="/grp/b.jpg",
                score=0.87,
            ),
        ]
        assert top_score_path_per_group(rows) == {"/grp/b.jpg"}
class TestNonKeepersForAggressiveDelete:
    """#517 — the aggressive-delete target set excludes low-confidence
    (pHash-only) near-dups so they are never auto-marked for deletion."""

    def test_low_confidence_non_keeper_excluded(self):
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/keep.jpg", "g1", 0.9, match_confidence="high",
                 action="REVIEW_DUPLICATE"),
            _Row("/hi.jpg", "g1", 0.5, match_confidence="high",
                 action="REVIEW_DUPLICATE"),
            _Row("/lo.jpg", "g1", 0.4, match_confidence="low",
                 action="REVIEW_DUPLICATE"),
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/keep.jpg"})
        assert targets == {"/hi.jpg"}            # high-confidence non-keeper deleted
        assert "/lo.jpg" not in targets          # low-confidence spared

    def test_missing_confidence_attr_stays_eligible(self):
        """EXACT / older rows with no match_confidence behave as before:
        eligible, PROVIDED the action is a duplicate action (#536)."""
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/keep.jpg", "g1", 0.9, action="EXACT"),
            # match_confidence defaults to None; EXACT action → still eligible.
            _Row("/dup.jpg", "g1", 0.5, action="EXACT"),
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/keep.jpg"})
        assert targets == {"/dup.jpg"}

    def test_ref_tier_passenger_excluded_from_aggressive_delete(self):
        """#536 — a Ref-tier passenger (action "" / KEEP / UNDATED) pulled into
        a group by the ungated, filename-based pair edge (same-stem RAW+JPG /
        HEIC+JPG, Live Photo) renders as the "—" passenger. It carries a real
        score and a group_id but match_confidence=None, so before this guard it
        slipped past the #517 ``!= "low"`` filter and a complementary ORIGINAL
        was aggressive-delete eligible (the observed DSC_0015 → "—" → delete).
        Only EXACT / REVIEW_DUPLICATE rows may be auto-deleted; both Ref-tier
        passengers here must be spared while the genuine duplicate is not."""
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/DSC_0015.nef", "g1", 0.92, action="UNDATED"),     # Ref winner (keeper)
            _Row("/DSC_0015.jpg", "g1", 0.71, action=""),            # "—" passenger (undecided)
            _Row("/IMG.heic", "g1", 0.65, action="UNDATED"),         # "—" passenger (undated)
            _Row("/real_dup.jpg", "g1", 0.40, action="REVIEW_DUPLICATE",
                 match_confidence="high"),                            # genuine duplicate
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/DSC_0015.nef"})
        # Complementary originals spared; only the asserted duplicate deleted.
        assert "/DSC_0015.jpg" not in targets
        assert "/IMG.heic" not in targets
        assert targets == {"/real_dup.jpg"}

    def test_unscored_and_isolated_excluded(self):
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/keep.jpg", "g1", 0.9, match_confidence="high"),
            _Row("/passenger.jpg", "g1", None, match_confidence="high"),  # score None
            _Row("/isolated.jpg", None, 0.5, match_confidence="high"),    # group None
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/keep.jpg"})
        assert targets == set()

    def test_raw_keeper_does_not_demote_the_best_lossy_copy(self):
        """#824 — a RAW that merges in via the new exact-tier complementary
        edge can outscore the incumbent and take keepership. The lossy row it
        displaced must NOT become delete-eligible: it was protected only by
        being the keeper, and #536's allowlist does not cover it because it
        carries a real EXACT (a byte-identical sibling exists).

        Without the guard the delete set here is {"/p/A.JPG"} — measured, and
        the review-time apply_best_copy path then wrote decision='delete' onto
        that JPEG export."""
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/p/A.DNG", "g1", 0.95, action=""),          # RAW, new keeper
            _Row("/p/A.JPG", "g1", 0.50, action="EXACT"),     # was the keeper
            _Row("/p/A copy.JPG", "g1", 0.40, action=""),     # its SHA keeper
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/p/A.DNG"})
        assert targets == set(), (
            "a RAW keeper demoted the best lossy copy into the delete set"
        )

    def test_raw_keeper_still_deletes_a_duplicate_that_was_never_keeper(self):
        """The #824 guard is narrow: it spares only the row that WOULD have
        been keeper without the RAW. A genuine duplicate ranked below that row
        stays deletable — otherwise a RAW anywhere in a group would silently
        disable aggressive delete for the whole group."""
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/p/A.DNG", "g1", 0.95, action=""),           # RAW keeper
            _Row("/p/best.jpg", "g1", 0.70, action="EXACT"),   # displaced → spared
            _Row("/p/worse.jpg", "g1", 0.30, action="EXACT",
                 match_confidence="high"),                      # never keeper → deleted
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/p/A.DNG"})
        assert targets == {"/p/worse.jpg"}

    def test_all_lossy_group_still_deletes_the_exact_copy(self):
        """False-positive half of the #824 guard: with no RAW in the group,
        nothing extra is spared and the asserted duplicate is still deleted."""
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/p/A.JPG", "g1", 0.95, action=""),
            _Row("/p/A copy.JPG", "g1", 0.40, action="EXACT",
                 match_confidence="high"),
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/p/A.JPG"})
        assert targets == {"/p/A copy.JPG"}

    def test_tiff_keeper_counts_as_raw(self):
        """``scanner.media.get_file_type`` maps .tif/.tiff to ``raw`` even
        though ``LOSSY_EXTENSIONS`` also lists them, so the guard's extension
        set must include them — otherwise a TIFF keeper would demote its JPEG
        export exactly the way a DNG keeper did."""
        from core.services.auto_select import non_keepers_for_aggressive_delete
        rows = [
            _Row("/p/A.tiff", "g1", 0.95, action=""),
            _Row("/p/A.JPG", "g1", 0.50, action="EXACT"),
        ]
        targets = non_keepers_for_aggressive_delete(rows, keepers={"/p/A.tiff"})
        assert targets == set()
