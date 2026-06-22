"""Action route: POST /api/action/bulk-decide.

Applies a pattern-matched decision or lock mutation to all rows in a manifest
that match the given field + pattern.

Security model
--------------
The manifest path is validated against ``app.state.allowed_roots`` before any
service call.  The service layer re-validates every resolved ``source_path``
against ``allowed_roots`` (the 2C1 ship-blocker fix: an in-root manifest
carrying out-of-root rows cannot mutate those files).

Error map
---------
- ``re.error`` (bad pattern) → 400 ``{"code":"invalid_pattern","detail":...}``
- ``ValueError(("locked_paths",[...]))`` → 409 ``{"code":"locked_paths",...}``
- ``ValueError`` (unknown action or other validation) → 422
- ``FileNotFoundError`` → 404
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.web.models import BulkDecideRequest, BulkDecideResult
from app.web.routes._path_guard import validate_under_roots

router = APIRouter()


def _allowed_roots(request: Request) -> list[Path]:
    """Return the current allowed_roots list from app state."""
    return getattr(request.app.state, "allowed_roots", [])


@router.post("/api/action/bulk-decide")
async def post_bulk_decide(body: BulkDecideRequest, request: Request) -> BulkDecideResult:
    """Apply a bulk decision or lock mutation to pattern-matched rows.

    Returns:
        200 BulkDecideResult
        400 invalid_pattern (bad regex)
        403 path outside allowed roots
        404 manifest not found
        409 locked_paths (locked rows exist and force_locked=False)
        422 unknown action or other validation error
    """
    roots = _allowed_roots(request)
    validate_under_roots(body.manifest_path, roots)

    if not Path(body.manifest_path).is_file():
        raise HTTPException(
            status_code=404, detail=f"Manifest not found: {body.manifest_path!r}"
        )

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _run_bulk_decide,
            body.manifest_path,
            body.field,
            body.pattern,
            body.action,
            body.force_locked,
            body.preview,
            [str(r) for r in roots],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_pattern", "detail": str(exc)},
        ) from exc
    except ValueError as exc:
        args = exc.args
        if args and args[0] == "locked_paths":
            raise HTTPException(
                status_code=409,
                detail={"code": "locked_paths", "locked_paths": args[1]},
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Bulk decide failed: {exc}") from exc

    return BulkDecideResult(**result)


def _run_bulk_decide(
    manifest_path: str,
    field: str,
    pattern: str,
    action: str,
    force_locked: bool,
    preview: bool,
    allowed_roots: list[str],
) -> dict:
    """Blocking worker — runs in the default thread pool."""
    from core.app_service.action_service import bulk_decide

    return bulk_decide(
        manifest_path=manifest_path,
        field=field,
        pattern=pattern,
        action=action,
        allowed_roots=allowed_roots,
        force_locked=force_locked,
        preview=preview,
    )
