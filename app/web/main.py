"""FastAPI application factory for the photo-manager web API.

Phase 1 scope: SCAN API (POST /api/scan + SSE + cancel) + GET /api/health.

Lifespan placeholder — full STA-executor drain migration is P1b; this
scaffolds the lifespan context for that future wiring.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.web.routes.health import router as health_router
from app.web.routes.scan import router as scan_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context.

    P1b placeholder: the STA-executor drain (graceful shutdown of the
    scan thread pool) will be wired here in Phase 1b.  For now we just
    yield to let the app run and let daemon threads exit with the process.
    """
    yield


def create_app() -> FastAPI:
    """Return a configured FastAPI application."""
    app = FastAPI(
        title="photo-manager web API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.include_router(health_router)
    app.include_router(scan_router)
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
