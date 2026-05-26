"""Tests for core/services/auto_select.py (photo-manager#212, #393).

Each test catches a real user-visible bug — see the docstring on every
case below for the failure mode it pins. No defensive-branch padding.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from core.services.auto_select import (
    apply_auto_select_decisions,
    top_score_path_per_group,
)


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


# ---------------------------------------------------------------------------
# apply_auto_select_decisions (#393)
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, paths: list[str]) -> Path:
    """Build a minimal SQLite manifest with one row per path.

    Uses the full schema from infrastructure.manifest_repository so
    the auto-migrate ALTER TABLE path in ``ManifestRepository.load``
    is a no-op against this DB — every column is already present.
    """
    from infrastructure.manifest_repository import _MIGRATIONS

    manifest = tmp_path / "manifest.sqlite"
    conn = sqlite3.connect(str(manifest))
    try:
        cols_sql = ",\n  ".join(f"{col} {ddl}" for col, ddl in _MIGRATIONS)
        conn.execute(
            "CREATE TABLE migration_manifest (\n"
            "  source_path TEXT PRIMARY KEY,\n"
            f"  {cols_sql}\n"
            ")"
        )
        for p in paths:
            conn.execute(
                "INSERT INTO migration_manifest (source_path) VALUES (?)",
                (p,),
            )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _read_decisions_and_locks(manifest: Path) -> dict[str, tuple[str, int]]:
    """Return ``{source_path: (user_decision, is_locked)}`` for the manifest."""
    conn = sqlite3.connect(str(manifest))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, is_locked "
            "FROM migration_manifest"
        ).fetchall()
    finally:
        conn.close()
    return {p: (d or "", lk) for p, d, lk in rows}


class TestApplyAutoSelectDecisions:
    """The helper writes ``user_decision=""`` (canonical keep — empty
    string, NOT the literal "keep") + ``is_locked=1`` on every keeper.
    The original ``action`` column is not touched — that remains the
    classifier's job, set during the scan pipeline before this helper
    runs.

    #425: pre-canonicalisation this wrote the literal "keep" string,
    which leaked into the tree Action column as raw text. The expected
    state for keepers is now ("", 1) — empty decision + locked.
    """

    def test_keepers_get_canonical_empty_decision_and_lock(self, tmp_path):
        """Catches: helper writes user_decision but forgets the lock
        (or vice versa). The whole point of #393 is the lock badge —
        if it doesn't get written, the feature has shipped invisibly
        again. Two keepers + one untouched non-keeper proves both
        writes hit the right rows AND don't bleed onto unrelated rows.

        #425: also catches regression where this helper would write
        the literal "keep" string instead of the canonical "".
        """
        manifest = _make_manifest(
            tmp_path,
            ["/grp1/keeper.jpg", "/grp1/dup.jpg", "/grp2/keeper.jpg"],
        )
        apply_auto_select_decisions(
            str(manifest),
            keepers={"/grp1/keeper.jpg", "/grp2/keeper.jpg"},
        )
        state = _read_decisions_and_locks(manifest)
        # #425 — keepers get user_decision="" (canonical) + is_locked=1.
        # The literal "keep" string MUST NOT be persisted; that's the
        # leak this fix is closing.
        assert state["/grp1/keeper.jpg"] == ("", 1)
        assert state["/grp2/keeper.jpg"] == ("", 1)
        # Non-keeper stays at the schema defaults; the helper must not
        # touch rows whose path wasn't in keepers.
        assert state["/grp1/dup.jpg"] == ("", 0)

    def test_aggressive_marks_non_keepers_delete_without_locking(self, tmp_path):
        """Catches: aggressive path writes is_locked=1 on non-keepers
        too, OR forgets the delete write. Non-keepers receive the
        delete decision but NOT the lock — locking them would make the
        decision uneditable in the standard flow, defeating the
        'pre-populated triage, user still confirms' contract."""
        manifest = _make_manifest(
            tmp_path,
            ["/g/keeper.jpg", "/g/dup_a.jpg", "/g/dup_b.jpg"],
        )
        apply_auto_select_decisions(
            str(manifest),
            keepers={"/g/keeper.jpg"},
            non_keepers_for_delete={"/g/dup_a.jpg", "/g/dup_b.jpg"},
        )
        state = _read_decisions_and_locks(manifest)
        # #425 — canonical empty keep (was the literal "keep" string).
        assert state["/g/keeper.jpg"] == ("", 1)
        # Non-keepers: delete decision, NOT locked.
        assert state["/g/dup_a.jpg"] == ("delete", 0)
        assert state["/g/dup_b.jpg"] == ("delete", 0)

    def test_empty_keepers_is_a_noop(self, tmp_path):
        """Catches: helper crashes on empty input, OR writes to all
        rows when keepers is empty. A scan that produced zero scored
        groups (e.g. every group is all-MOV-passengers) must produce a
        clean no-op so the worker doesn't have to wrap the call in an
        outer if-guard. Schema defaults stay everywhere."""
        manifest = _make_manifest(tmp_path, ["/a.jpg", "/b.jpg"])
        apply_auto_select_decisions(str(manifest), keepers=set())
        state = _read_decisions_and_locks(manifest)
        assert state == {"/a.jpg": ("", 0), "/b.jpg": ("", 0)}

    def test_empty_non_keepers_set_is_handled_like_none(self, tmp_path):
        """Catches: helper treats ``set()`` differently from ``None``.
        Both must skip the delete writes — passing an empty set is a
        natural caller idiom (build the set conditionally, pass it
        through) and shouldn't behave differently from the explicit
        non-aggressive default."""
        manifest = _make_manifest(
            tmp_path, ["/k.jpg", "/d.jpg"]
        )
        apply_auto_select_decisions(
            str(manifest),
            keepers={"/k.jpg"},
            non_keepers_for_delete=set(),
        )
        state = _read_decisions_and_locks(manifest)
        # #425 — canonical empty keep.
        assert state["/k.jpg"] == ("", 1)
        # No delete write fired for the empty set.
        assert state["/d.jpg"] == ("", 0)

    def test_migrates_legacy_manifest_missing_is_locked_column(self, tmp_path):
        """Catches: helper assumes ``is_locked`` already exists, hitting
        a sqlite3.OperationalError on a freshly-scanned manifest. This
        is the production failure mode discovered in s49 local-run:
        ``scanner.manifest.write_manifest`` writes the original DDL
        (no ``is_locked``), and the lazy ALTER lives in
        ``ManifestRepository.load()``. Auto-select fires BEFORE the
        first load, so without an explicit migrate the column doesn't
        exist when we try to UPDATE it — silent failure mode is the
        worker's ``failed`` signal firing instead of ``finished``."""
        from scanner.manifest import _DDL

        # Build a manifest with ONLY the original schema (one DDL
        # statement matching what write_manifest creates) — no is_locked
        # column. Mirrors the on-disk state immediately after a scan.
        manifest = tmp_path / "legacy.sqlite"
        conn = sqlite3.connect(str(manifest))
        try:
            # _DDL is a multi-statement script (CREATE TABLE + indexes).
            conn.executescript(_DDL)
            conn.execute(
                "INSERT INTO migration_manifest (source_path, source_label, "
                "action) VALUES (?, ?, ?)",
                ("/k.jpg", "src", "KEEP"),
            )
            conn.commit()
        finally:
            conn.close()

        # Verify the precondition the test is pinning: is_locked must
        # NOT exist before the helper runs.
        conn = sqlite3.connect(str(manifest))
        try:
            cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(migration_manifest)"
                )
            }
        finally:
            conn.close()
        assert "is_locked" not in cols, (
            "test precondition broken: write_manifest's schema now "
            "includes is_locked — update _DDL or this test's premise"
        )

        # Now the actual contract: helper migrates + writes.
        apply_auto_select_decisions(str(manifest), keepers={"/k.jpg"})
        state = _read_decisions_and_locks(manifest)
        # #425 — canonical empty keep.
        assert state["/k.jpg"] == ("", 1)

    def test_writes_are_persistent_across_connection(self, tmp_path):
        """Catches: helper forgets to commit, or commit is in the
        wrong scope so the writes vanish on connection close. Open a
        fresh connection (mimicking what the next manifest load does)
        and confirm the keep+lock writes survived."""
        manifest = _make_manifest(tmp_path, ["/k.jpg"])
        apply_auto_select_decisions(str(manifest), keepers={"/k.jpg"})
        # Fresh connection — proves the commit hit disk, not just
        # in-memory state of a still-open writer.
        conn = sqlite3.connect(str(manifest))
        try:
            row = conn.execute(
                "SELECT user_decision, is_locked FROM migration_manifest "
                "WHERE source_path=?",
                ("/k.jpg",),
            ).fetchone()
        finally:
            conn.close()
        # #425 — canonical empty keep.
        assert row == ("", 1)
