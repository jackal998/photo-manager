"""Per-user app-data root (``%LOCALAPPDATA%\\PhotoManager``) and its override.

``PHOTO_MANAGER_DATA_DIR`` redirects every default app-data path (thumbnail
cache, transcode cache, logs, delete audit CSVs) to one directory. Tests and
the local QA runner set it to a temp dir so no run touches the owner's real
profile (#968). Unset, every path resolves exactly as before.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

DATA_DIR_ENV = "PHOTO_MANAGER_DATA_DIR"


def app_data_override() -> str | None:
    """Return the ``PHOTO_MANAGER_DATA_DIR`` value, or ``None`` when unset/empty."""
    return os.environ.get(DATA_DIR_ENV) or None


def app_data_root() -> Path:
    """Return the app-data root: the override when set, else the profile default."""
    override = app_data_override()
    if override is not None:
        return Path(os.path.expandvars(override))
    return Path.home() / "AppData" / "Local" / "PhotoManager"


def ensure_isolated_app_data() -> str:
    """Set the override to a fresh temp dir unless one is already set; return the active dir.

    The QA entry points call this before any app code runs, so a local QA
    run cannot write into the owner's real profile by default. An override
    the caller set explicitly is respected.
    """
    existing = app_data_override()
    if existing is not None:
        return existing
    fresh = tempfile.mkdtemp(prefix="photo-manager-qa-")
    os.environ[DATA_DIR_ENV] = fresh
    return fresh
