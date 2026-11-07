"""GROUPING 10 (G3) — the ContentIdentifier backfill from exiftool extracts.

Pure helper, no pipeline run. The pipeline calls it after the exiftool stage,
so a result that reached classify carries the identity its extract read.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.app_service.scan_runner import _backfill_content_identifier, _fill_inmemory_extracts


def _result(path: str, cid: str | None = None):
    return SimpleNamespace(record=SimpleNamespace(path=Path(path)), content_identifier=cid)


def _extract(cid: str | None):
    return SimpleNamespace(content_identifier=cid)


def _jpeg_result(path: str):
    """A routed JPEG: in-memory signals present, and a converter that returns
    a marked extract so the test can tell which one landed in ``extracts``."""
    return SimpleNamespace(
        record=SimpleNamespace(path=Path(path)),
        inmemory_signals={"gps_present": False},
        to_media_extract=lambda: SimpleNamespace(
            name="inmemory", extraction_errors=[], content_identifier=None,
        ),
    )


def test_routed_jpeg_without_exiftool_extract_gets_its_inmemory_extract():
    """exiftool missing: the routed JPEG has no extract, so the in-memory
    signals fill the gap and it keeps its GPS, census and XMP evidence."""
    r = _jpeg_result("/cam/IMG_1.JPG")
    extracts: dict = {}
    _fill_inmemory_extracts([r], extracts)
    assert extracts[Path("/cam/IMG_1.JPG")].name == "inmemory"


def test_routed_jpeg_keeps_a_successful_exiftool_extract():
    """setdefault semantics: an exiftool extract with no errors is kept."""
    r = _jpeg_result("/cam/IMG_2.JPG")
    exif_ex = SimpleNamespace(name="exiftool", extraction_errors=[], content_identifier="C")
    extracts = {Path("/cam/IMG_2.JPG"): exif_ex}
    _fill_inmemory_extracts([r], extracts)
    assert extracts[Path("/cam/IMG_2.JPG")] is exif_ex


def test_routed_jpeg_with_a_failed_exiftool_extract_takes_the_inmemory_one():
    """A failed batch leaves a partial extract whose errors say so. The
    in-memory signals are better evidence than that empty partial."""
    r = _jpeg_result("/cam/IMG_3.JPG")
    partial = SimpleNamespace(name="partial", extraction_errors=["wedged"])
    extracts = {Path("/cam/IMG_3.JPG"): partial}
    _fill_inmemory_extracts([r], extracts)
    assert extracts[Path("/cam/IMG_3.JPG")].name == "inmemory"


def test_result_without_inmemory_signals_is_not_filled():
    """HEIC and video have no in-memory signals; their absence stays absent."""
    r = _result("/cam/IMG_4.HEIC")
    extracts: dict = {}
    _fill_inmemory_extracts([r], extracts)
    assert Path("/cam/IMG_4.HEIC") not in extracts


def test_identity_is_copied_from_the_extract():
    r = _result("/cam/IMG_1.HEIC")
    _backfill_content_identifier([r], {Path("/cam/IMG_1.HEIC"): _extract("CID-1")})
    assert r.content_identifier == "CID-1"


def test_extract_without_identity_leaves_none_not_empty():
    """A non-Apple or stripped file must stay None: an empty string would make
    two such files look like one capture."""
    r = _result("/cam/IMG_2.MOV")
    _backfill_content_identifier([r], {Path("/cam/IMG_2.MOV"): _extract(None)})
    assert r.content_identifier is None


def test_existing_value_is_not_overwritten():
    r = _result("/cam/IMG_3.HEIC", cid="KEEP-ME")
    _backfill_content_identifier([r], {Path("/cam/IMG_3.HEIC"): _extract("OTHER")})
    assert r.content_identifier == "KEEP-ME"


def test_result_without_an_extract_is_skipped():
    r = _result("/cam/IMG_4.JPG")
    _backfill_content_identifier([r], {Path("/cam/OTHER.JPG"): _extract("X")})
    assert r.content_identifier is None
