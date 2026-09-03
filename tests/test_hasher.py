"""Tests for scanner/hasher.py — SHA-256 and pHash computation."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


def _make_image(color: tuple = (128, 64, 32), size: tuple = (64, 64)) -> Image.Image:
    img = Image.new("RGB", size, color)
    return img


def _write_jpeg(path: Path, color=(128, 64, 32)) -> None:
    _make_image(color).save(path, "JPEG")


def _write_png(path: Path, color=(0, 128, 255)) -> None:
    _make_image(color).save(path, "PNG")


# ---------------------------------------------------------------------------
# compute_sha256
# ---------------------------------------------------------------------------

class TestComputeSha256:
    def test_stable_across_calls(self, tmp_path):
        from scanner.hasher import compute_sha256
        f = tmp_path / "file.bin"
        f.write_bytes(b"hello world")
        assert compute_sha256(f) == compute_sha256(f)

    def test_matches_hashlib(self, tmp_path):
        from scanner.hasher import compute_sha256
        data = b"test content " * 1000
        f = tmp_path / "data.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert compute_sha256(f) == expected

    def test_different_files_differ(self, tmp_path):
        from scanner.hasher import compute_sha256
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert compute_sha256(a) != compute_sha256(b)


# ---------------------------------------------------------------------------
# compute_phash
# ---------------------------------------------------------------------------

class TestComputePhash:
    def test_returns_string_for_jpeg(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "img.jpg"
        _write_jpeg(f)
        result = compute_phash(f, "jpeg")
        assert isinstance(result, str)
        assert len(result) == 16  # 64-bit hash = 16 hex chars

    def test_returns_string_for_png(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "img.png"
        _write_png(f)
        result = compute_phash(f, "png")
        assert isinstance(result, str)

    def test_returns_none_for_video(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "clip.mov"
        f.write_bytes(b"\x00" * 16)
        assert compute_phash(f, "mov") is None
        assert compute_phash(f, "mp4") is None

    def test_returns_none_for_gif(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "anim.gif"
        _make_image().save(f, "GIF")
        assert compute_phash(f, "gif") is None

    def test_same_image_same_hash(self, tmp_path):
        from scanner.hasher import compute_phash
        f1 = tmp_path / "a.jpg"
        f2 = tmp_path / "b.jpg"
        _write_jpeg(f1, color=(200, 100, 50))
        _write_jpeg(f2, color=(200, 100, 50))
        assert compute_phash(f1, "jpeg") == compute_phash(f2, "jpeg")

    def test_different_images_differ(self, tmp_path):
        from scanner.hasher import compute_phash
        f1 = tmp_path / "a.jpg"
        f2 = tmp_path / "b.jpg"
        # Use distinct gradient patterns — solid colors can produce identical pHash
        img1 = Image.new("RGB", (64, 64))
        img2 = Image.new("RGB", (64, 64))
        img1.putdata([(x * 4, 0, 0) for x in range(64 * 64)])          # red gradient
        img2.putdata([(0, 0, x * 4) for x in range(64 * 64)])          # blue gradient
        img1.save(f1, "JPEG", quality=95)
        img2.save(f2, "JPEG", quality=95)
        assert compute_phash(f1, "jpeg") != compute_phash(f2, "jpeg")

    def test_jpeg_png_same_content_same_hash(self, tmp_path):
        """Cross-format: JPEG and PNG of same image should produce same pHash."""
        from scanner.hasher import compute_phash
        img = _make_image(color=(100, 150, 200), size=(128, 128))
        jpg = tmp_path / "img.jpg"
        png = tmp_path / "img.png"
        # Save as JPEG with high quality to minimise compression artefacts
        img.save(jpg, "JPEG", quality=95)
        img.save(png, "PNG")
        h_jpg = compute_phash(jpg, "jpeg")
        h_png = compute_phash(png, "png")
        assert h_jpg is not None and h_png is not None
        # pHash may have small hamming distance due to JPEG artefacts; allow ≤ 4
        import imagehash
        dist = imagehash.hex_to_hash(h_jpg) - imagehash.hex_to_hash(h_png)
        assert dist <= 4, f"Expected cross-format pHash distance ≤ 4, got {dist}"

    def test_raw_embedded_preview(self, tmp_path):
        """RAW with embedded JPEG thumbnail → pHash extracted from thumbnail."""
        from scanner.hasher import compute_phash

        # Mock rawpy so we don't need a real RAW file
        thumb_mock = MagicMock()
        thumb_mock.format = MagicMock()

        buf = io.BytesIO()
        _make_image(color=(80, 160, 40), size=(64, 64)).save(buf, "JPEG")
        thumb_mock.data = buf.getvalue()

        import rawpy as _rawpy  # noqa: F401 (may not be installed)

        with patch("scanner.hasher.rawpy") as mock_rawpy, \
             patch("scanner.hasher._RAWPY_AVAILABLE", True):
            mock_rawpy.ThumbFormat.JPEG = thumb_mock.format
            mock_raw = MagicMock()
            mock_raw.extract_thumb.return_value = thumb_mock
            mock_rawpy.imread.return_value.__enter__ = lambda s: mock_raw
            mock_rawpy.imread.return_value.__exit__ = MagicMock(return_value=False)

            f = tmp_path / "photo.arw"
            f.write_bytes(b"\x00" * 16)
            result = compute_phash(f, "raw")

        assert result is not None


# ---------------------------------------------------------------------------
# compute_hashes — combined single-read API
# ---------------------------------------------------------------------------

class TestComputeHashes:
    def test_returns_tuple_sha_phash_date_for_jpeg(self, tmp_path):
        """compute_hashes returns (sha256, phash, colorhash, raw_date) for a JPEG."""
        from scanner.hasher import compute_hashes
        f = tmp_path / "img.jpg"
        _write_jpeg(f, color=(100, 180, 60))
        sha, ph, _, _date, *_ = compute_hashes(f, "jpeg")
        assert isinstance(sha, str) and len(sha) == 64
        assert isinstance(ph, str) and len(ph) == 16

    def test_sha_matches_compute_sha256(self, tmp_path):
        """SHA-256 from compute_hashes equals compute_sha256 on same file."""
        from scanner.hasher import compute_hashes, compute_sha256
        f = tmp_path / "img.jpg"
        _write_jpeg(f)
        sha_combined, *_ = compute_hashes(f, "jpeg")
        assert sha_combined == compute_sha256(f)

    def test_phash_matches_compute_phash(self, tmp_path):
        """pHash from compute_hashes equals compute_phash on same file."""
        from scanner.hasher import compute_hashes, compute_phash
        f = tmp_path / "img.jpg"
        _write_jpeg(f, color=(200, 80, 40))
        _, ph_combined, *_ = compute_hashes(f, "jpeg")
        assert ph_combined == compute_phash(f, "jpeg")

    def test_video_returns_none_phash_and_none_date(self, tmp_path):
        """Videos: pHash, colorhash, and date are None; SHA-256 is still computed."""
        from scanner.hasher import compute_hashes, compute_sha256
        f = tmp_path / "clip.mov"
        f.write_bytes(b"fake video data " * 100)
        sha, ph, ch, dt, *_ = compute_hashes(f, "mov")
        assert ph is None
        assert ch is None
        assert dt is None
        assert sha == compute_sha256(f)

    def test_png_single_read(self, tmp_path):
        """PNG is handled via the BytesIO path (same as JPEG)."""
        from scanner.hasher import compute_hashes, compute_sha256, compute_phash
        f = tmp_path / "img.png"
        _write_png(f)
        sha, ph, *_ = compute_hashes(f, "png")
        assert sha == compute_sha256(f)
        assert ph == compute_phash(f, "png")

    def test_corrupt_image_returns_sha_none_phash(self, tmp_path):
        """Unreadable image bytes: SHA is computed; pHash, colorhash, and date are None."""
        from scanner.hasher import compute_hashes
        f = tmp_path / "bad.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 50)  # JPEG magic but corrupt body
        sha, ph, ch, dt, *_ = compute_hashes(f, "jpeg")
        assert len(sha) == 64
        assert ph is None
        assert ch is None
        assert dt is None

    def test_mean_color_returned_for_jpeg(self, tmp_path):
        """compute_hashes returns a 'R,G,B' mean_color string for a valid JPEG."""
        from scanner.hasher import compute_hashes
        f = tmp_path / "img.jpg"
        _write_jpeg(f)
        _, _, _, ch, *_ = compute_hashes(f, "jpeg")  # (sha, phash, dhash, mean_color, …)
        assert ch is not None
        assert isinstance(ch, str)
        parts = ch.split(",")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_mean_color_none_for_video(self, tmp_path):
        """Videos return None mean_color (no PIL decode)."""
        from scanner.hasher import compute_hashes
        f = tmp_path / "clip.mov"
        f.write_bytes(b"fake video data " * 100)
        _, _, _, ch, *_ = compute_hashes(f, "mov")  # mean_color is index 3 now
        assert ch is None

    def test_jpeg_with_exif_date_returns_raw_date_string(self, tmp_path):
        """JPEG with DateTimeOriginal EXIF → raw_date_str returned in 4th element."""
        from scanner.hasher import compute_hashes
        from PIL import Image

        img = _make_image()
        exif = img.getexif()
        exif.get_ifd(0x8769)[36867] = "2024:06:15 10:30:00"  # ExifIFD DateTimeOriginal
        f = tmp_path / "dated.jpg"
        img.save(str(f), "JPEG", exif=exif.tobytes())

        _, _, _, _, raw_date, *_ = compute_hashes(f, "jpeg")  # raw_date is index 4 now
        assert raw_date == "2024:06:15 10:30:00"

    def test_jpeg_without_exif_date_returns_none_date(self, tmp_path):
        """JPEG with no date EXIF → raw_date_str is None."""
        from scanner.hasher import compute_hashes
        f = tmp_path / "nodated.jpg"
        _write_jpeg(f)
        _, _, _, _, raw_date, *_ = compute_hashes(f, "jpeg")  # raw_date is index 4 now
        # Plain solid-colour JPEG written by PIL has no DateTimeOriginal
        assert raw_date is None

    def test_raw_unsupported_format_returns_sha_only(self, tmp_path):
        """Misrouted RAW (e.g. non-camera TIFF) → LibRawFileUnsupportedError must not propagate.

        Regression for issue #46: rawpy.LibRawFileUnsupportedError used to abort
        the whole scan. Now it degrades to a sha-only record (same shape as a
        corrupted JPEG) and the caller can continue.
        """
        import rawpy as _rawpy

        from scanner.hasher import compute_hashes, compute_sha256

        f = tmp_path / "fake.tif"
        f.write_bytes(b"II*\x00" + b"\x00" * 64)  # TIFF magic but unparseable

        unsupported = _rawpy.LibRawFileUnsupportedError("Unsupported file format or not RAW file")

        with patch("scanner.hasher.rawpy") as mock_rawpy, \
             patch("scanner.hasher._RAWPY_AVAILABLE", True):
            # Preserve the real exception class so isinstance/except matches.
            mock_rawpy.LibRawError = _rawpy.LibRawError
            mock_rawpy.LibRawNoThumbnailError = _rawpy.LibRawNoThumbnailError
            # #591 — the RAW in-memory path now calls rawpy.imread(io.BytesIO(data))
            # (not the dropped rawpy.open_buffer); the path fallback also uses imread.
            mock_rawpy.imread.side_effect = unsupported

            sha, ph, dh, ch, dt, w, h, _ = compute_hashes(f, "raw")  # +dhash at index 2; #786 added index 7

        assert sha == compute_sha256(f)
        assert ph is None
        assert dh is None
        assert ch is None
        assert dt is None
        assert w is None
        assert h is None


# ── Coverage of guard branches: _HASH_AVAILABLE, _RAWPY_AVAILABLE, ───────
#    imagehash.phash failure, _raw_exif_date exception swallow ───────────


class TestRealDecodeFailures:
    """Tests using REAL malformed inputs, not mocked guard branches."""

    def test_compute_phash_returns_none_for_decode_failure(self, tmp_path):
        """Truncated JPEG → PIL.Image.load raises → compute_phash returns None.

        Uses an actual truncated JPEG (the same kind of corruption that
        triggered #57). The defensive `_HASH_AVAILABLE = False`,
        `_RAWPY_AVAILABLE = False`, and synthetic-imagehash-raising tests
        that previously lived here were dropped: they exercised guard
        branches by mocking dependencies, not by triggering real failures.
        Those guards are documented in scanner/hasher.py — if PIL/rawpy
        ever fail to import, the file gracefully reports None values.
        Real-world coverage of those paths belongs in an integration
        suite that runs without the optional deps installed.
        """
        from scanner.hasher import compute_phash
        from PIL import Image as _Img
        full = tmp_path / "_full.jpg"
        _Img.new("RGB", (200, 150), (10, 20, 30)).save(full, "JPEG")
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(full.read_bytes()[:512])
        full.unlink()
        assert compute_phash(bad, "jpeg") is None

    def test_raw_in_memory_path_skips_non_camera_tiff_unmocked(self, tmp_path):
        """#591 — the REAL in-memory RAW path (``rawpy.imread(io.BytesIO(data))``)
        must skip a non-camera TIFF cleanly: SHA present, all decode fields None,
        no crash.

        This is NOT mocked — it drives the actual rawpy 0.26.1 decode that every
        other RAW test stubs out (test_raw_unsupported_format_returns_sha_only
        patches ``rawpy`` entirely), which is exactly why the dead
        ``rawpy.open_buffer`` dead-end shipped untested. A regression that drops
        the ``io.BytesIO`` wrapper (passing raw ``bytes`` to ``imread`` raises an
        uncaught ``SystemError``) or breaks the LibRaw except chain fails here.
        """
        from scanner.hasher import compute_hashes, compute_sha256

        f = tmp_path / "scanner_output.tif"
        # TIFF magic but not a real camera RAW → LibRaw rejects it
        # (LibRawFileUnsupportedError) on both the io.BytesIO decode and the
        # path fallback — the #75 non-camera-TIFF class, routed to "raw".
        f.write_bytes(b"II*\x00" + b"\x00" * 256)

        sha, ph, dh, ch, dt, w, h, _ = compute_hashes(f, "raw")  # #786 added index 7

        assert sha == compute_sha256(f)
        assert ph is None and dh is None and ch is None
        assert dt is None and w is None and h is None


# ---------------------------------------------------------------------------
# run_hash_for_record — the picklable per-file compute path (#486)
# ---------------------------------------------------------------------------


def _record(path: Path, file_type: str):
    """Minimal FileRecord for the run_hash_for_record contract tests."""
    from scanner.walker import FileRecord

    return FileRecord(path=path, source_label="src", file_type=file_type)


class TestRunHashForRecord:
    """Contract of the picklable per-file dispatch used by the HASH stage.

    These exercise the orchestration run_hash_for_record layers on top of
    compute_hashes — idx passthrough, exception→HashFailure mapping, and
    the image-vs-non-image phash=None branch — using REAL inputs (a valid
    JPEG, a missing path, a truncated JPEG, a video-typed file), not mocked
    guard branches. The contract matters because a future ProcessPoolExecutor
    migration (#486 follow-up) relies on it: out-of-order completions are
    remapped by idx, and one unreadable file must degrade to a skip rather
    than abort the whole scan.
    """

    def test_valid_image_returns_hashresult_preserving_idx(self, tmp_path):
        """Catches: idx dropped/reordered or computed fields not mapped
        through — both corrupt the manifest when completions arrive out of
        order from a pool."""
        from scanner.hasher import run_hash_for_record
        from scanner.dedup import HashResult

        img = tmp_path / "ok.jpg"
        _write_jpeg(img, color=(200, 100, 50))
        expected_sha = hashlib.sha256(img.read_bytes()).hexdigest()

        idx, outcome = run_hash_for_record(7, _record(img, "jpeg"))

        assert idx == 7
        assert isinstance(outcome, HashResult)
        assert outcome.sha256 == expected_sha
        assert outcome.phash is not None
        assert outcome.mean_color is not None

    def test_missing_path_returns_hashfailure_not_raise(self, tmp_path):
        """Catches: an unreadable file (deleted mid-scan, permission loss)
        propagating its exception and aborting the whole scan instead of
        being recorded as a skip."""
        from scanner.hasher import run_hash_for_record, HashFailure

        gone = tmp_path / "deleted.jpg"  # never created

        idx, outcome = run_hash_for_record(3, _record(gone, "jpeg"))

        assert idx == 3
        assert isinstance(outcome, HashFailure)
        assert outcome.exc_type == "FileNotFoundError"
        assert outcome.exc_msg  # non-empty reason for the skipped[] log

    def test_truncated_image_returns_decode_failure(self, tmp_path):
        """Catches: a corrupt image (phash=None for an image type) silently
        entering the manifest as a success instead of being flagged as an
        ImageDecodeError skip."""
        from scanner.hasher import run_hash_for_record, HashFailure

        full = tmp_path / "_full.jpg"
        _write_jpeg(full)
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(full.read_bytes()[:512])
        full.unlink()

        idx, outcome = run_hash_for_record(0, _record(bad, "jpeg"))

        assert isinstance(outcome, HashFailure)
        assert outcome.exc_type == "ImageDecodeError"

    def test_video_phash_none_is_success_not_failure(self, tmp_path):
        """Catches: the `in _IMAGE_TYPES` guard regressing so a video
        (phash=None by design) is misclassified as a decode failure and
        dropped from the manifest."""
        from scanner.hasher import run_hash_for_record
        from scanner.dedup import HashResult

        vid = tmp_path / "clip.mov"
        vid.write_bytes(b"\x00\x01\x02not-a-real-video-but-real-bytes")

        idx, outcome = run_hash_for_record(1, _record(vid, "mov"))

        assert isinstance(outcome, HashResult)
        assert outcome.phash is None
        assert outcome.sha256 == hashlib.sha256(vid.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# read_for_record + compute_from_bytes — the split pipeline (#566)
# ---------------------------------------------------------------------------


class TestReadForRecord:
    """Contract of the READ stage of the bounded read→compute pipeline.

    These use REAL inputs so the tests catch actual I/O and routing bugs,
    not mocked guard branches.
    """

    def test_image_returns_bytes(self, tmp_path):
        """Catches: data=None or ReadFailure on a readable image file, which
        would silently bypass all hash computation downstream."""
        from scanner.hasher import read_for_record

        img = tmp_path / "ok.jpg"
        _write_jpeg(img)
        idx, record, data = read_for_record(5, _record(img, "jpeg"))

        assert idx == 5
        assert isinstance(data, bytes)
        assert len(data) == img.stat().st_size

    def test_idx_threaded_through_unchanged(self, tmp_path):
        """Catches: idx being reset to 0 or lost — would reorder
        hash_results and corrupt group_id determinism."""
        from scanner.hasher import read_for_record

        img = tmp_path / "ok.png"
        _write_png(img)
        idx, _, _ = read_for_record(42, _record(img, "png"))
        assert idx == 42

    def test_video_returns_data_none(self, tmp_path):
        """Video/gif/skip must return data=None so compute_from_bytes streams
        SHA from path without materialising the whole file into RAM (#453)."""
        from scanner.hasher import read_for_record

        vid = tmp_path / "v.mov"
        vid.write_bytes(b"fake" * 100)
        idx, _, data = read_for_record(3, _record(vid, "mov"))
        assert idx == 3
        assert data is None

    def test_gif_and_skip_return_data_none(self, tmp_path):
        """gif and skip types must also return data=None (not bytes)."""
        from scanner.hasher import read_for_record

        gif = tmp_path / "a.gif"
        gif.write_bytes(b"GIF89a" + b"\x00" * 20)
        _, _, data_gif = read_for_record(0, _record(gif, "gif"))
        assert data_gif is None

        # skip type — just a path that exists
        skip_f = tmp_path / "s.bin"
        skip_f.write_bytes(b"\x00")
        _, _, data_skip = read_for_record(0, _record(skip_f, "skip"))
        assert data_skip is None

    def test_missing_file_returns_read_failure(self, tmp_path):
        """An OSError on read must become a ReadFailure (not a raised
        exception), so compute_from_bytes maps it to HashFailure for the
        standard skip path rather than aborting the scan."""
        from scanner.hasher import read_for_record, ReadFailure

        gone = tmp_path / "deleted.jpg"  # never created
        idx, _, data = read_for_record(7, _record(gone, "jpeg"))

        assert idx == 7
        assert isinstance(data, ReadFailure)
        assert data.exc_type == "FileNotFoundError"
        assert data.exc_msg  # non-empty for the skipped[] log


class TestComputeFromBytes:
    """Contract of the COMPUTE stage of the bounded read→compute pipeline.

    Mirrors the existing run_hash_for_record contract tests: idx passthrough,
    ReadFailure→HashFailure mapping, video data-None→SHA stream, image happy
    path.  Each test catches a distinct failure mode in the two-stage split.
    """

    def test_image_returns_hashresult_preserving_idx(self, tmp_path):
        """Catches: idx not threaded through or hash fields not built — both
        corrupt the manifest when compute futures complete out of order."""
        from scanner.hasher import compute_from_bytes
        from scanner.dedup import HashResult

        img = tmp_path / "ok.jpg"
        _write_jpeg(img, color=(200, 100, 50))
        data = img.read_bytes()
        idx, outcome = compute_from_bytes(9, _record(img, "jpeg"), data)

        assert idx == 9
        assert isinstance(outcome, HashResult)
        assert outcome.sha256 == hashlib.sha256(data).hexdigest()
        assert outcome.phash is not None
        assert outcome.mean_color is not None

    def test_read_failure_maps_to_hash_failure(self, tmp_path):
        """Catches: ReadFailure silently becoming None (record skipped with
        no log entry) instead of HashFailure (record appears in skipped[])."""
        from scanner.hasher import compute_from_bytes, ReadFailure, HashFailure

        rf = ReadFailure("FileNotFoundError", "no such file")
        idx, outcome = compute_from_bytes(3, _record(tmp_path / "x.jpg", "jpeg"), rf)

        assert idx == 3
        assert isinstance(outcome, HashFailure)
        assert outcome.exc_type == "FileNotFoundError"

    def test_video_data_none_streams_sha(self, tmp_path):
        """Catches: video data=None triggering a KeyError or a full
        in-memory read (which would blow the #453 RAM ceiling on large
        video files)."""
        from scanner.hasher import compute_from_bytes
        from scanner.dedup import HashResult

        vid = tmp_path / "clip.mov"
        vid.write_bytes(b"\x00\x01\x02fake-video-payload" * 50)
        idx, outcome = compute_from_bytes(1, _record(vid, "mov"), None)

        assert idx == 1
        assert isinstance(outcome, HashResult)
        expected_sha = hashlib.sha256(vid.read_bytes()).hexdigest()
        assert outcome.sha256 == expected_sha
        assert outcome.phash is None

    def test_corrupt_image_returns_decode_failure(self, tmp_path):
        """Catches: a truncated JPEG silently entering the manifest as a
        success instead of appearing in skipped[] with ImageDecodeError."""
        from scanner.hasher import compute_from_bytes, HashFailure

        full = tmp_path / "_full.jpg"
        _write_jpeg(full)
        data = full.read_bytes()[:512]  # truncated
        full.unlink()

        _, outcome = compute_from_bytes(0, _record(tmp_path / "bad.jpg", "jpeg"), data)
        assert isinstance(outcome, HashFailure)
        assert outcome.exc_type == "ImageDecodeError"

    def test_uses_provided_bytes_not_re_reading_file(self, tmp_path):
        """compute_from_bytes must hash the PROVIDED data, not re-read the file
        from disk — preserving the single-read guarantee.

        Demonstrated on the JPEG path by passing bytes that differ from the
        on-disk content: the SHA must match the PASSED bytes, not the file.
        (The RAW in-memory path's single-read property is covered separately by
        the #591 fix and TestRealDecodeFailures.)
        """
        from scanner.hasher import compute_from_bytes

        img = tmp_path / "ok.jpg"
        _write_jpeg(img, color=(10, 20, 30))
        # Bytes we pass: a differently-colored JPEG written to a temp path
        img2 = tmp_path / "ok2.jpg"
        _write_jpeg(img2, color=(200, 180, 160))
        data_to_pass = img2.read_bytes()

        _, outcome = compute_from_bytes(0, _record(img, "jpeg"), data_to_pass)
        # SHA must match the PASSED bytes, not the on-disk file
        assert outcome.sha256 == hashlib.sha256(data_to_pass).hexdigest()


class TestImageDraftPhashSafety:
    """#569 — Image.draft (JPEG shrink-on-load at 256px) must not shift phash/
    dhash beyond the near-duplicate grouping threshold, or it would change group
    membership across a re-scan.

    An A/B on 597 real JPEGs proved it safe at (256,256) (0 over threshold, 0
    real group flips). This is the CI regression guard: shrink the draft target
    too far (e.g. 32px) or low-pass the image away and the phash drift exceeds
    the threshold and this fails. Not padding — it pins the (256,256) choice
    against a real correctness failure mode (silent group-membership flips).
    """

    def test_draft_preserves_phash_and_dhash_within_threshold(self):
        import io as _io

        import imagehash
        import numpy as np
        from PIL import Image

        PHASH_THRESHOLD = 10  # scan default (config.threshold)
        DHASH_THRESHOLD = 10

        def _make_jpeg(seed: int) -> bytes:
            # Photo-like content: a low-frequency gradient (what phash keys on)
            # plus BLOCK-level colour regions (128px) — NOT per-pixel white noise,
            # whose high-frequency energy aliases under any downscale and isn't
            # representative of real photos. >256px so draft really shrinks.
            rng = np.random.default_rng(seed)
            gy, gx = np.mgrid[0:768, 0:1024]
            r = (gx * 255 // 1024).astype(np.uint8)
            g = (gy * 255 // 768).astype(np.uint8)
            blocks = rng.integers(0, 256, size=(768 // 128, 1024 // 128), dtype=np.uint8)
            b = np.repeat(np.repeat(blocks, 128, axis=0), 128, axis=1).astype(np.uint8)
            arr = np.stack([r, g, b], axis=-1)
            buf = _io.BytesIO()
            Image.fromarray(arr, "RGB").save(buf, format="JPEG", quality=88)
            return buf.getvalue()

        for seed in range(6):
            data = _make_jpeg(seed)
            with Image.open(_io.BytesIO(data)) as im:
                base = im.convert("RGB")
                base.load()
            with Image.open(_io.BytesIO(data)) as im:
                im.draft("RGB", (256, 256))  # the #569 path
                drafted = im.convert("RGB")
                drafted.load()

            ph_drift = imagehash.phash(base) - imagehash.phash(drafted)
            dh_drift = imagehash.dhash(base) - imagehash.dhash(drafted)
            assert ph_drift <= PHASH_THRESHOLD, (
                f"seed {seed}: Image.draft shifted phash by {ph_drift} > "
                f"{PHASH_THRESHOLD} — would flip near-duplicate group membership (#569)"
            )
            assert dh_drift <= DHASH_THRESHOLD, (
                f"seed {seed}: Image.draft shifted dhash by {dh_drift} > {DHASH_THRESHOLD}"
            )


# ---------------------------------------------------------------------------
# #822 — EXIF Orientation applied before phash/dhash
# #826 — RAW: one LibRaw open, embedded preview hashed at working resolution
# ---------------------------------------------------------------------------


def _photo_array(w: int, h: int):
    """Photo-like pixels: two smooth gradients + 64 px colour blocks.

    Deliberately NOT flat colour and NOT white noise — a flat image collides on
    ``8000000000000000`` for every input (so it would pass whatever the hasher
    did), and per-pixel noise aliases under any downscale. The gradients carry
    the low-frequency energy phash keys on; the blocks give dhash a gradient
    sign to read.
    """
    import numpy as np

    gy, gx = np.mgrid[0:h, 0:w]
    r = (gx * 255 // w).astype(np.uint8)
    g = (gy * 255 // h).astype(np.uint8)
    b = (((gx // 64) * 37 + (gy // 64) * 91) % 256).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def _write_oriented_pair(tmp_path: Path) -> "tuple[Path, Path, Image.Image]":
    """One photo, two files whose CONTENT is pixel-identical up to the rotation.

    * ``rotated_ori6.jpg`` — pixels stored rotated, ``Orientation=6``. That tag
      means "rotate the stored pixels 90° CW to display", which is exactly what
      ``ImageOps.exif_transpose`` applies. This is a phone portrait.
    * ``upright.png`` — the same photo with the rotation baked into the pixels:
      an export, an edit, a messenger re-save. Derived by decoding the JPEG and
      rotating it, so the two files decode to exactly rotation-equivalent
      arrays and a correct hasher must produce the *same* hash, distance 0.

    The second file is lossless on purpose. Two independent JPEG encodes of
    90°-rotated arrays do NOT decode to rotation-equivalent pixels — the DCT
    blocks land differently against the content — which measured 2 bits of
    phash drift here and would put JPEG fidelity, not the Orientation tag, in
    the assertion. The JPEG-vs-JPEG case is asserted separately below against
    the threshold the grouping tier actually uses. Cross-format hash identity
    is already an invariant of this recipe
    (``TestComputePhash::test_jpeg_png_same_content_same_hash``).

    Returns ``(upright_path, rotated_path, stored_pixels)``.
    """
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation
    rot_path = tmp_path / "rotated_ori6.jpg"
    Image.fromarray(_photo_array(512, 384), "RGB").transpose(
        Image.Transpose.ROTATE_90
    ).save(rot_path, "JPEG", quality=95, exif=exif)

    with Image.open(rot_path) as im:
        stored = im.convert("RGB")  # what the file really holds, post-JPEG
    up_path = tmp_path / "upright.png"
    stored.transpose(Image.Transpose.ROTATE_270).save(up_path, "PNG")
    return up_path, rot_path, stored


def _write_jpeg_jpeg_oriented_pair(tmp_path: Path) -> "tuple[Path, Path]":
    """The same pair as two independent JPEG encodes — the lossy real-world case."""
    upright = Image.fromarray(_photo_array(512, 384), "RGB")
    up_path = tmp_path / "upright.jpg"
    upright.save(up_path, "JPEG", quality=95)

    exif = Image.Exif()
    exif[0x0112] = 6
    rot_path = tmp_path / "rotated_ori6.jpg"
    upright.transpose(Image.Transpose.ROTATE_90).save(
        rot_path, "JPEG", quality=95, exif=exif
    )
    return up_path, rot_path


class TestExifOrientationBeforeHashing:
    """#822 — a rotated copy and its upright original must land in the same
    near-duplicate tier.

    Before this fix ``scanner/hasher.py`` applied no orientation at all
    (``grep -rn "exif_transpose\\|ImageOps" scanner/`` returned zero hits),
    so 19.4 % of this library — every file carrying a non-identity Orientation
    tag — hashed as if it were sideways. The pair simply never met, and a
    near-duplicate miss reports nothing, so the gap was invisible.
    """

    def test_fixture_really_is_rotated(self, tmp_path):
        """The fixture must actually exercise the bug (no vacuous pass).

        If the two files' *stored* pixels hashed the same anyway, the test
        below would pass without the hasher doing anything — so pin both
        halves: the tag is really 6, and the un-oriented pixels really differ.
        """
        import imagehash

        up_path, rot_path, stored = _write_oriented_pair(tmp_path)

        with Image.open(rot_path) as im:
            assert im.getexif().get(0x0112) == 6, "fixture lost its Orientation tag"

        with Image.open(up_path) as im:
            upright_hash = imagehash.phash(im.convert("RGB"))
        stored_hash = imagehash.phash(stored)
        assert upright_hash - stored_hash > 0, (
            "fixture is orientation-blind: the stored pixels hash the same as "
            "the upright ones, so the orientation assertion would be vacuous"
        )

    def test_rotated_copy_and_upright_hash_to_distance_zero(self, tmp_path):
        import imagehash

        from scanner.hasher import compute_hashes

        up_path, rot_path, _stored = _write_oriented_pair(tmp_path)

        _sha_u, ph_u, dh_u, *_ = compute_hashes(up_path, "png")
        _sha_r, ph_r, dh_r, *_ = compute_hashes(rot_path, "jpeg")

        ph_dist = imagehash.hex_to_hash(ph_u) - imagehash.hex_to_hash(ph_r)
        dh_dist = imagehash.hex_to_hash(dh_u) - imagehash.hex_to_hash(dh_r)
        assert ph_dist == 0, (
            f"phash distance {ph_dist} between an upright copy and the same "
            f"photo stored with Orientation=6 ({ph_u} vs {ph_r}) — EXIF "
            "orientation is not applied before hashing (#822)"
        )
        assert dh_dist == 0, (
            f"dhash distance {dh_dist} between the same two files "
            f"({dh_u} vs {dh_r}) — EXIF orientation is not applied (#822)"
        )

    def test_jpeg_pair_lands_inside_the_near_duplicate_tier(self, tmp_path):
        """The lossy real-world case: two independent JPEG encodes.

        This is what #822 actually promises the user — a phone portrait and a
        rotation-baked re-save MEET in the near-duplicate tier. On the base
        recipe this pair measured **30** bits of phash distance, far outside
        the scan's default threshold of 10, so the pair never met and nothing
        was ever reported. Asserting against the threshold rather than 0 keeps
        JPEG re-encode noise (measured: 2 bits) out of the assertion.
        """
        import imagehash

        from scanner.hasher import compute_hashes

        PHASH_THRESHOLD = 10  # scan default (config.threshold)

        up_path, rot_path = _write_jpeg_jpeg_oriented_pair(tmp_path)
        _sha_u, ph_u, dh_u, *_ = compute_hashes(up_path, "jpeg")
        _sha_r, ph_r, dh_r, *_ = compute_hashes(rot_path, "jpeg")

        ph_dist = imagehash.hex_to_hash(ph_u) - imagehash.hex_to_hash(ph_r)
        dh_dist = imagehash.hex_to_hash(dh_u) - imagehash.hex_to_hash(dh_r)
        assert ph_dist < PHASH_THRESHOLD, (
            f"phash distance {ph_dist} >= {PHASH_THRESHOLD}: the rotated copy "
            f"misses the near-duplicate tier ({ph_u} vs {ph_r}, #822)"
        )
        assert dh_dist < PHASH_THRESHOLD, f"dhash distance {dh_dist} (#822)"

    def test_compute_from_bytes_path_is_oriented_too(self, tmp_path):
        """The #566 read→compute pipeline must get the same treatment.

        ``compute_from_bytes`` is the stage a real thread-mode scan actually
        runs; ``compute_hashes`` is the fused/process-mode path. A fix applied
        to only one of them would leave real scans broken while the test above
        stayed green.
        """
        from scanner.hasher import compute_from_bytes

        up_path, rot_path, _stored = _write_oriented_pair(tmp_path)

        _i, up_res = compute_from_bytes(0, _record(up_path, "png"), up_path.read_bytes())
        _j, rot_res = compute_from_bytes(1, _record(rot_path, "jpeg"), rot_path.read_bytes())

        assert up_res.phash == rot_res.phash
        assert up_res.dhash == rot_res.dhash


def _fake_rawpy(thumb_bytes: bytes, width: int, height: int, opens: list):
    """A stand-in ``rawpy`` module that counts LibRaw opens.

    Real camera RAW is not in this repo's fixtures (a ProRAW DNG is 124–129 MB),
    so the open COUNT — the quantity #826 is about — is observed against a fake
    handle. The real exception classes are kept so the production ``except``
    clauses still match.
    """
    import rawpy as _rawpy

    class _Sizes:
        def __init__(self):
            self.width = width
            self.height = height

    class _Thumb:
        format = _rawpy.ThumbFormat.JPEG
        data = thumb_bytes

    class _Handle:
        def __init__(self):
            self.sizes = _Sizes()

        def extract_thumb(self):
            return _Thumb()

        def postprocess(self, **_kw):
            raise AssertionError("embedded JPEG present — full decode must not run")

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _FakeRawpy:
        ThumbFormat = _rawpy.ThumbFormat
        LibRawError = _rawpy.LibRawError
        LibRawNoThumbnailError = _rawpy.LibRawNoThumbnailError

        @staticmethod
        def imread(source):
            opens.append(source)
            return _Handle()

    return _FakeRawpy


class TestRawSingleLibRawOpen:
    """#826 — one LibRaw open per RAW file, and hash the preview at working size.

    Measured on this library's three largest DNGs (iPhone ProRAW, 124–129 MB,
    8064×6048): the embedded preview is a full-sensor 48.8 MP JPEG, and the two
    LibRaw opens alone cost 0.34–0.38 s per file with the bytes already in RAM.
    """

    def test_raw_single_libraw_open_and_downscaled_preview(self, tmp_path, monkeypatch):
        import imagehash

        from scanner import hasher

        buf = io.BytesIO()
        # Stands in for the real 8064×6048 embedded preview; 2048×1536 is the
        # smallest size that still distinguishes "hashed the preview as-is"
        # (2048 px) from "reduced to the JPEG path's working size" (512 px).
        Image.fromarray(_photo_array(2048, 1536), "RGB").save(buf, "JPEG", quality=85)

        opens: list = []
        monkeypatch.setattr(hasher, "_RAWPY_AVAILABLE", True)
        monkeypatch.setattr(hasher, "rawpy", _fake_rawpy(buf.getvalue(), 8064, 6048, opens))

        hashed_sizes: list = []
        real_phash = imagehash.phash

        def _spy_phash(image, *a, **kw):
            hashed_sizes.append(image.size)
            return real_phash(image, *a, **kw)

        monkeypatch.setattr(hasher.imagehash, "phash", _spy_phash)

        f = tmp_path / "shot.dng"
        f.write_bytes(b"\x00" * 64)
        sha, ph, dh, _mc, _date, px_w, px_h, _sig = hasher.compute_hashes(f, "raw")

        assert len(opens) == 1, (
            f"LibRaw opened {len(opens)}× for one RAW file — #826 wants exactly 1 "
            f"(sources: {opens})"
        )
        assert (px_w, px_h) == (8064, 6048), (
            "sensor dimensions must still come from rawpy metadata, off the same handle"
        )
        assert ph and dh and len(sha) == 64
        assert hashed_sizes, "imagehash.phash was never called for the RAW path"
        assert max(hashed_sizes[0]) <= 1024, (
            f"RAW preview hashed at {hashed_sizes[0]} — #826 wants it reduced to "
            "the JPEG path's ~1024 px working resolution before hashing"
        )


# Pinned outputs of the hash recipe for an Orientation-1 JPEG, captured on the
# BASE recipe (HASH_RECIPE_VERSION "3", commit b14b59f) before #822/#826 landed.
# A file with no orientation to apply must hash EXACTLY as it did before, or the
# recipe change was not the surgical one it claims to be. If a future change to
# the recipe (or a Pillow decoder change) moves these, that must be a loud,
# deliberate re-pin accompanied by a HASH_RECIPE_VERSION bump — not a silent drift.
_PARITY_PHASH = "881a4f1b0b19f56f"
_PARITY_DHASH = "fbe7befbefbef7ff"
_PARITY_MEAN = "127,127,117"


class TestHashRecipeParity:
    def test_orientation1_jpeg_matches_pinned_base_recipe(self, tmp_path):
        from scanner.hasher import compute_hashes

        f = tmp_path / "parity.jpg"
        Image.fromarray(_photo_array(640, 480), "RGB").save(f, "JPEG", quality=90)

        with Image.open(f) as im:
            assert im.getexif().get(0x0112) is None, "parity fixture must carry no Orientation"

        _sha, ph, dh, mean_color, *_ = compute_hashes(f, "jpeg")
        assert (ph, dh, mean_color) == (_PARITY_PHASH, _PARITY_DHASH, _PARITY_MEAN), (
            f"hash recipe drifted for an Orientation-1 JPEG: got "
            f"phash={ph} dhash={dh} mean_color={mean_color}, pinned "
            f"phash={_PARITY_PHASH} dhash={_PARITY_DHASH} mean_color={_PARITY_MEAN}"
        )
