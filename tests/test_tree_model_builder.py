"""Tests for app/views/tree_model_builder — Qt model construction.

Pure-logic helpers (`_hamming_to_pct`, `_file_similarity`) are tested
directly; `build_model` is exercised end-to-end with synthetic
PhotoGroup data and the resulting QStandardItemModel is inspected.
"""

from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

import pytest

from app.views.tree_model_builder import (
    _ACTION_SORT,
    _DECISION_SORT,
    _action_display,
    _file_similarity,
    _hamming_to_pct,
    _nearest_member,
    _pick_ref_winner,
    build_model,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _rec(**overrides):
    """Build a minimal record-shaped object using attributes the builder reads."""
    base = dict(
        file_path="/photos/a.jpg",
        folder_path="/photos",
        file_size_bytes=12345,
        action="",
        user_decision="",
        is_locked=False,
        hamming_distance=None,
        shot_date=None,
        creation_date=None,
        pixel_width=None,
        pixel_height=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _group(items, group_number=1):
    return SimpleNamespace(group_number=group_number, items=items)


# ── _hamming_to_pct ────────────────────────────────────────────────────────


class TestHammingToPct:
    def test_none_returns_placeholder(self):
        assert _hamming_to_pct(None) == "~dup"

    def test_zero_distance_is_100(self):
        assert _hamming_to_pct(0) == "100%"

    def test_full_distance_is_zero_pct(self):
        assert _hamming_to_pct(64) == "0%"

    @pytest.mark.parametrize("hamming,expected", [
        (2, "97%"),    # 62/64 = 0.96875 → 97
        (4, "94%"),    # 60/64 = 0.9375 → 94
        (6, "91%"),
        (32, "50%"),
    ])
    def test_intermediate_values(self, hamming, expected):
        assert _hamming_to_pct(hamming) == expected


# ── _file_similarity ───────────────────────────────────────────────────────


class TestFileSimilarity:
    def test_exact_returns_100(self):
        assert _file_similarity("EXACT", _rec()) == "100%"

    def test_review_duplicate_uses_hamming(self):
        assert _file_similarity("REVIEW_DUPLICATE", _rec(hamming_distance=2)) == "97%"

    def test_review_duplicate_with_no_hamming_returns_placeholder(self):
        assert _file_similarity("REVIEW_DUPLICATE", _rec(hamming_distance=None)) == "~dup"

    # #433 — "MOVE" dropped; "" (the undecided action that replaced it) and an
    # unknown action both still render as Ref via the non-EXACT/REVIEW branch.
    @pytest.mark.parametrize("action", ["KEEP", "UNDATED", "", "FOO_UNKNOWN"])
    def test_other_actions_render_as_ref(self, action):
        assert _file_similarity(action, _rec()) == "Ref"


class TestPassengerRelabel:
    """#536 Direction A (option D) — a Ref-tier passenger shows its similarity to
    the DISPLAYED Ref with a trailing star (a consistent column + a marker that
    it's an indirect/transitive member); build_model adds a tooltip naming the
    nearest member. "—" stays reserved for a passenger with no comparable pHash."""

    REF = "ffffffffffffffff"

    def test_passenger_shows_vs_ref_pct_with_star(self):
        """Headline: a non-winner Ref-tier row shows its % vs the DISPLAYED Ref
        (like a REVIEW_DUPLICATE row) marked with a trailing star."""
        passenger = _rec(action="", phash="ffffffffff0000ff")  # 16 bits from REF → 75%
        result = _file_similarity("", passenger, is_ref_winner=False, ref_phash=self.REF)
        assert result == "75*%"

    def test_star_distinguishes_passenger_from_direct_dup(self):
        """Same distance, same reference (the Ref): a REVIEW_DUPLICATE shows a
        bare %, the passenger shows the SAME % with a star — consistent column,
        distinguishable member type."""
        rec = _rec(phash="ffffffffff0000ff")
        dup = _file_similarity("REVIEW_DUPLICATE", rec, ref_phash=self.REF)
        passenger = _file_similarity("", rec, is_ref_winner=False, ref_phash=self.REF)
        assert dup == "75%"
        assert passenger == "75*%"
        assert passenger == dup.replace("%", "*%")

    def test_no_phash_passenger_falls_back_to_dash(self):
        """A passenger with no comparable pHash (a Live Photo MOV) still renders
        the bare "—" — "—" stays reserved for that case, with no star."""
        mov = _rec(action="", phash=None)
        fallback = _file_similarity("", _rec(action=""), is_ref_winner=False)  # legacy "—"
        result = _file_similarity("", mov, is_ref_winner=False, ref_phash=self.REF)
        assert result == fallback
        assert "*" not in result

    def test_ref_winner_still_renders_ref(self):
        """The chosen Ref is unchanged — only non-winner passengers get a star."""
        ref = _rec(action="", phash=self.REF)
        assert _file_similarity("", ref, is_ref_winner=True, ref_phash=self.REF) == "Ref"

    def test_nearest_member_picks_closest(self):
        """`_nearest_member` (drives the tooltip) returns the CLOSEST other
        member as (item, distance) — catches a max-instead-of-min bug."""
        passenger = _rec(file_path="/a.jpg", action="", phash="ffffffffffffffff")
        near = _rec(file_path="/b.jpg", action="", phash="fffffffffffffffc")    # 2 bits
        nearer = _rec(file_path="/c.jpg", action="", phash="fffffffffffffffe")  # 1 bit
        item, dist = _nearest_member(passenger, [passenger, near, nearer])
        assert item is nearer and dist == 1

    def test_nearest_member_excludes_self_by_identity(self):
        """Self excluded by IDENTITY not pHash equality: a pixel-identical twin
        (distance 0) is still picked, but the row itself is not."""
        passenger = _rec(file_path="/a.jpg", action="", phash="ffffffffffffffff")
        twin = _rec(file_path="/b.jpg", action="", phash="ffffffffffffffff")
        item, dist = _nearest_member(passenger, [passenger, twin])
        assert item is twin and dist == 0

    def test_nearest_member_none_when_alone_or_no_phash(self):
        """No comparable peer → None (drives the "—" fallback + no tooltip)."""
        solo = _rec(action="", phash="ffffffffffffffff")
        assert _nearest_member(solo, [solo]) is None
        assert _nearest_member(_rec(phash=None), [_rec(phash="ffffffffffffffff")]) is None


# ── #253: % against the displayed Ref, not the scanner's anchor ────────────


class TestFileSimilarityAgainstDisplayedRef:
    """#253 — REVIEW_DUPLICATE rows render % measured against the Ref
    winner ``_pick_ref_winner`` selects, NOT the scanner's anchor whose
    distance lives in ``record.hamming_distance``. After #241 the two
    can diverge: scanner anchors on the lex-first Ref-tier row, but the
    score-aware Ref pick may select a different Ref-tier sibling.
    """

    def test_render_time_recomputation_supersedes_stored_hamming(self):
        """The stored hamming_distance (vs scanner anchor) should be
        ignored when ref_phash is supplied — render-time recomputation
        wins. Concrete divergence: stored=63 (would render "2%") vs
        recomputed=1 (renders "98%"). If the fix walks back, the legacy
        2% would resurface and this test would catch it.
        """
        dup = _rec(
            action="REVIEW_DUPLICATE",
            phash="0000000000000001",
            hamming_distance=63,  # scanner measured this against a different anchor
        )
        result = _file_similarity(
            "REVIEW_DUPLICATE", dup, ref_phash="0000000000000000",
        )
        # round((64-1)/64*100) == round(98.4375) == 98
        assert result == "98%"

    def test_falls_back_to_stored_hamming_when_ref_phash_missing(self):
        """Old manifests pre-date the phash column wiring through
        PhotoRecord; when ref_phash is None the renderer must still
        produce a meaningful % from the legacy stored value rather
        than blanking the cell.
        """
        dup = _rec(
            action="REVIEW_DUPLICATE",
            phash="0000000000000001",
            hamming_distance=2,
        )
        result = _file_similarity(
            "REVIEW_DUPLICATE", dup, ref_phash=None,
        )
        # round((64-2)/64*100) == 97
        assert result == "97%"

    def test_falls_back_to_stored_hamming_when_record_phash_missing(self):
        """Symmetric to the previous case — a row whose own phash is
        None (video / RAW with no thumbnail / hash failure) cannot
        be re-measured, so the stored hamming_distance is the only
        signal available.
        """
        dup = _rec(
            action="REVIEW_DUPLICATE",
            phash=None,
            hamming_distance=4,
        )
        result = _file_similarity(
            "REVIEW_DUPLICATE", dup, ref_phash="0000000000000000",
        )
        # round((64-4)/64*100) == round(93.75) == 94
        assert result == "94%"

    def test_recompute_path_handles_zero_distance(self):
        """A REVIEW_DUPLICATE row whose pHash exactly matches the
        Ref winner's renders 100% — same arithmetic as EXACT but
        reached via the recomputation branch instead of the action
        check, so this catches an off-by-one in the new code path.
        """
        dup = _rec(
            action="REVIEW_DUPLICATE",
            phash="abcdef0123456789",
            hamming_distance=10,  # ignored
        )
        result = _file_similarity(
            "REVIEW_DUPLICATE", dup, ref_phash="abcdef0123456789",
        )
        assert result == "100%"


# ── _pick_ref_winner ───────────────────────────────────────────────────────


class TestPickRefWinner:
    """``_pick_ref_winner`` returns the items_list element that should
    carry the "Ref" label. #253 changed the return type from id() to
    the item itself so the caller can read ``winner.phash`` — the
    score-aware tie-break itself must still match #241's behaviour.
    """

    def test_returns_none_when_no_ref_tier(self):
        only_dups = [
            _rec(file_path="/p/a.jpg", action="REVIEW_DUPLICATE"),
            _rec(file_path="/p/b.jpg", action="EXACT"),
        ]
        assert _pick_ref_winner(only_dups) is None

    def test_returns_the_winner_item_not_just_its_id(self):
        """Regression guard for the API change: previous helper returned
        ``id(item)``. Callers reading ``winner.phash`` would crash on an
        int — this test pins that the returned value is the actual
        record-shaped object.
        """
        ref_a = _rec(file_path="/p/a.jpg", action="", score=0.9, phash="aaaa")
        ref_b = _rec(file_path="/p/b.jpg", action="", score=0.5, phash="bbbb")
        winner = _pick_ref_winner([ref_a, ref_b])
        assert winner is ref_a
        assert getattr(winner, "phash") == "aaaa"

    def test_score_winner_supersedes_lex_order(self):
        """The #241 canonical case: two Ref-tier rows, the lex-first
        (ref_b) loses to the higher-scored ref_a. With this in place,
        ``build_model`` reads the higher-scored row's phash and that
        becomes the basis for #253's render-time recomputation.
        """
        ref_a = _rec(file_path="/p/zzz.jpg", action="", score=0.9)
        ref_b = _rec(file_path="/p/aaa.jpg", action="", score=0.5)
        winner = _pick_ref_winner([ref_a, ref_b])
        assert winner is ref_a


# ── #253: build_model end-to-end against the displayed Ref ─────────────────


class TestBuildModelSimilarityAgainstDisplayedRef:
    """End-to-end through build_model: a group whose score-winner
    differs from where the scanner would have anchored its
    hamming_distance must render the REVIEW_DUPLICATE row's % against
    the score-winner's phash. The stored hamming_distance is left in
    place so old manifests still degrade gracefully, but it is NOT what
    the user sees when phashes are available."""

    def test_review_duplicate_pct_uses_score_winner_phash(self, qapp):
        from app.views.constants import COL_GROUP, COL_NAME, PATH_ROLE
        # Group: two Ref-tier rows (score-winner != lex-first) + one
        # REVIEW_DUPLICATE whose stored hamming was measured against
        # ref_low (the lex-first scanner anchor).
        ref_high = _rec(
            file_path="/p/ref_high.jpg",
            action="",
            score=0.9,
            phash="0000000000000000",
        )
        ref_low = _rec(
            file_path="/p/aaa_ref_low.jpg",  # lex-first
            action="",
            score=0.3,
            phash="ffffffffffffffff",
        )
        # Stored hamming=63 vs ref_low (the scanner's anchor).
        # Distance to the score-winner (ref_high) is 1 → should render 98%.
        dup = _rec(
            file_path="/p/dup.jpg",
            action="REVIEW_DUPLICATE",
            phash="0000000000000001",
            hamming_distance=63,
        )
        model, _ = build_model([_group([ref_high, ref_low, dup])])
        group_row = model.item(0, 0)
        # Find the dup row by file path and read its similarity cell.
        dup_sim = None
        for r in range(group_row.rowCount()):
            name_item = group_row.child(r, COL_NAME)
            if name_item.data(PATH_ROLE) == "/p/dup.jpg":
                dup_sim = group_row.child(r, COL_GROUP).text()
        assert dup_sim == "98%", (
            f"REVIEW_DUPLICATE row should render % against ref_high (score=0.9, "
            f"distance=1 → 98%), not against ref_low (stored hamming=63 → 2%); "
            f"got {dup_sim!r}"
        )

    def test_passenger_renders_star_pct_and_nearest_member_tooltip(self, qapp):
        """#536 Direction A (option D) end-to-end: a passenger row renders its %
        vs the displayed Ref WITH a star, and its tooltip names the NEAREST
        member (not the Ref). Exercises the live build_model wiring + the
        ``tree.similarity_passenger_tooltip`` substitution."""
        from app.views.constants import COL_GROUP, COL_NAME, PATH_ROLE
        ref = _rec(file_path="/p/ref.jpg", action="", score=0.9,
                   phash="0000000000000000")
        passenger = _rec(file_path="/p/passenger.jpg", action="", score=0.3,
                         phash="000000000000ffff")        # 16 bits from ref → 75%
        nearest = _rec(file_path="/p/nearest.jpg", action="REVIEW_DUPLICATE",
                       phash="000000000000fffc")          # 2 bits from passenger → 97%
        model, _ = build_model([_group([ref, passenger, nearest])])
        group_row = model.item(0, 0)
        cell = None
        for r in range(group_row.rowCount()):
            if group_row.child(r, COL_NAME).data(PATH_ROLE) == "/p/passenger.jpg":
                cell = group_row.child(r, COL_GROUP)
        assert cell is not None
        assert cell.text() == "75*%", (
            f"passenger renders % vs the Ref with a star; got {cell.text()!r}"
        )
        tip = cell.toolTip()
        assert "nearest.jpg" in tip and "97%" in tip, (
            f"tooltip should name the nearest member (nearest.jpg, 97%); got {tip!r}"
        )


# ── _ACTION_SORT / _DECISION_SORT mappings ─────────────────────────────────


class TestSortMappings:
    def test_ref_tier_actions_share_top_priority(self):
        # Per #76 + #81: every "Ref"-displayed action sorts at position 1.
        # #433 — the legacy MOVE key was dropped; unique non-duplicate files
        # now carry the empty action "", which is the explicit Ref-tier entry.
        assert _ACTION_SORT["KEEP"] == 1
        assert _ACTION_SORT["UNDATED"] == 1
        assert _ACTION_SORT[""] == 1
        # The dropped MOVE key (and any unknown action) falls to tier 1 via
        # the default-1 rule, so its Ref-tier sort behaviour is preserved.
        assert _ACTION_SORT.get("MOVE", 1) == 1

    def test_exact_before_review_duplicate(self):
        # Per #81: descending similarity within a group (Ref → 100% → near-match).
        assert _ACTION_SORT["EXACT"] < _ACTION_SORT["REVIEW_DUPLICATE"]

    def test_decision_sort_delete_before_keep_before_undecided(self):
        assert _DECISION_SORT["delete"] < _DECISION_SORT["keep"]


# ── build_model ────────────────────────────────────────────────────────────


class TestBuildModel:
    def test_returns_model_and_proxy(self, qapp):
        model, proxy = build_model([])
        assert model is not None
        assert proxy is not None

    def test_headers_set(self, qapp):
        from PySide6.QtCore import Qt
        model, _ = build_model([])
        assert model.columnCount() > 0
        header_text = model.headerData(0, Qt.Orientation.Horizontal)
        assert isinstance(header_text, str)
        assert header_text  # non-empty

    def test_one_group_with_two_files_appears_as_group_row_plus_two_children(self, qapp):
        rec_ref = _rec(file_path="/p/ref.jpg", action="")
        rec_dup = _rec(file_path="/p/dup.jpg", action="REVIEW_DUPLICATE", hamming_distance=4)
        model, _ = build_model([_group([rec_ref, rec_dup])])

        # Top-level: 1 row (the group)
        assert model.rowCount() == 1
        group_row = model.item(0, 0)
        assert "Group 1" in group_row.text()
        # Group has 2 children
        assert group_row.rowCount() == 2

    def test_child_row_similarity_column_shows_ref_or_pct(self, qapp):
        rec_ref = _rec(file_path="/p/ref.jpg", action="")
        rec_exact = _rec(file_path="/p/exact.jpg", action="EXACT")
        rec_review = _rec(
            file_path="/p/near.jpg", action="REVIEW_DUPLICATE", hamming_distance=2
        )
        model, _ = build_model([_group([rec_ref, rec_exact, rec_review])])

        group_row = model.item(0, 0)
        sims = {group_row.child(i, 0).text() for i in range(group_row.rowCount())}
        assert sims == {"Ref", "100%", "97%"}

    def test_size_and_dates_render_in_child_row(self, qapp):
        rec = _rec(
            file_path="/p/a.jpg",
            file_size_bytes=4321,
            shot_date=datetime(2024, 5, 1, 12, 0, 0),
            creation_date=datetime(2024, 5, 1, 11, 0, 0),
            pixel_width=320, pixel_height=240,
        )
        model, _ = build_model([_group([rec])])

        group_row = model.item(0, 0)
        child = group_row.child(0)
        # Find the row's cell texts via the model
        cells = [
            group_row.child(0, c).text() if group_row.child(0, c) else ""
            for c in range(model.columnCount())
        ]
        joined = " ".join(cells)
        assert "4321" in joined
        assert "320×240" in joined
        assert "2024-05-01" in joined  # both dates start with this

    def test_multiple_groups_yield_multiple_top_rows(self, qapp):
        g1 = _group([_rec(file_path="/p/a.jpg")], group_number=1)
        g2 = _group([_rec(file_path="/p/b.jpg")], group_number=2)
        g3 = _group([_rec(file_path="/p/c.jpg")], group_number=3)
        model, _ = build_model([g1, g2, g3])
        assert model.rowCount() == 3

    def test_empty_group_still_yields_a_group_row(self, qapp):
        # Defensive — no items, but still appears as a top-level Group row.
        empty = _group([], group_number=42)
        model, _ = build_model([empty])
        assert model.rowCount() == 1
        assert "Group 42" in model.item(0, 0).text()
        assert model.item(0, 0).rowCount() == 0

    def test_proxy_failure_returns_none_proxy(self, qapp, monkeypatch):
        """If QSortFilterProxyModel construction throws, build_model returns
        (model, None) — the caller is documented to handle proxy=None."""
        from app.views import tree_model_builder as tmb

        class _BoomProxy:
            def __init__(self, *a, **k):
                raise RuntimeError("synthetic proxy failure")

        monkeypatch.setattr(tmb, "QSortFilterProxyModel", _BoomProxy)
        model, proxy = build_model([])
        assert model is not None
        assert proxy is None


# ── _action_display: lock glyph (photo-manager#164) ────────────────────────


class TestActionDisplayUnaffectedByLock:
    """``_action_display`` returns just the localized decision label —
    the lock indicator moved to its own COL_LOCK column in #182. The
    ``is_locked`` parameter is still accepted for backward compatibility
    but no longer affects the returned text.

    Post-#425: returns ``t()`` lookups for "delete" / "remove_from_list",
    empty string for "" (canonical keep) AND for legacy "keep" (back-
    compat with auto-select writes pre-canonicalisation).
    """

    def test_unlocked_decision_unchanged(self):
        # #425 — was raw "delete"; now goes through t() so the zh_TW
        # locale sees "刪除" instead of leaked English "delete".
        from infrastructure.i18n import t
        assert _action_display("delete", is_locked=False) == t("decision.delete")

    def test_locked_no_longer_prefixes_glyph(self):
        """Pre-#182 this returned ``"🔒 delete"``; post-#182 the glyph
        moved to COL_LOCK and Action shows the bare decision."""
        from infrastructure.i18n import t
        out = _action_display("delete", is_locked=True)
        assert "\U0001F512" not in out
        assert out == t("decision.delete")

    def test_locked_empty_decision_returns_empty(self):
        """Lock-but-undecided no longer renders a glyph in Action;
        COL_LOCK carries the visual signal instead."""
        assert _action_display("", is_locked=True) == ""

    def test_legacy_keep_renders_as_empty(self):
        """#425 back-compat: manifests written before auto-select was
        canonicalised to "" still carry the literal "keep" string.
        Render those as the canonical empty cell so the leak doesn't
        surface to the user."""
        assert _action_display("keep") == ""
        assert _action_display("keep", is_locked=True) == ""

    def test_canonical_empty_keep_renders_as_empty(self):
        """The canonical keep state is the empty string. Confirm
        both the canonical and the legacy literal path render
        identically (empty cell)."""
        assert _action_display("") == ""


class TestLockDisplay:
    """``_lock_display`` is the COL_LOCK cell renderer added in #182."""

    def test_locked_returns_glyph(self):
        from app.views.tree_model_builder import _lock_display
        assert _lock_display(True) == "\U0001F512"

    def test_unlocked_returns_empty(self):
        from app.views.tree_model_builder import _lock_display
        assert _lock_display(False) == ""


class TestLockColumnInBuiltModel:
    """End-to-end: a locked record's row has 🔒 in COL_LOCK and the
    Action column stays as just the decision label."""

    def test_lock_column_renders_glyph_and_action_is_clean(self, qapp):
        from app.views.constants import COL_ACTION, COL_LOCK, COL_NAME, PATH_ROLE

        locked_rec = _rec(file_path="/p/locked.jpg",
                          user_decision="delete", is_locked=True)
        unlocked_rec = _rec(file_path="/p/free.jpg",
                            user_decision="delete", is_locked=False)
        g = _group([locked_rec, unlocked_rec])
        model, _ = build_model([g])

        group_row = model.item(0, 0)
        assert group_row.rowCount() == 2
        locked_action = None
        locked_lock = None
        unlocked_action = None
        unlocked_lock = None
        for r in range(group_row.rowCount()):
            name_item = group_row.child(r, COL_NAME)
            action_item = group_row.child(r, COL_ACTION)
            lock_item = group_row.child(r, COL_LOCK)
            if name_item.data(PATH_ROLE) == "/p/locked.jpg":
                locked_action = action_item.text()
                locked_lock = lock_item.text()
            elif name_item.data(PATH_ROLE) == "/p/free.jpg":
                unlocked_action = action_item.text()
                unlocked_lock = lock_item.text()
        assert locked_action == "delete"  # bare decision, no glyph
        assert "\U0001F512" in locked_lock  # glyph lives in COL_LOCK
        assert unlocked_action == "delete"
        assert unlocked_lock == ""  # empty for unlocked

    def test_lock_column_sort_role(self, qapp):
        """COL_LOCK exposes a 0/1 SORT_ROLE so users can sort by lock
        state. Ascending → unlocked first; descending → locked first."""
        from app.views.constants import COL_LOCK, COL_NAME, PATH_ROLE, SORT_ROLE

        locked_rec = _rec(file_path="/p/locked.jpg",
                          user_decision="", is_locked=True)
        unlocked_rec = _rec(file_path="/p/free.jpg",
                            user_decision="", is_locked=False)
        g = _group([locked_rec, unlocked_rec])
        model, _ = build_model([g])

        group_row = model.item(0, 0)
        for r in range(group_row.rowCount()):
            name_item = group_row.child(r, COL_NAME)
            lock_item = group_row.child(r, COL_LOCK)
            sort_val = lock_item.data(SORT_ROLE)
            if name_item.data(PATH_ROLE) == "/p/locked.jpg":
                assert sort_val == 1
            elif name_item.data(PATH_ROLE) == "/p/free.jpg":
                assert sort_val == 0


# ── COL_SCORE — #187 PR 5 ──────────────────────────────────────────────────


class TestScoreColumn:
    """COL_SCORE displays a 2-decimal float for scored rows and an em-dash
    for unscored rows (Live Photo MOV passengers, isolated rows, old
    manifests). The SORT_ROLE carries the numeric value so within-group
    and inter-group sort by score work correctly under the proxy.
    """

    def test_score_cell_text_for_scored_row(self):
        from app.views.constants import COL_SCORE, SORT_ROLE
        from app.views.tree_model_builder import build_model

        a = _rec(file_path="/p/a.jpg", score=0.87)
        b = _rec(file_path="/p/b.jpg", score=0.42)
        g = _group([a, b])
        model, _ = build_model([g])
        group_row = model.item(0, 0)
        # Two file rows under the group
        assert group_row.rowCount() == 2
        for r in range(group_row.rowCount()):
            score_item = group_row.child(r, COL_SCORE)
            # Real scores render with two decimals
            assert score_item.text() in {"0.87", "0.42"}

    def test_score_cell_text_for_none_score(self):
        """Unscored rows (None) render as em-dash, not empty / not '0.00'."""
        from app.views.constants import COL_SCORE
        from app.views.tree_model_builder import build_model

        a = _rec(file_path="/p/a.jpg", score=None)
        g = _group([a])
        model, _ = build_model([g])
        group_row = model.item(0, 0)
        score_item = group_row.child(0, COL_SCORE)
        assert score_item.text() == "—"

    def test_score_sort_role_is_float_for_scored_rows(self):
        from app.views.constants import COL_SCORE, SORT_ROLE
        from app.views.tree_model_builder import build_model

        a = _rec(file_path="/p/a.jpg", score=0.87)
        g = _group([a])
        model, _ = build_model([g])
        group_row = model.item(0, 0)
        score_item = group_row.child(0, COL_SCORE)
        assert score_item.data(SORT_ROLE) == pytest.approx(0.87)

    def test_score_sort_role_for_unscored_is_below_zero(self):
        """Unscored rows must sort below any real-score row under
        descending order. A negative sentinel guarantees that."""
        from app.views.constants import COL_SCORE, SORT_ROLE
        from app.views.tree_model_builder import build_model

        a = _rec(file_path="/p/a.jpg", score=None)
        g = _group([a])
        model, _ = build_model([g])
        group_row = model.item(0, 0)
        score_item = group_row.child(0, COL_SCORE)
        sort_val = score_item.data(SORT_ROLE)
        assert isinstance(sort_val, float)
        assert sort_val < 0.0  # sentinel below the [0.0, 1.0] real-score range

    def test_group_header_score_is_max_in_group(self):
        """Group-level COL_SCORE.SORT_ROLE = max score across files so
        the column header sort orders groups by their best member."""
        from app.views.constants import COL_SCORE, SORT_ROLE
        from app.views.tree_model_builder import build_model

        low = _rec(file_path="/p/low.jpg", score=0.30)
        high = _rec(file_path="/p/high.jpg", score=0.90)
        g = _group([low, high])
        model, _ = build_model([g])
        group_row_score_item = model.item(0, COL_SCORE)
        assert group_row_score_item.data(SORT_ROLE) == pytest.approx(0.90)

    def test_group_header_score_is_negative_when_all_unscored(self):
        """A group whose every file is unscored (Live Photo group with
        only MOV passengers, or old manifests) gets the negative sentinel
        at group level too — sorts below scored groups under desc."""
        from app.views.constants import COL_SCORE, SORT_ROLE
        from app.views.tree_model_builder import build_model

        only_none = _rec(file_path="/p/x.mov", score=None)
        g = _group([only_none])
        model, _ = build_model([g])
        group_row_score_item = model.item(0, COL_SCORE)
        sort_val = group_row_score_item.data(SORT_ROLE)
        assert sort_val < 0.0


