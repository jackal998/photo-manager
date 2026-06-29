"""GET /api/settings, PATCH /api/settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.web.models import SettingsUpdate

router = APIRouter()

# Curated set of settings keys the web UI may read/write. Keeping this
# explicit (a) prevents GET from leaking any future sensitive key in
# settings.json, and (b) bounds PATCH to known, dict-intermediate paths
# so a stray key can't silently restructure the file. Extend as the web
# UI grows to read/write more settings.
_WEB_SETTINGS_KEYS: tuple[str, ...] = (
    "sorting.defaults",
    "ui.locale",
    "ui.prune_singletons",
    "ui.scan_dialog.autotune_read_knee",
    # ScanDialog source persistence (#678-E / s23a/s23b). The list value is
    # [{"path": str, "recursive": bool}, ...] and output is a str — the same
    # shape the Qt ScanDialog._save_to_settings writes. These only pre-fill the
    # web ScanDialog on open; the scanner's sources + allowed_roots come from
    # the scan POST body, so persisting them here widens no execution boundary.
    # GET echoes these user filesystem paths back to the caller — acceptable on
    # the loopback-only (127.0.0.1) server; a future expose-beyond-loopback
    # change must reconsider this path disclosure.
    "sources.list",
    "sources.output",
)


def _load_settings():
    """Return a JsonSettings pointed at settings.json — same resolution as scan.py."""
    import os
    from pathlib import Path

    from infrastructure.settings import JsonSettings

    home_env = os.environ.get("PHOTO_MANAGER_HOME")
    # app/web/routes/settings.py → app/web/ → app/ → repo root
    repo_root = Path(__file__).parent.parent.parent.parent
    if home_env:
        config_home = (repo_root / home_env).resolve()
    else:
        config_home = repo_root
    return JsonSettings(config_home / "settings.json")


@router.get("/api/settings")
async def get_settings() -> dict:
    """Return the allowlisted web-facing settings as a dict.

    Only keys in _WEB_SETTINGS_KEYS are returned — this prevents GET from
    leaking any future sensitive key that might be added to settings.json.

    ``sources.list`` is resolved through the shared migration helper so an
    upgrader carrying only the legacy ``sources.{iphone,takeout,jdrive}``
    keys (no ``sources.list``) still gets their source list in the web UI —
    the same reconstruction the Qt ScanDialog performs (#258).

    Returns:
        200 {<allowlisted key>: value, ...}  (absent keys return None)
    """
    from core.app_service.settings_migration import resolve_source_entries

    settings = _load_settings()
    result = {key: settings.get(key) for key in _WEB_SETTINGS_KEYS}
    if not result.get("sources.list"):
        resolved = resolve_source_entries(settings)
        if resolved:
            result["sources.list"] = resolved
    return result


@router.patch("/api/settings")
async def patch_settings(body: SettingsUpdate) -> dict:
    """Apply dotted-key updates and persist.

    Only keys in _WEB_SETTINGS_KEYS are accepted. All keys in the request
    are validated before any write is applied (validate-all-then-apply).

    Returns:
        200 {updated: int}
        400 if any key is not in the allowlist
        422 on write error
    """
    for key in body.updates:
        if key not in _WEB_SETTINGS_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"settings key not writable: {key!r}",
            )
    settings = _load_settings()
    for key, value in body.updates.items():
        settings.set(key, value)
    try:
        settings.save()
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to save settings: {exc}",
        ) from exc
    return {"updated": len(body.updates)}
