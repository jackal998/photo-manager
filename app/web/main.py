"""FastAPI application factory for the photo-manager web API.

Phase 1 scope: SCAN API (POST /api/scan + SSE + cancel) + GET /api/health
              + GET /api/image (Phase 1b).
"""

from __future__ import annotations

import atexit
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.web.routes.health import router as health_router
from app.web.routes.image import router as image_router
from app.web.routes.scan import router as scan_router


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


def create_app() -> FastAPI:
    """Return a configured FastAPI application."""
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

    app.include_router(health_router)
    app.include_router(scan_router)
    app.include_router(image_router)
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
