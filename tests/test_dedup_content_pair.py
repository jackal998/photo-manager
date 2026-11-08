"""GROUPING 10 (G4) — a filename never creates a group edge.

Same-stem files used to group by name alone. Under GROUPING 10 a still<->still
stem pair gives no edge, and a pair with a video side gives an edge only when
both files carry the same Apple ContentIdentifier. These cases pin that rule.
"""
from __future__ import annotations

from pathlib import Path

from scanner.dedup import classify
from tests.test_dedup import _dt, _hr, _rows

PHASH = "aaaaaaaaaaaaaaaa"
PHASH_FAR = "5555555555555555"  # hamming 64 from PHASH


def _gid(rows: dict, path: str):
    return rows[Path(path).as_posix()].group_id


def test_lone_raw_jpg_pair_gated_out_by_phash_quality_gets_no_group():
    """A RAW+JPG pair of one stem, same pHash, where the pHash is too flat to
    trust (quality 5 < 16). GROUPING 9 grouped it through the stem alone. Under
    GROUPING 10 neither still has a content tier that passes, so both stay out."""
    raw = _hr("/cam/DSC_0030.NEF", sha256="raw", phash=PHASH, exif_date=_dt(),
              file_type="raw", pair_cluster=(Path("/cam/DSC_0030.JPG"),))
    raw.phash_quality = 5.0
    jpg = _hr("/cam/DSC_0030.JPG", sha256="jpg", phash=PHASH, exif_date=_dt(),
              file_type="jpeg", pair_cluster=(Path("/cam/DSC_0030.NEF"),))
    jpg.phash_quality = 5.0
    rows = _rows(classify([raw, jpg], min_phash_quality=16.0))
    assert _gid(rows, "/cam/DSC_0030.NEF") is None
    assert _gid(rows, "/cam/DSC_0030.JPG") is None


def test_same_stem_stills_with_far_phash_form_no_group():
    """A same-stem still pair whose pHashes sit 64 bits apart is a name
    collision, not a capture. No group, and no edge of any kind."""
    heic = _hr("/cam/IMG_5.HEIC", sha256="h", phash=PHASH, exif_date=_dt(),
               file_type="heic", pair_cluster=(Path("/cam/IMG_5.JPG"),))
    jpg = _hr("/cam/IMG_5.JPG", sha256="j", phash=PHASH_FAR, exif_date=_dt(),
              file_type="jpeg", pair_cluster=(Path("/cam/IMG_5.HEIC"),))
    rows = _rows(classify([heic, jpg]))
    assert _gid(rows, "/cam/IMG_5.HEIC") is None
    assert _gid(rows, "/cam/IMG_5.JPG") is None


def test_same_stem_videos_without_cid_form_no_group():
    """MOV and MP4 of one stem with distinct bytes and no identity: a stem
    alone does not make two videos one capture."""
    mov = _hr("/cam/IMG_6.MOV", sha256="m", phash=None, exif_date=_dt(),
              file_type="mov", pair_cluster=(Path("/cam/IMG_6.MP4"),))
    mp4 = _hr("/cam/IMG_6.MP4", sha256="p", phash=None, exif_date=_dt(),
              file_type="mp4", pair_cluster=(Path("/cam/IMG_6.MOV"),))
    rows = _rows(classify([mov, mp4]))
    assert _gid(rows, "/cam/IMG_6.MOV") is None
    assert _gid(rows, "/cam/IMG_6.MP4") is None


def test_pair_joins_video_only_component_when_only_one_still_component_is_touched():
    """G7 refined: a still-bearing component and a video-only component, linked
    by equal CIDs, are one capture's artefacts and must union. The HEIC and its
    byte copy form one component with a still; the MOV and its byte copy form a
    video-only one."""
    heic = _hr("/iphone/IMG_1.HEIC", sha256="h", phash=PHASH, exif_date=_dt(),
               file_type="heic", pair_partner=Path("/iphone/IMG_1.MOV"),
               content_identifier="CID-1")
    mov = _hr("/iphone/IMG_1.MOV", sha256="m", phash=None, exif_date=_dt(),
              file_type="mov", pair_partner=Path("/iphone/IMG_1.HEIC"),
              content_identifier="CID-1")
    heic_copy = _hr("/jdrive/IMG_1.HEIC", sha256="h", phash=PHASH, exif_date=_dt(),
                    file_type="heic")
    mov_copy = _hr("/jdrive/IMG_1.MOV", sha256="m", phash=None, exif_date=_dt(),
                   file_type="mov")
    rows = _rows(classify([heic, mov, heic_copy, mov_copy]))
    gid = _gid(rows, "/iphone/IMG_1.HEIC")
    assert gid is not None
    assert _gid(rows, "/iphone/IMG_1.MOV") == gid
    assert _gid(rows, "/jdrive/IMG_1.HEIC") == gid
    assert _gid(rows, "/jdrive/IMG_1.MOV") == gid


