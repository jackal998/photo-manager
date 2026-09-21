"""Qt-free settings migration helpers shared by the Qt dialog and web API.

The legacy-keys -> ``sources.list`` migration (#258) originally lived inside
``ScanDialog._load_from_settings``. It is pure data logic with no Qt
dependency, so it lives here where both the Qt ScanDialog and the web
settings loader can call it: users upgrading from a pre-``sources.list``
build still carry only the legacy ``sources.{iphone,takeout,jdrive}`` keys,
and dropping this reconstruction silently empties their source list on first
launch with no error or warning.

``tests/test_settings_migration.py`` pins the contract; a PR that
intentionally drops this shim must drop that test too and ship a migration
story. See #258.
"""
from __future__ import annotations

from typing import Any, Protocol

# Legacy single-path source keys from pre-``sources.list`` builds, in the
# order they were historically presented in the dialog.
_LEGACY_SOURCE_KEYS: tuple[str, ...] = ("iphone", "takeout", "jdrive")


class _SettingsLike(Protocol):
    """Minimal read interface (JsonSettings / Qt settings both satisfy)."""

    def get(self, key: str, default: Any | None = ...) -> Any:  # pragma: no cover - protocol
        ...


def resolve_source_entries(settings: _SettingsLike) -> list[dict]:
    """Return the resolved source list as ``[{"path", "recursive"}, ...]``.

    Resolution order:

    1. If ``sources.list`` is present, return its valid entries (each must be
       a dict carrying a non-empty ``path``; ``recursive`` defaults to True).
    2. Otherwise fall back to the legacy ``sources.{iphone,takeout,jdrive}``
       keys (the #258 migration shim), each reconstructed as a recursive
       source.

    The returned dicts use the canonical ``sources.list`` shape so callers
    can map them straight onto their own row/entry type.
    """
    sources_list = settings.get("sources.list")
    if sources_list:
        return [
            {"path": item["path"], "recursive": item.get("recursive", True)}
            for item in sources_list
            if isinstance(item, dict) and item.get("path")
        ]

    entries: list[dict] = []
    for key in _LEGACY_SOURCE_KEYS:
        path = settings.get(f"sources.{key}", "")
        if path:
            entries.append({"path": path, "recursive": True})
    return entries
