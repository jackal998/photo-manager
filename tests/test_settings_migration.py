"""Regression test pinning the legacy-keys -> sources.list migration shim.

The shim lives in the Qt-free ``resolve_source_entries`` helper
(``core/app_service/settings_migration.py``): when ``sources.list`` is
absent it reconstructs the source list from the legacy
``sources.{iphone,takeout,jdrive}`` keys. Both ``ScanDialog._load_from_settings``
and the web ``GET /api/settings`` loader call it. Users upgrading from a
pre-``sources.list`` build still carry those legacy keys in their
settings.json; deleting the shim silently empties their source list on
first launch with no error or warning.

The Qt-free tests at the bottom of this file pin the helper directly (they
survive the eventual app/views deletion); the ScanDialog tests pin the Qt
wiring. A future PR that intentionally drops the shim must remove these
tests in the same commit, with a migration story for upgraders (see #258).
"""

from __future__ import annotations

import json

import pytest

from app.views.dialogs.scan_dialog import ScanDialog
from infrastructure.settings import JsonSettings


def _write_settings(tmp_path, data: dict) -> JsonSettings:
    """Write ``data`` to a tmp settings.json and return a JsonSettings."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return JsonSettings(path)


def test_legacy_keys_reconstruct_sources_list_when_sources_list_missing(
    qapp, tmp_path
):
    """Old settings.json with sources.{iphone,takeout} but no sources.list
    must populate the dialog's source list from the legacy keys.

    Failure mode being pinned: deleting the shim leaves the dialog with
    an empty source list on first launch for any user upgrading from a
    pre-sources.list build.
    """
    settings = _write_settings(
        tmp_path,
        {
            "sources": {
                "iphone": "C:/test/iphone",
                "takeout": "C:/test/takeout",
                # jdrive intentionally absent -> only 2 entries reconstructed
                # list intentionally absent -> triggers the shim
            }
        },
    )

    dlg = ScanDialog(settings=settings)
    try:
        entries = dlg._source_list.entries()
        assert len(entries) == 2
        paths = {e.path for e in entries}
        assert paths == {"C:/test/iphone", "C:/test/takeout"}
        assert all(e.recursive is True for e in entries)
    finally:
        dlg.deleteLater()


def test_sources_list_takes_precedence_when_both_present(qapp, tmp_path):
    """When ``sources.list`` AND legacy keys both exist, ``sources.list``
    wins. Pins the precedence so the shim cannot accidentally clobber
    new-format data written by a current build.
    """
    settings = _write_settings(
        tmp_path,
        {
            "sources": {
                "list": [{"path": "C:/new", "recursive": False}],
                "iphone": "C:/legacy",  # would otherwise be picked
            }
        },
    )

    dlg = ScanDialog(settings=settings)
    try:
        entries = dlg._source_list.entries()
        assert len(entries) == 1
        assert entries[0].path == "C:/new"
        assert entries[0].recursive is False
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Qt-free tests of the migration helper directly. These survive the eventual
# app/views deletion (no qapp / ScanDialog dependency).
# ---------------------------------------------------------------------------


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
