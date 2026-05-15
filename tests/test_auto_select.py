"""Tests for core/services/auto_select.py (photo-manager#212).

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
        from MOVE, breaking the move-to-destination flow."""
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
                dest_path=None,
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
                dest_path=None,
                action="MOVE",
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
