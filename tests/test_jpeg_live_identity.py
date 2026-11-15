"""A JPEG Live still gives up its Apple ContentIdentifier in the in-memory pass.

The identity lives in the Apple MakerNote, which PIL hands over as raw bytes.
``apple_maker_note_content_id`` parses it, so a JPEG needs no exiftool round
trip to pair with its video, whatever the video is named. Each test names the
wrong pairing it guards against.
"""
from __future__ import annotations

import sqlite3

from PIL import Image

from scanner.exif import apple_maker_note_content_id, extract_pil_scoring_signals

CID = "ABCDEF12-3456-7890-ABCD-EF1234567890"
_EXIF_IFD, _MAKER_NOTE = 0x8769, 0x927C


def _apple_note(entries: list[tuple[int, int, bytes]], order: str = "big") -> bytes:
    """An Apple MakerNote holding ``(tag, type, value bytes)`` entries. Values
    longer than four bytes sit after the IFD, addressed from the note start."""
    mark = b"MM" if order == "big" else b"II"
    head = b"Apple iOS\x00\x00\x01" + mark + len(entries).to_bytes(2, order)
    data_at = len(head) + 12 * len(entries) + 4
    table, data = b"", b""
    for tag, kind, value in entries:
        table += tag.to_bytes(2, order) + kind.to_bytes(2, order) + len(value).to_bytes(4, order)
        if len(value) <= 4:
            table += value.ljust(4, b"\x00")
        else:
            table += (data_at + len(data)).to_bytes(4, order)
            data += value
    return head + table + (0).to_bytes(4, order) + data


def _cid_entry(text: str = CID) -> tuple[int, int, bytes]:
    return (0x0011, 2, text.encode("ascii") + b"\x00")


def _jpeg(path, maker_note: bytes | None) -> None:
    img = Image.new("RGB", (32, 32))
    exif = img.getexif()
    if maker_note is not None:
        exif.get_ifd(_EXIF_IFD)[_MAKER_NOTE] = maker_note
    img.save(str(path), "JPEG", quality=90, exif=exif.tobytes())


class TestAppleMakerNote:
    def test_identifier_is_read_among_other_tags(self):
        """Catches: reading the first entry instead of tag 0x0011."""
        note = _apple_note([(0x0001, 9, (14).to_bytes(4, "big")), _cid_entry(), (0x0014, 9, (1).to_bytes(4, "big"))])
        assert apple_maker_note_content_id(note) == CID

    def test_little_endian_note_reads_the_same(self):
        """Catches: assuming one byte order, which would read a garbage offset."""
        assert apple_maker_note_content_id(_apple_note([_cid_entry()], "little")) == CID

    def test_value_is_upper_cased_like_the_exiftool_route(self):
        """Catches: a lower-case still identity never equalling its video's."""
        assert apple_maker_note_content_id(_apple_note([_cid_entry(CID.lower())])) == CID

    def test_apple_note_without_the_tag_gives_none(self):
        """Catches: a non-Live iPhone JPEG getting some other tag as identity."""
        assert apple_maker_note_content_id(_apple_note([(0x0001, 9, (14).to_bytes(4, "big"))])) is None

    def test_another_makers_note_gives_none(self):
        """Catches: parsing a Nikon or Canon note as if it were Apple's."""
        assert apple_maker_note_content_id(b"Nikon\x00\x02\x10\x00\x00MM\x00\x2a" + bytes(64)) is None

    def test_truncated_note_gives_none_not_a_partial_identity(self):
        """Catches: a cut-off note pairing two files on a shared prefix."""
        whole = _apple_note([_cid_entry()])
        assert apple_maker_note_content_id(whole[:-10]) is None

    def test_missing_note_gives_none(self):
        assert apple_maker_note_content_id(None) is None


class TestJpegThroughTheInMemoryPass:
    def test_signals_carry_the_identifier_of_a_real_jpeg(self, tmp_path):
        p = tmp_path / "live.jpg"
        _jpeg(p, _apple_note([_cid_entry()]))
        with Image.open(p) as img:
            assert extract_pil_scoring_signals(img)["content_identifier"] == CID

    def test_jpeg_without_a_maker_note_has_no_identifier(self, tmp_path):
        p = tmp_path / "plain.jpg"
        _jpeg(p, None)
        with Image.open(p) as img:
            assert extract_pil_scoring_signals(img)["content_identifier"] is None

    def test_hash_result_extract_carries_the_identifier(self, tmp_path):
        """Catches: the signal being read but dropped before the extract, so the
        backfill finds nothing and the JPEG never pairs."""
        from scanner.hasher import compute_from_bytes
        from scanner.walker import FileRecord

        p = tmp_path / "live.jpg"
        _jpeg(p, _apple_note([_cid_entry()]))
        rec = FileRecord(path=p, source_label="src", file_type="jpeg")
        _idx, outcome = compute_from_bytes(0, rec, p.read_bytes())
        assert outcome.to_media_extract().content_identifier == CID

    def test_scan_without_exiftool_stores_the_jpeg_identifier(self, tmp_path, monkeypatch):
        """The real path: a scan with no exiftool on PATH still writes the JPEG's
        identity to the manifest, with no video named like it in the folder."""
        import scanner.exif as _exif
        from tests.test_scan_pipeline import _PipelineRun

        src = tmp_path / "src"
        src.mkdir()
        _jpeg(src / "renamed_still.jpg", _apple_note([_cid_entry()]))
        _jpeg(src / "plain.jpg", None)

        def raise_missing(*_a, **_kw):
            raise FileNotFoundError("exiftool not found")

        monkeypatch.setattr(_exif, "ExiftoolProcess", raise_missing)
        out = tmp_path / "manifest.sqlite"
        _PipelineRun(sources={"src": str(src)}, output_path=str(out), workers=2).run()

        with sqlite3.connect(out) as conn:
            got = dict(conn.execute("SELECT source_path, content_identifier FROM migration_manifest"))
        by_name = {path.replace("\\", "/").rsplit("/", 1)[-1]: cid for path, cid in got.items()}
        assert by_name == {"renamed_still.jpg": CID, "plain.jpg": None}
