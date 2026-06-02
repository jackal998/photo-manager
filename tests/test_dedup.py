"""Tests for scanner/dedup.py — duplicate classification logic."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from scanner.dedup import HashResult, ManifestRow, classify
from scanner.walker import FileRecord


def _dt(year: int = 2024, month: int = 6, day: int = 1) -> datetime:
    return datetime(year, month, day, 12, 0, 0)


def _rows(result: list) -> dict:
    """Index ManifestRows by posix source_path (Windows-safe)."""
    return {Path(r.source_path).as_posix(): r for r in result}


def _record(
    path: str,
    source_label: str = "jdrive",
    file_type: str = "jpeg",
    pair_partner: Path | None = None,
    pair_cluster: tuple[Path, ...] | None = None,
) -> FileRecord:
    """Build a FileRecord. ``pair_partner`` is accepted as a back-compat
    alias and converted to a single-member ``pair_cluster``; tests that
    need multi-peer clusters should pass ``pair_cluster`` directly."""
    if pair_cluster is None:
        pair_cluster = (pair_partner,) if pair_partner is not None else ()
    return FileRecord(
        path=Path(path),
        source_label=source_label,
        file_type=file_type,
        pair_cluster=pair_cluster,
    )


def _hr(
    path: str,
    sha256: str = "aaa",
    phash: str | None = "0000000000000000",
    mean_color: str | None = None,
    exif_date: datetime | None = None,
    source_label: str = "jdrive",
    file_type: str = "jpeg",
    pair_partner: Path | None = None,
    pair_cluster: tuple[Path, ...] | None = None,
    dhash: str | None = None,
    pixel_width: int | None = 4000,
    pixel_height: int | None = 3000,
) -> HashResult:
    return HashResult(
        record=_record(
            path, source_label=source_label, file_type=file_type,
            pair_partner=pair_partner, pair_cluster=pair_cluster,
        ),
        sha256=sha256,
        phash=phash,
        dhash=dhash,
        mean_color=mean_color,
        exif_date=exif_date,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
    )


# ---------------------------------------------------------------------------
# EXACT_DUPLICATE
# ---------------------------------------------------------------------------

class TestExactDuplicate:
    def test_lower_priority_source_skipped(self):
        src_a = _hr("/src_a/a.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        src_b = _hr("/src_b/a.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        src_c = _hr("/src_c/a.jpg", sha256="same", source_label="src_c", exif_date=_dt())
        rows = _rows(classify(
            [src_a, src_b, src_c],
            source_priority={"src_a": 0, "src_b": 1, "src_c": 2},
        ))
        assert rows["/src_a/a.jpg"].action == ""   # survivor — undecided (#433: was MOVE)
        assert rows["/src_b/a.jpg"].action == "EXACT"
        assert rows["/src_c/a.jpg"].action == "EXACT"

    def test_skip_points_to_kept_file(self):
        a = _hr("/jdrive/a.jpg", sha256="x", source_label="jdrive", exif_date=_dt())
        b = _hr("/takeout/b.jpg", sha256="x", source_label="takeout", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"takeout": 0, "jdrive": 1}))
        kept = "/takeout/b.jpg"  # takeout priority 0 > jdrive priority 1
        assert Path(rows["/jdrive/a.jpg"].duplicate_of).as_posix() == kept


# ---------------------------------------------------------------------------
# Dynamic source priority
# ---------------------------------------------------------------------------

class TestDynamicSourcePriority:
    def test_first_source_wins_exact_dup(self):
        """Source with priority 0 wins; lower-priority copy gets EXACT."""
        a = _hr("/src_a/photo.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        b = _hr("/src_b/photo.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/src_a/photo.jpg"].action == ""   # #433: survivor undecided
        assert rows["/src_b/photo.jpg"].action == "EXACT"

    def test_second_source_priority_reversed(self):
        """With reversed priority, src_b wins."""
        a = _hr("/src_a/photo.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        b = _hr("/src_b/photo.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"src_a": 1, "src_b": 0}))
        assert rows["/src_b/photo.jpg"].action == ""   # #433: survivor undecided
        assert rows["/src_a/photo.jpg"].action == "EXACT"

    def test_no_source_priority_auto_infers_from_order(self):
        """Without explicit source_priority, first-seen label gets priority 0."""
        first = _hr("/first/photo.jpg", sha256="dup", source_label="first_src",
                    exif_date=_dt())
        second = _hr("/second/photo.jpg", sha256="dup", source_label="second_src",
                     exif_date=_dt())
        rows = _rows(classify([first, second]))   # no source_priority
        assert rows["/first/photo.jpg"].action == ""   # first-seen wins (#433: undecided)
        assert rows["/second/photo.jpg"].action == "EXACT"


# ---------------------------------------------------------------------------
# FORMAT_DUPLICATE
# ---------------------------------------------------------------------------

class TestFormatDuplicate:
    def test_heic_kept_over_jpeg_same_phash(self):
        heic = _hr("/a.heic", sha256="h1", phash="a" * 16, file_type="heic",
                   source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="a" * 16, file_type="jpeg",
                   source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.heic"].action in ("", "KEEP")   # #433: survivor undecided
        assert rows["/a.jpg"].action == "EXACT"

    def test_raw_and_jpeg_both_move(self):
        """RAW + JPEG of same shot must both be kept (complementary rule).

        #433: complementary RAW+lossy survivors are undecided ('') — neither
        is marked EXACT; both stay for the user to triage.
        """
        raw = _hr("/a.arw", sha256="r1", phash="a" * 16, file_type="raw",
                  source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="j1", phash="a" * 16, file_type="jpeg",
                   source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([raw, jpeg]))
        assert rows["/a.arw"].action == ""
        assert rows["/a.jpg"].action == ""

    def test_flat_image_phash_collision_rejected_by_mean_color(self):
        """#462 — flat images (black/white/grey) sharing pHash 8000000000000000
        must NOT be marked EXACT of each other; the format-duplicate path now
        gates on mean_color the same way the near-duplicate path does."""
        flat_hash = "8000000000000000"
        black = _hr("/a.jpg", sha256="b1", phash=flat_hash, file_type="jpeg",
                    mean_color="0,0,0", source_label="takeout", exif_date=_dt())
        white = _hr("/b.jpg", sha256="w1", phash=flat_hash, file_type="jpeg",
                    mean_color="255,255,255", source_label="takeout", exif_date=_dt())
        grey = _hr("/c.jpg", sha256="g1", phash=flat_hash, file_type="jpeg",
                   mean_color="128,128,128", source_label="takeout", exif_date=_dt())
        rows = _rows(classify([black, white, grey]))
        # None should be EXACT of another — mean_color differs >> threshold 30.
        for path in ("/a.jpg", "/b.jpg", "/c.jpg"):
            assert rows[path].action != "EXACT", (
                f"{path}: expected non-EXACT, got {rows[path].action} — "
                f"flat-image pHash collision gate didn't fire"
            )

    def test_format_duplicate_similar_mean_color_still_marked_exact(self):
        """#462 regression guard — HEIC + JPEG with same pHash AND similar
        mean_color must still be marked EXACT (the gate doesn't over-reject
        genuine format duplicates)."""
        heic = _hr("/a.heic", sha256="h1", phash="a" * 16, file_type="heic",
                   mean_color="120,130,140", source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="a" * 16, file_type="jpeg",
                   mean_color="118,132,138", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.jpg"].action == "EXACT"

    def test_format_duplicate_missing_mean_color_falls_back_to_phash_only(self):
        """#462 — if either side lacks mean_color (RAW thumbnail, hash
        failure), the format-duplicate path still marks EXACT; gate skips on
        missing data, matching _classify_near_duplicates' behavior."""
        heic = _hr("/a.heic", sha256="h1", phash="a" * 16, file_type="heic",
                   mean_color=None, source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="a" * 16, file_type="jpeg",
                   mean_color="118,132,138", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.jpg"].action == "EXACT"

    def test_raw_plus_lossy_with_color_mismatch_still_complementary(self):
        """#462 — RAW + lossy with mismatched mean_color must still return
        early (both undecided ''); the gate must not affect the RAW+lossy
        complementary branch (#433: was MOVE)."""
        raw = _hr("/a.arw", sha256="r1", phash="a" * 16, file_type="raw",
                  mean_color="10,20,30", source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="j1", phash="a" * 16, file_type="jpeg",
                   mean_color="200,180,160", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([raw, jpeg]))
        assert rows["/a.arw"].action == ""
        assert rows["/a.jpg"].action == ""


# ---------------------------------------------------------------------------
# REVIEW_DUPLICATE (near-duplicate)
# ---------------------------------------------------------------------------

class TestNearDuplicate:
    def test_near_duplicate_flagged(self):
        import imagehash
        base = imagehash.hex_to_hash("a" * 16)
        # Flip 5 bits → hamming distance 5 (within default threshold 10)
        near = imagehash.hex_to_hash("5" + "a" * 15)
        a = _hr("/a.jpg", sha256="s1", phash=str(base), source_label="takeout",
                exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), source_label="jdrive",
                exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"

    def test_beyond_threshold_not_flagged(self):
        import imagehash
        # hamming distance = 4 (one nibble flipped), but threshold is 3 below
        h1 = imagehash.hex_to_hash("a" * 16)
        h2 = imagehash.hex_to_hash("aaaaaaaaaaaaaaa5")
        a = _hr("/a.jpg", sha256="s1", phash=str(h1), exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(h2), exif_date=_dt())
        # threshold=3 → distance 4 is beyond threshold
        rows = _rows(classify([a, b], threshold=3))
        assert rows["/b.jpg"].action != "REVIEW_DUPLICATE"

    def test_mean_color_mismatch_rejects_false_positive(self):
        """pHash near-duplicate with very different mean_color is NOT flagged."""
        import imagehash
        base = imagehash.hex_to_hash("a" * 16)
        near = imagehash.hex_to_hash("5" + "a" * 15)   # hamming=4, within threshold
        # Mean colors with L2 ≈ 280 (>> threshold 30) — clearly different colors
        a = _hr("/a.jpg", sha256="s1", phash=str(base), mean_color="10,20,30",
                source_label="takeout", exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), mean_color="200,180,160",
                source_label="takeout", exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        assert rows["/b.jpg"].action != "REVIEW_DUPLICATE"

    def test_mean_color_match_confirms_near_duplicate(self):
        """pHash near-duplicate with similar mean_color IS flagged."""
        import imagehash
        base = imagehash.hex_to_hash("a" * 16)
        near = imagehash.hex_to_hash("5" + "a" * 15)   # hamming=4, within threshold
        # Mean colors with L2 ≈ 6 (<< threshold 30) — same color palette
        a = _hr("/a.jpg", sha256="s1", phash=str(base), mean_color="100,120,140",
                source_label="takeout", exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), mean_color="105,118,142",
                source_label="takeout", exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"

    def test_missing_mean_color_falls_back_to_phash_only(self):
        """If mean_color is None for either file, gate is skipped (pHash-only behavior)."""
        import imagehash
        base = imagehash.hex_to_hash("a" * 16)
        near = imagehash.hex_to_hash("5" + "a" * 15)
        a = _hr("/a.jpg", sha256="s1", phash=str(base), mean_color=None,
                source_label="takeout", exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), mean_color=None,
                source_label="takeout", exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        # No mean_color → gate not applied → flagged on pHash alone
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"


# ---------------------------------------------------------------------------
# UNDATED
# ---------------------------------------------------------------------------

class TestUndated:
    def test_no_exif_becomes_undated(self):
        hr = _hr("/jdrive/mystery.jpg", exif_date=None)
        rows = classify([hr])
        assert rows[0].action == "UNDATED"

    def test_undated_file_from_any_source_becomes_undated(self):
        hr = _hr("/any_source/IMG.HEIC", source_label="any_source", exif_date=None)
        rows = classify([hr])
        assert rows[0].action == "UNDATED"


# ---------------------------------------------------------------------------
# Live Photo pair propagation
# ---------------------------------------------------------------------------

class TestLivePhotoPair:
    def test_mov_pairs_with_dup_heic_keeps_own_action(self):
        """When the HEIC is a duplicate of another HEIC, the paired MOV
        is NOT auto-marked as EXACT — it keeps its independent
        classification (here, '' undecided because exif_date is set and
        SHA is unique; #433: was MOVE). Both still share the same
        group_id via the pair edge.

        Per photo-manager#88: pairing is coupled at matching/grouping
        but per-row at set/execute action. The image's destruction is
        no longer automatically the video's — the user makes that call.

        Was: ``test_mov_skipped_when_heic_skipped`` (pre-#88, when
        action propagation forced the MOV to EXACT alongside the HEIC).
        """
        heic_path = Path("/iphone/IMG_1234.HEIC")
        mov_path = Path("/iphone/IMG_1234.MOV")
        orig_heic_path = Path("/jdrive/IMG_1234.HEIC")

        heic = _hr(str(heic_path), sha256="x", source_label="jdrive",
                   file_type="heic", exif_date=_dt(),
                   pair_partner=mov_path)
        mov = _hr(str(mov_path), sha256="y", phash=None,
                  source_label="jdrive", file_type="mov", exif_date=_dt(),
                  pair_partner=heic_path)
        orig = _hr(str(orig_heic_path), sha256="x", source_label="iphone",
                   file_type="heic", exif_date=_dt())

        rows = _rows(classify([heic, mov, orig], source_priority={"iphone": 0, "jdrive": 1}))
        # HEIC duplicate of orig → EXACT (unchanged)
        assert rows[heic_path.as_posix()].action == "EXACT"
        # MOV no longer auto-EXACT — keeps its own '' classification (#433)
        assert rows[mov_path.as_posix()].action == ""
        # But both share a group_id via the pair edge (#88 invariant)
        heic_gid = rows[heic_path.as_posix()].group_id
        mov_gid = rows[mov_path.as_posix()].group_id
        assert heic_gid is not None
        assert mov_gid is not None
        assert heic_gid == mov_gid

    def test_unique_pair_forms_group(self):
        """Headline regression for photo-manager#88 — a Live Photo pair
        where NEITHER side is a duplicate of anything else still forms a
        group of two. Pre-#88 both rows would end up with group_id=None
        and the manifest loader's ``len(db_rows) < 2`` filter would drop
        both, surfacing as ``total_rows=0`` in the tree.
        """
        heic_path = Path("/iphone/IMG_unique.HEIC")
        mov_path = Path("/iphone/IMG_unique.MOV")
        heic = _hr(str(heic_path), sha256="heic-unique", source_label="iphone",
                   file_type="heic", exif_date=_dt(),
                   pair_partner=mov_path)
        mov = _hr(str(mov_path), sha256="mov-unique", phash=None,
                  source_label="iphone", file_type="mov", exif_date=_dt(),
                  pair_partner=heic_path)
        rows = _rows(classify([heic, mov]))

        # Both rows survive into manifest
        assert len(rows) == 2
        # Both classified as '' undecided (unique, exif_date present; #433)
        assert rows[heic_path.as_posix()].action == ""
        assert rows[mov_path.as_posix()].action == ""
        # Both share the same group_id thanks to the pair edge
        heic_gid = rows[heic_path.as_posix()].group_id
        mov_gid = rows[mov_path.as_posix()].group_id
        assert heic_gid is not None, (
            "unique pair must receive a group_id — without one, the "
            "manifest loader filters both rows as singletons (#88)"
        )
        assert mov_gid is not None
        assert heic_gid == mov_gid

    def test_pair_with_separate_sha_groups_unions_correctly(self):
        """The asymmetric case the in-place-mutation approach would have
        missed: HEIC=EXACT(dup of orig_heic) AND MOV=EXACT(dup of
        other_mov). Both partners already have ``duplicate_of`` set by
        Pass 1. Without the edge-list approach + transitive union in
        ``_assign_group_ids``, the pair would be split across two
        SHA-groups.

        Expected: all four files end up in a single component / group_id.
        """
        heic_path = Path("/iphone/IMG.HEIC")
        mov_path = Path("/iphone/IMG.MOV")
        orig_heic_path = Path("/jdrive/IMG.HEIC")
        other_mov_path = Path("/jdrive/IMG.MOV")

        heic = _hr(str(heic_path), sha256="hh", source_label="jdrive",
                   file_type="heic", exif_date=_dt(),
                   pair_partner=mov_path)
        mov = _hr(str(mov_path), sha256="mm", phash=None,
                  source_label="jdrive", file_type="mov", exif_date=_dt(),
                  pair_partner=heic_path)
        orig_heic = _hr(str(orig_heic_path), sha256="hh", source_label="iphone",
                        file_type="heic", exif_date=_dt())
        other_mov = _hr(str(other_mov_path), sha256="mm", phash=None,
                        source_label="iphone", file_type="mov", exif_date=_dt())

        rows = _rows(classify(
            [heic, mov, orig_heic, other_mov],
            source_priority={"iphone": 0, "jdrive": 1},
        ))

        gids = {rows[p].group_id for p in (
            heic_path.as_posix(), mov_path.as_posix(),
            orig_heic_path.as_posix(), other_mov_path.as_posix(),
        )}
        assert None not in gids, "all four must have a group_id"
        assert len(gids) == 1, (
            f"all four (HEIC + MOV pair, plus orig HEIC + other MOV they SHA-match) "
            f"must share one group_id via transitive closure of "
            f"duplicate_of + pair edges; got {gids}"
        )

    def test_multi_member_cluster_shares_one_group_id(self):
        """``IMG_4278.HEIC + IMG_4278.MOV + IMG_4278.MP4`` — Google
        transcoded one Live Photo to both video formats. The walker
        emits a 3-member ``pair_cluster`` on each file; ``_collect_pair_edges``
        unions them into a single component. All three must share one
        group_id.

        Production case from ``D:\\Takeout-0508\\Takeout\\Google 相簿\\2023 年的相片``.
        """
        heic_path = Path("/iphone/IMG_4278.HEIC")
        mov_path = Path("/iphone/IMG_4278.MOV")
        mp4_path = Path("/iphone/IMG_4278.MP4")
        cluster = (heic_path, mov_path, mp4_path)

        heic = _hr(str(heic_path), sha256="h1", source_label="iphone",
                   file_type="heic", exif_date=_dt(),
                   pair_cluster=tuple(p for p in cluster if p != heic_path))
        mov = _hr(str(mov_path), sha256="m1", phash=None,
                  source_label="iphone", file_type="mov", exif_date=_dt(),
                  pair_cluster=tuple(p for p in cluster if p != mov_path))
        mp4 = _hr(str(mp4_path), sha256="p1", phash=None,
                  source_label="iphone", file_type="mp4", exif_date=_dt(),
                  pair_cluster=tuple(p for p in cluster if p != mp4_path))

        rows = _rows(classify([heic, mov, mp4]))
        gids = {rows[p.as_posix()].group_id for p in cluster}
        assert None not in gids, "every cluster member must have a group_id"
        assert len(gids) == 1, (
            f"all three same-exact-stem files must share one group_id; got {gids}"
        )

    def test_pair_actions_independent_when_heic_is_dup(self):
        """When the HEIC is itself a duplicate of another HEIC, the
        paired MOV must NOT inherit the EXACT classification. This
        pins the explicit decoupling intent of #88: the MOV stays
        '' (undecided) / UNDATED based on its own data, even though its
        group_id is shared with the HEIC's group via the pair edge.
        """
        heic_path = Path("/jdrive/IMG_5678.HEIC")
        mov_path = Path("/jdrive/IMG_5678.MOV")
        orig_path = Path("/iphone/IMG_5678.HEIC")
        heic = _hr(str(heic_path), sha256="dup", source_label="jdrive",
                   file_type="heic", exif_date=_dt(),
                   pair_partner=mov_path)
        mov = _hr(str(mov_path), sha256="solo", phash=None,
                  source_label="jdrive", file_type="mov", exif_date=_dt(),
                  pair_partner=heic_path)
        orig = _hr(str(orig_path), sha256="dup", source_label="iphone",
                   file_type="heic", exif_date=_dt())

        rows = _rows(classify(
            [heic, mov, orig],
            source_priority={"iphone": 0, "jdrive": 1},
        ))
        assert rows[heic_path.as_posix()].action == "EXACT"
        # CRITICAL: NOT EXACT — pre-#88 propagation would have made it EXACT.
        assert rows[mov_path.as_posix()].action == ""   # #433: was MOVE
        # But still grouped together
        assert rows[heic_path.as_posix()].group_id == rows[mov_path.as_posix()].group_id


# ---------------------------------------------------------------------------
# group_id — transitive connected-component assignment
# ---------------------------------------------------------------------------

class TestGroupId:
    def test_isolated_file_has_no_group_id(self):
        hr = _hr("/jdrive/solo.jpg", sha256="unique_hash", exif_date=_dt())
        rows = classify([hr])
        assert rows[0].group_id is None

    def test_exact_duplicate_pair_shares_group_id(self):
        a = _hr("/a/photo.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        b = _hr("/b/photo.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/a/photo.jpg"].group_id is not None
        assert rows["/b/photo.jpg"].group_id is not None
        assert rows["/a/photo.jpg"].group_id == rows["/b/photo.jpg"].group_id

    def test_near_duplicate_pair_shares_group_id(self):
        import imagehash
        base = imagehash.hex_to_hash("a" * 16)
        near = imagehash.hex_to_hash("5" + "a" * 15)
        a = _hr("/a.jpg", sha256="s1", phash=str(base), source_label="takeout", exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        assert rows["/a.jpg"].group_id is not None
        assert rows["/b.jpg"].group_id is not None
        assert rows["/a.jpg"].group_id == rows["/b.jpg"].group_id

    def test_transitive_grouping_three_files(self):
        """A near-dup of B, and B near-dup of C → all three in the same group."""
        import imagehash
        # Three hashes: a~b (distance≈4), b~c (distance≈4), but a and c may not match
        h_a = imagehash.hex_to_hash("aaaaaaaaaaaaaaaa")
        h_b = imagehash.hex_to_hash("aaaaaaaaaaaaaaa5")  # 4 bits from a
        h_c = imagehash.hex_to_hash("aaaaaaaaaaaaaa55")  # 4 bits from b (8 from a)
        a = _hr("/a.jpg", sha256="s1", phash=str(h_a), source_label="src", exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(h_b), source_label="src", exif_date=_dt())
        c = _hr("/c.jpg", sha256="s3", phash=str(h_c), source_label="src", exif_date=_dt())
        # threshold=5: a~b (dist=4) ✓, b~c (dist=4) ✓, a~c (dist=8) beyond threshold
        rows = _rows(classify([a, b, c], threshold=5))
        gids = {rows[p].group_id for p in ("/a.jpg", "/b.jpg", "/c.jpg")}
        assert None not in gids, "All three should have a group_id"
        assert len(gids) == 1, "All three should share the same group_id"

    def test_live_photo_pair_shares_group_id(self):
        heic_path = Path("/iphone/IMG.HEIC")
        mov_path = Path("/iphone/IMG.MOV")
        orig_path = Path("/jdrive/IMG.HEIC")

        heic = _hr(str(heic_path), sha256="x", source_label="jdrive",
                   file_type="heic", exif_date=_dt(), pair_partner=mov_path)
        mov = _hr(str(mov_path), sha256="y", phash=None,
                  source_label="jdrive", file_type="mov", exif_date=_dt(),
                  pair_partner=heic_path)
        orig = _hr(str(orig_path), sha256="x", source_label="iphone",
                   file_type="heic", exif_date=_dt())

        rows = _rows(classify([heic, mov, orig], source_priority={"iphone": 0, "jdrive": 1}))
        heic_gid = rows[heic_path.as_posix()].group_id
        mov_gid = rows[mov_path.as_posix()].group_id
        assert heic_gid is not None
        assert mov_gid is not None
        assert heic_gid == mov_gid


# ---------------------------------------------------------------------------
# Undecided non-duplicate (#433 — replaced the legacy MOVE + dest_path path)
# ---------------------------------------------------------------------------

class TestUndecidedUnique:
    def test_unique_dated_file_is_undecided(self):
        """A unique file with an EXIF date is classified '' (undecided) — the
        legacy MOVE action and dest_path column were dropped in #433."""
        hr = _hr("/jdrive/IMG.jpg", sha256="u", phash=None,
                 source_label="jdrive", exif_date=_dt(2024, 6, 1))
        rows = classify([hr])
        assert rows[0].action == ""

    def test_manifest_row_has_no_dest_path_field(self):
        """#433 — the dest_path field is gone from ManifestRow entirely, so the
        photo-transfer handshake can never be reconstructed from a row."""
        hr = _hr("/jdrive/IMG.jpg", sha256="u", phash=None,
                 source_label="jdrive", exif_date=_dt(2024, 6, 1))
        row = classify([hr])[0]
        assert not hasattr(row, "dest_path")


# ---------------------------------------------------------------------------
# Case-sensitive path collision (photo-manager#170)
# ---------------------------------------------------------------------------

class TestCaseSensitiveCollision:
    """Two genuinely-distinct files differing only by filename case must each
    produce their own ManifestRow.

    Background: on Windows ``Path("a.MOV") == Path("a.mov")`` is True and
    ``hash()`` matches too, so a ``dict[Path, ...]`` collapses both into one
    bucket — silently dropping the second file. ``classify()`` keys its
    internal ``rows`` dict by ``str(path)`` for exactly this reason.

    These pairs CAN coexist on disk in case-sensitive NTFS dirs (rare; opt-in
    via ``fsutil setCaseSensitiveInfo enable``) and Google Takeout has been
    observed emitting both for genuinely-distinct iPhone videos.
    """

    def test_case_only_filename_diff_keeps_both(self):
        """Both case variants survive when content hashes differ."""
        upper = _hr(r"D:\Photos\IMG_2063.MOV", sha256="aaa", phash=None,
                    file_type="mov", exif_date=_dt())
        lower = _hr(r"D:\Photos\IMG_2063.mov", sha256="bbb", phash=None,
                    file_type="mov", exif_date=_dt())
        result = classify([upper, lower])
        assert len(result) == 2, (
            "Both case-variants must survive — Path-keyed dict would collapse "
            "them on Windows because pathlib equality is case-insensitive."
        )
        source_paths = {r.source_path for r in result}
        assert r"D:\Photos\IMG_2063.MOV" in source_paths
        assert r"D:\Photos\IMG_2063.mov" in source_paths

    def test_case_only_diff_with_same_hash_dedups_normally(self):
        """When the hashes ARE identical, normal EXACT-duplicate dedup runs
        (lower-priority loses) — case difference does not break that."""
        upper = _hr(r"D:\Photos\IMG.JPG", sha256="same",
                    source_label="takeout", exif_date=_dt())
        lower = _hr(r"D:\Photos\IMG.jpg", sha256="same",
                    source_label="jdrive", exif_date=_dt())
        result = classify([upper, lower],
                          source_priority={"takeout": 0, "jdrive": 1})
        assert len(result) == 2  # both rows still exist — one survivor, one EXACT
        actions = {r.source_path: r.action for r in result}
        assert actions[r"D:\Photos\IMG.JPG"] == ""   # #433: survivor undecided
        assert actions[r"D:\Photos\IMG.jpg"] == "EXACT"

    def test_case_only_diff_in_live_photo_pair_partner(self):
        """A Live Photo HEIC paired with its MOV must keep its OWN action
        (per photo-manager#88, no propagation), even when a case-collision
        sibling exists at the same parent. The pair shares a group_id.

        Pre-#88, this test asserted the MOV was auto-marked EXACT to
        mirror the HEIC. Post-#88, action stays per-row independent and
        the pair is coupled only at the group_id level.
        """
        heic = Path(r"D:\Photos\IMG_X.HEIC")
        mov_lower = Path(r"D:\Photos\IMG_X.mov")
        mov_upper = Path(r"D:\Photos\IMG_X.MOV")  # case-collision sibling

        # HEIC is an exact duplicate of an earlier file (action=EXACT).
        # The case-collision MOV (mov_upper) MUST NOT be confused with
        # mov_lower in pair-edge construction (#170 case-sensitivity).
        records = [
            _hr(r"D:\Photos\earlier.heic", sha256="dup",
                source_label="takeout", exif_date=_dt()),
            _hr(str(heic), sha256="dup", source_label="jdrive",
                file_type="heic", pair_partner=mov_lower, exif_date=_dt()),
            _hr(str(mov_lower), sha256="movhash", phash=None,
                source_label="jdrive", file_type="mov", exif_date=_dt()),
            _hr(str(mov_upper), sha256="distinct", phash=None,
                source_label="jdrive", file_type="mov", exif_date=_dt()),
        ]
        result = classify(records,
                          source_priority={"takeout": 0, "jdrive": 1})

        # All four records must survive into ManifestRows
        assert len(result) == 4
        by_path = {r.source_path: r for r in result}
        # MOV partner keeps its own non-EXACT classification (no propagation).
        assert by_path[str(mov_lower)].action != "EXACT"
        assert by_path[str(mov_upper)].action != "EXACT"
        # Pair edge unioned mov_lower with the heic's component → same group_id.
        # mov_upper is unrelated → its own component (or group_id None if isolated).
        assert by_path[str(heic)].group_id == by_path[str(mov_lower)].group_id
        assert by_path[str(heic)].group_id != by_path[str(mov_upper)].group_id


# ---------------------------------------------------------------------------
# pHash-entropy guard (#516) — flat-image false grouping
# ---------------------------------------------------------------------------

class TestPhashEntropyGuard:
    """Flat / near-empty images degenerate to an all-zero (or all-one) pHash
    that collides with every other flat image. The #462 mean-color gate
    doesn't save the case where the flat images also share a similar mean
    colour (common for mostly-white icons), so the entropy guard distrusts
    the degenerate hash itself.
    """

    # Three GENUINELY-DISTINCT flat images (different SHA), same degenerate
    # pHash, same mean colour — so the mean-colour gate cannot reject them.
    def _three_flat(self):
        return [
            _hr("/a/icon1.png", sha256="f1", phash="0000000000000000",
                mean_color="250,250,250", file_type="png", exif_date=_dt()),
            _hr("/a/icon2.png", sha256="f2", phash="0000000000000000",
                mean_color="250,250,250", file_type="png", exif_date=_dt()),
            _hr("/a/icon3.png", sha256="f3", phash="0000000000000000",
                mean_color="250,250,250", file_type="png", exif_date=_dt()),
        ]

    def test_flat_images_falsely_grouped_without_guard(self):
        """Reproduces #516: with the guard disabled the three distinct icons
        collapse into one false duplicate group."""
        rows = _rows(classify(self._three_flat(), min_phash_entropy_bits=0))
        actions = {p: r.action for p, r in rows.items()}
        # At least two of the three get marked as duplicates of the third.
        dup = [a for a in actions.values() if a in ("EXACT", "REVIEW_DUPLICATE")]
        assert len(dup) >= 2, actions
        gids = {r.group_id for r in rows.values()}
        assert gids != {None}  # they were unioned into a (false) group

    def test_flat_images_not_grouped_with_guard(self):
        """The fix: at the default guard the degenerate-pHash icons are
        excluded from pHash grouping and fall through to undecided/isolated."""
        rows = _rows(classify(self._three_flat()))  # default min_phash_entropy_bits=4
        for p, r in rows.items():
            assert r.action == "", (p, r.action)        # undecided, not a dup
            assert r.group_id is None, (p, r.group_id)   # not unioned into a group

    def test_exact_sha_dup_of_flat_image_still_flagged(self):
        """The guard must NOT weaken exact-SHA dedup: two byte-identical flat
        icons (same SHA) are still flagged EXACT."""
        recs = [
            _hr("/a/copy1.png", sha256="same", phash="0000000000000000",
                source_label="src_a", file_type="png", exif_date=_dt()),
            _hr("/b/copy2.png", sha256="same", phash="0000000000000000",
                source_label="src_b", file_type="png", exif_date=_dt()),
        ]
        rows = _rows(classify(recs, source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/a/copy1.png"].action == ""        # survivor
        assert rows["/b/copy2.png"].action == "EXACT"   # exact dup still caught
        assert rows["/a/copy1.png"].group_id == rows["/b/copy2.png"].group_id

    def test_textured_near_duplicates_still_group_with_guard(self):
        """No regression: a real near-duplicate pair (non-degenerate pHash,
        small hamming) still groups with the guard on."""
        recs = [
            _hr("/a/photo.jpg", sha256="p1", phash="ffffffff00000000",
                file_type="jpeg", exif_date=_dt()),
            _hr("/a/photo_edit.jpg", sha256="p2", phash="ffffffff00000001",
                file_type="jpeg", exif_date=_dt()),
        ]
        rows = _rows(classify(recs))  # guard on by default
        actions = {r.action for r in rows.values()}
        assert "REVIEW_DUPLICATE" in actions
        gids = {r.group_id for r in rows.values()}
        assert gids != {None} and len(gids) == 1  # both in one real group


# ---------------------------------------------------------------------------
# Multi-hash confidence vote (#517)
# ---------------------------------------------------------------------------

class TestMatchConfidence:
    """A pHash near-dup match is still grouped, but flagged ``match_confidence``
    'high' only when an independent dHash also agrees. Grouping is unchanged;
    the flag gates the auto-select aggressive-delete (low → never auto-deleted).
    """

    # pHash near-dup pair (hamming 4, both non-degenerate)
    _PA, _PB = "aaaaaaaaaaaaaaaa", "5aaaaaaaaaaaaaaa"

    def test_high_confidence_when_dhash_agrees(self):
        recs = [
            _hr("/a.jpg", sha256="s1", phash=self._PA, dhash="cccccccccccccccc",
                source_label="takeout", exif_date=_dt()),
            _hr("/b.jpg", sha256="s2", phash=self._PB, dhash="cccccccccccccccc",
                source_label="jdrive", exif_date=_dt()),
        ]
        rows = _rows(classify(recs, threshold=10))
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"
        assert rows["/b.jpg"].match_confidence == "high"

    def test_dhash_disagree_not_grouped(self):
        """#524 — pHash within threshold but dHash disagrees → the pair is a
        pHash false positive and is NOT grouped (the real fix for the
        different-scene-at-hamming-10 false groups)."""
        recs = [
            _hr("/a.jpg", sha256="s1", phash=self._PA, dhash="cccccccccccccccc",
                source_label="takeout", exif_date=_dt()),
            _hr("/b.jpg", sha256="s2", phash=self._PB, dhash="3333333333333333",
                source_label="jdrive", exif_date=_dt()),
        ]
        rows = _rows(classify(recs, threshold=10))
        assert rows["/b.jpg"].action == ""           # not a near-dup
        assert rows["/a.jpg"].group_id is None and rows["/b.jpg"].group_id is None

    def test_low_confidence_when_dhash_missing(self):
        recs = [
            _hr("/a.jpg", sha256="s1", phash=self._PA, dhash=None,
                source_label="takeout", exif_date=_dt()),
            _hr("/b.jpg", sha256="s2", phash=self._PB, dhash=None,
                source_label="jdrive", exif_date=_dt()),
        ]
        rows = _rows(classify(recs, threshold=10))
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"
        assert rows["/b.jpg"].match_confidence == "low"

    def test_exact_sha_dup_is_high_confidence(self):
        recs = [
            _hr("/a/x.jpg", sha256="same", source_label="src_a", exif_date=_dt()),
            _hr("/b/x.jpg", sha256="same", source_label="src_b", exif_date=_dt()),
        ]
        rows = _rows(classify(recs, source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/b/x.jpg"].action == "EXACT"
        assert rows["/b/x.jpg"].match_confidence == "high"  # byte-identical is certain


# ---------------------------------------------------------------------------
# Dimension gate — tiny-icon false grouping
# ---------------------------------------------------------------------------

class TestDimensionGate:
    """Tiny images (UI icons / thumbnails) downsample to too few DCT
    coefficients for a 64-bit pHash to tell apart, so unrelated small images
    land within the near-dup threshold and form false groups — even though
    their pHash isn't degenerate (#516) and their dHash agrees (#517). The
    dimension gate keeps sub-photo-sized images out of pHash grouping.
    """

    # Non-degenerate pHashes 1 bit apart → a near-dup match if grouped.
    _PA, _PB = "ffff0000ffff0000", "ffff0000ffff0001"

    def _pair(self, w, h):
        return [
            _hr("/a.png", sha256="s1", phash=self._PA, file_type="png",
                exif_date=_dt(), pixel_width=w, pixel_height=h),
            _hr("/b.png", sha256="s2", phash=self._PB, file_type="png",
                exif_date=_dt(), pixel_width=w, pixel_height=h),
        ]

    def test_tiny_images_not_grouped(self):
        """24×24 icons below the 128px default gate are excluded from pHash
        grouping → undecided, no group."""
        rows = _rows(classify(self._pair(24, 24)))  # default min_phash_dimension=128
        assert rows["/b.png"].action == ""
        assert rows["/a.png"].group_id is None and rows["/b.png"].group_id is None

    def test_tiny_images_grouped_when_gate_disabled(self):
        """Reproduces the bug: with the gate off the same icons collapse."""
        rows = _rows(classify(self._pair(24, 24), min_phash_dimension=0))
        assert rows["/b.png"].action == "REVIEW_DUPLICATE"

    def test_photo_sized_images_still_grouped(self):
        """A real near-dup pair (well above the gate) still groups."""
        rows = _rows(classify(self._pair(512, 512)))
        assert rows["/b.png"].action == "REVIEW_DUPLICATE"
        assert rows["/a.png"].group_id == rows["/b.png"].group_id

    def test_exact_sha_tiny_still_flagged(self):
        """The gate only affects the pHash path — a byte-identical tiny icon
        is still flagged EXACT."""
        recs = [
            _hr("/a/i.png", sha256="same", file_type="png", source_label="src_a",
                exif_date=_dt(), pixel_width=24, pixel_height=24),
            _hr("/b/i.png", sha256="same", file_type="png", source_label="src_b",
                exif_date=_dt(), pixel_width=24, pixel_height=24),
        ]
        rows = _rows(classify(recs, source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/b/i.png"].action == "EXACT"

    def test_unknown_dimensions_pass_through(self):
        """Unknown dims (None — video / decode failure) are not excluded."""
        rows = _rows(classify(self._pair(None, None)))
        assert rows["/b.png"].action == "REVIEW_DUPLICATE"


# ---------------------------------------------------------------------------
# BK-tree candidate generation (#526)
# ---------------------------------------------------------------------------

import random as _random

import scanner.dedup as _dedup
from scanner.dedup import _BKTree, _near_dup_neighbors

# Captured so resets restore the real production default rather than a
# hardcoded copy that would silently drift if the source constant changes.
_DEFAULT_FLOOR = _dedup._BKTREE_MIN_CANDIDATES


def _popcount(x: int) -> int:
    return bin(x).count("1")


def _naive_neighbors(int_hashes: list[int], i: int, threshold: int) -> list[int]:
    """Reference: every j != i within ``threshold`` Hamming of i, ascending."""
    return sorted(
        j
        for j in range(len(int_hashes))
        if j != i and _popcount(int_hashes[i] ^ int_hashes[j]) <= threshold
    )


class TestBKTreeStructure:
    """The hand-rolled BK-tree must return exactly the within-threshold set a
    naive all-pairs popcount scan would — it is candidate *generation*, so a
    missed neighbour would silently drop a real near-dup group."""

    def test_query_matches_naive_scan(self):
        rng = _random.Random(1234)
        # Cluster centres + jitter so the set actually has near-neighbours;
        # pure random 64-bit hashes sit ~32 apart and would never match.
        ints: list[int] = []
        for _ in range(40):
            centre = rng.getrandbits(64)
            for _ in range(rng.randint(1, 5)):
                v = centre
                for _ in range(rng.randint(0, 8)):
                    v ^= 1 << rng.randint(0, 63)
                ints.append(v)
        tree = _BKTree(ints[0], 0)
        for idx in range(1, len(ints)):
            tree.add(ints[idx], idx)
        for threshold in (0, 5, 10, 20):
            for i in range(len(ints)):
                got = sorted(set(tree.query(ints[i], threshold)))
                want = sorted(
                    j
                    for j in range(len(ints))
                    if _popcount(ints[i] ^ ints[j]) <= threshold
                )
                # query includes i itself (distance 0) plus any equal-hash peers
                assert got == want, f"threshold={threshold} i={i}"

    def test_equal_hashes_collapse_onto_one_node(self):
        """Two records with the identical pHash must both be returned — the
        BK-tree stores them as an index list on a single node, since it can't
        hold two children at edge-distance 0."""
        v = 0xABCDABCDABCDABCD
        tree = _BKTree(v, 0)
        tree.add(v, 1)
        tree.add(v, 2)
        assert sorted(tree.query(v, 0)) == [0, 1, 2]

    def test_neighbors_enumerator_only_yields_forward(self):
        """``_near_dup_neighbors`` returns j > i in ascending order under both
        the brute and BK strategies, so the caller walks pairs in the same
        ``(i, j)`` order the legacy nested loop used."""
        rng = _random.Random(99)
        ints = []
        for _ in range(30):
            centre = rng.getrandbits(64)
            for _ in range(rng.randint(1, 4)):
                v = centre
                for _ in range(rng.randint(0, 6)):
                    v ^= 1 << rng.randint(0, 63)
                ints.append(v)
        threshold = 10
        n = len(ints)
        try:
            # BK strategy (floor 0): the enumerator itself returns the
            # threshold-filtered forward set.
            _dedup._BKTREE_MIN_CANDIDATES = 0
            bk_enum = _near_dup_neighbors(ints, threshold)
            for i in range(n):
                want = [j for j in _naive_neighbors(ints, i, threshold) if j > i]
                assert list(bk_enum(i)) == want, f"bk i={i}"
            # Brute strategy (huge floor): returns ALL forward indices by
            # contract — the caller's ``0 < distance <= threshold`` check does
            # the filtering. After applying that same filter the two
            # strategies must agree.
            _dedup._BKTREE_MIN_CANDIDATES = 10 ** 9
            brute_enum = _near_dup_neighbors(ints, threshold)
            for i in range(n):
                assert list(brute_enum(i)) == list(range(i + 1, n)), f"brute i={i}"
                filtered = [
                    j for j in brute_enum(i)
                    if _popcount(ints[i] ^ ints[j]) <= threshold
                ]
                assert filtered == list(bk_enum(i)), f"agree i={i}"
        finally:
            _dedup._BKTREE_MIN_CANDIDATES = _DEFAULT_FLOOR


def _parity_fixture(seed: int, count: int) -> list[HashResult]:
    """Deterministic varied HashResult set with real near-dup clusters.

    Cluster centres + bit jitter create genuine near-duplicate chains
    (transitive grouping, gate interactions); varied source labels exercise
    priority ordering; varied mean_color exercises the #462 gate. dHash is
    left None here so the enumeration is what's under test — gate-agreement
    parity is covered separately by ``test_parity_dhash_gates``.
    """
    rng = _random.Random(seed)
    hrs: list[HashResult] = []
    n = 0
    labels = ["takeout", "jdrive", "backup"]
    while len(hrs) < count:
        centre = rng.getrandbits(64)
        # Per-cluster base colour with small intra-cluster jitter — real
        # near-dups share a palette, so members pass the #462 mean-color gate
        # and form genuine groups (rich transitive parity surface). A few
        # clusters get a clashing colour so the gate also rejects under test.
        cr, cg, cb = (rng.randint(20, 235) for _ in range(3))
        for _ in range(rng.randint(1, 6)):
            if len(hrs) >= count:
                break
            v = centre
            for _ in range(rng.randint(0, 7)):  # 0–7 flips → some within, some beyond t=10
                v ^= 1 << rng.randint(0, 63)
            r = min(255, max(0, cr + rng.randint(-8, 8)))
            g = min(255, max(0, cg + rng.randint(-8, 8)))
            b = min(255, max(0, cb + rng.randint(-8, 8)))
            hrs.append(_hr(
                f"/{rng.choice(labels)}/img{n:05d}.jpg",
                sha256=f"sha-{n}",                       # unique → isolate near-dup pass
                phash=format(v, "016x"),
                mean_color=f"{r},{g},{b}",
                source_label=rng.choice(labels),
                exif_date=_dt(),
            ))
            n += 1
    rng.shuffle(hrs)
    return hrs


def _force_floor(floor: int):
    _dedup._BKTREE_MIN_CANDIDATES = floor


class TestBKTreeParity:
    """The BK-tree path must produce output bit-identical to the brute-force
    path (#526 hard constraint): the tree changes *which pairs are tested*,
    never *the verdict*. Each test runs the same fixture through both
    strategies and asserts the full ManifestRow list (values AND order) is
    identical."""

    def teardown_method(self):
        _dedup._BKTREE_MIN_CANDIDATES = _DEFAULT_FLOOR

    def _both_paths(self, recs: list[HashResult], **kwargs) -> tuple[list, list]:
        _force_floor(10 ** 9)          # always brute
        brute = classify(list(recs), **kwargs)
        _force_floor(0)                # always BK-tree
        bk = classify(list(recs), **kwargs)
        return brute, bk

    def test_parity_small_varied_fixture(self):
        recs = _parity_fixture(seed=7, count=60)
        brute, bk = self._both_paths(recs)
        assert brute == bk
        # Sanity: the fixture actually produces groupings, else parity is vacuous.
        assert any(r.action == "REVIEW_DUPLICATE" for r in brute)

    def test_parity_large_fixture_natural_bk_path(self):
        """At 400 candidates the *unpatched* pipeline already takes the BK
        path (>16). Assert it equals the forced brute path — proves the
        production default is bit-identical, not just a monkeypatched run."""
        recs = _parity_fixture(seed=42, count=400)
        _force_floor(10 ** 9)
        brute = classify(list(recs))
        _dedup._BKTREE_MIN_CANDIDATES = _DEFAULT_FLOOR   # default (16) → BK at N=400
        natural = classify(list(recs))
        assert brute == natural
        assert sum(r.action == "REVIEW_DUPLICATE" for r in brute) > 5

    def test_parity_varied_thresholds(self):
        recs = _parity_fixture(seed=11, count=120)
        for threshold in (3, 10, 20):
            brute, bk = self._both_paths(recs, threshold=threshold)
            assert brute == bk, f"threshold={threshold}"

    def test_parity_dhash_gates(self):
        """Mixed dHash agree/disagree/unknown across a near-dup cluster — the
        #524 gate must fire identically under both enumeration strategies."""
        rng = _random.Random(5)
        centre = 0xF0F0F0F0F0F0F0F0
        recs: list[HashResult] = []
        for k in range(40):
            v = centre
            for _ in range(rng.randint(0, 5)):
                v ^= 1 << rng.randint(0, 63)
            # Three dHash regimes: agree (near centre dhash), disagree (far),
            # unknown (None) — so the gate takes every branch within the cluster.
            regime = k % 3
            if regime == 0:
                dh = format(0xCCCCCCCCCCCCCCCC ^ (1 << rng.randint(0, 63)), "016x")
            elif regime == 1:
                dh = format(0x3333333333333333 ^ (1 << rng.randint(0, 63)), "016x")
            else:
                dh = None
            recs.append(_hr(
                f"/src{k % 3}/d{k:03d}.jpg", sha256=f"s{k}",
                phash=format(v, "016x"), dhash=dh,
                source_label=f"src{k % 3}", exif_date=_dt(),
            ))
        brute, bk = self._both_paths(recs)
        assert brute == bk
        # The disagree regime must actually have blocked some grouping.
        assert any(r.action == "" for r in brute)

    def test_parity_via_classify_param(self):
        """#526 PR2 — the public ``bktree_min_candidates`` param (the route the
        scan worker uses to pass its calibrated floor) selects the strategy
        without touching the module global, and both forced strategies match
        the default. Proves the param threads classify → _classify_phash →
        _classify_near_duplicates → _near_dup_neighbors intact."""
        recs = _parity_fixture(seed=21, count=120)
        default = classify(list(recs))                              # module floor
        forced_bk = classify(list(recs), bktree_min_candidates=0)   # always BK
        forced_brute = classify(list(recs), bktree_min_candidates=10 ** 9)  # brute
        assert default == forced_bk == forced_brute
        assert any(r.action == "REVIEW_DUPLICATE" for r in default)

    def test_parity_with_exact_sha_interplay(self):
        """SHA-exact dups (pass 1) remove rows before the near-dup pass runs;
        the BK-tree builds over whatever survives. Parity must hold with that
        interplay too."""
        recs = _parity_fixture(seed=3, count=80)
        # Inject a few exact-SHA duplicates sharing a hash → pass 1 classifies
        # them, so they're absent from the near-dup candidate set.
        recs[5] = _hr("/takeout/dupA.jpg", sha256="DUP", phash=recs[5].phash,
                      source_label="takeout", exif_date=_dt())
        recs[6] = _hr("/jdrive/dupA.jpg", sha256="DUP", phash=recs[6].phash,
                      source_label="jdrive", exif_date=_dt())
        brute, bk = self._both_paths(recs)
        assert brute == bk
