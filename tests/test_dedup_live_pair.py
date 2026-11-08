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
    cid: str | None = None,
):
    """A Live Photo still (HEIC) and its MOV, linked by the walker's pair cluster.

    GROUPING 10 (G4): the two files pair only when both carry the same
    ContentIdentifier, so ``cid`` is passed to both. ``None`` = no identity."""
    still_path = f"{folder}/{stem}.HEIC"
    mov_path = f"{folder}/{stem}.MOV"
    still = _hr(
        still_path, sha256=still_sha or f"s-{stem}", phash=phash, dhash=dhash,
        file_type="heic", exif_date=_dt(), pair_cluster=(Path(mov_path),),
        content_identifier=cid,
    )
    mov = _hr(
        mov_path, sha256=mov_sha or f"m-{stem}", phash=None,
        file_type="mov", exif_date=_dt(), pair_cluster=(Path(still_path),),
        content_identifier=cid,
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
    still, mov = _pair("IMG_2", still_sha="same", cid="CID-IMG2")
    copy = _hr("/copy/IMG_2.HEIC", sha256="same", file_type="heic", exif_date=_dt())
    rows = _rows(classify([still, mov, copy]))
    gid = _gid(rows, "/cam/IMG_2.HEIC")
    assert gid is not None
    assert _gid(rows, "/copy/IMG_2.HEIC") == gid
    assert _gid(rows, "/cam/IMG_2.MOV") == gid


def test_mov_without_matching_cid_stays_ungrouped_when_still_is_byte_copy():
    """GROUPING 10 (G4) twin: the same shape with no ContentIdentifier. The
    stem alone never pairs, so the MOV is not the still's passenger."""
    still, mov = _pair("IMG_2", still_sha="same")
    copy = _hr("/copy/IMG_2.HEIC", sha256="same", file_type="heic", exif_date=_dt())
    rows = _rows(classify([still, mov, copy]))
    assert _gid(rows, "/cam/IMG_2.HEIC") is not None
    assert _gid(rows, "/cam/IMG_2.MOV") is None


def test_mov_joins_group_when_still_is_high_near_dup():
    still, mov = _pair("IMG_3", dhash=DHASH, cid="CID-IMG3")
    other = _hr("/other/IMG_9.jpg", sha256="x9", phash=PHASH_NEAR, dhash=DHASH,
                exif_date=_dt())
    rows = _rows(classify([still, mov, other], threshold=10))
    gid = _gid(rows, "/cam/IMG_3.HEIC")
    assert gid is not None
    assert _gid(rows, "/other/IMG_9.jpg") == gid
    assert _gid(rows, "/cam/IMG_3.MOV") == gid


def test_mov_without_matching_cid_stays_ungrouped_when_still_is_high_near_dup():
    """GROUPING 10 (G4) twin: same near-dup shape, no ContentIdentifier."""
    still, mov = _pair("IMG_3", dhash=DHASH)
    other = _hr("/other/IMG_9.jpg", sha256="x9", phash=PHASH_NEAR, dhash=DHASH,
                exif_date=_dt())
    rows = _rows(classify([still, mov, other], threshold=10))
    assert _gid(rows, "/cam/IMG_3.HEIC") is not None
    assert _gid(rows, "/cam/IMG_3.MOV") is None


def test_two_lone_live_stills_with_only_a_hint_form_no_group():
    still_a, mov_a = _pair("IMG_4359")
    still_b, mov_b = _pair("IMG_4361", phash=PHASH_NEAR)
    rows = _rows(classify([still_a, mov_a, still_b, mov_b]))
    # GROUPING 10: a pHash match with no dHash is a hint. Two lone stills
    # never group, and their MOVs have no pHash, so nothing groups at all.
    gids = {_gid(rows, p) for p in (
        "/cam/IMG_4359.HEIC", "/cam/IMG_4359.MOV",
        "/cam/IMG_4361.HEIC", "/cam/IMG_4361.MOV",
    )}
    assert gids == {None}


def test_lone_live_still_with_a_hint_to_high_member_stays_ungrouped():
    """A lone still hints at a high member A (group G = {A, byte copy of A}).
    A hint never attaches, so the still and its MOV stay out of G. The still's
    own edge is low (no dHash on either side)."""
    still, mov = _pair("IMG_5", phash=PHASH_NEAR)
    a = _hr("/other/IMG_6.jpg", sha256="a6", phash=PHASH, exif_date=_dt())
    a_copy = _hr("/copy/IMG_6.jpg", sha256="a6", phash=PHASH, exif_date=_dt())
    rows = _rows(classify([still, mov, a, a_copy], threshold=10))
    # GROUPING 10: the still's link is a hint, so it never joins the high core
    # {A, byte copy of A}. The still and its MOV stay ungrouped.
    gid = _gid(rows, "/other/IMG_6.jpg")
    assert gid is not None
    assert _gid(rows, "/copy/IMG_6.jpg") == gid
    assert _gid(rows, "/cam/IMG_5.HEIC") is None
    assert _gid(rows, "/cam/IMG_5.MOV") is None
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
    # GROUPING 10: X, Y and the still are all pHash-only, so every link is a
    # hint. None of them is grouped.
    grouped = [p for p in ("/cam/X.jpg", "/cam/Y.jpg") if _gid(rows, p) is not None]
    assert grouped == []
    assert _gid(rows, "/cam/IMG_7.HEIC") is None
    assert _gid(rows, "/cam/IMG_7.MOV") is None


def test_false_stem_pair_without_other_edges_gets_no_group():
    jpg = _hr("/cam/DSC_0001.JPG", sha256="j", phash=PHASH, exif_date=_dt(),
              pair_cluster=(Path("/cam/DSC_0001.MP4"),))
    mp4 = _hr("/cam/DSC_0001.MP4", sha256="p", phash=None, file_type="mp4",
              exif_date=_dt(), pair_cluster=(Path("/cam/DSC_0001.JPG"),))
    rows = _rows(classify([jpg, mp4]))
    assert _gid(rows, "/cam/DSC_0001.JPG") is None
    assert _gid(rows, "/cam/DSC_0001.MP4") is None


_SKIP_TEXT = "pair link skipped: its stills are in different groups"
_JUNE_CLUSTER = ("jpg", "dng", "mov", "mp4")


def _january_june(cids: dict[str, str]) -> list:
    """GROUPING 10 (G7) January/June shape: one folder, one stem. A JPG and a
    DNG that are both still frames of different moments, each with a byte copy
    elsewhere, and a MOV and an MP4 video. Their pHashes are far apart, so the
    two stills never join each other by content. ``cids`` maps a member name to
    its ContentIdentifier; a missing name has none."""
    paths = {
        "jpg": Path("/cam/IMG_7.JPG"), "dng": Path("/cam/IMG_7.DNG"),
        "mov": Path("/cam/IMG_7.MOV"), "mp4": Path("/cam/IMG_7.MP4"),
    }

    def cluster_of(name: str) -> tuple[Path, ...]:
        return tuple(p for k, p in paths.items() if k != name)

    jpg = _hr(str(paths["jpg"]), sha256="j", phash=PHASH, file_type="jpeg",
              exif_date=_dt(), pair_cluster=cluster_of("jpg"),
              content_identifier=cids.get("jpg"))
    dng = _hr(str(paths["dng"]), sha256="d", phash=PHASH_FAR, file_type="raw",
              exif_date=_dt(), pair_cluster=cluster_of("dng"),
              content_identifier=cids.get("dng"))
    mov = _hr(str(paths["mov"]), sha256="m", phash=None, file_type="mov",
              exif_date=_dt(), pair_cluster=cluster_of("mov"),
              content_identifier=cids.get("mov"))
    mp4 = _hr(str(paths["mp4"]), sha256="p", phash=None, file_type="mp4",
              exif_date=_dt(), pair_cluster=cluster_of("mp4"),
              content_identifier=cids.get("mp4"))
    jpg_copy = _hr("/elsewhere/IMG_7.jpg", sha256="j", phash=PHASH,
                   file_type="jpeg", exif_date=_dt())
    dng_copy = _hr("/elsewhere/IMG_7.dng", sha256="d", phash=PHASH_FAR,
                   file_type="raw", exif_date=_dt())
    return [jpg, dng, mov, mp4, jpg_copy, dng_copy]


_JUNE_PATHS = {
    "jpg": "/cam/IMG_7.JPG", "dng": "/cam/IMG_7.DNG",
    "mov": "/cam/IMG_7.MOV", "mp4": "/cam/IMG_7.MP4",
}


def _june_rows(cids: dict[str, str], order: tuple[int, ...]) -> dict:
    records = _january_june(cids)
    cluster, copies = records[:4], records[4:]
    ordered = [cluster[i] for i in order] + copies
    return _rows(classify(ordered))


def test_january_june_no_cids_gives_two_groups_and_no_video_group():
    """No ContentIdentifier anywhere: the stem alone pairs nothing. Each still
    groups with its own byte copy, and both videos stay ungrouped."""
    rows = _june_rows({}, (0, 1, 2, 3))
    jpg_gid = _gid(rows, _JUNE_PATHS["jpg"])
    dng_gid = _gid(rows, _JUNE_PATHS["dng"])
    assert jpg_gid is not None and jpg_gid == _gid(rows, "/elsewhere/IMG_7.jpg")
    assert dng_gid is not None and dng_gid == _gid(rows, "/elsewhere/IMG_7.dng")
    assert jpg_gid != dng_gid
    assert _gid(rows, _JUNE_PATHS["mov"]) is None
    assert _gid(rows, _JUNE_PATHS["mp4"]) is None


def test_january_june_videos_join_the_jpg_group_when_their_cid_matches_it():
    """JPG, MOV and MP4 share one identity: the videos join the JPG's group.
    The DNG, with no identity, stays in its own group."""
    cids = {"jpg": "CID-7", "mov": "CID-7", "mp4": "CID-7"}
    rows = _june_rows(cids, (0, 1, 2, 3))
    jpg_gid = _gid(rows, _JUNE_PATHS["jpg"])
    assert jpg_gid is not None
    assert _gid(rows, _JUNE_PATHS["mov"]) == jpg_gid
    assert _gid(rows, _JUNE_PATHS["mp4"]) == jpg_gid
    assert _gid(rows, "/elsewhere/IMG_7.jpg") == jpg_gid
    dng_gid = _gid(rows, _JUNE_PATHS["dng"])
    assert dng_gid is not None and dng_gid != jpg_gid


def test_january_june_all_four_share_one_cid_refuses_to_bridge_the_two_groups():
    """All four share one identity. The cluster would bridge the JPG group and
    the DNG group, which content kept apart, so no video joins. Each video's
    reason says the link was skipped, so the refusal is visible."""
    cids = {name: "CID-7" for name in _JUNE_CLUSTER}
    rows = _june_rows(cids, (0, 1, 2, 3))
    jpg_gid = _gid(rows, _JUNE_PATHS["jpg"])
    dng_gid = _gid(rows, _JUNE_PATHS["dng"])
    assert jpg_gid is not None and dng_gid is not None and jpg_gid != dng_gid
    for name in ("mov", "mp4"):
        row = rows[_JUNE_PATHS[name]]
        assert row.group_id is None
        assert _SKIP_TEXT in row.reason


def _result_of(rows: dict) -> dict:
    return {p: (r.group_id, r.action, r.reason) for p, r in rows.items()}


@pytest.mark.parametrize("cids", [
    {},
    {"jpg": "CID-7", "mov": "CID-7", "mp4": "CID-7"},
    {name: "CID-7" for name in _JUNE_CLUSTER},
])
def test_january_june_result_is_identical_for_all_24_orders(cids):
    """Every order of the four cluster files gives the same group ids, actions
    and reasons. Union-find order must never decide a pairing."""
    expected = _result_of(_june_rows(cids, (0, 1, 2, 3)))
    for order in itertools.permutations(range(4)):
        assert _result_of(_june_rows(cids, order)) == expected, order


def test_image_image_same_stem_pair_still_groups():
    """Scope guard: RAW+JPG of one shot (image<->image) still groups with no
    other edge. GROUPING 10 (G4) removed the stem edge, so the group now comes
    from the pHash tiers alone: the two stills have an identical pHash."""
    ph = "f0f0f0f0f0f0f0f0"
    jpg = _hr("/cam/DSC_0020.jpg", sha256="aa", phash=ph, exif_date=_dt(),
              file_type="jpeg", pair_cluster=(Path("/cam/DSC_0020.nef"),))
    raw = _hr("/cam/DSC_0020.nef", sha256="bb", phash=ph, exif_date=_dt(),
              file_type="raw", pair_cluster=(Path("/cam/DSC_0020.jpg"),))
    rows = _rows(classify([jpg, raw]))
    gid = _gid(rows, "/cam/DSC_0020.jpg")
    assert gid is not None
    assert _gid(rows, "/cam/DSC_0020.nef") == gid
