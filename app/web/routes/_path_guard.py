"""Shared path-traversal guard for file-mutating routes.

Extracted from image.py so every route that touches the filesystem
can call the same validation function rather than duplicating the
logic. A path-traversal hole in any of these routes means arbitrary
file deletion, so the guard lives in one place.

The membership test (is a path under an allowed root?) is delegated to
``core.app_service.path_safety.is_under_roots`` so that the service layer
can reuse the same symlink-resolution logic without importing FastAPI.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from core.app_service.path_safety import is_under_roots


def validate_under_roots(
    path: str, allowed_roots: list[Path]
) -> Path:
    """Resolve ``path`` and verify it falls under at least one allowed root.

    Status codes — do NOT collapse:
    - 400: empty, whitespace-only, or malformed path string (e.g. null bytes
           that make Path() raise ValueError/OSError).
    - 403: the resolved path escapes every allowed root (traversal guard).

    Note: 404 (not on disk) is the CALLER's responsibility, not this function's.
    The caller may want different 404 semantics (e.g. "missing means skip" for
    execute vs. "missing is an error" for save). This function only validates
    structure and authorization.

    Args:
        path: Raw path string from the client.
        allowed_roots: List of trusted root directories (from app.state).

    Returns:
        The resolved :class:`~pathlib.Path` object.

    Raises:
        HTTPException(400): Empty / malformed path.
        HTTPException(403): Path escapes all allowed roots.
    """
    if not path or not path.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "path must not be empty"},
        )

    try:
        resolved = Path(path).resolve()
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": f"malformed path: {exc}"},
        ) from exc

    # Delegate the membership test to path_safety so service-layer code can
    # reuse identical resolution logic without importing FastAPI.
    roots_as_str = [str(r) for r in allowed_roots]
    if not is_under_roots(path, roots_as_str):
        raise HTTPException(
            status_code=403,
            detail={"code": "permission_denied", "message": "path is outside all allowed roots"},
        )

    return resolved


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is equal to or under ``parent``.

    Polyfill for Path.is_relative_to() (Python 3.9+); written as a plain
    function so it's testable without mocking the Path API.

    NOTE: The canonical implementation now lives in
    ``core.app_service.path_safety._is_relative_to``.  This copy is kept so
    that any existing tests that import ``_is_relative_to`` from this module
    directly continue to work.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
