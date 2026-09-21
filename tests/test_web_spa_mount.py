"""SPA static-mount behaviour of ``app.web.main.create_app`` (#772).

Dev/tests tolerate a missing ``frontend/dist`` (the API is usable without
the SPA, and unit tests must not depend on an npm build). A FROZEN build
(PyInstaller) must NOT tolerate it: a bundle without the SPA boots a
WebView2 window onto a dead page with no error anywhere, so the factory
fails loudly at startup instead.
"""
from __future__ import annotations

import sys

import pytest

from app.web.main import create_app


def test_missing_dist_is_tolerated_in_dev(tmp_path):
    app = create_app(frontend_dist=tmp_path / "nope")
    assert not any(getattr(r, "name", None) == "spa" for r in app.routes)


def test_missing_dist_fails_loud_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="Bundled frontend is missing"):
        create_app(frontend_dist=tmp_path / "nope")


def test_built_dist_mounts_spa(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>pm</title>")
    app = create_app(frontend_dist=tmp_path)
    assert any(getattr(r, "name", None) == "spa" for r in app.routes)
