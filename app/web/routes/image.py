"""GET /api/image — serve decoded JPEG bytes for a given source path."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter()

# Default thumbnail side when the caller omits `size`.
_DEFAULT_THUMB_SIDE = 256


@router.get("/api/image")
async def get_image(
    request: Request,
    path: str = "",
    size: int = _DEFAULT_THUMB_SIDE,
    quality: int = 85,
    raw: int = 0,
) -> Response:
    """Return a decoded JPEG for the requested source file.

    Path validation uses THREE distinct status codes — do not collapse:
    - 400: empty or malformed path string
    - 403: path resolves outside every allowed root (traversal guard)
    - 404: path is under an allowed root but does not exist on disk

    ``size == 0`` requests the full-resolution decode.
    ``raw == 1`` is not supported in Phase 1 (only decoded JPEG is served).
    ``allowed_roots`` is read from ``app.state.allowed_roots``; an empty list
    means every request is 403 — secure-by-default until Phase 2 wires in
    a manifest-backed root set.
    """
    # -- 400: empty / malformed ------------------------------------------
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="path must not be empty")

    try:
        resolved = Path(path).resolve()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"malformed path: {exc}") from exc

    # -- 415: raw decode unsupported in Phase 1 --------------------------
    if raw:
        raise HTTPException(
            status_code=415,
            detail="raw=1 is not supported in Phase 1; only decoded JPEG is served",
        )

    # -- 403: traversal / not under any allowed root ---------------------
    allowed_roots: list[Path] = getattr(request.app.state, "allowed_roots", [])
    if not any(
        _is_relative_to(resolved, Path(r).resolve()) for r in allowed_roots
    ):
        raise HTTPException(
            status_code=403,
            detail="path is outside all allowed roots",
        )

    # -- 404: does not exist on disk -------------------------------------
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path!r}")

    # -- Decode JPEG via image service -----------------------------------
    svc = getattr(request.app.state, "image_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="image_service not initialised")

    loop = asyncio.get_running_loop()
    # run_in_executor dispatches the blocking decode to the default thread pool.
    # The WIC path inside image_service internally re-dispatches to its own
    # STA-initialised _wic_executor — the nested-executor pattern is intentional
    # so WIC COM objects always run on an STA thread regardless of the caller.
    jpeg: bytes = await loop.run_in_executor(
        None, svc.get_image_bytes, str(resolved), size, quality
    )

    # -- ETag from disk-cache file mtime_ns + size -----------------------
    etag: str | None = None
    try:
        cache_path = svc.image_disk_cache_path(str(resolved), size)
        st = cache_path.stat()
        etag = f'"{st.st_mtime_ns}-{st.st_size}"'
    except (OSError, AttributeError):
        # Disk cache not yet written (placeholder or in-mem hit) — skip ETag.
        pass

    # -- 304 Not Modified -------------------------------------------------
    if etag and request.headers.get("if-none-match") == etag:
        return Response(status_code=304)

    # -- Cache-Control ---------------------------------------------------
    if size and size > 0:
        # Thumbnails / previews: long public cache + stale-while-revalidate.
        cache_control = "public, max-age=86400, stale-while-revalidate=604800"
    else:
        # Full-res: shorter cache, no stale-while-revalidate (large payload).
        cache_control = "public, max-age=3600"

    headers: dict[str, str] = {"Cache-Control": cache_control}
    if etag:
        headers["ETag"] = etag

    return Response(content=jpeg, media_type="image/jpeg", headers=headers)


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is equal to or under ``parent``.

    Polyfill for Path.is_relative_to() (Python 3.9+); written as a plain
    function so it's testable without mocking the Path API.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
