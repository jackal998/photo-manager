"""Layer-2 integration test: JPEG in-memory extraction (#786) vs real exiftool.

Marked @pytest.mark.integration and skipped when exiftool is absent.

What this proves
-----------------
``scanner.exif.extract_pil_scoring_signals`` — the in-memory JPEG pass that
lets ``core/app_service/scan_runner.py::_route_outcome`` skip the exiftool
subprocess round-trip entirely for JPEG — produces field-for-field IDENTICAL
``exif_date`` / ``gps_present`` / ``exif_tag_count`` / ``xmp_derived`` to
exiftool's own ``batch_read_extracts``, against real files:

* every ``qa/sandbox/exif-edge/*.jpg`` edge case (date fallback chain,
  sentinels, subsecond/timezone stripping)
* ``qa/sandbox/scoring-mixed/scoring_clean.jpg`` (GPS present) and
  ``scoring_no_gps.jpg`` (no GPS)
* two files generated at test time — one exercising the full 14-tag image
  EXIF census, one exercising a real ``xmpMM:DerivedFrom`` (Lightroom's
  compact attribute-form XMP) — because no file in this repo's committed
  corpus carries real XMP, so parity for those signals can't be checked
  against a committed fixture alone.

Run locally (exiftool must be on PATH):

    .venv/Scripts/python.exe -m pytest \\
        tests/integration/test_scanner_exif_parity.py -m integration -s --no-cov
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scanner.dedup import HashResult
from scanner.exif import ExiftoolProcess, batch_read_extracts
from scanner.hasher import run_hash_for_record
from scanner.walker import FileRecord

pytestmark = pytest.mark.integration

_EXIFTOOL = shutil.which("exiftool")


def _skip_if_no_exiftool() -> None:
    if _EXIFTOOL is None:
        pytest.skip("exiftool not installed — skipping in-memory JPEG parity test")


_REPO_ROOT = Path(__file__).resolve().parents[2]

# Real fixtures this PR flips JPEG onto the in-memory path for.
_REAL_FIXTURES = [
    _REPO_ROOT / "qa/sandbox/exif-edge/createdate_only.jpg",
    _REPO_ROOT / "qa/sandbox/exif-edge/dash_sentinel.jpg",
    _REPO_ROOT / "qa/sandbox/exif-edge/datetime_tag_only.jpg",
    _REPO_ROOT / "qa/sandbox/exif-edge/subsecond.jpg",
    _REPO_ROOT / "qa/sandbox/exif-edge/tz_offset.jpg",
    _REPO_ROOT / "qa/sandbox/exif-edge/zero_date_sentinel.jpg",
    _REPO_ROOT / "qa/sandbox/scoring-mixed/scoring_clean.jpg",
    _REPO_ROOT / "qa/sandbox/scoring-mixed/scoring_no_gps.jpg",
]


def _write_full_census_jpeg(path: Path) -> None:
    """A JPEG exercising all 14 image-EXIF census tags + GPS — no committed
    fixture covers this, so parity for the census counter's tag-by-tag
    mapping (Make/Model/ISO/FocalLength/... -> PIL numeric tag ids) would
    otherwise go unverified against real exiftool."""
    arr = np.zeros((32, 32, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    exif = img.getexif()
    exif[271] = "TestMake"
    exif[272] = "TestModel"
    exif[274] = 1
    exif[305] = "TestSoftware"
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[36867] = "2024:08:01 12:00:00"
    exif_ifd[34855] = 200
    exif_ifd[37386] = (50, 1)
    exif_ifd[33434] = (1, 200)
    exif_ifd[33437] = (28, 10)
    exif_ifd[37385] = 0
    exif_ifd[42036] = "TestLens"
    exif_ifd[40961] = 1
    exif_ifd[41987] = 0
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (37.0, 46.0, 0.0)
    gps[3] = "W"
    gps[4] = (122.0, 25.0, 0.0)
    img.save(str(path), "JPEG", quality=90, exif=exif.tobytes())


# Compact attribute-form xmpMM:DerivedFrom — verified live against real
# exiftool to be the form it actually reports as XMP:DerivedFrom (the
# nested-element form needs exiftool's -struct flag, which
# batch_read_extracts does not pass, so it would NOT be reported — using
# the attribute form here is deliberate, not an oversight).
_XMP_DERIVED_FROM_PACKET = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about=""'
    b' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"'
    b' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
    b' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' xmpMM:DerivedFrom="integration-test-original-id">'
    b'<xmp:Rating>4</xmp:Rating>'
    b'<dc:subject><rdf:Bag><rdf:li>vacation</rdf:li></rdf:Bag></dc:subject>'
    b'</rdf:Description></rdf:RDF></x:xmpmeta>'
)


def _write_xmp_derived_jpeg(path: Path) -> None:
    arr = np.zeros((32, 32, 3), dtype=np.uint8)
    Image.fromarray(arr).save(
        str(path), "JPEG", quality=90, xmp=_XMP_DERIVED_FROM_PACKET
    )


def _inmemory_extract(path: Path):
    rec = FileRecord(path=path, source_label="src", file_type="jpeg")
    idx, outcome = run_hash_for_record(0, rec)
    assert isinstance(outcome, HashResult), f"expected HashResult, got {outcome!r}"
    return outcome.to_media_extract()


def test_jpeg_inmemory_extraction_matches_exiftool_ground_truth(tmp_path):
    """The full parity table: every real qa/sandbox fixture this PR flips to
    the in-memory path, plus two synthetic files exercising signals no
    committed fixture has (full census, real XMP DerivedFrom)."""
    _skip_if_no_exiftool()

    missing = [p for p in _REAL_FIXTURES if not p.exists()]
    assert not missing, f"expected real fixtures missing from repo: {missing}"

    full_census = tmp_path / "full_census.jpg"
    _write_full_census_jpeg(full_census)
    xmp_derived = tmp_path / "xmp_derived.jpg"
    _write_xmp_derived_jpeg(xmp_derived)

    paths = list(_REAL_FIXTURES) + [full_census, xmp_derived]

    with ExiftoolProcess() as et:
        ground_truth = batch_read_extracts(paths, et)

    mismatches = []
    for p in paths:
        gt = ground_truth[p]
        me = _inmemory_extract(p)
        gt_tuple = (gt.exif_date, gt.gps_present, gt.exif_tag_count, gt.xmp_derived)
        me_tuple = (me.exif_date, me.gps_present, me.exif_tag_count, me.xmp_derived)
        if gt_tuple != me_tuple:
            mismatches.append((p.name, gt_tuple, me_tuple))

    assert not mismatches, (
        "in-memory JPEG extraction disagrees with real exiftool ground truth "
        f"on (exif_date, gps_present, exif_tag_count, xmp_derived): {mismatches}"
    )
