"""App-data root isolation (#968).

Every resolver of the per-user ``PhotoManager`` folder must honour the
``PHOTO_MANAGER_DATA_DIR`` override. Each test here points the real default
profile (``USERPROFILE``/``HOME`` and ``LOCALAPPDATA``) at a decoy tmp dir,
then asserts that a write which would normally land in the profile lands in
the override dir instead, and that nothing appears under the decoy profile.
Before the override existed these tests failed on the decoy assertion.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

_OVERRIDE_ENV = "PHOTO_MANAGER_DATA_DIR"


@pytest.fixture
def decoy_profile(tmp_path, monkeypatch):
    """Point the real default profile at a decoy and return (home, local, override)."""
    home = tmp_path / "decoy-home"
    local = tmp_path / "decoy-localappdata"
    override = tmp_path / "override-data"
    home.mkdir()
    local.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv(_OVERRIDE_ENV, str(override))
    return home, local, override


def test_unset_override_falls_back_to_profile_default(tmp_path, monkeypatch):
    from infrastructure.app_data import app_data_root

    home = tmp_path / "decoy-home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PHOTO_MANAGER_DATA_DIR", raising=False)

    assert app_data_root() == home / "AppData" / "Local" / "PhotoManager"


def test_ensure_isolated_app_data_uses_fresh_temp_dir_when_unset(monkeypatch):
    import os

    from infrastructure.app_data import ensure_isolated_app_data

    monkeypatch.delenv("PHOTO_MANAGER_DATA_DIR", raising=False)

    active = ensure_isolated_app_data()

    assert os.environ["PHOTO_MANAGER_DATA_DIR"] == active
    assert os.path.isdir(active)


def test_ensure_isolated_app_data_respects_explicit_override(tmp_path, monkeypatch):
    import os

    from infrastructure.app_data import ensure_isolated_app_data

    explicit = str(tmp_path / "explicit")
    monkeypatch.setenv("PHOTO_MANAGER_DATA_DIR", explicit)

    assert ensure_isolated_app_data() == explicit
    assert os.environ["PHOTO_MANAGER_DATA_DIR"] == explicit


def test_default_transcode_cache_lands_under_override(decoy_profile):
    from infrastructure.transcode_service import TRANSCODE_RECIPE_VERSION, TranscodeService

    home, local, override = decoy_profile

    service = TranscodeService()

    assert service._base_dir == override / "transcodes" / f"v{TRANSCODE_RECIPE_VERSION}"
    _assert_decoy_untouched(home, local)


def _unpatched_logging_module():
    """Load a private copy of infrastructure/logging.py.

    The #948 conftest fixture replaces ``get_delete_log_directory`` on the live
    module, so the resolver under test is read from a fresh copy instead.
    """
    import importlib.util

    import infrastructure.logging as live

    spec = importlib.util.spec_from_file_location("_logging_unpatched", live.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_delete_log_dir_lands_under_override(decoy_profile):
    home, local, override = decoy_profile

    resolved = _unpatched_logging_module().get_delete_log_directory()

    assert resolved == str(override / "delete_logs")
    _assert_decoy_untouched(home, local)


def _assert_decoy_untouched(*roots) -> None:
    for root in roots:
        leaked = [p for p in root.rglob("*")]
        assert not leaked, f"write escaped the override into {root}: {leaked[:5]}"


def test_default_thumb_cache_lands_under_override(decoy_profile):
    from infrastructure.image_service import ImageService

    home, local, override = decoy_profile

    ImageService()

    assert (override / "thumbs").is_dir(), "thumb cache ignored PHOTO_MANAGER_DATA_DIR"
    _assert_decoy_untouched(home, local)


def test_default_init_logging_lands_under_override(decoy_profile, monkeypatch):
    import infrastructure.logging as log_mod

    home, local, override = decoy_profile
    # Stub the loguru sink: this test only proves where the log dir is created.
    monkeypatch.setattr(
        log_mod,
        "logger",
        SimpleNamespace(remove=lambda: None, add=lambda *a, **k: None),
    )

    log_mod.init_logging()

    assert (override / "logs").is_dir(), "log dir ignored PHOTO_MANAGER_DATA_DIR"
    _assert_decoy_untouched(home, local)
