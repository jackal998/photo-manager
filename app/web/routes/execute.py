"""File-mutating routes: POST /api/execute, /api/remove, /api/prune, /api/save, /api/reveal.

Security model
--------------
Every client-supplied path is validated against ``app.state.allowed_roots``
via ``validate_under_roots`` before any filesystem or SQLite operation.
Path traversal here means arbitrary file DELETION, so the guard applies to
EVERY path in EVERY request body — manifest_path, file_paths, scope_paths,
and target_path.

Concurrency
-----------
POST /api/execute acquires ``app.state.execute_lock`` (an asyncio.Lock
created during lifespan startup).  A second concurrent execute returns 409
``execute_already_running`` immediately — no blocking.  All blocking I/O
(send2trash, sqlite, copy2) runs in ``loop.run_in_executor(None, ...)`` so
the asyncio event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.web.models import (
    ExecuteRequest,
    ExecuteResult,
    PruneRequest,
    PruneResult,
    RemoveRequest,
    RemoveResult,
    RevealRequest,
    RevealResult,
    SaveRequest,
    SaveResult,
)
from app.web.routes._path_guard import validate_under_roots

router = APIRouter()


def _allowed_roots(request: Request) -> list[Path]:
    """Return the current allowed_roots list from app state."""
    return getattr(request.app.state, "allowed_roots", [])


def _is_windows() -> bool:
    """Return True when running on Windows (os.name == 'nt').

    Extracted into its own function so tests can monkeypatch this specific
    check without changing the global ``os.name`` (which would break
    ``pathlib.Path`` on Windows when set to 'posix').
    """
    return os.name == "nt"


def _validate_manifest(manifest_path: str, allowed_roots: list[Path]) -> Path:
    """Validate manifest_path against allowed_roots and return the resolved path."""
    return validate_under_roots(manifest_path, allowed_roots)


@router.post("/api/execute")
async def post_execute(body: ExecuteRequest, request: Request) -> ExecuteResult:
    """Execute all decided rows in the manifest.

    Returns:
        200 ExecuteResult
        400 bad path
        403 path outside allowed roots
        404 manifest not found
        409 locked_paths (when locked delete rows exist and force_locked=False)
        409 execute_already_running (concurrent execute in progress)
        422 on unexpected error
    """
    roots = _allowed_roots(request)

    # Validate manifest_path.
    _validate_manifest(body.manifest_path, roots)

    # Validate all scope_paths (if provided).
    if body.scope_paths is not None:
        for sp in body.scope_paths:
            validate_under_roots(sp, roots)

    # Manifest must exist on disk.
    if not Path(body.manifest_path).is_file():
        raise HTTPException(status_code=404, detail=f"Manifest not found: {body.manifest_path!r}")

    # Concurrency gate — try to acquire without blocking.
    execute_lock: asyncio.Lock = getattr(request.app.state, "execute_lock", None)
    if execute_lock is None:
        # Defensive — lifespan should have set this up; fall through if missing.
        execute_lock = asyncio.Lock()
        request.app.state.execute_lock = execute_lock

    if execute_lock.locked():
        raise HTTPException(
            status_code=409,
            detail={"code": "execute_already_running"},
        )

    async with execute_lock:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                _run_execute,
                body.manifest_path,
                body.scope_paths,
                body.recycle,
                body.force_locked,
                [str(r) for r in roots],
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            args = exc.args
            if args and args[0] == "locked_paths":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "locked_paths", "locked_paths": args[1]},
                ) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Execute failed: {exc}") from exc

    return ExecuteResult(**result)


def _run_execute(
    manifest_path: str,
    scope_paths: list[str] | None,
    recycle: bool,
    force_locked: bool,
    allowed_roots: list[str],
) -> dict:
    """Blocking worker — runs in the default thread pool."""
    from core.app_service.execute_service import execute_decisions
    return execute_decisions(
        manifest_path=manifest_path,
        scope_paths=scope_paths,
        recycle=recycle,
        force_locked=force_locked,
        allowed_roots=allowed_roots,
    )


@router.post("/api/remove")
async def post_remove(body: RemoveRequest, request: Request) -> RemoveResult:
    """Remove files from the review manifest (outcome='ignored', files untouched on disk).

    Returns:
        200 RemoveResult
        400 bad path
        403 path outside allowed roots
        404 manifest not found
        409 locked_paths
        422 on unexpected error
    """
    roots = _allowed_roots(request)
    _validate_manifest(body.manifest_path, roots)

    for fp in body.file_paths:
        validate_under_roots(fp, roots)

    if not Path(body.manifest_path).is_file():
        raise HTTPException(status_code=404, detail=f"Manifest not found: {body.manifest_path!r}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _run_remove,
            body.manifest_path,
            body.file_paths,
            body.force_locked,
            [str(r) for r in roots],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        args = exc.args
        if args and args[0] == "locked_paths":
            raise HTTPException(
                status_code=409,
                detail={"code": "locked_paths", "locked_paths": args[1]},
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Remove failed: {exc}") from exc

    return RemoveResult(**result)


def _run_remove(
    manifest_path: str,
    file_paths: list[str],
    force_locked: bool,
    allowed_roots: list[str],
) -> dict:
    from core.app_service.execute_service import remove_from_review
    return remove_from_review(
        manifest_path=manifest_path,
        file_paths=file_paths,
        force_locked=force_locked,
        allowed_roots=allowed_roots,
    )


@router.post("/api/prune")
async def post_prune(body: PruneRequest, request: Request) -> PruneResult:
    """Remove singleton groups from the review manifest.

    Returns:
        200 PruneResult
        400 bad path
        403 path outside allowed roots
        404 manifest not found
        422 on unexpected error
    """
    roots = _allowed_roots(request)
    _validate_manifest(body.manifest_path, roots)

    if not Path(body.manifest_path).is_file():
        raise HTTPException(status_code=404, detail=f"Manifest not found: {body.manifest_path!r}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _run_prune,
            body.manifest_path,
            body.include_actioned,
            [str(r) for r in roots],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Prune failed: {exc}") from exc

    return PruneResult(**result)


def _run_prune(
    manifest_path: str,
    include_actioned: bool,
    allowed_roots: list[str],
) -> dict:
    from core.app_service.execute_service import prune_singletons
    return prune_singletons(
        manifest_path=manifest_path,
        include_actioned=include_actioned,
        allowed_roots=allowed_roots,
    )


@router.post("/api/save")
async def post_save(body: SaveRequest, request: Request) -> SaveResult:
    """Save manifest decisions in-place or as a copy.

    Returns:
        200 SaveResult
        400 bad path
        403 path outside allowed roots
        404 manifest not found
        422 on unexpected error
    """
    roots = _allowed_roots(request)
    _validate_manifest(body.manifest_path, roots)

    # target_path MUST also validate against allowed_roots.
    if body.target_path is not None:
        validate_under_roots(body.target_path, roots)

    if not Path(body.manifest_path).is_file():
        raise HTTPException(status_code=404, detail=f"Manifest not found: {body.manifest_path!r}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _run_save,
            body.manifest_path,
            body.target_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Save failed: {exc}") from exc

    return SaveResult(**result)


def _run_save(manifest_path: str, target_path: str | None) -> dict:
    from core.app_service.execute_service import save_manifest
    return save_manifest(manifest_path=manifest_path, target_path=target_path)


@router.post("/api/reveal")
async def post_reveal(body: RevealRequest, request: Request) -> RevealResult:
    """Open Explorer to reveal a file (Windows-only, localhost-only).

    Returns:
        200 RevealResult
        400 bad path
        403 non-localhost client OR path outside allowed roots
        501 non-Windows platform
        500 subprocess error
    """
    # Localhost guard — reveal must only fire from the local machine.
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail={"code": "permission_denied", "message": "reveal only allowed from localhost"},
        )

    roots = _allowed_roots(request)
    resolved = validate_under_roots(body.file_path, roots)

    # Platform guard.
    if not _is_windows():
        raise HTTPException(
            status_code=501,
            detail={"code": "platform_unsupported", "message": "reveal only supported on Windows"},
        )

    # Fire-and-forget: explorer /select,<path>
    norm_path = os.path.normpath(str(resolved))
    try:
        subprocess.Popen(["explorer", "/select,", norm_path])
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to launch Explorer: {exc}",
        ) from exc

    return RevealResult(status="ok")
