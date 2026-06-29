"""GET /api/media — serve raw video (and generic media) bytes with HTTP Range support.

Mirrors the security pattern of /api/image: the same path guard is reused.
V1 scope: pass-through byte serving only — no transcoding.
V2 scope: optional transcode=h264 query param triggers lazy H.264 transcode
          (cached) so HEVC and other browser-incompatible codecs degrade
          gracefully.

RFC 7233 byte-range semantics:
  - No Range header → 200, full file, Accept-Ranges: bytes
  - Range: bytes=START-END → 206 (END is inclusive; Content-Length = END-START+1)
  - Range: bytes=START-  → 206, START to EOF
  - Unsatisfiable / malformed range → 416, Content-Range: bytes */TOTAL
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Generator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.web.routes._path_guard import validate_under_roots

router = APIRouter()

# Stream in 1 MiB chunks so large files never fully load into RAM.
_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB

# Explicit map for types Python's mimetypes gets wrong or misses.
_CONTENT_TYPE: dict[str, str] = {
    ".mp4":  "video/mp4",
    ".mov":  "video/quicktime",
    ".m4v":  "video/x-m4v",
    ".webm": "video/webm",
    ".avi":  "video/x-msvideo",
    ".mkv":  "video/x-matroska",
}

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _CONTENT_TYPE:
        return _CONTENT_TYPE[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _stream_slice(
    path: Path, start: int, end: int
) -> Generator[bytes, None, None]:
    """Yield file bytes from start to end (inclusive) in _CHUNK_SIZE chunks."""
    remaining = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        while remaining > 0:
            chunk = fh.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _serve_file(
    file_path: Path,
    media_type: str,
    range_header: str,
) -> StreamingResponse:
    """Build a 200 or 206 StreamingResponse for ``file_path``.

    Extracted so both the direct and transcode serving paths share
    identical Range / 206 / 416 logic without duplication.
    """
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not stat file: {exc}"
        ) from exc

    if not range_header:
        return StreamingResponse(
            _stream_slice(file_path, 0, max(file_size - 1, 0)),
            status_code=200,
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return StreamingResponse(
            iter([]),
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_raw = m.group(1)
    end_raw = m.group(2)

    if not start_raw:
        if not end_raw or int(end_raw) == 0:
            return StreamingResponse(
                iter([]),
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        suffix_len = int(end_raw)
        start = max(file_size - suffix_len, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1

    end = min(end, file_size - 1)
    if start >= file_size or start > end:
        return StreamingResponse(
            iter([]),
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    slice_len = end - start + 1
    return StreamingResponse(
        _stream_slice(file_path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(slice_len),
        },
    )


@router.get("/api/media")
async def get_media(
    request: Request, path: str = "", transcode: str = ""
) -> StreamingResponse:
    """Serve raw media bytes for the given source path.

    Path validation:
    - 400: empty or malformed path string
    - 403: path resolves outside every allowed root (traversal guard)
    - 404: path is under an allowed root but does not exist on disk
    - 416: unsatisfiable byte range

    No Range header → 200 (full file).
    Range header    → 206 (byte slice, RFC 7233 inclusive END).

    When ``transcode=h264`` is set the source file is lazily transcoded
    to H.264 MP4 (cached) and the cached file is served.  Only the
    SOURCE path is validated against allowed_roots; the service-internal
    cache path is never exposed to the path guard.

    Error codes for the transcode path:
    - 501: ffmpeg not installed (TranscodeUnavailable)
    - 500: ffmpeg exited non-zero or produced no output (TranscodeError)
    """
    import asyncio

    from infrastructure.transcode_service import (
        TranscodeError,
        TranscodeUnavailable,
    )

    allowed_roots: list[Path] = getattr(request.app.state, "allowed_roots", [])
    resolved = validate_under_roots(path, allowed_roots)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path!r}")

    range_header = request.headers.get("range", "")

    if transcode == "h264":
        # The source is already validated; the cache path is service-internal
        # and must NOT be path-guarded (it lives outside any scan root).
        svc = getattr(request.app.state, "transcode_service", None)
        if svc is None:
            raise HTTPException(
                status_code=500,
                detail="transcode service not initialised",
            )
        try:
            loop = asyncio.get_event_loop()
            cached_path: Path = await loop.run_in_executor(
                None, svc.get_transcoded_path, resolved
            )
        except TranscodeUnavailable as exc:
            raise HTTPException(
                status_code=501,
                detail="video transcoding unavailable (ffmpeg not installed)",
            ) from exc
        except TranscodeError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"video transcode failed: {exc}",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"transcode I/O error: {exc}",
            ) from exc

        return _serve_file(cached_path, "video/mp4", range_header)

    # Direct serve (V1 path).
    media_type = _content_type(resolved)
    return _serve_file(resolved, media_type, range_header)
