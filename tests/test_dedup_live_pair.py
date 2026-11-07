"""GROUPING_STRATEGY_VERSION "9" — a Live Photo image<->video pair is a group
only when its still is grouped for another reason.

An image<->video same-stem edge is a "pair" edge. It joins a group only when a
member of its cluster is already grouped by a high edge, a taken hint, or a
byte/near-dup link. A lone pair (still + its own video, nothing else) gets no
group_id, so it is not shown and auto-select never locks its still as KEEP.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from scanner.dedup import HINT_ONLY_PREFIX, classify
from tests.test_dedup import _dt, _hr, _rows

DHASH = "cccccccccccccccc"
PHASH = "aaaaaaaaaaaaaaaa"
PHASH_NEAR = "5aaaaaaaaaaaaaaa"  # hamming 4 from PHASH
PHASH_LEAF = "aaaaaaaaaaaaaaa5"  # hamming 4 from PHASH, 8 from PHASH_NEAR
PHASH_FAR = "5555555555555555"  # hamming 64 from PHASH: the #539 gate drops its edge


def _pair(
    stem: str,
    folder: str = "/cam",
    still_sha: str | None = None,
    mov_sha: str | None = None,
    phash: str | None = PHASH,
    dhash: str | None = None,
):
    """A Live Photo still (HEIC) and its MOV, linked by the walker's pair cluster."""
    still_path = f"{folder}/{stem}.HEIC"
    mov_path = f"{folder}/{stem}.MOV"
    still = _hr(
        still_path, sha256=still_sha or f"s-{stem}", phash=phash, dhash=dhash,
        file_type="heic", exif_date=_dt(), pair_cluster=(Path(mov_path),),
    )
    mov = _hr(
        mov_path, sha256=mov_sha or f"m-{stem}", phash=None,
        file_type="mov", exif_date=_dt(), pair_cluster=(Path(still_path),),
    )
    return still, mov


def _gid(rows: dict, path: str):
    return rows[Path(path).as_posix()].group_id


def test_lone_live_photo_pair_gets_no_group():
    still, mov = _pair("IMG_1")
    rows = _rows(classify([still, mov]))
    assert _gid(rows, "/cam/IMG_1.HEIC") is None
    assert _gid(rows, "/cam/IMG_1.MOV") is None
    assert rows["/cam/IMG_1.HEIC"].action == ""
    assert rows["/cam/IMG_1.HEIC"].reason == "unique"
    assert rows["/cam/IMG_1.MOV"].action == ""


def test_mov_joins_group_when_still_is_byte_copy():
    still, mov = _pair("IMG_2", still_sha="same")
    copy = _hr("/copy/IMG_2.HEIC", sha256="same", file_type="heic", exif_date=_dt())
    rows = _rows(classify([still, mov, copy]))
    gid = _gid(rows, "/cam/IMG_2.HEIC")
    assert gid is not None
    assert _gid(rows, "/copy/IMG_2.HEIC") == gid
    assert _gid(rows, "/cam/IMG_2.MOV") == gid


def test_mov_joins_group_when_still_is_high_near_dup():
    still, mov = _pair("IMG_3", dhash=DHASH)
    other = _hr("/other/IMG_9.jpg", sha256="x9", phash=PHASH_NEAR, dhash=DHASH,
                exif_date=_dt())
    rows = _rows(classify([still, mov, other], threshold=10))
    gid = _gid(rows, "/cam/IMG_3.HEIC")
    assert gid is not None
    assert _gid(rows, "/other/IMG_9.jpg") == gid
    assert _gid(rows, "/cam/IMG_3.MOV") == gid


def test_two_lone_live_stills_with_low_hint_form_one_group_with_movs():
    still_a, mov_a = _pair("IMG_4359")
    still_b, mov_b = _pair("IMG_4361", phash=PHASH_NEAR)
    rows = _rows(classify([still_a, mov_a, still_b, mov_b]))
    gids = {_gid(rows, p) for p in (
        "/cam/IMG_4359.HEIC", "/cam/IMG_4359.MOV",
        "/cam/IMG_4361.HEIC", "/cam/IMG_4361.MOV",
    )}
    assert None not in gids
    assert len(gids) == 1, "two low-hinted stills and their MOVs form one group of four"
    review = rows["/cam/IMG_4361.HEIC"]
    assert review.duplicate_of, "the lower-numbered still is the REVIEW target"
    assert _gid(rows, review.duplicate_of) == review.group_id


def test_lone_live_still_attaches_to_high_member_with_its_mov():
    """A lone still low-hints a high member A (group G = {A, byte copy of A}).
    The hint is taken because A is a high member, so the still joins G with its
    MOV. The still's own edge stays low (no dHash on either side)."""
    still, mov = _pair("IMG_5", phash=PHASH_NEAR)
    a = _hr("/other/IMG_6.jpg", sha256="a6", phash=PHASH, exif_date=_dt())
    a_copy = _hr("/copy/IMG_6.jpg", sha256="a6", phash=PHASH, exif_date=_dt())
    rows = _rows(classify([still, mov, a, a_copy], threshold=10))
    gid = _gid(rows, "/other/IMG_6.jpg")
    assert gid is not None
    assert _gid(rows, "/copy/IMG_6.jpg") == gid
    assert _gid(rows, "/cam/IMG_5.HEIC") == gid
    assert _gid(rows, "/cam/IMG_5.MOV") == gid
    assert rows["/cam/IMG_5.HEIC"].match_confidence == "low"


