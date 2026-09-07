"""Regression test pinning the legacy-keys -> sources.list migration shim.

The shim lives in ``resolve_source_entries``
(``core/app_service/settings_migration.py``): when ``sources.list`` is
absent it reconstructs the source list from the legacy
``sources.{iphone,takeout,jdrive}`` keys, and the web ``GET /api/settings``
loader calls it. Users upgrading from a pre-``sources.list`` build still
carry those legacy keys in their settings.json; deleting the shim silently
empties their source list on first launch with no error or warning.

A future PR that intentionally drops the shim must remove these tests in the
same commit, with a migration story for upgraders (see #258).

#646 removed the two dialog-level duplicates of the first two cases below;
the helper they exercised is the same one, pinned directly here.
"""

from __future__ import annotations

import json

from infrastructure.settings import JsonSettings


def _write_settings(tmp_path, data: dict) -> JsonSettings:
    """Write ``data`` to a tmp settings.json and return a JsonSettings."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return JsonSettings(path)


def test_resolve_reconstructs_legacy_keys_when_list_missing(tmp_path):
    """resolve_source_entries rebuilds the list from legacy keys (#258)."""
    from core.app_service.settings_migration import resolve_source_entries

    settings = _write_settings(
        tmp_path,
        {"sources": {"iphone": "C:/test/iphone", "takeout": "C:/test/takeout"}},
    )
    entries = resolve_source_entries(settings)
    assert entries == [
        {"path": "C:/test/iphone", "recursive": True},
        {"path": "C:/test/takeout", "recursive": True},
    ]


def test_resolve_prefers_sources_list_over_legacy(tmp_path):
    """A present ``sources.list`` wins over the legacy keys."""
    from core.app_service.settings_migration import resolve_source_entries

    settings = _write_settings(
        tmp_path,
        {
            "sources": {
                "list": [{"path": "C:/new", "recursive": False}],
                "iphone": "C:/legacy",
            }
        },
    )
    entries = resolve_source_entries(settings)
    assert entries == [{"path": "C:/new", "recursive": False}]


def test_resolve_drops_list_entries_without_path(tmp_path):
    """Malformed ``sources.list`` entries (no path / not a dict) are skipped."""
    from core.app_service.settings_migration import resolve_source_entries

    settings = _write_settings(
        tmp_path,
        {
            "sources": {
                "list": [
                    {"path": "C:/keep", "recursive": True},
                    {"recursive": True},  # no path -> dropped
                    "not-a-dict",  # wrong type -> dropped
                    {"path": "", "recursive": True},  # empty path -> dropped
                ]
            }
        },
    )
    entries = resolve_source_entries(settings)
    assert entries == [{"path": "C:/keep", "recursive": True}]


def test_resolve_empty_when_no_sources_at_all(tmp_path):
    """No list and no legacy keys -> empty list (a fresh install)."""
    from core.app_service.settings_migration import resolve_source_entries

    settings = _write_settings(tmp_path, {})
    assert resolve_source_entries(settings) == []
