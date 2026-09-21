"""Serving-contract tests for the SPA static mount in app/web/main.py.

These use FastAPI's TestClient (httpx under the hood) — NO Playwright,
NO live server.  They verify two real invariants:

1. When the frontend dist directory exists with an index.html, GET "/"
   returns 200 and the HTML content from that file; GET "/api/health"
   still returns 200 (API routers take precedence over the SPA mount).

2. When the dist directory is absent (or empty), no "spa" mount is
   registered; GET "/api/health" still returns 200.

These tests clear the 70%-per-file coverage floor for the new lines
added to main.py in Phase 3 (the ``frontend_dist`` parameter, the
``dist.exists()`` guard, and the ``StaticFiles`` mount).  They are
genuine contract tests, not metric padding — each assertion corresponds
to a real serving behaviour the user would experience.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dist(tmp_path: Path, html: str = "<html>test</html>") -> Path:
    """Create a minimal frontend dist directory under *tmp_path*."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(html, encoding="utf-8")
    return dist


def _route_names(app: object) -> list[str]:
    """Return the list of named route names registered on *app*."""
    return [r.name for r in app.routes if hasattr(r, "name") and r.name is not None]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 1.  Dist directory exists — SPA is mounted; API precedence preserved
# ---------------------------------------------------------------------------


class TestStaticMountPresent:
    """create_app(frontend_dist=<real dist>) mounts the SPA and serves HTML."""

    def test_get_root_returns_200_with_spa_html(self, tmp_path: Path) -> None:
        """GET "/" returns 200 and the fake index.html content."""
        from app.web.main import create_app

        html_body = "<html><body>fake-spa</body></html>"
        dist = _make_dist(tmp_path, html_body)

        app = create_app(frontend_dist=dist)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")

        assert response.status_code == 200
        assert "fake-spa" in response.text

    def test_api_health_wins_over_spa_mount(self, tmp_path: Path) -> None:
        """GET /api/health returns 200 even when the SPA mount is active.

        API routers are registered before the StaticFiles mount, so /api/*
        always takes precedence.  If the mount shadow were to win here the
        health check would return a 404 from StaticFiles (no api/health file
        in the fake dist), which would break monitoring and Playwright probes.
        """
        from app.web.main import create_app

        dist = _make_dist(tmp_path)

        app = create_app(frontend_dist=dist)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_spa_mount_named_spa_in_routes(self, tmp_path: Path) -> None:
        """The SPA mount registers under the name 'spa' so callers can detect it."""
        from app.web.main import create_app

        dist = _make_dist(tmp_path)
        app = create_app(frontend_dist=dist)

        assert "spa" in _route_names(app), (
            "Expected a route named 'spa' when frontend_dist exists; "
            f"found: {_route_names(app)}"
        )


# ---------------------------------------------------------------------------
# 2.  Dist directory absent — no SPA mount; API still works
# ---------------------------------------------------------------------------


class TestStaticMountAbsent:
    """create_app(frontend_dist=<nonexistent>) skips the SPA mount silently."""

    def test_no_spa_route_when_dist_missing(self, tmp_path: Path) -> None:
        """No 'spa' route is registered when the dist directory does not exist."""
        from app.web.main import create_app

        missing = tmp_path / "nonexistent_dist"
        # Do NOT create it — we want to test the guard path.
        app = create_app(frontend_dist=missing)

        assert "spa" not in _route_names(app), (
            "Expected NO 'spa' route when frontend_dist is absent; "
            f"found: {_route_names(app)}"
        )

    def test_api_health_works_without_spa(self, tmp_path: Path) -> None:
        """GET /api/health returns 200 even without a frontend build present."""
        from app.web.main import create_app

        missing = tmp_path / "nonexistent_dist"
        app = create_app(frontend_dist=missing)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_no_spa_route_when_dist_empty(self, tmp_path: Path) -> None:
        """No 'spa' route when the dist directory exists but has no index.html.

        StaticFiles(html=True) needs an index.html to serve.  The mount guard
        is on ``(dist / 'index.html').exists()`` (not just the directory), so a
        CI step that creates the directory but whose build fails produces NO
        'spa' route — never a non-servable lazy-404 mount.
        """
        from app.web.main import create_app

        empty_dist = tmp_path / "empty_dist"
        empty_dist.mkdir()
        # No index.html — not a servable build, so create_app must NOT mount it.
        app = create_app(frontend_dist=empty_dist)

        assert "spa" not in _route_names(app), (
            "Expected NO 'spa' route when dist has no index.html; "
            f"found: {_route_names(app)}"
        )
        # Health still works regardless (API precedence is preserved).
        with TestClient(app, raise_server_exceptions=False) as client:
            health = client.get("/api/health")

        assert health.status_code == 200