def test_live_still_with_skipped_hint_is_reset_and_ungrouped():
    # L and M pair up as two lone low-hinted photos; the Live still S hints L,
    # but L is no longer lone and not a high member, so the hint is skipped.
    lone_l = _hr("/cam/L.jpg", sha256="l", phash=PHASH, exif_date=_dt())
    lone_m = _hr("/cam/M.jpg", sha256="mm", phash=PHASH_NEAR, exif_date=_dt())
    still, mov = _pair("S", phash=PHASH_LEAF)
    rows = _rows(classify([lone_l, lone_m, still, mov], threshold=5))
    still_row = rows["/cam/S.HEIC"]
    assert still_row.group_id is None
    assert rows["/cam/S.MOV"].group_id is None
    assert still_row.action in ("", "UNDATED")
    assert HINT_ONLY_PREFIX in still_row.reason


def test_live_still_is_not_a_hint_anchor():
    # X and Y are lone photos that both low-hint the Live still S. S may pair
    # with one of them; the second hint must be skipped, as for any photo.
    # X and Y sit 4 bits from S on opposite sides, 8 bits from each other.
    still, mov = _pair("IMG_7", phash=PHASH)
    x = _hr("/cam/X.jpg", sha256="x", phash=PHASH_NEAR, exif_date=_dt())
    y = _hr("/cam/Y.jpg", sha256="y", phash=PHASH_LEAF, exif_date=_dt())
    rows = _rows(classify([x, y, still, mov], threshold=5))
    grouped = [p for p in ("/cam/X.jpg", "/cam/Y.jpg") if _gid(rows, p) is not None]
    assert len(grouped) == 1, "exactly one of X and Y pairs with the still"
    assert _gid(rows, "/cam/IMG_7.HEIC") == _gid(rows, grouped[0])
    assert _gid(rows, "/cam/IMG_7.MOV") == _gid(rows, grouped[0])


def test_false_stem_pair_without_other_edges_gets_no_group():
    jpg = _hr("/cam/DSC_0001.JPG", sha256="j", phash=PHASH, exif_date=_dt(),
              pair_cluster=(Path("/cam/DSC_0001.MP4"),))
    mp4 = _hr("/cam/DSC_0001.MP4", sha256="p", phash=None, file_type="mp4",
              exif_date=_dt(), pair_cluster=(Path("/cam/DSC_0001.JPG"),))
    rows = _rows(classify([jpg, mp4]))
    assert _gid(rows, "/cam/DSC_0001.JPG") is None
    assert _gid(rows, "/cam/DSC_0001.MP4") is None


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_cluster_attaches_whole_when_any_member_grouped(order):
    """Pair cluster {HEIC, JPG, MOV}, with exactly ONE member pre-joined.

    The JPG is a byte copy of a file elsewhere, so it is grouped by a high
    edge in pass 1. The HEIC<->JPG pair edge is dropped by the #539 content
    gate (both have a pHash and they differ), so the HEIC has no edge of its
    own. The HEIC joins only through the whole-cluster rule (HEIC<->MOV and
    JPG<->MOV pair edges). An edge-by-edge pass depends on the record order.
    """
    heic_p, jpg_p, mov_p = Path("/cam/IMG_7.HEIC"), Path("/cam/IMG_7.JPG"), Path("/cam/IMG_7.MOV")
    cluster = {heic_p: (jpg_p, mov_p), jpg_p: (heic_p, mov_p), mov_p: (heic_p, jpg_p)}
    members = [
        _hr(str(heic_p), sha256="h", phash=PHASH, file_type="heic", exif_date=_dt(),
            pair_cluster=cluster[heic_p]),
        _hr(str(jpg_p), sha256="j", phash=PHASH_FAR, file_type="jpeg", exif_date=_dt(),
            pair_cluster=cluster[jpg_p]),
        _hr(str(mov_p), sha256="m", phash=None, file_type="mov", exif_date=_dt(),
            pair_cluster=cluster[mov_p]),
    ]
    copy = _hr("/elsewhere/IMG_7.jpg", sha256="j", phash=PHASH_FAR,
               file_type="jpeg", exif_date=_dt())
    records = [members[i] for i in order] + [copy]
    rows = _rows(classify(records))
    gids = {_gid(rows, str(p)) for p in (heic_p, jpg_p, mov_p)}
    gids.add(_gid(rows, "/elsewhere/IMG_7.jpg"))
    assert gids == {str(heic_p)}, (
        f"whole cluster must share the lex-min group_id, got {gids}"
    )


def test_image_image_same_stem_pair_still_groups():
    """Scope guard: RAW+JPG of one shot (image<->image) stays a "high" pair
    edge, so it groups with no other edge, as in GROUPING "8"."""
    ph = "f0f0f0f0f0f0f0f0"
    jpg = _hr("/cam/DSC_0020.jpg", sha256="aa", phash=ph, exif_date=_dt(),
              file_type="jpeg", pair_cluster=(Path("/cam/DSC_0020.nef"),))
    raw = _hr("/cam/DSC_0020.nef", sha256="bb", phash=ph, exif_date=_dt(),
              file_type="raw", pair_cluster=(Path("/cam/DSC_0020.jpg"),))
    rows = _rows(classify([jpg, raw]))
    gid = _gid(rows, "/cam/DSC_0020.jpg")
    assert gid is not None
    assert _gid(rows, "/cam/DSC_0020.nef") == gid
