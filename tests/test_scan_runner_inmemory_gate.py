"""GROUPING 10 (G2) — which hash outcomes may skip the exiftool queue.

A JPEG Live still carries its ContentIdentifier only in an Apple MakerNote,
which PIL never parses. A still with a video in its same-stem cluster must
therefore take the exiftool route, where the identity is read. Every other
in-memory format keeps the free route. Pure predicate; no exiftool, no I/O.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.app_service.scan_runner import _inmemory_extract_ok


def _record(*peers: str) -> SimpleNamespace:
    return SimpleNamespace(pair_cluster=tuple(Path(p) for p in peers))


def _outcome(inmemory: object | None) -> SimpleNamespace:
    return SimpleNamespace(inmemory_signals=inmemory)


class TestInMemoryExtractGate:
    def test_lone_jpeg_takes_the_in_memory_route(self):
        assert _inmemory_extract_ok(_record(), _outcome({"gps": False})) is True

    def test_jpeg_with_mov_peer_takes_the_exiftool_route(self):
        """A still beside a MOV can only be paired by its ContentIdentifier,
        which PIL cannot read, so it must go to exiftool."""
        rec = _record("/cam/IMG_1.MOV")
        assert _inmemory_extract_ok(rec, _outcome({"gps": False})) is False

    def test_jpeg_with_mp4_peer_takes_the_exiftool_route(self):
        rec = _record("/cam/IMG_1.mp4")
        assert _inmemory_extract_ok(rec, _outcome({"gps": False})) is False

    def test_jpeg_with_a_still_peer_keeps_the_in_memory_route(self):
        """Only a video side needs the identity. A still-to-still cluster is
        not a pairing question."""
        rec = _record("/cam/IMG_1.PNG")
        assert _inmemory_extract_ok(rec, _outcome({"gps": False})) is True

    def test_heic_without_inmemory_signals_takes_the_exiftool_route(self):
        """HEIC has no in-memory signals (None), so it always goes to exiftool
        regardless of its cluster."""
        assert _inmemory_extract_ok(_record("/cam/IMG_2.MOV"), _outcome(None)) is False
        assert _inmemory_extract_ok(_record(), _outcome(None)) is False
