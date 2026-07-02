"""GET /api/manifest, PATCH /api/decision, PATCH /api/lock."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.web.models import DecisionUpdate, LockUpdate
from core.app_service.review_service import load_review, set_decisions, set_locks

router = APIRouter()

# Module-level lock to guard concurrent allowed_roots merges.
_roots_lock = threading.Lock()


@router.get("/api/manifest")
async def get_manifest(path: str = "", request: Request = None) -> dict:  # type: ignore[assignment]
    """Load a manifest and return its groups.

    The manifest's folder roots are registered server-side into
    ``app.state.allowed_roots`` (so the image route will serve thumbnails)
    but are NOT returned in the response — they are internal authorization
    state, not a client contract.

    The load runs synchronously in the handler. For a localhost single-user
    app this is the simplest correct choice: a manifest load is infrequent
    (after a scan / on initial open) and the high-frequency thumbnail path
    already offloads decode work (see image.py). Offloading the load to a
    *dedicated* executor is a deliberate future enhancement for multi-client
    perf — the loop's shared default executor is unsafe here (it is shut down
    by client/lifespan churn), so it is intentionally not used.

    Returns:
        200 {manifest_path, groups, total_groups, total_files}
        400 if path is empty
        404 if the file does not exist on disk
        422 if the sqlite/load raises an unexpected error
    """
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="path must not be empty")

    if not Path(path).exists():
        raise HTTPException(
            status_code=404,
            detail=f"Manifest not found: {path!r}",
        )

    try:
        result = load_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Manifest not found: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to load manifest: {exc}",
        ) from exc

    # Register the manifest's folder roots into allowed_roots so the image
    # route will serve thumbnails for files in this manifest.
    if request is not None:
        _merge_roots(request, result.get("roots", []))

    return {
        "manifest_path": result["manifest_path"],
        "groups": result["groups"],
        "total_groups": result["total_groups"],
        "total_files": result["total_files"],
    }


def _merge_roots(request: Request, roots: list[str]) -> None:
    """Merge new root paths into app.state.allowed_roots (append-only, idempotent)."""
    new_paths = [Path(r) for r in roots if r]
    with _roots_lock:
        # Read allowed INSIDE the lock so read-modify-write is atomic.
        allowed: list[Path] = getattr(request.app.state, "allowed_roots", [])
        existing = {str(p) for p in allowed}
        for p in new_paths:
            if str(p) not in existing:
                allowed.append(p)
                existing.add(str(p))
        request.app.state.allowed_roots = allowed


@router.patch("/api/decision")
async def patch_decision(body: DecisionUpdate) -> dict:
    """Persist user decisions for a batch of files, gated on locked rows.

    The returned count is the number of rows ACTUALLY submitted for write
    (narrowed by ``skip_locked``), not necessarily the number matched in
    the DB (executemany does not report per-row matches).

    Returns:
        200 {updated: int}
        400 if a decision value is invalid
        404 if manifest_path does not exist on disk
        409 locked_paths (locked rows targeted and force_locked=False and
            skip_locked=False) — mirrors POST /api/action/bulk-decide's
            409 detail shape: {code, locked_paths, matched_total}
        422 on db error
    """
    decisions = {item.file_path: item.decision for item in body.decisions}
    try:
        updated = set_decisions(
            body.manifest_path,
            decisions,
            force_locked=body.force_locked,
            skip_locked=body.skip_locked,
        )
    except ValueError as exc:
        args = exc.args
        if args and args[0] == "locked_paths":
            detail: dict = {"code": "locked_paths", "locked_paths": args[1]}
            if len(args) > 2:
                detail["matched_total"] = args[2]
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Manifest not found: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to update decisions: {exc}",
        ) from exc
    return {"updated": updated}


@router.patch("/api/lock")
async def patch_lock(body: LockUpdate) -> dict:
    """Persist lock state for a batch of files.

    The returned count is the number of rows REQUESTED, not necessarily
    the number matched in the DB (executemany does not report per-row matches).

    Returns:
        200 {updated: int}
        404 if manifest_path does not exist on disk
        422 on db error
    """
    locks = {item.file_path: item.locked for item in body.locks}
    try:
        updated = set_locks(body.manifest_path, locks)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Manifest not found: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to update lock state: {exc}",
        ) from exc
    return {"updated": updated}
