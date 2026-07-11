"""Action routes: POST /api/action/bulk-decide, POST /api/action/apply-best-copy.

bulk-decide applies a pattern-matched decision or lock mutation to all rows in
a manifest that match the given field + pattern. apply-best-copy (#744) is
scoped to a single already-loaded group (like PATCH /api/decision, not
pattern-resolved) — see its own docstring / core.app_service.action_service.
apply_best_copy for the keeper-selection semantics.

Security model
--------------
The manifest ``.db`` itself is NOT roots-gated — it is an output file the user
may store anywhere, exactly as ``GET /api/manifest`` loads it (a live
ActionDialog run surfaced that gating it 403'd a perfectly valid db stored
outside the scanned photo roots). The real boundary is the affected PHOTO
paths: the service layer filters every resolved ``source_path`` through
``is_under_roots`` (the 2C1 ship-blocker fix — an in-root manifest carrying
out-of-root rows cannot mutate those files).

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

from app.web.models import ApplyBestCopyRequest, BulkDecideRequest, BulkDecideResult

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
        404 manifest not found
        409 locked_paths (locked rows exist and force_locked=False)
        422 unknown action or other validation error
    """
    roots = _allowed_roots(request)

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
            body.skip_locked,
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
            detail: dict = {"code": "locked_paths", "locked_paths": args[1]}
            # bulk_decide raises a 3-tuple ("locked_paths", locked, matched_total);
            # forward the full matched count so the FE can size the unlocked
            # subset without a separate preview round-trip (#674).
            if len(args) > 2:
                detail["matched_total"] = args[2]
            raise HTTPException(status_code=409, detail=detail) from exc
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
    skip_locked: bool,
    preview: bool,
    allowed_roots: list[str],
) -> dict:
    """Blocking worker — runs in the default thread pool.

    ``skip_locked`` sits between ``force_locked`` and ``preview`` in BOTH this
    signature and the positional ``run_in_executor`` call above; keep the two in
    lock-step (a positional mismatch would silently default-False skip_locked —
    the route test asserts it actually reaches the service).
    """
    from core.app_service.action_service import bulk_decide

    return bulk_decide(
        manifest_path=manifest_path,
        field=field,
        pattern=pattern,
        action=action,
        allowed_roots=allowed_roots,
        force_locked=force_locked,
        skip_locked=skip_locked,
        preview=preview,
    )


# ---------------------------------------------------------------------------
# POST /api/action/apply-best-copy (#744)
# ---------------------------------------------------------------------------


@router.post("/api/action/apply-best-copy")
async def post_apply_best_copy(body: ApplyBestCopyRequest) -> BulkDecideResult:
    """Apply best-copy decisions to ONE duplicate group.

    The review-time twin of scan-time auto-select: within the target group,
    the top-score row becomes the keeper (decision "" + locked); every row
    the classifier positively identified as a duplicate gets 'delete'.

    Returns:
        200 BulkDecideResult (action_applied="apply_best_copy")
        404 manifest not found, or group_number not found in the manifest
        409 locked_paths (locked rows would be written and force_locked=False
            and skip_locked=False) — same detail shape as bulk-decide's 409
        422 force_locked and skip_locked both set, or other validation error
    """
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            _run_apply_best_copy,
            body.manifest_path,
            body.group_number,
            body.force_locked,
            body.skip_locked,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        args = exc.args
        if args and args[0] == "locked_paths":
            detail: dict = {"code": "locked_paths", "locked_paths": args[1]}
            if len(args) > 2:
                detail["matched_total"] = args[2]
            raise HTTPException(status_code=409, detail=detail) from exc
        if args and args[0] == "group_not_found":
            raise HTTPException(
                status_code=404,
                detail=f"Group not found: {args[1] if len(args) > 1 else body.group_number!r}",
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Apply best copy failed: {exc}") from exc

    return BulkDecideResult(**result)


def _run_apply_best_copy(
    manifest_path: str,
    group_number: int,
    force_locked: bool,
    skip_locked: bool,
) -> dict:
    """Blocking worker — runs in the default thread pool."""
    from core.app_service.action_service import apply_best_copy

    return apply_best_copy(
        manifest_path=manifest_path,
        group_number=group_number,
        force_locked=force_locked,
        skip_locked=skip_locked,
    )
