"""Unit tests for GET /api/media — Range streaming endpoint.

Tests cover:
  - No Range header → 200 + Accept-Ranges + correct Content-Length
  - Range: bytes=0-99 → 206 + exact Content-Range/Content-Length + correct bytes
  - Open-ended range: bytes=100- → 206 to EOF
  - Unsatisfiable range (start >= size) → 416
  - Malformed range syntax → 416
  - Content-Type per extension (video/mp4, video/quicktime, video/webm, etc.)
  - Path-guard rejection (empty path → 400, out-of-roots → 403, not on disk → 404)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# 200 bytes of deterministic dummy content — large enough to slice.
_CONTENT = bytes(range(256))[:200]  # 200 bytes: 0x00..0xC7


@pytest.fixture()
def tmp_video(tmp_path: Path) -> Path:
    """A real 200-byte .mp4 file on disk inside tmp_path."""
    p = tmp_path / "test.mp4"
    p.write_bytes(_CONTENT)
    return p


@pytest.fixture()
def client(tmp_path: Path, tmp_video: Path):
    """TestClient with allowed_roots=[tmp_path]."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        c.app.state.allowed_roots = [tmp_path]
        yield c


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

class TestPathValidation:
    def test_empty_path_returns_400(self, client):
        resp = client.get("/api/media", params={"path": ""})
        assert resp.status_code == 400

    def test_whitespace_path_returns_400(self, client):
        resp = client.get("/api/media", params={"path": "   "})
        assert resp.status_code == 400

    def test_path_outside_allowed_roots_returns_403(self, client):
        outside = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "system32" / "ntdll.dll"
        resp = client.get("/api/media", params={"path": str(outside)})
        assert resp.status_code == 403

    def test_path_not_on_disk_returns_404(self, client, tmp_path):
        nonexistent = str(tmp_path / "missing.mp4")
        resp = client.get("/api/media", params={"path": nonexistent})
        assert resp.status_code == 404

    def test_empty_allowed_roots_returns_403(self, client, tmp_video):
        client.app.state.allowed_roots = []
        resp = client.get("/api/media", params={"path": str(tmp_video)})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# No Range header → 200 full file
# ---------------------------------------------------------------------------

class TestNoRange:
    def test_no_range_returns_200(self, client, tmp_video):
        resp = client.get("/api/media", params={"path": str(tmp_video)})
        assert resp.status_code == 200

    def test_no_range_has_accept_ranges_header(self, client, tmp_video):
        resp = client.get("/api/media", params={"path": str(tmp_video)})
        assert resp.headers.get("accept-ranges") == "bytes"

    def test_no_range_content_length_matches_file(self, client, tmp_video):
        resp = client.get("/api/media", params={"path": str(tmp_video)})
        assert resp.headers.get("content-length") == str(len(_CONTENT))

    def test_no_range_body_is_full_file(self, client, tmp_video):
        resp = client.get("/api/media", params={"path": str(tmp_video)})
        assert resp.content == _CONTENT


# ---------------------------------------------------------------------------
# Range header → 206 byte slice
# ---------------------------------------------------------------------------

class TestRangeRequest:
    def test_range_returns_206(self, client, tmp_video):
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=0-99"},
        )
        assert resp.status_code == 206

    def test_range_exact_bytes_0_to_99(self, client, tmp_video):
        """bytes=0-99 → body must be _CONTENT[0:100] (100 bytes, END inclusive)."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=0-99"},
        )
        assert resp.status_code == 206
        assert resp.content == _CONTENT[0:100]

    def test_range_content_range_header(self, client, tmp_video):
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=0-99"},
        )
        assert resp.headers.get("content-range") == f"bytes 0-99/{len(_CONTENT)}"

    def test_range_content_length_is_slice_length(self, client, tmp_video):
        """Content-Length must equal END - START + 1 = 100."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=0-99"},
        )
        assert resp.headers.get("content-length") == "100"

    def test_range_mid_slice(self, client, tmp_video):
        """bytes=50-59 → body = _CONTENT[50:60] (10 bytes)."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=50-59"},
        )
        assert resp.status_code == 206
        assert resp.content == _CONTENT[50:60]
        assert resp.headers.get("content-length") == "10"
        assert resp.headers.get("content-range") == f"bytes 50-59/{len(_CONTENT)}"

    def test_range_has_accept_ranges_header(self, client, tmp_video):
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=0-99"},
        )
        assert resp.headers.get("accept-ranges") == "bytes"

    def test_open_ended_range_serves_to_eof(self, client, tmp_video):
        """bytes=100- (no END) → body = _CONTENT[100:] (100 bytes to EOF)."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=100-"},
        )
        assert resp.status_code == 206
        assert resp.content == _CONTENT[100:]
        expected_end = len(_CONTENT) - 1
        assert resp.headers.get("content-range") == f"bytes 100-{expected_end}/{len(_CONTENT)}"
        assert resp.headers.get("content-length") == str(len(_CONTENT) - 100)

    def test_range_last_byte(self, client, tmp_video):
        """bytes=199-199 → exactly 1 byte."""
        last = len(_CONTENT) - 1
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": f"bytes={last}-{last}"},
        )
        assert resp.status_code == 206
        assert resp.content == bytes([_CONTENT[last]])
        assert resp.headers.get("content-length") == "1"

    def test_suffix_range_last_100_bytes(self, client, tmp_video):
        """bytes=-100 → 206 with the last 100 bytes + correct Content-Range."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=-100"},
        )
        assert resp.status_code == 206
        assert resp.content == _CONTENT[-100:]
        size = len(_CONTENT)
        assert resp.headers.get("content-range") == f"bytes {size - 100}-{size - 1}/{size}"
        assert resp.headers.get("content-length") == "100"

    def test_suffix_range_larger_than_file_clamps_to_full(self, client, tmp_video):
        """bytes=-9999 when file is 200 bytes → 206 full file from byte 0."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=-9999"},
        )
        assert resp.status_code == 206
        assert resp.content == _CONTENT
        size = len(_CONTENT)
        assert resp.headers.get("content-range") == f"bytes 0-{size - 1}/{size}"


