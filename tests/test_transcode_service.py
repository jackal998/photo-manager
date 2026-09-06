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
import json
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

def _make_service(tmp_path: Path, *, ffprobe: str | None = "/fake/ffprobe") -> TranscodeService:
    """Return a TranscodeService whose cache lives under tmp_path.

    ffmpeg (and, unless ``ffprobe=None``, ffprobe) is patched to a
    sentinel path so init doesn't require a real install.
    """
    cache_dir = str(tmp_path / "transcodes")

    class _FakeSettings:
        def get(self, key: str, default=None):
            if key == "video_transcode_cache_dir":
                return cache_dir
            return default

    def _which(name: str) -> str | None:
        return ffprobe if name == "ffprobe" else "/fake/ffmpeg"

    with patch("shutil.which", side_effect=_which):
        svc = TranscodeService(settings=_FakeSettings())
    return svc


def _is_probe(cmd: list) -> bool:
    """True when ``cmd`` is the ffprobe codec probe rather than an ffmpeg run."""
    return "-print_format" in cmd


class _ProbeResult:
    """Minimal stand-in for a completed ffprobe subprocess."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _probe_unknown() -> _ProbeResult:
    """An ffprobe result that yields no verdict → caller re-encodes.

    Used by the tests below that are about the CACHE/lock/atomic-rename
    contract, not about the codec probe: they must keep exercising the
    libx264 encode path they were written for.
    """
    return _ProbeResult(returncode=1, stderr=b"probe unavailable")


def _probe_streams(*streams: dict) -> _ProbeResult:
    """An ffprobe result carrying ``streams`` in ffprobe's JSON shape."""
    payload = json.dumps({"streams": list(streams)}).encode("utf-8")
    return _ProbeResult(returncode=0, stdout=payload)


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
            if _is_probe(cmd):
                return _probe_unknown()
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
            if _is_probe(cmd):
                return _probe_unknown()
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
            if _is_probe(cmd):
                return _probe_unknown()
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
            if _is_probe(cmd):
                return _probe_unknown()

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
            if _is_probe(cmd):
                return _probe_unknown()
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


# ---------------------------------------------------------------------------
# #853 codec passthrough: which ffmpeg recipe runs, and when
#
# The bug these guard: an H.264 .mov reaching this service (the #787
# pre-check has to guess from the EXTENSION, so plain H.264 iPhone clips
# do reach it) was re-encoded with libx264 — a multi-second stall and a
# quality loss on a file whose video stream could simply be copied.  The
# mirror-image bug is worse: copying a stream the browser cannot decode
# (HEVC) would hand back an unplayable MP4 where the encode worked.  So
# every test here asserts WHICH recipe ran, not that "a branch was hit".
# ---------------------------------------------------------------------------

class _FfmpegRecorder:
    """subprocess.run stand-in: answers the probe, records ffmpeg runs."""

    def __init__(self, probe: _ProbeResult, *, fail_copy: bool = False):
        self._probe = probe
        self._fail_copy = fail_copy
        self.ffmpeg_cmds: list[list] = []
        self.probe_cmds: list[list] = []

    def __call__(self, cmd, **kwargs):
        if _is_probe(cmd):
            self.probe_cmds.append(cmd)
            if isinstance(self._probe, BaseException):
                raise self._probe
            return self._probe

        self.ffmpeg_cmds.append(cmd)
        if self._fail_copy and "copy" in cmd:
            class _Failed:
                returncode = 1
                stderr = b"muxer refused the stream"
            return _Failed()

        out_index = cmd.index("-y") + 1
        Path(cmd[out_index]).write_bytes(b"fake-mp4")

        class _Ok:
            returncode = 0
            stderr = b""
        return _Ok()


def _recipe_of(cmd: list) -> str:
    """Name the recipe an ffmpeg argv encodes: 'copy' or 'encode'."""
    if "libx264" in cmd:
        return "encode"
    if "copy" in cmd:
        return "copy"
    return f"unknown: {cmd!r}"


