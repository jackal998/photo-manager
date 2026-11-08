"""Tests for core/services/auto_select.py (photo-manager#212, #393).

Each test catches a real user-visible bug — see the docstring on every
case below for the failure mode it pins. No defensive-branch padding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

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
    # PR-4b — the Apple ContentIdentifier that pairs a Live still with its video.
    content_identifier: str | None = None


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

    def test_burst_frames_never_delete_eligible(self):
        """GROUPING 7 — a same-moment burst is a keep-all stack. Its frames are
        "" / high, so none is aggressive-delete eligible; only the byte copy
        (EXACT / high, same sha as a frame) is. Master: the frames were
        REVIEW_DUPLICATE / high and the non-keepers all delete-eligible."""
        from datetime import datetime, timedelta
        from pathlib import Path

        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from scanner.dedup import classify
        from tests.test_dedup_hint_edges import _hr

        t0 = datetime(2024, 6, 12, 20, 38, 0)
        records = [
            _hr("/a/f0.jpg", "s0", "00000000ffffffff", t0),
            _hr("/a/f1.jpg", "s1", "00000001fffffffe", t0 + timedelta(seconds=2)),
            _hr("/a/f2.jpg", "s2", "00000003fffffffc", t0 + timedelta(seconds=4)),
            _hr("/z/copy.jpg", "s1", "00000001fffffffe", t0 + timedelta(seconds=2)),
        ]
        rows = classify(records, source_priority={"src_a": 0})
        scores = {"/a/f0.jpg": 0.9, "/a/f1.jpg": 0.5, "/a/f2.jpg": 0.4, "/z/copy.jpg": 0.3}
        for row in rows:
            row.score = scores[Path(row.source_path).as_posix()]
        keepers = top_score_path_per_group(rows)
        targets = non_keepers_for_aggressive_delete(rows, keepers=keepers)
        assert targets == {str(Path("/z/copy.jpg"))}

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


class TestConfidenceThreshold:
    """PR-3 — the confirm threshold, the #1047 aggressive half, and the
    keeper-lock removal. Pure functions only; no I/O."""

    def test_row_confidence_level_maps_action_and_confidence(self):
        """Catches: a level mapping that drops the medium tier (REVIEW_DUPLICATE
        read as high would let a pixels-differ near-duplicate be deleted as a
        copy, #1047) or that ranks a low row above its action."""
        from core.services.auto_select import row_confidence_level as level

        assert level(_Row("/a", "g", 0.5, "high", "EXACT")) == "high"
        assert level(_Row("/a", "g", 0.5, "high", "REVIEW_DUPLICATE")) == "medium"
        assert level(_Row("/a", "g", 0.5, "low", "REVIEW_DUPLICATE")) == "low"
        assert level(_Row("/a", "g", 0.5, "low", "UNDATED")) == "low"
        assert level(_Row("/a", "g", 0.5, None, "")) is None
        assert level(_Row("/a", "g", 0.5, None, "EXACT")) == "high"
        assert level(_Row("/a", "g", 0.5, "high", "")) == "high"

    def test_review_high_not_delete_eligible_when_threshold_high(self):
        """#1047 — at confirm-below "high", a REVIEW_DUPLICATE/high row (same
        pHash, pixels differ) is a near-duplicate, not a copy, so it must NOT be
        marked delete. The EXACT copy in the same group stays eligible.
        Flipped by PR-3 threshold; master deleted both."""
        from core.services.auto_select import non_keepers_for_aggressive_delete

        rows = [
            _Row("/p/keep.jpg", "g1", 0.9, "high", "EXACT"),
            _Row("/p/copy.jpg", "g1", 0.5, "high", "EXACT"),
            _Row("/p/near.jpg", "g1", 0.4, "high", "REVIEW_DUPLICATE"),
        ]
        targets = non_keepers_for_aggressive_delete(
            rows, keepers={"/p/keep.jpg"}, confirm_below="high"
        )
        assert "/p/near.jpg" not in targets
        assert "/p/copy.jpg" in targets

    def test_review_high_delete_eligible_when_threshold_medium(self):
        """#517 parity — at confirm-below "medium" the REVIEW_DUPLICATE/high
        row is admitted and deleted exactly as before PR-3."""
        from core.services.auto_select import non_keepers_for_aggressive_delete

        rows = [
            _Row("/p/keep.jpg", "g1", 0.9, "high", "EXACT"),
            _Row("/p/copy.jpg", "g1", 0.5, "high", "EXACT"),
            _Row("/p/near.jpg", "g1", 0.4, "high", "REVIEW_DUPLICATE"),
        ]
        targets = non_keepers_for_aggressive_delete(
            rows, keepers={"/p/keep.jpg"}, confirm_below="medium"
        )
        assert targets == {"/p/copy.jpg", "/p/near.jpg"}

    def test_low_row_never_eligible_at_either_threshold(self):
        """A low-confidence row is never a delete candidate, whichever threshold
        is set; a pHash-only match stays a manual decision (#517)."""
        from core.services.auto_select import non_keepers_for_aggressive_delete

        rows = [
            _Row("/p/keep.jpg", "g1", 0.9, "high", "EXACT"),
            _Row("/p/low.jpg", "g1", 0.4, "low", "REVIEW_DUPLICATE"),
        ]
        for threshold in ("high", "medium"):
            targets = non_keepers_for_aggressive_delete(
                rows, keepers={"/p/keep.jpg"}, confirm_below=threshold
            )
            assert "/p/low.jpg" not in targets, threshold

    def test_plan_auto_select_auto_stamps_no_keeper_in_a_below_threshold_group(self):
        """Catches: the keeper of an uncertain group is stamped KEEP anyway,
        which is the PR-3 bug (auto mode decides a group it cannot judge).
        g1 holds a REVIEW/high row so at "high" it gets no stamp; g2 is all EXACT
        and is stamped."""
        from core.services.auto_select import plan_auto_select

        rows = [
            _Row("/p/a1.jpg", "g1", 0.9, "high", "REVIEW_DUPLICATE"),
            _Row("/p/a2.jpg", "g1", 0.4, "high", "EXACT"),
            _Row("/p/b1.jpg", "g2", 0.8, "high", "EXACT"),
            _Row("/p/b2.jpg", "g2", 0.3, "high", "EXACT"),
        ]
        stamped, deletes = plan_auto_select(
            rows, aggressive=False, confirm_below="high"
        )
        assert stamped == {"/p/b1.jpg"}
        assert deletes == set()

    def test_plan_auto_select_aggressive_never_deletes_the_unstamped_keeper(self):
        """Catches: an unstamped keeper is treated as an ordinary non-keeper and
        deleted. Delete protection uses every group's top scorer, stamped or not,
        so the uncertain group's keeper survives while its EXACT copy is removed."""
        from core.services.auto_select import plan_auto_select

        rows = [
            _Row("/p/a1.jpg", "g1", 0.9, "high", "REVIEW_DUPLICATE"),
            _Row("/p/a2.jpg", "g1", 0.4, "high", "EXACT"),
        ]
        stamped, deletes = plan_auto_select(
            rows, aggressive=True, confirm_below="high"
        )
        assert "/p/a1.jpg" not in stamped
        assert "/p/a1.jpg" not in deletes
        assert deletes == {"/p/a2.jpg"}

    def test_plan_auto_select_aggressive_mixed_group_deletes_only_admitted_rows(self):
        """Catches: a below-threshold row (REVIEW/high at "high") is deleted
        just because its group has an admitted EXACT copy. The group needs a look,
        so it gets no stamp; only the admitted EXACT non-keeper is deleted."""
        from core.services.auto_select import plan_auto_select

        rows = [
            _Row("/p/keep.jpg", "g1", 0.9, "high", "EXACT"),
            _Row("/p/copy.jpg", "g1", 0.5, "high", "EXACT"),
            _Row("/p/near.jpg", "g1", 0.4, "high", "REVIEW_DUPLICATE"),
        ]
        stamped, deletes = plan_auto_select(
            rows, aggressive=True, confirm_below="high"
        )
        assert stamped == set()
        assert deletes == {"/p/copy.jpg"}

    def test_groups_needing_look_ignores_unrated_rows(self):
        """Catches: an unrated row (no level, e.g. a Ref-tier passenger) marks
        its whole group as needing a look. g2 has only unrated rows and must
        stay quiet; g1's REVIEW row counts at "high" but not at "medium"."""
        from core.services.auto_select import groups_needing_look

        rows = [
            _Row("/p/a1.jpg", "g1", 0.9, "high", "REVIEW_DUPLICATE"),
            _Row("/p/a2.jpg", "g1", 0.4, "high", "EXACT"),
            _Row("/p/b1.jpg", "g2", 0.5, None, ""),
        ]
        assert groups_needing_look(rows, "high") == {"g1"}
        assert groups_needing_look(rows, "medium") == set()

    def test_resolve_confirm_below_falls_back_to_high(self):
        """Catches: a junk or missing stored value widening the threshold. Only
        "high" and "medium" are accepted; anything else resolves to "high"."""
        from core.services.auto_select import resolve_confirm_below

        assert resolve_confirm_below(None) == "high"
        assert resolve_confirm_below("junk") == "high"
        assert resolve_confirm_below("low") == "high"
        assert resolve_confirm_below("medium") == "medium"

    def test_build_writes_without_lock_returns_no_lock_states(self):
        """Catches: the scan path still locking its keepers. PR-3 writes the
        KEEP stamp without a lock; lock_keepers=True keeps the best-copy lock."""
        from core.services.auto_select import build_auto_select_writes

        decisions, locks = build_auto_select_writes({"/a"}, lock_keepers=False)
        assert decisions == {"/a": ""}
        assert locks == {}
        _, locks_default = build_auto_select_writes({"/a"})
        assert locks_default == {"/a": True}


class TestRuleToggles:
    """PR-4a — the B3 / B4 / B8 rules narrow the aggressive delete set, and
    ``rule_trace`` names the rule behind each decision. Built on the same
    rows as the classes above; the default call keeps the legacy set."""

    @staticmethod
    def _scene():
        """A B4 group (RAW keeper, lossy EXACT displaced), a REVIEW group at
        high and medium, and an EXACT copy in its own group."""
        return [
            _Row("/p/keep.dng", "g1", 0.95, match_confidence="high", action="EXACT"),
            _Row("/p/lossy.jpg", "g1", 0.80, match_confidence="high", action="EXACT"),
            _Row("/p/rev_pair.jpg", "g2", 0.90, match_confidence="high",
                 action="REVIEW_DUPLICATE"),
            _Row("/p/rev_other.jpg", "g2", 0.50, match_confidence="high",
                 action="REVIEW_DUPLICATE"),
            _Row("/p/copy_keep.jpg", "g3", 0.90, match_confidence="high", action="EXACT"),
            _Row("/p/copy.jpg", "g3", 0.40, match_confidence="high", action="EXACT"),
        ]

    def test_function_defaults_are_legacy_rules(self):
        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from core.services.auto_select_rules import LEGACY_RULES

        rows = self._scene()
        keepers = top_score_path_per_group(rows)
        default = non_keepers_for_aggressive_delete(rows, keepers, confirm_below="medium")
        explicit = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium", rules=LEGACY_RULES
        )
        assert default == explicit

    def test_b3_off_spares_exact_rows(self):
        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from core.services.auto_select_rules import RuleToggles

        rows = self._scene()
        keepers = top_score_path_per_group(rows)
        rules = RuleToggles(b3=False, b4=True, b5=True, b7=True, b8=False)
        deleted = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium", rules=rules
        )
        assert "/p/copy.jpg" not in deleted
        assert "/p/rev_other.jpg" in deleted

    def test_b3_off_keeps_the_keeper_stamp(self):
        from core.services.auto_select import plan_auto_select
        from core.services.auto_select_rules import RuleToggles

        rows = self._scene()
        rules = RuleToggles(b3=False, b4=True, b5=True, b7=True, b8=True)
        stamped, _ = plan_auto_select(
            rows, aggressive=True, confirm_below="medium", rules=rules
        )
        assert "/p/copy_keep.jpg" in stamped

    def test_b4_off_drops_raw_displaced_protection(self):
        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from core.services.auto_select_rules import RuleToggles

        rows = self._scene()
        keepers = top_score_path_per_group(rows)
        on = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium",
            rules=RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=False),
        )
        off = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium",
            rules=RuleToggles(b3=True, b4=False, b5=True, b7=True, b8=False),
        )
        assert "/p/lossy.jpg" not in on
        assert "/p/lossy.jpg" in off

    def test_b8_on_excludes_review_at_medium(self):
        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from core.services.auto_select_rules import RuleToggles

        rows = self._scene()
        keepers = top_score_path_per_group(rows)
        deleted = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium",
            rules=RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=True),
        )
        assert "/p/rev_other.jpg" not in deleted
        assert "/p/copy.jpg" in deleted

    def test_b8_on_changes_nothing_at_high(self):
        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from core.services.auto_select_rules import RuleToggles

        rows = self._scene()
        keepers = top_score_path_per_group(rows)
        with_b8 = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="high",
            rules=RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=True),
        )
        without_b8 = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="high",
            rules=RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=False),
        )
        assert with_b8 == without_b8

    def test_b8_off_at_medium_equals_517_set(self):
        from core.services.auto_select import (
            non_keepers_for_aggressive_delete,
            top_score_path_per_group,
        )
        from core.services.auto_select_rules import LEGACY_RULES

        rows = self._scene()
        keepers = top_score_path_per_group(rows)
        legacy = non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium", rules=LEGACY_RULES
        )
        assert "/p/rev_other.jpg" in legacy
        assert legacy == non_keepers_for_aggressive_delete(
            rows, keepers, confirm_below="medium"
        )

    def test_rule_trace_attributes_rules(self):
        from core.services.auto_select import plan_auto_select, rule_trace
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = self._scene()
        stamped, _ = plan_auto_select(rows, aggressive=True, confirm_below="medium")
        trace = rule_trace(
            rows, stamped, aggressive=True, confirm_below="medium", rules=DEFAULT_RULES
        )
        assert trace["/p/copy.jpg"] == "B3:delete"
        assert trace["/p/rev_other.jpg"] == "B8:spared"
        assert trace["/p/lossy.jpg"] == "B4:spared"
        assert trace["/p/keep.dng"] == "B1:keep"

        high = rule_trace(
            rows, stamped, aggressive=True, confirm_below="high", rules=DEFAULT_RULES
        )
        # At high the REVIEW row is held back by the threshold, not by B8.
        assert "/p/rev_other.jpg" not in high

    def test_rule_trace_is_keep_only_when_not_aggressive(self):
        from core.services.auto_select import plan_auto_select, rule_trace
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = self._scene()
        stamped, deletes = plan_auto_select(rows, aggressive=False, confirm_below="medium")
        assert deletes == set()
        trace = rule_trace(
            rows, stamped, aggressive=False, confirm_below="medium", rules=DEFAULT_RULES
        )
        assert set(trace.values()) == {"B1:keep"}