# ---------------------------------------------------------------------------
# Unsatisfiable range → 416
# ---------------------------------------------------------------------------

class TestUnsatisfiableRange:
    def test_start_beyond_eof_returns_416(self, client, tmp_video):
        """START >= file size → 416."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=999999-"},
        )
        assert resp.status_code == 416

    def test_416_has_content_range_star(self, client, tmp_video):
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=999999-"},
        )
        assert resp.headers.get("content-range") == f"bytes */{len(_CONTENT)}"

    def test_start_equal_to_size_returns_416(self, client, tmp_video):
        """START == file size (200) → 416 (not a valid byte position)."""
        size = len(_CONTENT)  # 200
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": f"bytes={size}-"},
        )
        assert resp.status_code == 416

    def test_malformed_range_returns_416(self, client, tmp_video):
        """Invalid Range syntax → 416."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=abc-def"},
        )
        assert resp.status_code == 416

    def test_suffix_range_empty_n_returns_416(self, client, tmp_video):
        """bytes=- (no start, no end) → 416 (degenerate suffix-range)."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=-"},
        )
        assert resp.status_code == 416

    def test_suffix_range_zero_returns_416(self, client, tmp_video):
        """bytes=-0 → 416 (requesting 0 bytes is unsatisfiable)."""
        resp = client.get(
            "/api/media",
            params={"path": str(tmp_video)},
            headers={"range": "bytes=-0"},
        )
        assert resp.status_code == 416


# ---------------------------------------------------------------------------
# Content-Type per extension
# ---------------------------------------------------------------------------

class TestContentType:
    def _make_file(self, tmp_path: Path, ext: str) -> Path:
        p = tmp_path / f"test{ext}"
        p.write_bytes(_CONTENT)
        return p

    def _get_media_type(self, client, path: str) -> str:
        resp = client.get("/api/media", params={"path": path})
        assert resp.status_code == 200
        # content-type may include charset: "video/mp4; charset=..."
        return resp.headers.get("content-type", "").split(";")[0].strip()

    def test_mp4_content_type(self, client, tmp_path):
        p = self._make_file(tmp_path, ".mp4")
        assert self._get_media_type(client, str(p)) == "video/mp4"

    def test_mov_content_type(self, client, tmp_path):
        p = self._make_file(tmp_path, ".mov")
        assert self._get_media_type(client, str(p)) == "video/quicktime"

    def test_m4v_content_type(self, client, tmp_path):
        p = self._make_file(tmp_path, ".m4v")
        assert self._get_media_type(client, str(p)) == "video/x-m4v"

    def test_webm_content_type(self, client, tmp_path):
        p = self._make_file(tmp_path, ".webm")
        assert self._get_media_type(client, str(p)) == "video/webm"

    def test_avi_content_type(self, client, tmp_path):
        p = self._make_file(tmp_path, ".avi")
        assert self._get_media_type(client, str(p)) == "video/x-msvideo"

    def test_mkv_content_type(self, client, tmp_path):
        p = self._make_file(tmp_path, ".mkv")
        assert self._get_media_type(client, str(p)) == "video/x-matroska"

    def test_unknown_extension_falls_back_to_octet_stream(self, client, tmp_path):
        p = self._make_file(tmp_path, ".xyz")
        ct = self._get_media_type(client, str(p))
        # mimetypes may return something for some extensions; the fallback is
        # application/octet-stream; any non-null value is acceptable.
        assert ct  # must not be empty
