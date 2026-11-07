"""GROUPING 10 (G3) — the ContentIdentifier backfill from exiftool extracts.

Pure helper, no pipeline run. The pipeline calls it after the exiftool stage,
so a result that reached classify carries the identity its extract read.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.app_service.scan_runner import _backfill_content_identifier


def _result(path: str, cid: str | None = None):
    return SimpleNamespace(record=SimpleNamespace(path=Path(path)), content_identifier=cid)


def _extract(cid: str | None):
    return SimpleNamespace(content_identifier=cid)


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
