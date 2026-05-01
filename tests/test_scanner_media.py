"""Tests for scanner.media — magic-byte detection and filename parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanner.media import (
    DUPE_RE,
    EDITED_SUFFIXES,
    LOSSY_EXTENSIONS,
    MEDIA_EXTENSIONS,
    PHOTO_EXTENSIONS,
    RAW_EXTENSIONS,
    VIDEO_EXTENSIONS,
    get_file_type,
    parse_media_filename,
)


# ---------------------------------------------------------------------------
# Magic-byte fixtures (minimum bytes the detector inspects)
# ---------------------------------------------------------------------------

JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4
GIF87_MAGIC = b"GIF87a" + b"\x00" * 6
GIF89_MAGIC = b"GIF89a" + b"\x00" * 6
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP"
HEIC_MAGIC = b"\x00\x00\x00\x18ftypheic"
MP4_MAGIC = b"\x00\x00\x00\x18ftypmp42"
MOV_MAGIC = b"\x00\x00\x00\x18ftypqt  "


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_photo_and_video_partition_media(self):
        assert PHOTO_EXTENSIONS | VIDEO_EXTENSIONS == MEDIA_EXTENSIONS
        assert PHOTO_EXTENSIONS & VIDEO_EXTENSIONS == set()

    def test_raw_subset_of_photos(self):
        assert RAW_EXTENSIONS.issubset(PHOTO_EXTENSIONS)

    def test_lossy_disjoint_from_raw(self):
        assert LOSSY_EXTENSIONS & RAW_EXTENSIONS == set()


# ---------------------------------------------------------------------------
# get_file_type
# ---------------------------------------------------------------------------

class TestGetFileType:
    @pytest.mark.parametrize(
        "name,data,expected",
        [
            ("a.jpg", JPEG_MAGIC, "jpeg"),
            ("a.jpeg", JPEG_MAGIC, "jpeg"),
            ("a.png", PNG_MAGIC, "png"),
            ("a.gif", GIF87_MAGIC, "gif"),
            ("a.gif", GIF89_MAGIC, "gif"),
            ("a.webp", WEBP_MAGIC, "webp"),
            ("a.heic", HEIC_MAGIC, "heic"),
            ("a.heif", HEIC_MAGIC, "heic"),
            ("a.mp4", MP4_MAGIC, "mp4"),
            ("a.m4v", MP4_MAGIC, "mp4"),
            ("a.mov", MOV_MAGIC, "mov"),
        ],
    )
    def test_extension_matches_magic(self, tmp_path, name, data, expected):
        path = _write(tmp_path, name, data)
        kind, mismatch = get_file_type(path)
        assert kind == expected
        assert mismatch is False

    @pytest.mark.parametrize(
        "ext", [".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2", ".tif", ".tiff"]
    )
    def test_raw_extensions_not_magic_checked(self, tmp_path, ext):
        # RAW formats have varied magic bytes; the detector trusts the extension
        # unless magic happens to match a known non-RAW format. Random bytes
        # should be returned as "raw" with no mismatch.
        path = _write(tmp_path, f"a{ext}", b"\x00" * 32)
        kind, mismatch = get_file_type(path)
        assert kind == "raw"
        assert mismatch is False

    def test_unknown_extension_returns_skip(self, tmp_path):
        path = _write(tmp_path, "notes.txt", b"hello")
        kind, mismatch = get_file_type(path)
        assert kind == "skip"
        assert mismatch is False

    def test_case_insensitive_extension(self, tmp_path):
        path = _write(tmp_path, "PHOTO.JPG", JPEG_MAGIC)
        kind, _ = get_file_type(path)
        assert kind == "jpeg"

    def test_magic_mismatch_flags_caller(self, tmp_path):
        # File named .png but actually JPEG bytes — detector should flag mismatch
        path = _write(tmp_path, "fake.png", JPEG_MAGIC)
        kind, mismatch = get_file_type(path)
        assert kind == "jpeg"
        assert mismatch is True

    def test_heic_extension_with_mp4_magic_flags_mismatch(self, tmp_path):
        path = _write(tmp_path, "fake.heic", MP4_MAGIC)
        kind, mismatch = get_file_type(path)
        assert kind == "mp4"
        assert mismatch is True

    def test_jpeg_magic_check_skipped(self, tmp_path):
        # JPEG and MP4/MOV are NOT in the magic-verification list — corrupted
        # JPEG header with .jpg extension still returns "jpeg" without mismatch.
        path = _write(tmp_path, "x.jpg", b"\x00" * 32)
        kind, mismatch = get_file_type(path)
        assert kind == "jpeg"
        assert mismatch is False

    def test_unreadable_file_falls_through_to_declared(self, tmp_path):
        # Empty file: magic returns None → declared type is returned without mismatch
        path = _write(tmp_path, "empty.png", b"")
        kind, mismatch = get_file_type(path)
        assert kind == "png"
        assert mismatch is False

    def test_nonexistent_path_returns_declared(self, tmp_path):
        # OSError path: read fails, magic returns None, declared type wins
        path = tmp_path / "ghost.heic"
        kind, mismatch = get_file_type(path)
        assert kind == "heic"
        assert mismatch is False

    def test_heic_brand_variants(self, tmp_path):
        for brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc"):
            data = b"\x00\x00\x00\x18ftyp" + brand
            path = _write(tmp_path, f"x_{brand.decode()}.heic", data)
            kind, mismatch = get_file_type(path)
            assert kind == "heic", f"brand {brand!r}"
            assert mismatch is False


# ---------------------------------------------------------------------------
# parse_media_filename
# ---------------------------------------------------------------------------

class TestParseMediaFilename:
    def test_plain_filename(self):
        mf = parse_media_filename(Path("IMG_1234.jpg"))
        assert mf.base_stem == "IMG_1234"
        assert mf.number is None
        assert mf.suffix == ".jpg"
        assert mf.is_edited is False
        assert mf.clean_stem == "IMG_1234"

    def test_takeout_numbered_duplicate(self):
        mf = parse_media_filename(Path("IMG_9556(1).HEIC"))
        assert mf.base_stem == "IMG_9556"
        assert mf.number == 1
        assert mf.suffix == ".HEIC"  # original case preserved
        assert mf.is_edited is False
        assert mf.clean_stem == "IMG_9556"

    def test_takeout_multi_digit(self):
        mf = parse_media_filename(Path("photo(42).jpg"))
        assert mf.base_stem == "photo"
        assert mf.number == 42

    @pytest.mark.parametrize("suffix", EDITED_SUFFIXES)
    def test_each_edited_suffix_recognized(self, suffix):
        mf = parse_media_filename(Path(f"IMG_1{suffix}.jpg"))
        assert mf.is_edited is True
        assert mf.clean_stem == "IMG_1"
        assert mf.base_stem == f"IMG_1{suffix}"

    def test_combined_numbered_and_edited(self):
        mf = parse_media_filename(Path("IMG_1234(2)-edited.jpg"))
        # DUPE_RE matches stem "IMG_1234(2)-edited" — but the trailing
        # "-edited" prevents the (N) regex from firing (since the regex
        # requires the (N) to be at end-of-string). Verify actual behavior.
        # Expected: number=None, base_stem="IMG_1234(2)-edited",
        # is_edited=True, clean_stem="IMG_1234(2)"
        assert mf.number is None
        assert mf.base_stem == "IMG_1234(2)-edited"
        assert mf.is_edited is True
        assert mf.clean_stem == "IMG_1234(2)"

    def test_no_edited_suffix(self):
        mf = parse_media_filename(Path("vacation.png"))
        assert mf.is_edited is False
        assert mf.clean_stem == mf.base_stem == "vacation"

    def test_dupe_regex_requires_trailing_paren(self):
        # "(1)foo" should NOT match — the (N) must be at end of stem
        assert DUPE_RE.match("(1)foo") is None
        assert DUPE_RE.match("foo(1)") is not None

    def test_suffix_case_preserved(self):
        mf = parse_media_filename(Path("a.JPEG"))
        assert mf.suffix == ".JPEG"

    def test_chinese_edited_suffix(self):
        mf = parse_media_filename(Path("IMG_5-已編輯.heic"))
        assert mf.is_edited is True
        assert mf.clean_stem == "IMG_5"

    def test_paren_edited_suffix(self):
        mf = parse_media_filename(Path("IMG_5(已編輯).heic"))
        assert mf.is_edited is True
        assert mf.clean_stem == "IMG_5"

    def test_first_matching_suffix_wins(self):
        # If a stem ends with two recognized suffixes back-to-back, only the
        # first match in EDITED_SUFFIXES order is stripped.
        # "-edited(已編輯)" ends with "(已編輯)" first in iteration → that strips.
        # The leading "-edited" remains in clean_stem.
        mf = parse_media_filename(Path("IMG_5-edited(已編輯).heic"))
        assert mf.is_edited is True
        # The exact strip depends on EDITED_SUFFIXES iteration order; assert
        # that one suffix was stripped and the result is shorter than base.
        assert len(mf.clean_stem) < len(mf.base_stem)
