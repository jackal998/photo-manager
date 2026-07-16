"""GET /api/image — serve decoded JPEG bytes for a given source path."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.web.routes._path_guard import validate_under_roots

router = APIRouter()

# Default thumbnail side when the caller omits `size`.
_DEFAULT_THUMB_SIDE = 256

# Maximum on-disk source file size (in bytes) served at full resolution
# (size=0).  Files larger than this return 413 before any decode is
# attempted so the server never tries to load a multi-hundred-MB RAW into
# RAM in a single request.  Default: 40 MiB.  Thumbnails (size > 0) are
# unaffected — their decode already runs through the ImageService tile path
# which keeps peak RAM bounded by the requested size.
_FULLRES_MAX_SOURCE_BYTES: int = 40 * 1024 * 1024  # 40 MiB


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

    ``size == 0`` requests the full-resolution decode.  Source files
    larger than ``_FULLRES_MAX_SOURCE_BYTES`` return 413 before decoding.
    ``raw == 1`` is not supported in Phase 1 (only decoded JPEG is served).
    ``allowed_roots`` is read from ``app.state.allowed_roots``; an empty list
    means every request is 403 — secure-by-default until Phase 2 wires in
    a manifest-backed root set.
    """
    # -- 415: raw decode unsupported in Phase 1 --------------------------
    if raw:
        raise HTTPException(
            status_code=415,
            detail="raw=1 is not supported in Phase 1; only decoded JPEG is served",
        )

    # -- 400 / 403: empty/malformed/traversal guard ----------------------
    allowed_roots: list[Path] = getattr(request.app.state, "allowed_roots", [])
    resolved = validate_under_roots(path, allowed_roots)

    # -- 404: does not exist on disk -------------------------------------
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path!r}")

    # -- 413: full-res OOM guard (size==0 only) --------------------------
    # Thumbnails (size > 0) are bounded by the requested tile size and are
    # unaffected.  For size==0 we gate on the on-disk source size BEFORE
    # any decode attempt so the server never loads a multi-hundred-MB RAW
    # file into RAM in one shot.
    if size == 0:
        try:
            file_size = resolved.stat().st_size
        except OSError:
            file_size = 0
        if file_size > _FULLRES_MAX_SOURCE_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "file_too_large_for_full_res",
                    "file_size_bytes": file_size,
                    "limit_bytes": _FULLRES_MAX_SOURCE_BYTES,
                },
            )

    # -- Decode JPEG via image service -----------------------------------
    svc = getattr(request.app.state, "image_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="image_service not initialised")

    # -- Source mtime for cache-key freshness (Correction #11) -----------
    # Stat'd ONCE here and threaded through both calls below so the same
    # source-mtime-derived cache key backs the decode AND the ETag lookup
    # without a second stat of the source file (perf: at most one extra
    # os.stat per request).
    try:
        source_mtime_ns = resolved.stat().st_mtime_ns
    except OSError:
        source_mtime_ns = 0

    loop = asyncio.get_running_loop()
    # run_in_executor dispatches the blocking decode to the default thread pool.
    # The WIC path inside image_service internally re-dispatches to its own
    # STA-initialised _wic_executor — the nested-executor pattern is intentional
    # so WIC COM objects always run on an STA thread regardless of the caller.
    jpeg: bytes = await loop.run_in_executor(
        None, svc.get_image_bytes, str(resolved), size, quality, source_mtime_ns
    )

    # -- ETag from disk-cache file mtime_ns + size -----------------------
    # The disk-cache file itself is keyed on (path, size, source_mtime_ns), so
    # an in-place source edit resolves to a DIFFERENT cache file — freshly
    # written just now — whose own mtime naturally busts this ETag too.
    etag: str | None = None
    try:
        cache_path = svc.image_disk_cache_path(str(resolved), size, source_mtime_ns)
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


