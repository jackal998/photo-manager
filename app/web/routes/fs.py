"""GET /api/fs/browse — filesystem directory listing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.app_service.fs_browse import browse

router = APIRouter()


@router.get("/api/fs/browse")
def fs_browse(path: str = "") -> dict:
    """List directory contents or filesystem roots.

    Declared as a plain ``def`` so FastAPI runs it in its worker threadpool:
    ``browse()`` walks ``iterdir()`` and stats every entry, which over an SMB
    /NAS directory is a burst of blocking network round-trips. On ``async def``
    that would run on the event loop and stall every other request (incl. the
    live SSE scan-progress stream). See #790.

    Args:
        path: Directory to list. Empty or missing → list filesystem roots.

    Returns:
        200 {path, parent, entries}
        400 NotADirectoryError (path exists but is not a dir)
        404 FileNotFoundError (path given but does not exist)
    """
    try:
        return browse(path if path else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