def _live_row(path, group, score, *, action="", cid=None, confidence="high"):
    return _Row(
        path, group, score,
        match_confidence=confidence, action=action, content_identifier=cid,
    )


def _live_scene(*, video_cid="C1", still_cid="C1", keep_motion=True):
    """A capture: keeper still + deleted EXACT copy, each with a MOV beside it.

    ``/a`` holds the keeper, ``/b`` the copy. ``keep_motion=False`` drops the
    keeper's MOV, so the copy's MOV is the capture's only motion clip.
    """
    rows = [
        _live_row("/a/IMG_1.HEIC", "g1", 0.90, action="EXACT", cid="C1"),
        _live_row("/b/IMG_1.HEIC", "g1", 0.60, action="EXACT", cid=still_cid),
        _live_row("/b/IMG_1.MOV", "g1", None, cid=video_cid),
    ]
    if keep_motion:
        rows.append(_live_row("/a/IMG_1.MOV", "g1", None, cid="C1"))
    return rows


def _live_plan(rows, rules, *, aggressive=True):
    """Return ``(deletes, trace)`` for the aggressive pass at the high threshold."""
    from core.services.auto_select import (
        plan_auto_select,
        rule_trace,
    )

    stamped, deletes = plan_auto_select(
        rows, aggressive=aggressive, confirm_below="high", rules=rules
    )
    trace = rule_trace(
        rows, stamped, aggressive=aggressive, confirm_below="high", rules=rules
    )
    return deletes, trace


