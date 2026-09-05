"""Parity tests: core/app_service/review_view.py vs app/views/tree_model_builder.py.

Each compute_similarity case is validated against tree_model_builder._file_similarity
via the oracle mapping (kind → label), so the headless module stays
behaviourally identical to the Qt tree.
"""

from __future__ import annotations

from dataclasses import dataclass


import app.views.tree_model_builder as tmb
import core.app_service.review_view as rv
from infrastructure.i18n import t


# ---------------------------------------------------------------------------
# Oracle: maps similarity kind → the Qt label the tree would show
# ---------------------------------------------------------------------------

def _kind_to_label(sim: dict) -> str:
    """Convert a compute_similarity dict to the Qt display label.

    This is the PARITY ORACLE — the same mapping tree_model_builder applies:
      percent   → f"{percent}%"
      passenger → f"{percent}*%"
      ref       → t("tree.similarity_ref")
      near_dup  → t("tree.similarity_near_dup")
      none      → t("tree.similarity_passenger")
    """
    kind = sim["kind"]
    pct = sim["percent"]
    if kind == "percent":
        return f"{pct}%"
    if kind == "passenger":
        return f"{pct}*%"
    if kind == "ref":
        return t("tree.similarity_ref")
    if kind == "near_dup":
        return t("tree.similarity_near_dup")
    if kind == "none":
        return t("tree.similarity_passenger")
    raise ValueError(f"Unknown similarity kind: {kind!r}")


# ---------------------------------------------------------------------------
# Minimal PhotoRecord stand-in for tests (no Qt, no DB)
# ---------------------------------------------------------------------------

@dataclass
class _FakeRecord:
    file_path: str
    action: str = ""
    user_decision: str = ""
    is_locked: bool = False
    phash: str | None = None
    hamming_distance: int | None = None
    score: float | None = None
    folder_path: str = "/fake/"
    file_size_bytes: int = 1024
    pixel_width: int | None = None
    pixel_height: int | None = None
    shot_date: object = None
    creation_date: object = None
    # #680 — per-dimension scoring signals; same types/defaults as
    # core.models.PhotoRecord so the fake cannot drift from the real record.
    exif_tag_count: int | None = None
    gps_present: bool = False
    xmp_derived: bool = False


# Phashes for deterministic hamming-distance tests
_PHASH_A = "0" * 16  # 64 zero bits
_PHASH_B = "f" * 16  # 64 one bits  — hamming = 64 → 0%
_PHASH_NEAR = "0" * 15 + "f"  # 60 zeros + 4 ones → hamming = 4 → 94%


# ---------------------------------------------------------------------------
# pick_ref_winner parity
# ---------------------------------------------------------------------------

