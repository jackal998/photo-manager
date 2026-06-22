"""FastAPI application factory for the photo-manager web API.

Phase 1 scope: SCAN API (POST /api/scan + SSE + cancel) + GET /api/health
              + GET /api/image (Phase 1b).
Phase 3 scope: SPA static mount (frontend/dist) served from "/".
"""

from __future__ import annotations

import asyncio
import atexit
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.web.routes.action import router as action_router
from app.web.routes.execute import router as execute_router
from app.web.routes.fs import router as fs_router
from app.web.routes.health import router as health_router
from app.web.routes.i18n import router as i18n_router
from app.web.routes.image import router as image_router
from app.web.routes.review import router as review_router
from app.web.routes.scan import router as scan_router
from app.web.routes.settings import router as settings_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup init + graceful shutdown.

    Startup:
    - Constructs a shared ImageService and stores it on app.state.
    - Unregisters the atexit drain for _drain_wic_executor so the Qt
      process path's safety-net remains in place for the Qt app while
      the FastAPI process owns its own explicit drain on shutdown.
    - Sets app.state.allowed_roots = [] (secure-by-default; Phase 2
      wires in a manifest-backed root set).

    Shutdown (the finally block after yield):
    - Requests cancel on all running scan tasks and joins their threads
      (short timeout) so the SSE bus never calls call_soon_threadsafe
      on a closed loop. The P1a try/except is the safety net; this is
      the clean primary shutdown path.
    - Drains the WIC executor (CoUninitialize + shutdown).
    """
    import infrastructure.image_service as _img_svc_mod

    # Startup ----------------------------------------------------------------
    image_service = _img_svc_mod.ImageService()
    app.state.image_service = image_service
    app.state.allowed_roots = []
    # asyncio.Lock for POST /api/execute — prevents concurrent destructive
    # operations from racing on the same manifest.  Created during startup
    # (not at import time) so it is always bound to the running event loop.
    #
    # SINGLE-WORKER REQUIREMENT: asyncio.Lock is per-process.  If the app
    # were run with multiple uvicorn workers (workers > 1), each worker
    # process would have its own independent lock and concurrent executes
    # across processes would NOT be serialised.  This app MUST run with a
    # single worker (uvicorn default; no --workers flag).  Do not add a
    # workers= argument to uvicorn.run() below unless it is kept at 1.
    app.state.execute_lock = asyncio.Lock()

    # Prevent double-drain: we own the drain explicitly in the finally block.
    # The atexit.register in image_service.py is the safety net for the Qt
    # process path; we remove it here so a clean FastAPI shutdown doesn't
    # call it twice.
    atexit.unregister(_img_svc_mod._drain_wic_executor)

    try:
        yield

    finally:
        # Shutdown: graceful scan cancel + join ---------------------------------
        from app.web.registry import registry

        with registry._lock:
            running = [
                t for t in registry._tasks.values() if t.status == "running"
            ]

        for task in running:
            task.cancel_token.request()

        for task in running:
            if task.thread is not None and task.thread.is_alive():
                task.thread.join(timeout=5.0)

        # Drain the WIC executor (CoUninitialize + shutdown).
        _img_svc_mod._drain_wic_executor()


_DEFAULT_FRONTEND_DIST = (
    Path(__file__).resolve().parents[2] / "frontend" / "dist"
)


def create_app(frontend_dist: Optional[Path] = None) -> FastAPI:
    """Return a configured FastAPI application.

    Parameters
    ----------
    frontend_dist:
        Path to the built frontend ``dist/`` directory.  When *None* (the
        default) the factory resolves the path relative to this file:
        ``<repo-root>/frontend/dist``.  Pass an explicit path in tests to
        control whether the SPA static mount is active without relying on the
        presence of a real build.

        If the resolved dist has no ``index.html`` (the frontend has not been
        built) the SPA mount is silently skipped, so the factory never raises
        on a missing or half-built frontend.  API routers are always
        registered before the mount so ``/api/*`` routes always take precedence.
    """
    dist = frontend_dist if frontend_dist is not None else _DEFAULT_FRONTEND_DIST

    app = FastAPI(
        title="photo-manager web API",
        version="0.1.0",
        lifespan=_lifespan,
    )

    # CORS is enabled only in dev mode so the Vite dev server (port 5173) can
    # reach the API (port 8765).  Production is same-origin; no CORS needed.
    if os.environ.get("PHOTO_MANAGER_DEV_MODE") == "1":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=False,
        )

    # API routers are registered FIRST so /api/* always wins over the SPA mount.
    app.include_router(health_router)
    app.include_router(scan_router)
    app.include_router(image_router)
    app.include_router(review_router)
    app.include_router(settings_router)
    app.include_router(fs_router)
    app.include_router(execute_router)
    app.include_router(action_router)
    app.include_router(i18n_router)

    # SPA static mount — only when a built dist with an index.html exists.
    # Guarded on index.html (not just the directory) so a half-built or empty
    # dist never mounts a non-servable StaticFiles (html=True needs index.html).
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app


if __name__ == "__main__":  # pragma: no cover - script entry; uvicorn.run needs a live server
    import uvicorn

    uvicorn.run(
        "app.web.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        reload=False,
    )
