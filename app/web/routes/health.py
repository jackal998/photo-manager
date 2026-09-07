"""GET /api/health — liveness probe."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