class TestPickRefWinnerParity:
    def test_exact_duplicate_group_no_ref(self):
        items = [
            _FakeRecord("/a.jpg", action="EXACT"),
            _FakeRecord("/b.jpg", action="EXACT"),
        ]
        # Both headless and Qt implementations must explicitly return None.
        rv_result = rv.pick_ref_winner(items)
        qt_result = tmb._pick_ref_winner(items)
        assert rv_result is qt_result          # parity: same object identity (both None)
        assert rv_result is None               # explicit: headless returns None
        assert qt_result is None               # explicit: Qt returns None

        # serialize_groups for an all-EXACT group must mark every row
        # is_ref_winner=False (there is no Ref winner to label).
        class _Group:
            group_number = 1
            items = []  # filled below
        g = _Group()
        g.items = items
        serialized = rv.serialize_groups([g])
        for row in serialized[0]["items"]:
            assert row["is_ref_winner"] is False, (
                f"Expected is_ref_winner=False for all-EXACT group, got True on {row['file_path']!r}"
            )

    def test_single_ref_tier_winner(self):
        items = [
            _FakeRecord("/ref.jpg", action="", score=0.9),
            _FakeRecord("/dup.jpg", action="REVIEW_DUPLICATE"),
        ]
        rv_winner = rv.pick_ref_winner(items)
        qt_winner = tmb._pick_ref_winner(items)
        assert rv_winner is qt_winner
        assert rv_winner is items[0]

    def test_score_based_tiebreak(self):
        """Higher score wins among Ref-tier items."""
        items = [
            _FakeRecord("/low.jpg", action="", score=0.3),
            _FakeRecord("/high.jpg", action="", score=0.9),
            _FakeRecord("/dup.jpg", action="REVIEW_DUPLICATE"),
        ]
        rv_winner = rv.pick_ref_winner(items)
        qt_winner = tmb._pick_ref_winner(items)
        assert rv_winner is qt_winner
        assert rv_winner is items[1]  # highest score

    def test_path_lexicographic_tiebreak_when_score_equal(self):
        """When scores are equal, lex-min file_path wins."""
        items = [
            _FakeRecord("/z.jpg", action="", score=0.5),
            _FakeRecord("/a.jpg", action="", score=0.5),
        ]
        rv_winner = rv.pick_ref_winner(items)
        qt_winner = tmb._pick_ref_winner(items)
        assert rv_winner is qt_winner
        assert rv_winner is items[1]  # "/a.jpg" < "/z.jpg"

    def test_unscored_loses_to_scored(self):
        """Unscored (score=None) ranks last among Ref-tier items."""
        items = [
            _FakeRecord("/scored.jpg", action="", score=0.1),
            _FakeRecord("/unscored.jpg", action=""),
        ]
        rv_winner = rv.pick_ref_winner(items)
        qt_winner = tmb._pick_ref_winner(items)
        assert rv_winner is qt_winner
        assert rv_winner is items[0]

    def test_keep_action_is_ref_tier(self):
        items = [
            _FakeRecord("/keep.jpg", action="KEEP", score=0.8),
            _FakeRecord("/dup.jpg", action="REVIEW_DUPLICATE"),
        ]
        rv_winner = rv.pick_ref_winner(items)
        qt_winner = tmb._pick_ref_winner(items)
        assert rv_winner is qt_winner
        assert rv_winner is items[0]

    def test_undated_action_is_ref_tier(self):
        items = [
            _FakeRecord("/undated.jpg", action="UNDATED", score=0.5),
        ]
        rv_winner = rv.pick_ref_winner(items)
        qt_winner = tmb._pick_ref_winner(items)
        assert rv_winner is qt_winner


# ---------------------------------------------------------------------------
# compute_similarity parity
# ---------------------------------------------------------------------------

