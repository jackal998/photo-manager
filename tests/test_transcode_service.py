"""Unit tests for infrastructure.transcode_service.

CI-safe: these tests never invoke ffmpeg.  The cache-key determinism,
mtime-sensitivity, cache-hit short-circuit, and per-key lock / atomic-rename
naming are all verified without a real encode.

Coverage note: subprocess-driven paths (_transcode + TranscodeUnavailable)
are covered by tests/integration/test_transcode_integration.py (ffmpeg-gated)
and by the transcode route tests in test_web_media_route.py (service mocked).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from infrastructure.transcode_service import (
    TranscodeService,
    TranscodeError,
    TranscodeUnavailable,
    _compute_cache_key,
    _KEY_LOCKS,
    _KEY_LOCKS_LOCK,
    TRANSCODE_RECIPE_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(tmp_path: Path) -> TranscodeService:
    """Return a TranscodeService whose cache lives under tmp_path.

    ffmpeg is patched to a sentinel path so init doesn't require a real install.
    """
    cache_dir = str(tmp_path / "transcodes")

    class _FakeSettings:
        def get(self, key: str, default=None):
            if key == "video_transcode_cache_dir":
                return cache_dir
            return default

    with patch("shutil.which", return_value="/fake/ffmpeg"):
        svc = TranscodeService(settings=_FakeSettings())
    return svc


# ---------------------------------------------------------------------------
# Cache-key determinism
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_same_path_same_mtime_same_key(self, tmp_path: Path) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 100)
        k1 = _compute_cache_key(f)
        k2 = _compute_cache_key(f)
        assert k1 == k2

    def test_different_paths_different_keys(self, tmp_path: Path) -> None:
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"\x00" * 100)
        b.write_bytes(b"\x00" * 100)
        # Ensure identical content — only path differs.
        assert _compute_cache_key(a) != _compute_cache_key(b)

    def test_mtime_change_changes_key(self, tmp_path: Path) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 100)
        k1 = _compute_cache_key(f)
        # Touch the file slightly into the future (at least 1 ns).
        time.sleep(0.01)
        f.write_bytes(b"\x01" * 100)
        k2 = _compute_cache_key(f)
        assert k1 != k2

    def test_key_is_40_char_hex(self, tmp_path: Path) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 10)
        k = _compute_cache_key(f)
        assert len(k) == 40
        assert all(c in "0123456789abcdef" for c in k)


# ---------------------------------------------------------------------------
# Versioned cache directory
# ---------------------------------------------------------------------------

class TestCacheDirectory:
    def test_versioned_subdir_created_on_init(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        expected = tmp_path / "transcodes" / f"v{TRANSCODE_RECIPE_VERSION}"
        assert expected.is_dir()


# ---------------------------------------------------------------------------
# Cache-hit: no ffmpeg invoked
# ---------------------------------------------------------------------------

class TestCacheHit:
    def test_cache_hit_returns_existing_file_without_ffmpeg(self, tmp_path: Path) -> None:
        """Pre-place the cached .mp4 and assert get_transcoded_path returns it
        without ever calling subprocess.run (the real cache-hit logic).
        """
        svc = _make_service(tmp_path)
        source = tmp_path / "source.mp4"
        source.write_bytes(b"\x00" * 50)

        # Pre-place the cache file at the key path so the cache is warm.
        cache_key = _compute_cache_key(source)
        versioned_dir = tmp_path / "transcodes" / f"v{TRANSCODE_RECIPE_VERSION}"
        cached_file = versioned_dir / f"{cache_key}.mp4"
        cached_file.write_bytes(b"fake-h264-bytes")

        subprocess_calls: list = []

        def _record_run(*args, **kwargs):
            subprocess_calls.append(args)
            # Should never be reached on a cache hit.
            raise AssertionError("subprocess.run called on a cache hit")

        with patch("subprocess.run", side_effect=_record_run):
            result = svc.get_transcoded_path(source)

        assert result == cached_file
        assert result.exists()
        assert result.read_bytes() == b"fake-h264-bytes"
        # The real assertion: ffmpeg was never invoked.
        assert subprocess_calls == [], "ffmpeg subprocess called on a cache hit"

    def test_cache_hit_returns_correct_path_object(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        source = tmp_path / "video.mkv"
        source.write_bytes(b"\x00" * 10)

        cache_key = _compute_cache_key(source)
        versioned_dir = tmp_path / "transcodes" / f"v{TRANSCODE_RECIPE_VERSION}"
        cached_file = versioned_dir / f"{cache_key}.mp4"
        cached_file.write_bytes(b"cached")

        result = svc.get_transcoded_path(source)
        assert result == cached_file
        assert result.suffix == ".mp4"


# ---------------------------------------------------------------------------
# TranscodeUnavailable when ffmpeg is absent
# ---------------------------------------------------------------------------

class TestTranscodeUnavailable:
    def test_raises_when_ffmpeg_not_found(self, tmp_path: Path) -> None:
        cache_dir = str(tmp_path / "transcodes")

        class _FakeSettings:
            def get(self, key: str, default=None):
                if key == "video_transcode_cache_dir":
                    return cache_dir
                return default

        # Construct with ffmpeg=None (not installed).
        with patch("shutil.which", return_value=None):
            svc = TranscodeService(settings=_FakeSettings())

        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 10)

        with pytest.raises(TranscodeUnavailable):
            svc.get_transcoded_path(source)


# ---------------------------------------------------------------------------
# Atomic rename: output path ends with .mp4, not .mp4.tmp
# ---------------------------------------------------------------------------

class TestAtomicNaming:
    def test_tmp_path_is_sibling_and_cleaned_up(self, tmp_path: Path) -> None:
        """The staging file must be a sibling .mp4 of the final output,
        and must not exist after the atomic os.replace.
        """
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 10)

        cache_key = _compute_cache_key(source)
        versioned_dir = tmp_path / "transcodes" / f"v{TRANSCODE_RECIPE_VERSION}"
        expected_out = versioned_dir / f"{cache_key}.mp4"
        # Staging file uses stem + "_tmp.mp4" to avoid ffmpeg container confusion.
        expected_staging = versioned_dir / f"{cache_key}_tmp.mp4"

        recorded_cmds: list = []

        def _fake_run(cmd, **kwargs):
            recorded_cmds.append(cmd)
            # Simulate a successful ffmpeg: write the staging output file.
            out_index = cmd.index("-y") + 1
            Path(cmd[out_index]).write_bytes(b"fake-h264")

            class _Result:
                returncode = 0
                stderr = b""
            return _Result()

        with patch("subprocess.run", side_effect=_fake_run):
            result = svc.get_transcoded_path(source)

        assert result == expected_out
        assert expected_out.exists()
        # Staging file must be cleaned up by os.replace (atomic rename).
        assert not expected_staging.exists()


# ---------------------------------------------------------------------------
# Per-key lock: second concurrent request should find the cache hit
# ---------------------------------------------------------------------------

class TestPerKeyLock:
    def test_second_thread_finds_cache_hit(self, tmp_path: Path) -> None:
        """Two threads requesting the same source: the first transcodes, the
        second should find the cache hit without spawning a second ffmpeg.
        """
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 10)

        ffmpeg_call_count = [0]
        barrier = threading.Barrier(2)
        first_entered = threading.Event()

        def _fake_run(cmd, **kwargs):
            ffmpeg_call_count[0] += 1
            # Signal that thread 1 is inside ffmpeg, then block briefly.
            first_entered.set()
            time.sleep(0.05)
            out_index = cmd.index("-y") + 1
            Path(cmd[out_index]).write_bytes(b"fake-h264")

            class _Result:
                returncode = 0
                stderr = b""
            return _Result()

        results: list = []
        errors: list = []

        def _thread_fn():
            try:
                r = svc.get_transcoded_path(source)
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        with patch("subprocess.run", side_effect=_fake_run):
            t1 = threading.Thread(target=_thread_fn)
            t2 = threading.Thread(target=_thread_fn)
            t1.start()
            first_entered.wait(timeout=5)
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        assert not errors, f"thread errors: {errors}"
        assert len(results) == 2
        # Both threads must return the same path.
        assert results[0] == results[1]
        # ffmpeg must have been called exactly once.
        assert ffmpeg_call_count[0] == 1, (
            f"Expected 1 ffmpeg call; got {ffmpeg_call_count[0]} "
            f"(second thread should have hit the cache)"
        )


# ---------------------------------------------------------------------------
# Failure modes: timeout, missing output, non-zero exit — each must surface
# as TranscodeError (route maps it to 500) and never leave a staging file.
# ---------------------------------------------------------------------------

class TestTranscodeFailures:
    def _staging_path(self, svc: TranscodeService, source: Path) -> Path:
        key = _compute_cache_key(source)
        return svc._base_dir / f"{key}_tmp.mp4"

    def test_timeout_raises_transcode_error_and_cleans_tmp(self, tmp_path: Path) -> None:
        """A hung encode (TimeoutExpired) → TranscodeError, no orphaned _tmp.mp4."""
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 10)
        staging = self._staging_path(svc, source)

        def _fake_run(cmd, **kwargs):
            # Simulate ffmpeg having written a partial staging file, then hanging.
            out_index = cmd.index("-y") + 1
            Path(cmd[out_index]).write_bytes(b"partial")
            raise subprocess.TimeoutExpired(cmd, timeout=300)

        with patch("subprocess.run", side_effect=_fake_run):
            with pytest.raises(TranscodeError):
                svc.get_transcoded_path(source)

        assert not staging.exists(), "partial staging file left behind after timeout"

    def test_rc_zero_but_no_output_raises_transcode_error(self, tmp_path: Path) -> None:
        """ffmpeg exits 0 but writes nothing → TranscodeError (not raw FileNotFoundError)."""
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 10)

        def _fake_run(cmd, **kwargs):
            class _Result:
                returncode = 0
                stderr = b""
            return _Result()  # note: does NOT write the staging file

        with patch("subprocess.run", side_effect=_fake_run):
            with pytest.raises(TranscodeError, match="produced no output"):
                svc.get_transcoded_path(source)

    def test_nonzero_rc_raises_and_cleans_tmp(self, tmp_path: Path) -> None:
        """ffmpeg exits non-zero → TranscodeError, partial _tmp.mp4 removed."""
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 10)
        staging = self._staging_path(svc, source)

        def _fake_run(cmd, **kwargs):
            out_index = cmd.index("-y") + 1
            Path(cmd[out_index]).write_bytes(b"partial")

            class _Result:
                returncode = 1
                stderr = b"some ffmpeg error"
            return _Result()

        with patch("subprocess.run", side_effect=_fake_run):
            with pytest.raises(TranscodeError):
                svc.get_transcoded_path(source)

        assert not staging.exists(), "partial staging file left behind after non-zero exit"