class TestLiveVideoRule:
    """PR-4b rule B6: a Live video follows its photo into aggressive delete.

    Each decision of the design (follower filter, motion guard, several
    stills) has its own test here, besides the cases the design lists.
    """

    def test_b6_video_follows_deleted_photo(self):
        """Catches: B6 leaving the copy's MOV as an orphan passenger in
        the copy folder, the case the rule exists to fix."""
        from core.services.auto_select_rules import DEFAULT_RULES

        deletes, trace = _live_plan(_live_scene(), DEFAULT_RULES)
        assert deletes == {"/b/IMG_1.HEIC", "/b/IMG_1.MOV"}
        assert trace["/b/IMG_1.MOV"] == "B6:delete"

    def test_b6_video_of_keeper_photo_stays(self):
        """Catches: B6 deleting the video of the photo that is kept."""
        from core.services.auto_select_rules import DEFAULT_RULES

        deletes, trace = _live_plan(_live_scene(), DEFAULT_RULES)
        assert "/a/IMG_1.MOV" not in deletes
        assert trace.get("/a/IMG_1.MOV") != "B6:delete"

    @pytest.mark.parametrize(
        ("video_cid", "still_cid"),
        [("C2", "C1"), (None, "C1"), ("C1", None)],
        ids=["cid-mismatch", "video-cid-missing", "still-cid-missing"],
    )
    def test_b6_cid_mismatch_or_missing_not_paired(self, video_cid, still_cid):
        """Catches: pairing by stem alone, which would delete a clip that
        belongs to a different capture."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene(video_cid=video_cid, still_cid=still_cid)
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert "/b/IMG_1.MOV" not in deletes

    def test_b6_off_is_today(self):
        """Catches: B6 changing the delete set when its toggle is off. RuleToggles
        built without b6 must equal the v1 set, so the copy's MOV stays."""
        from core.services.auto_select_rules import RuleToggles

        rules = RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=True)
        deletes, trace = _live_plan(_live_scene(), rules)
        assert deletes == {"/b/IMG_1.HEIC"}
        assert "B6:delete" not in trace.values()

    def test_b6_not_aggressive_no_change(self):
        """Catches: B6 adding deletes outside aggressive mode."""
        from core.services.auto_select_rules import DEFAULT_RULES

        deletes, trace = _live_plan(_live_scene(), DEFAULT_RULES, aggressive=False)
        assert deletes == set()
        assert "B6:delete" not in trace.values()

    def test_b6_video_without_same_stem_still_not_paired(self):
        """Catches: pairing a clip with a still of a different name in the same
        folder, which the rule does not allow."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene()
        rows[2] = _live_row("/b/clip.MOV", "g1", None, cid="C1")
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert "/b/clip.MOV" not in deletes

    def test_b6_video_in_other_folder_not_paired(self):
        """Catches: pairing across folders. A copy's still must not pull in a
        clip that sits in a different directory."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene()
        rows[2] = _live_row("/c/IMG_1.MOV", "g1", None, cid="C1")
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert "/c/IMG_1.MOV" not in deletes

    def test_b6_never_deletes_a_keeper_video(self):
        """Catches: B6 deleting a video that is itself the group keeper, even
        when its still is deleted. The keeper is scored, so it is never a
        follower."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = [
            _live_row("/b/IMG_1(1).MOV", "g1", 0.95, cid="C1"),
            _live_row("/b/IMG_1.HEIC", "g1", 0.60, action="EXACT", cid="C1"),
        ]
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert "/b/IMG_1(1).MOV" not in deletes

    def test_b6_photo_spared_by_threshold_keeps_video(self):
        """Catches: B6 following a photo the threshold already spared. A video
        follows only a photo that is itself in the delete set."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene()
        rows[1] = _live_row(
            "/b/IMG_1.HEIC", "g1", 0.60, action="REVIEW_DUPLICATE", cid="C1"
        )
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert "/b/IMG_1.HEIC" not in deletes
        assert "/b/IMG_1.MOV" not in deletes

    def test_decision1_exact_video_follows_when_b3_on(self):
        """Decision 1: an EXACT byte-copy video follows its deleted photo while
        B3 is on, so a copy folder keeps no orphan clip."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene()
        rows[2] = _live_row("/b/IMG_1.MOV", "g1", None, action="EXACT", cid="C1")
        deletes, trace = _live_plan(rows, DEFAULT_RULES)
        assert "/b/IMG_1.MOV" in deletes
        assert trace["/b/IMG_1.MOV"] == "B6:delete"

    def test_decision1_exact_video_spared_when_b3_off(self):
        """Decision 1: with B3 off an EXACT video is left alone. Its photo is
        deleted here (a REVIEW_DUPLICATE with B8 off, at the medium threshold),
        so only the EXACT-video clause keeps the MOV."""
        from core.services.auto_select import plan_auto_select
        from core.services.auto_select_rules import RuleToggles

        rules = RuleToggles(b3=False, b4=True, b5=True, b7=True, b8=False, b6=True)
        rows = [
            _live_row("/a/IMG_1.HEIC", "g1", 0.90, action="REVIEW_DUPLICATE", cid="C1"),
            _live_row("/b/IMG_1.HEIC", "g1", 0.60, action="REVIEW_DUPLICATE", cid="C1"),
            _live_row("/a/IMG_1.MOV", "g1", None, cid="C1"),
            _live_row("/b/IMG_1.MOV", "g1", None, action="EXACT", cid="C1"),
        ]
        _, deletes = plan_auto_select(
            rows, aggressive=True, confirm_below="medium", rules=rules
        )
        assert "/b/IMG_1.HEIC" in deletes
        assert "/b/IMG_1.MOV" not in deletes

    def test_decision3_video_follows_when_every_still_is_deleted(self):
        """Decision 3: two qualifying stills, both in the delete set, so the
        video follows. Requiring only one still to be deleted would miss this."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene()
        rows.append(
            _live_row("/b/IMG_1.JPG", "g1", 0.55, action="EXACT", cid="C1")
        )
        deletes, trace = _live_plan(rows, DEFAULT_RULES)
        assert {"/b/IMG_1.HEIC", "/b/IMG_1.JPG", "/b/IMG_1.MOV"} <= deletes
        assert trace["/b/IMG_1.MOV"] == "B6:delete"

    def test_decision2_motion_guard_keeps_last_clip(self):
        """Decision 2: B6 never deletes a capture's last motion clip. With no
        other MOV in the capture, the copy's MOV stays."""
        from core.services.auto_select_rules import DEFAULT_RULES

        deletes, trace = _live_plan(
            _live_scene(keep_motion=False), DEFAULT_RULES
        )
        assert deletes == {"/b/IMG_1.HEIC"}
        assert "B6:delete" not in trace.values()

    def test_decision2_guard_keeps_one_of_two_candidate_clips(self):
        """Decision 2: two candidate MOVs of one capture, neither with a kept
        clip beside it. Exactly one follows and one survives as the clip."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = [
            _live_row("/a/IMG_1.HEIC", "g1", 0.90, action="EXACT", cid="C1"),
            _live_row("/b/IMG_1.HEIC", "g1", 0.60, action="EXACT", cid="C1"),
            _live_row("/b/IMG_1.MOV", "g1", None, cid="C1"),
            _live_row("/b/IMG_1(1).MOV", "g1", None, cid="C1"),
        ]
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert len({"/b/IMG_1.MOV", "/b/IMG_1(1).MOV"} & deletes) == 1

    def test_decision3_video_spared_when_one_still_is_kept(self):
        """Decision 3: a video with two qualifying stills follows only when
        EVERY still is in the delete set. A kept JPG beside the deleted HEIC
        keeps the video."""
        from core.services.auto_select_rules import DEFAULT_RULES

        rows = _live_scene()
        rows.append(
            _live_row("/b/IMG_1.JPG", "g1", 0.55, action="REVIEW_DUPLICATE", cid="C1")
        )
        deletes, _ = _live_plan(rows, DEFAULT_RULES)
        assert "/b/IMG_1.HEIC" in deletes
        assert "/b/IMG_1.JPG" not in deletes
        assert "/b/IMG_1.MOV" not in deletes
