"""Tests for infrastructure.settings.JsonSettings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.settings import JsonSettings


@pytest.fixture
def settings_file(tmp_path):
    """Write a minimal settings.json and return its path."""
    data = {
        "thumbnail_size": 512,
        "delete": {"confirm_group_full_delete": True},
        "sources": {
            "iphone": "/nas/iphone",
            "takeout": "/downloads/takeout",
        },
    }
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_get_top_level(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("thumbnail_size") == 512


def test_get_dotted_key(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("delete.confirm_group_full_delete") is True


def test_get_nested_source(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("sources.iphone") == "/nas/iphone"


def test_get_missing_key_returns_default(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("does.not.exist") is None
    assert s.get("missing", "fallback") == "fallback"


def test_get_partial_path_returns_dict(settings_file):
    s = JsonSettings(settings_file)
    result = s.get("sources")
    assert isinstance(result, dict)
    assert result["iphone"] == "/nas/iphone"


def test_set_existing_key(settings_file):
    s = JsonSettings(settings_file)
    s.set("thumbnail_size", 256)
    assert s.get("thumbnail_size") == 256


def test_set_dotted_existing(settings_file):
    s = JsonSettings(settings_file)
    s.set("sources.iphone", "/new/path")
    assert s.get("sources.iphone") == "/new/path"


def test_set_creates_intermediate_dicts(settings_file):
    s = JsonSettings(settings_file)
    s.set("ui.locale", "en-US")
    assert s.get("ui.locale") == "en-US"


def test_set_deep_new_key(settings_file):
    s = JsonSettings(settings_file)
    s.set("a.b.c.d", 42)
    assert s.get("a.b.c.d") == 42


def test_save_persists_to_disk(settings_file):
    s = JsonSettings(settings_file)
    s.set("sources.jdrive", "/j/photos")
    s.save()

    reloaded = json.loads(settings_file.read_text(encoding="utf-8"))
    assert reloaded["sources"]["jdrive"] == "/j/photos"


def test_save_preserves_existing_keys(settings_file):
    s = JsonSettings(settings_file)
    s.set("sources.output", "manifest.sqlite")
    s.save()

    reloaded = json.loads(settings_file.read_text(encoding="utf-8"))
    assert reloaded["thumbnail_size"] == 512
    assert reloaded["sources"]["iphone"] == "/nas/iphone"


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        JsonSettings(Path("/does/not/exist/settings.json"))


def test_malformed_json_raises(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        JsonSettings(bad)
