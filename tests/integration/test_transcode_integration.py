"""Layer-2 integration test for TranscodeService with real ffmpeg.

Marked @pytest.mark.integration and skipped when ffmpeg is absent.

What this proves
----------------
TranscodeService.get_transcoded_path() produces a real, valid H.264 MP4
when given an HEVC input clip.  ffprobe is used to verify:
  - video codec_name == "h264"
  - moov atom appears before mdat (faststart flag honoured)

Run locally once ffmpeg is on PATH:

    .venv/Scripts/python.exe -m pytest \\
        tests/integration/test_transcode_integration.py -m integration -s --no-cov

CI integration: the bench-sanity job in web-eval-gates.yml installs ffmpeg
via apt-get and runs this test there (advisory, continue-on-error: true).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Skip the entire module if ffmpeg or ffprobe are absent.
_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.integration


def _skip_if_no_ffmpeg() -> None:
    if _FFMPEG is None:
        pytest.skip("ffmpeg not installed — skipping integration transcode test")
    if _FFPROBE is None:
        pytest.skip("ffprobe not installed — skipping integration transcode test")


@pytest.fixture(scope="module")
def hevc_clip_path() -> Path:
    """Generate a tiny (64×64, 1-second, black) HEVC clip and return its path.

    The tmpdir persists for the module so the clip is generated once per run.
    """
    _skip_if_no_ffmpeg()
    tmp = tempfile.mkdtemp(prefix="qa_transcode_int_")
    out = Path(tmp) / "hevc_input.mp4"
    cmd = [
        _FFMPEG,
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:size=64x64:rate=1",
        "-t", "1",
        "-c:v", "libx265",
        "-tag:v", "hvc1",
        str(out),
    ]
    result = subprocess.run(
        cmd, check=False, capture_output=True, timeout=60
    )
    if result.returncode != 0:
        pytest.skip(
            f"Could not generate HEVC test clip (libx265 not available?): "
            f"{result.stderr.decode('utf-8', errors='replace')[:200]}"
        )
    yield out
    shutil.rmtree(tmp, ignore_errors=True)


def _ffprobe_video_codec(path: Path) -> str:
    """Return the codec_name for the first video stream in ``path``."""
    cmd = [
        _FFPROBE,
        "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        return ""
    return streams[0].get("codec_name", "")


def _is_faststart(path: Path) -> bool:
    """Return True if the moov atom appears before mdat (faststart).

    Uses ffprobe's trace output to check atom ordering.  Robust enough for
    the test purpose without parsing raw bytes.
    """
    cmd = [
        _FFPROBE,
        "-v", "trace",
        str(path),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, timeout=30)
    stderr = result.stderr.decode("utf-8", errors="replace")
    # Find the positions of 'moov' and 'mdat' in the trace output.
    moov_pos = stderr.find("'moov'")
    mdat_pos = stderr.find("'mdat'")
    if moov_pos < 0 or mdat_pos < 0:
        # Fall back to checking for 'moov' before 'mdat' in any form.
        moov_pos = stderr.find("moov")
        mdat_pos = stderr.find("mdat")
    if moov_pos < 0 or mdat_pos < 0:
        return False
    return moov_pos < mdat_pos


class TestTranscodeIntegration:
    def test_transcode_produces_h264(self, hevc_clip_path: Path, tmp_path: Path) -> None:
        """get_transcoded_path on an HEVC source produces H.264 output."""
        from infrastructure.transcode_service import TranscodeService

        cache_dir = str(tmp_path / "transcodes")

        class _Settings:
            def get(self, key, default=None):
                if key == "video_transcode_cache_dir":
                    return cache_dir
                return default

        svc = TranscodeService(settings=_Settings())
        result = svc.get_transcoded_path(hevc_clip_path)

        assert result.exists(), f"Transcoded file not found: {result}"
        assert result.suffix == ".mp4"

        codec = _ffprobe_video_codec(result)
        assert codec == "h264", (
            f"Expected H.264 output but ffprobe reports codec_name={codec!r}. "
            f"Source: {hevc_clip_path}, output: {result}"
        )

    def test_transcode_output_is_faststart(
        self, hevc_clip_path: Path, tmp_path: Path
    ) -> None:
        """The -movflags +faststart flag puts moov before mdat."""
        from infrastructure.transcode_service import TranscodeService

        cache_dir = str(tmp_path / "transcodes_fs")

        class _Settings:
            def get(self, key, default=None):
                if key == "video_transcode_cache_dir":
                    return cache_dir
                return default

        svc = TranscodeService(settings=_Settings())
        result = svc.get_transcoded_path(hevc_clip_path)

        assert _is_faststart(result), (
            f"moov atom is NOT before mdat in {result} — "
            f"-movflags +faststart may not have applied"
        )

    def test_transcode_cache_hit_identical_path(
        self, hevc_clip_path: Path, tmp_path: Path
    ) -> None:
        """A second call returns the same path (cache hit, no re-transcode)."""
        from infrastructure.transcode_service import TranscodeService

        cache_dir = str(tmp_path / "transcodes_hit")

        class _Settings:
            def get(self, key, default=None):
                if key == "video_transcode_cache_dir":
                    return cache_dir
                return default

        svc = TranscodeService(settings=_Settings())
        r1 = svc.get_transcoded_path(hevc_clip_path)
        r2 = svc.get_transcoded_path(hevc_clip_path)

        assert r1 == r2
        assert r1.exists()
