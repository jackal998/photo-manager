"""Pure, headless path-safety predicate shared between route and service layers.

The route layer (app/web/routes/_path_guard.py) raises HTTPException on
violations; the service layer (core/app_service/execute_service.py) does not
import FastAPI — it calls this module's predicate directly and builds its own
refused/failed lists.  Keeping the membership test here means both layers
resolve symlinks identically.
"""

from __future__ import annotations

from pathlib import Path


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is equal to or under ``parent``.

    Polyfill for Path.is_relative_to() (Python 3.9+), matching the
    implementation in app/web/routes/_path_guard.py exactly so symlink
    resolution is identical across both callers.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def is_under_roots(path: str, allowed_roots: list[str]) -> bool:
    """Return True iff ``path`` resolves to a location under at least one allowed root.

    Returns False (never raises) for:
    - empty or whitespace-only ``path``
    - paths that raise ValueError / OSError during resolution (e.g. null bytes)
    - empty ``allowed_roots``
    - paths that escape every allowed root

    Args:
        path: Raw path string (typically a source_path read from SQLite).
        allowed_roots: Trusted root directories as plain strings.

    Returns:
        True if the resolved path is under at least one resolved allowed root.
        False otherwise.
    """
    if not path or not path.strip():
        return False

    if not allowed_roots:
        return False

    try:
        resolved = Path(path).resolve()
    except (ValueError, OSError):
        return False

    for root in allowed_roots:
        try:
            root_resolved = Path(root).resolve()
        except (ValueError, OSError):
            continue
        if _is_relative_to(resolved, root_resolved):
            return True

    return False