class TestComputeSimilarityParity:
    def _assert_parity(self, action, record, is_ref_winner, ref_phash):
        sim = rv.compute_similarity(action, record, is_ref_winner, ref_phash)
        qt_label = tmb._file_similarity(action, record, is_ref_winner, ref_phash)
        headless_label = _kind_to_label(sim)
        assert headless_label == qt_label, (
            f"Similarity mismatch for action={action!r}, "
            f"is_ref_winner={is_ref_winner}, ref_phash={ref_phash!r}: "
            f"headless={headless_label!r} != qt={qt_label!r}"
        )
        return sim

    def test_exact_action_gives_100_percent(self):
        rec = _FakeRecord("/a.jpg", action="EXACT")
        sim = self._assert_parity("EXACT", rec, False, None)
        assert sim == {"kind": "percent", "percent": 100}

    def test_review_duplicate_with_phash_recomputed(self):
        # 4 bits different → (64-4)/64*100 = 93.75 → round = 94
        rec = _FakeRecord("/b.jpg", action="REVIEW_DUPLICATE", phash=_PHASH_NEAR)
        sim = self._assert_parity("REVIEW_DUPLICATE", rec, False, _PHASH_A)
        assert sim["kind"] == "percent"
        assert sim["percent"] == round((64 - 4) / 64 * 100)

    def test_review_duplicate_falls_back_to_stored_hamming(self):
        """When ref_phash is None and record has stored hamming_distance."""
        rec = _FakeRecord("/b.jpg", action="REVIEW_DUPLICATE", hamming_distance=8)
        sim = self._assert_parity("REVIEW_DUPLICATE", rec, False, None)
        assert sim["kind"] == "percent"
        assert sim["percent"] == round((64 - 8) / 64 * 100)

    def test_review_duplicate_near_dup_when_no_phash(self):
        """No phash on either side → near_dup kind."""
        rec = _FakeRecord("/b.jpg", action="REVIEW_DUPLICATE")
        sim = self._assert_parity("REVIEW_DUPLICATE", rec, False, None)
        assert sim == {"kind": "near_dup", "percent": None}

    def test_ref_winner_returns_ref_kind(self):
        rec = _FakeRecord("/ref.jpg", action="")
        sim = self._assert_parity("", rec, True, None)
        assert sim == {"kind": "ref", "percent": None}

    def test_ref_tier_passenger_with_phash_gives_starred_percent(self):
        """Ref-tier non-winner + phash → passenger kind with percent."""
        rec = _FakeRecord("/passenger.jpg", action="KEEP", phash=_PHASH_NEAR)
        sim = self._assert_parity("KEEP", rec, False, _PHASH_A)
        assert sim["kind"] == "passenger"
        assert sim["percent"] == round((64 - 4) / 64 * 100)

    def test_ref_tier_passenger_no_phash_gives_none_kind(self):
        """Ref-tier non-winner + no phash → none kind ('—')."""
        rec = _FakeRecord("/mov.mov", action="")
        sim = self._assert_parity("", rec, False, None)
        assert sim == {"kind": "none", "percent": None}

    def test_keep_action_ref_winner(self):
        rec = _FakeRecord("/keep.jpg", action="KEEP")
        sim = self._assert_parity("KEEP", rec, True, _PHASH_A)
        assert sim["kind"] == "ref"

    def test_undated_action_ref_winner(self):
        rec = _FakeRecord("/undated.jpg", action="UNDATED")
        sim = self._assert_parity("UNDATED", rec, True, None)
        assert sim["kind"] == "ref"


# ---------------------------------------------------------------------------
# serialize_groups shape
# ---------------------------------------------------------------------------

class TestSerializeGroups:
    def _make_group(self, items):
        """Build a minimal group-like object."""
        class _Group:
            def __init__(self, n, its):
                self.group_number = n
                self.items = its
        return _Group(1, items)

    def test_basic_shape(self):
        items = [
            _FakeRecord("/ref.jpg", action="", score=0.9, folder_path="/photos/"),
            _FakeRecord("/dup.jpg", action="REVIEW_DUPLICATE", hamming_distance=5),
        ]
        group = self._make_group(items)
        result = rv.serialize_groups([group])
        assert len(result) == 1
        g = result[0]
        assert g["group_number"] == 1
        assert g["member_count"] == 2
        assert len(g["items"]) == 2

    def test_ref_winner_flagged(self):
        items = [
            _FakeRecord("/ref.jpg", action="", score=0.9),
            _FakeRecord("/dup.jpg", action="REVIEW_DUPLICATE"),
        ]
        group = self._make_group(items)
        result = rv.serialize_groups([group])
        rows = result[0]["items"]
        assert rows[0]["is_ref_winner"] is True
        assert rows[1]["is_ref_winner"] is False

    def test_all_exact_group_no_ref_winner(self):
        """All-EXACT group: no Ref winner — all rows are 'not is_ref_winner'."""
        items = [
            _FakeRecord("/a.jpg", action="EXACT"),
            _FakeRecord("/b.jpg", action="EXACT"),
        ]
        group = self._make_group(items)
        result = rv.serialize_groups([group])
        rows = result[0]["items"]
        assert all(not r["is_ref_winner"] for r in rows)

    def test_file_row_keys(self):
        """FileRow must contain all required keys."""
        required_keys = {
            "file_path", "basename", "folder", "action", "user_decision",
            "is_locked", "is_ref_winner", "similarity", "score",
            "file_size_bytes", "pixel_width", "pixel_height",
            "shot_date", "creation_date", "phash", "hamming_distance",
            "thumbnail_url",
            "exif_tag_count", "gps_present", "xmp_derived",
        }
        items = [_FakeRecord("/x.jpg", action="")]
        group = self._make_group(items)
        result = rv.serialize_groups([group])
        row = result[0]["items"][0]
        assert required_keys <= set(row.keys())

    def test_thumbnail_url_format(self):
        items = [_FakeRecord("/photos/a.jpg", action="")]
        group = self._make_group(items)
        result = rv.serialize_groups([group])
        url = result[0]["items"][0]["thumbnail_url"]
        assert url.startswith("/api/image?path=")
        assert "size=512" in url
        # path must be URL-encoded
        assert "%2F" in url or "photos" in url

    def test_legacy_keep_normalized(self):
        """user_decision='keep' must be normalized to '' in the output."""
        items = [_FakeRecord("/x.jpg", action="", user_decision="keep")]
        group = self._make_group(items)
        result = rv.serialize_groups([group])
        assert result[0]["items"][0]["user_decision"] == ""

    def test_empty_groups_list(self):
        assert rv.serialize_groups([]) == []


