"""GET /api/fs/browse — filesystem directory listing.

DELIBERATELY UNGUARDED (decision recorded 2026-07-22, PR #805): this route
does NOT call ``_path_guard.validate_under_roots``, unlike execute/image/
media. It backs the FsBrowser picker, whose whole job is browsing the
machine to choose NEW scan sources / manifest paths — an allowed-roots
check would make first-time selection impossible. Read-only listing, no
mutation. Threat model rests on the server binding loopback-only
(127.0.0.1, see launcher.py): revisit this exemption BEFORE any change
that exposes the server beyond localhost (LAN bind, reverse proxy) or
runs it with elevated privileges.
"""

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