def _run_with(tmp_path: Path, probe, *, name: str = "clip.mov", **kwargs):
    """Transcode a source once with a given probe answer; return the recorder."""
    svc = _make_service(tmp_path)
    source = tmp_path / name
    source.write_bytes(b"\x00" * 10)
    recorder = _FfmpegRecorder(probe, **kwargs)
    with patch("subprocess.run", side_effect=recorder):
        svc.get_transcoded_path(source)
    return recorder


class TestStreamCopySelection:
    def test_h264_aac_is_copied_not_reencoded(self, tmp_path: Path) -> None:
        """The ticket's case: an H.264 .mov must not be re-encoded."""
        recorder = _run_with(
            tmp_path,
            _probe_streams(
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ),
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["copy"]
        assert "libx264" not in recorder.ffmpeg_cmds[0]
        # The copy must still produce a streamable MP4, not a bare copy.
        assert "+faststart" in recorder.ffmpeg_cmds[0]
        cmd = recorder.ffmpeg_cmds[0]
        assert cmd[cmd.index("-f") + 1] == "mp4"

    def test_h264_without_audio_is_copied(self, tmp_path: Path) -> None:
        """A silent clip has no audio stream to disqualify it."""
        recorder = _run_with(
            tmp_path,
            _probe_streams({"codec_type": "video", "codec_name": "h264"}),
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["copy"]

    def test_hevc_is_still_reencoded(self, tmp_path: Path) -> None:
        """The files the fallback exists for must keep their encode."""
        recorder = _run_with(
            tmp_path,
            _probe_streams(
                {"codec_type": "video", "codec_name": "hevc"},
                {"codec_type": "audio", "codec_name": "aac"},
            ),
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_h264_with_undecodable_audio_is_reencoded(self, tmp_path: Path) -> None:
        """H.264 video + AC-3 audio: copying it would give the browser an
        audio track it cannot decode, so the encode (which re-codes audio
        to AAC) must still run."""
        recorder = _run_with(
            tmp_path,
            _probe_streams(
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "ac3"},
            ),
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_second_audio_stream_can_veto_the_copy(self, tmp_path: Path) -> None:
        """A dual-audio file is only copyable if EVERY audio stream is."""
        recorder = _run_with(
            tmp_path,
            _probe_streams(
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "audio", "codec_name": "pcm_s16le"},
            ),
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_data_stream_does_not_disqualify(self, tmp_path: Path) -> None:
        """iPhone .mov files carry a timecode/metadata track; it is neither
        video nor audio and must not push the file back onto the encode."""
        recorder = _run_with(
            tmp_path,
            _probe_streams(
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "data", "codec_name": "bin_data"},
            ),
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["copy"]
        # …and the copy must not try to mux that track into MP4.
        assert "0:v:0" in recorder.ffmpeg_cmds[0]
        assert "0:a:0?" in recorder.ffmpeg_cmds[0]

    def test_cache_hit_runs_no_probe(self, tmp_path: Path) -> None:
        """One ffprobe per file, not per request: a warm cache probes zero
        times (this is what keeps the passthrough off the request path)."""
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mov"
        source.write_bytes(b"\x00" * 10)
        cache_key = _compute_cache_key(source)
        cached = svc._base_dir / f"{cache_key}.mp4"
        cached.write_bytes(b"already-transcoded")

        recorder = _FfmpegRecorder(
            _probe_streams({"codec_type": "video", "codec_name": "h264"})
        )
        with patch("subprocess.run", side_effect=recorder):
            result = svc.get_transcoded_path(source)

        assert result == cached
        assert recorder.probe_cmds == [], "ffprobe ran on a cache hit"
        assert recorder.ffmpeg_cmds == []


class TestProbeFailureModes:
    """Every way the probe can fail must degrade to the pre-#853 encode —
    never a raised exception (the route would turn that into a 500 and the
    user would see "Video cannot be played" for a file that used to play).
    """

    def test_ffprobe_not_installed_falls_back_to_encode(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path, ffprobe=None)
        source = tmp_path / "clip.mov"
        source.write_bytes(b"\x00" * 10)
        recorder = _FfmpegRecorder(_probe_unknown())
        with patch("subprocess.run", side_effect=recorder):
            svc.get_transcoded_path(source)
        assert recorder.probe_cmds == [], "probed despite ffprobe being absent"
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_nonzero_exit_falls_back_to_encode(self, tmp_path: Path) -> None:
        recorder = _run_with(
            tmp_path, _ProbeResult(returncode=1, stderr=b"Invalid data found")
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_timeout_falls_back_to_encode(self, tmp_path: Path) -> None:
        """A wedged probe (a NAS file that stops responding) must not hold
        the request open or kill it — the encode still runs."""
        recorder = _run_with(
            tmp_path, subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=30)
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_unparseable_output_falls_back_to_encode(self, tmp_path: Path) -> None:
        recorder = _run_with(
            tmp_path, _ProbeResult(returncode=0, stdout=b"not json at all")
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_missing_streams_key_falls_back_to_encode(self, tmp_path: Path) -> None:
        recorder = _run_with(tmp_path, _ProbeResult(returncode=0, stdout=b"{}"))
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_audio_only_file_falls_back_to_encode(self, tmp_path: Path) -> None:
        """No video stream → no verdict → the pre-#853 path."""
        recorder = _run_with(
            tmp_path, _probe_streams({"codec_type": "audio", "codec_name": "aac"})
        )
        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["encode"]

    def test_failed_copy_falls_back_to_encode_and_succeeds(
        self, tmp_path: Path
    ) -> None:
        """A stream copy ffmpeg refuses (an exotic track the MP4 muxer
        rejects) must end in the encode, not a 500 — the passthrough can
        never leave a file less playable than it was before #853.
        """
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mov"
        source.write_bytes(b"\x00" * 10)
        recorder = _FfmpegRecorder(
            _probe_streams({"codec_type": "video", "codec_name": "h264"}),
            fail_copy=True,
        )
        with patch("subprocess.run", side_effect=recorder):
            result = svc.get_transcoded_path(source)

        assert [_recipe_of(c) for c in recorder.ffmpeg_cmds] == ["copy", "encode"]
        assert result.exists(), "no cached output after the copy→encode fallback"
        assert not (svc._base_dir / f"{_compute_cache_key(source)}_tmp.mp4").exists()


class TestProbeInvocation:
    def test_probe_is_argv_never_a_shell_string(self, tmp_path: Path) -> None:
        """A filename holding shell metacharacters must reach ffprobe as one
        argument, not as something a shell could split or expand.
        """
        name = "clip & rm -rf $HOME `id`.mov"
        recorder = _run_with(tmp_path, _probe_unknown(), name=name)

        assert len(recorder.probe_cmds) == 1
        cmd = recorder.probe_cmds[0]
        assert isinstance(cmd, list)
        assert cmd[-1] == str(tmp_path / name)

    def test_probe_is_bounded_and_not_shelled(self, tmp_path: Path) -> None:
        """shell=False and a finite timeout are what keep a hostile filename
        harmless and a wedged NAS read from pinning the per-key lock."""
        svc = _make_service(tmp_path)
        source = tmp_path / "clip.mov"
        source.write_bytes(b"\x00" * 10)
        seen: list[dict] = []

        def _capture(cmd, **kwargs):
            if _is_probe(cmd):
                seen.append(kwargs)
                return _probe_unknown()
            out_index = cmd.index("-y") + 1
            Path(cmd[out_index]).write_bytes(b"fake-mp4")

            class _Ok:
                returncode = 0
                stderr = b""
            return _Ok()

        with patch("subprocess.run", side_effect=_capture):
            svc.get_transcoded_path(source)

        assert len(seen) == 1
        assert seen[0]["shell"] is False
        assert 0 < seen[0]["timeout"] <= 60
