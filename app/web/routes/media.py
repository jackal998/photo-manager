"""GET /api/media — serve raw video (and generic media) bytes with HTTP Range support.

Mirrors the security pattern of /api/image: the same path guard is reused.
V1 scope: pass-through byte serving only — no transcoding.

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


@router.get("/api/media")
def get_media(request: Request, path: str = "") -> StreamingResponse:
    """Serve raw media bytes for the given source path.

    Path validation:
    - 400: empty or malformed path string
    - 403: path resolves outside every allowed root (traversal guard)
    - 404: path is under an allowed root but does not exist on disk
    - 416: unsatisfiable byte range

    No Range header → 200 (full file).
    Range header    → 206 (byte slice, RFC 7233 inclusive END).
    """
    allowed_roots: list[Path] = getattr(request.app.state, "allowed_roots", [])
    resolved = validate_under_roots(path, allowed_roots)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path!r}")

    try:
        file_size = resolved.stat().st_size
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not stat file: {exc}"
        ) from exc

    media_type = _content_type(resolved)
    range_header = request.headers.get("range", "")

    if not range_header:
        # No Range → 200 full file.
        return StreamingResponse(
            _stream_slice(resolved, 0, max(file_size - 1, 0)),
            status_code=200,
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # Parse Range header.
    m = _RANGE_RE.match(range_header.strip())
    if not m:
        # Malformed range syntax → 416.
        return StreamingResponse(
            iter([]),
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_raw = m.group(1)
    end_raw = m.group(2)

    if not start_raw:
        # Suffix-range: bytes=-N  →  last N bytes.
        if not end_raw or int(end_raw) == 0:
            # bytes=-  or  bytes=-0 → unsatisfiable.
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

    # Clamp end to last byte; reject unsatisfiable start.
    end = min(end, file_size - 1)
    if start >= file_size or start > end:
        return StreamingResponse(
            iter([]),
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    slice_len = end - start + 1
    return StreamingResponse(
        _stream_slice(resolved, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(slice_len),
        },
    )
