"""Tests for the env-gated CORS middleware in app/web/main.py.

Two branches:
  - PHOTO_MANAGER_DEV_MODE=1  → CORS headers present for the Vite dev origins.
  - env var absent             → CORS header must not appear (same-origin prod).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def dev_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PHOTO_MANAGER_DEV_MODE", "1")
    from app.web.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def prod_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("PHOTO_MANAGER_DEV_MODE", raising=False)
    from app.web.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_cors_header_present_in_dev_mode(dev_client: TestClient) -> None:
    """Dev-mode CORS: Vite origin is echoed back in the response header."""
    response = dev_client.get(
        "/api/health", headers={"Origin": "http://localhost:5173"}
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_in_dev_mode(dev_client: TestClient) -> None:
    """Dev-mode CORS: OPTIONS preflight returns the Vite origin."""
    response = dev_client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_header_absent_in_prod(prod_client: TestClient) -> None:
    """Prod mode: no CORS middleware means no access-control-allow-origin header."""
    response = prod_client.get(
        "/api/health", headers={"Origin": "http://localhost:5173"}
    )
    assert "access-control-allow-origin" not in response.headers