def test_cluster_refuses_to_bridge_two_still_components_and_skips_its_unjoined_video():
    """G7 refined: JPG and DNG each have a still-bearing byte-copy group, and
    MOV has a video-only copy group. With equal CIDs on all four, the cluster
    would bridge two still-bearing components, so nothing unions. The MP4 has
    no copy and stays unjoined, and its reason says why."""
    cid = "CID-9"
    jpg = _hr("/cam/IMG_9.JPG", sha256="j", phash=PHASH, exif_date=_dt(),
              file_type="jpeg", content_identifier=cid,
              pair_cluster=(Path("/cam/IMG_9.DNG"), Path("/cam/IMG_9.MOV"),
                            Path("/cam/IMG_9.MP4")))
    dng = _hr("/cam/IMG_9.DNG", sha256="d", phash=PHASH_FAR, exif_date=_dt(),
              file_type="raw", content_identifier=cid,
              pair_cluster=(Path("/cam/IMG_9.JPG"), Path("/cam/IMG_9.MOV"),
                            Path("/cam/IMG_9.MP4")))
    mov = _hr("/cam/IMG_9.MOV", sha256="m", phash=None, exif_date=_dt(),
              file_type="mov", content_identifier=cid,
              pair_cluster=(Path("/cam/IMG_9.JPG"), Path("/cam/IMG_9.DNG"),
                            Path("/cam/IMG_9.MP4")))
    mp4 = _hr("/cam/IMG_9.MP4", sha256="p", phash=None, exif_date=_dt(),
              file_type="mp4", content_identifier=cid,
              pair_cluster=(Path("/cam/IMG_9.JPG"), Path("/cam/IMG_9.DNG"),
                            Path("/cam/IMG_9.MOV")))
    jpg_copy = _hr("/copy/IMG_9.jpg", sha256="j", phash=PHASH, exif_date=_dt(),
                   file_type="jpeg")
    dng_copy = _hr("/copy/IMG_9.dng", sha256="d", phash=PHASH_FAR, exif_date=_dt(),
                   file_type="raw")
    mov_copy = _hr("/copy/IMG_9.mov", sha256="m", phash=None, exif_date=_dt(),
                   file_type="mov")
    rows = _rows(classify([jpg, dng, mov, mp4, jpg_copy, dng_copy, mov_copy]))
    jpg_gid = _gid(rows, "/cam/IMG_9.JPG")
    dng_gid = _gid(rows, "/cam/IMG_9.DNG")
    assert jpg_gid is not None and dng_gid is not None and jpg_gid != dng_gid
    assert _gid(rows, "/cam/IMG_9.MP4") is None
    assert "pair link skipped: its stills are in different groups" in rows["/cam/IMG_9.MP4"].reason


def test_same_stem_videos_with_equal_cid_join_a_byte_copy_of_the_mov():
    """Two videos with one ContentIdentifier are one capture. A byte copy of the
    MOV elsewhere gives the pair something to join, and both videos join it."""
    mov = _hr("/cam/IMG_7.MOV", sha256="m", phash=None, exif_date=_dt(),
              file_type="mov", pair_cluster=(Path("/cam/IMG_7.MP4"),),
              content_identifier="CID-VID")
    mp4 = _hr("/cam/IMG_7.MP4", sha256="p", phash=None, exif_date=_dt(),
              file_type="mp4", pair_cluster=(Path("/cam/IMG_7.MOV"),),
              content_identifier="CID-VID")
    copy = _hr("/copy/IMG_7.MOV", sha256="m", phash=None, exif_date=_dt(),
               file_type="mov")
    rows = _rows(classify([mov, mp4, copy]))
    gid = _gid(rows, "/cam/IMG_7.MOV")
    assert gid is not None
    assert _gid(rows, "/copy/IMG_7.MOV") == gid
    assert _gid(rows, "/cam/IMG_7.MP4") == gid
