"""GET /api/fs/browse — filesystem directory listing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.app_service.fs_browse import browse

router = APIRouter()


@router.get("/api/fs/browse")
async def fs_browse(path: str = "") -> dict:
    """List directory contents or filesystem roots.

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
