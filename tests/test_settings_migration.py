"""Regression test pinning the legacy-keys -> sources.list migration shim.

The shim lives in ``ScanDialog._load_from_settings``: when
``sources.list`` is absent it reconstructs the dialog's source list
from the legacy ``sources.{iphone,takeout,jdrive}`` keys. Users
upgrading from a pre-``sources.list`` build still carry those legacy
keys in their settings.json; deleting the shim silently empties their
source list on first launch with no error or warning.

A future PR that intentionally drops the shim must remove this test in
the same commit, with a migration story for upgraders (see #258).
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
