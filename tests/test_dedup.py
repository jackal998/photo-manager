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
) -> HashResult:
    return HashResult(
        record=_record(
            path, source_label=source_label, file_type=file_type,
            pair_partner=pair_partner, pair_cluster=pair_cluster,
        ),
        sha256=sha256,
        phash=phash,
        mean_color=mean_color,
        exif_date=exif_date,
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
        assert rows["/src_a/a.jpg"].action == "MOVE"   # survivor — MOVE, not KEEP
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
        assert rows["/src_a/photo.jpg"].action == "MOVE"
        assert rows["/src_b/photo.jpg"].action == "EXACT"

    def test_second_source_priority_reversed(self):
        """With reversed priority, src_b wins."""
        a = _hr("/src_a/photo.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        b = _hr("/src_b/photo.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"src_a": 1, "src_b": 0}))
        assert rows["/src_b/photo.jpg"].action == "MOVE"
        assert rows["/src_a/photo.jpg"].action == "EXACT"

    def test_no_source_priority_auto_infers_from_order(self):
        """Without explicit source_priority, first-seen label gets priority 0."""
        first = _hr("/first/photo.jpg", sha256="dup", source_label="first_src",
                    exif_date=_dt())
        second = _hr("/second/photo.jpg", sha256="dup", source_label="second_src",
                     exif_date=_dt())
        rows = _rows(classify([first, second]))   # no source_priority
        assert rows["/first/photo.jpg"].action == "MOVE"   # first-seen wins
        assert rows["/second/photo.jpg"].action == "EXACT"


# ---------------------------------------------------------------------------
# FORMAT_DUPLICATE
# ---------------------------------------------------------------------------

class TestFormatDuplicate:
    def test_heic_kept_over_jpeg_same_phash(self):
        heic = _hr("/a.heic", sha256="h1", phash="0" * 16, file_type="heic",
                   source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="0" * 16, file_type="jpeg",
                   source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.heic"].action in ("MOVE", "KEEP")
        assert rows["/a.jpg"].action == "EXACT"

    def test_raw_and_jpeg_both_move(self):
        """RAW + JPEG of same shot must both be kept (complementary rule)."""
        raw = _hr("/a.arw", sha256="r1", phash="0" * 16, file_type="raw",
                  source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="j1", phash="0" * 16, file_type="jpeg",
                   source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([raw, jpeg]))
        assert rows["/a.arw"].action == "MOVE"
        assert rows["/a.jpg"].action == "MOVE"

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
        heic = _hr("/a.heic", sha256="h1", phash="0" * 16, file_type="heic",
                   mean_color="120,130,140", source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="0" * 16, file_type="jpeg",
                   mean_color="118,132,138", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.jpg"].action == "EXACT"

    def test_format_duplicate_missing_mean_color_falls_back_to_phash_only(self):
        """#462 — if either side lacks mean_color (RAW thumbnail, hash
        failure), the format-duplicate path still marks EXACT; gate skips on
        missing data, matching _classify_near_duplicates' behavior."""
        heic = _hr("/a.heic", sha256="h1", phash="0" * 16, file_type="heic",
                   mean_color=None, source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="0" * 16, file_type="jpeg",
                   mean_color="118,132,138", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.jpg"].action == "EXACT"

    def test_raw_plus_lossy_with_color_mismatch_still_complementary(self):
        """#462 — RAW + lossy with mismatched mean_color must still return
        early (both MOVE); the gate must not affect the RAW+lossy
        complementary branch."""
        raw = _hr("/a.arw", sha256="r1", phash="0" * 16, file_type="raw",
                  mean_color="10,20,30", source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="j1", phash="0" * 16, file_type="jpeg",
                   mean_color="200,180,160", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([raw, jpeg]))
        assert rows["/a.arw"].action == "MOVE"
        assert rows["/a.jpg"].action == "MOVE"


# ---------------------------------------------------------------------------
# REVIEW_DUPLICATE (near-duplicate)
# ---------------------------------------------------------------------------

class TestNearDuplicate:
    def test_near_duplicate_flagged(self):
        import imagehash
        base = imagehash.hex_to_hash("0" * 16)
        # Flip 5 bits → hamming distance 5 (within default threshold 10)
        near = imagehash.hex_to_hash("f" + "0" * 15)
        a = _hr("/a.jpg", sha256="s1", phash=str(base), source_label="takeout",
                exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), source_label="jdrive",
                exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"

    def test_beyond_threshold_not_flagged(self):
        import imagehash
        # 16-bit difference: hamming distance = 4 (0x000f vs 0x0000)
        h1 = imagehash.hex_to_hash("0" * 16)
        h2 = imagehash.hex_to_hash("000000000000000f")
        a = _hr("/a.jpg", sha256="s1", phash=str(h1), exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(h2), exif_date=_dt())
        # threshold=3 → distance 4 is beyond threshold
        rows = _rows(classify([a, b], threshold=3))
        assert rows["/b.jpg"].action != "REVIEW_DUPLICATE"

    def test_mean_color_mismatch_rejects_false_positive(self):
        """pHash near-duplicate with very different mean_color is NOT flagged."""
        import imagehash
        base = imagehash.hex_to_hash("0" * 16)
        near = imagehash.hex_to_hash("f" + "0" * 15)   # hamming=4, within threshold
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
        base = imagehash.hex_to_hash("0" * 16)
        near = imagehash.hex_to_hash("f" + "0" * 15)   # hamming=4, within threshold
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
        base = imagehash.hex_to_hash("0" * 16)
        near = imagehash.hex_to_hash("f" + "0" * 15)
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
        classification (here, MOVE because exif_date is set and SHA is
        unique). Both still share the same group_id via the pair edge.

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
        # MOV no longer auto-EXACT — keeps its own MOVE classification
        assert rows[mov_path.as_posix()].action == "MOVE"
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
        # Both classified as MOVE (unique, exif_date present)
        assert rows[heic_path.as_posix()].action == "MOVE"
        assert rows[mov_path.as_posix()].action == "MOVE"
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
        MOVE / UNDATED based on its own data, even though its group_id
        is shared with the HEIC's group via the pair edge.
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
        assert rows[mov_path.as_posix()].action == "MOVE"
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
        base = imagehash.hex_to_hash("0" * 16)
        near = imagehash.hex_to_hash("f" + "0" * 15)
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
        h_a = imagehash.hex_to_hash("0000000000000000")
        h_b = imagehash.hex_to_hash("000000000000000f")  # 4 bits from a
        h_c = imagehash.hex_to_hash("00000000000000ff")  # 4 bits from b (8 from a)
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
# dest_path
# ---------------------------------------------------------------------------

class TestDestPath:
    def test_move_has_dest_path(self):
        hr = _hr("/jdrive/IMG.jpg", sha256="u", phash=None,
                 source_label="jdrive", exif_date=_dt(2024, 6, 1))
        rows = classify([hr])
        assert rows[0].action == "MOVE"
        assert rows[0].dest_path == "2024/20240601_jdrive/IMG.jpg"

    def test_skip_has_no_dest_path(self):
        a = _hr("/a.jpg", sha256="dup", source_label="takeout", exif_date=_dt())
        b = _hr("/b.jpg", sha256="dup", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([a, b]))
        assert rows["/b.jpg"].dest_path is None


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
        assert len(result) == 2  # both rows still exist — one MOVE, one EXACT
        actions = {r.source_path: r.action for r in result}
        assert actions[r"D:\Photos\IMG.JPG"] == "MOVE"
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