# ---------------------------------------------------------------------------
# #680 — per-dimension scoring signals cross the API boundary
# ---------------------------------------------------------------------------

class TestScoringSignalSerialisation:
    """The three raw signals behind ``score`` must reach GET /api/manifest.

    Without them the web s42 driver can only assert the composite score, so a
    regression that silently kills ONE dimension (e.g. the exiftool args lose
    ``-GPSLatitude``, or the extended census pass is starved as in #556) still
    produces a plausible composite and goes unnoticed — the desktop s42 driver
    catches it only because it reads the SQLite columns directly.
    """

    def _row(self, record):
        class _Group:
            def __init__(self, its):
                self.group_number = 1
                self.items = its
        return rv.serialize_groups([_Group([record])])[0]["items"][0]

    def test_signals_serialised_from_record(self):
        row = self._row(_FakeRecord(
            "/photos/clean.jpg", action="", score=0.8,
            exif_tag_count=11, gps_present=True, xmp_derived=True,
        ))
        assert row["exif_tag_count"] == 11
        assert row["gps_present"] is True
        assert row["xmp_derived"] is True

    def test_gps_absent_is_false_not_missing(self):
        """A GPS-stripped file must serialise ``false``, not drop the key —
        that distinction is what lets s42 assert the no-GPS variant."""
        row = self._row(_FakeRecord(
            "/photos/no_gps.jpg", action="", score=0.4,
            exif_tag_count=6, gps_present=False,
        ))
        assert "gps_present" in row
        assert row["gps_present"] is False

    def test_exif_tag_count_none_is_preserved(self):
        """None ("census pass did not run") must NOT collapse to 0 ("ran,
        found nothing"). #556 was exactly a NULL exif_tag_count on a
        non-deterministic subset of files; coercing it to 0 would hide that
        class of failure behind a legitimate-looking count."""
        row = self._row(_FakeRecord("/photos/x.jpg", action=""))
        assert row["exif_tag_count"] is None

    def test_signals_present_on_unscored_row(self):
        """Live Photo MOV passengers and isolated rows get score=NULL, but
        extraction still ran, so the signals must still be serialised."""
        row = self._row(_FakeRecord(
            "/photos/IMG_0001.MOV", action="", score=None,
            exif_tag_count=3, gps_present=True,
        ))
        assert row["score"] is None
        assert row["exif_tag_count"] == 3
        assert row["gps_present"] is True

    def test_sqlite_integer_is_coerced_to_json_bool(self):
        """SQLite hands back INTEGER 0/1 for the two NOT NULL columns. Passing
        those through raw would put ``1`` in the JSON, breaking any strict
        ``=== true`` check on the TypeScript side (FileRow types them boolean)."""
        row = self._row(_FakeRecord(
            "/photos/x.jpg", action="", gps_present=1, xmp_derived=0,
        ))
        assert row["gps_present"] is True
        assert row["xmp_derived"] is False
