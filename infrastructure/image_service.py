from __future__ import annotations

from typing import Any

from loguru import logger

from core.services.interfaces import IImageService


class ImageService(IImageService):
    def get_thumbnail(self, path: str, size: int) -> Any:
        # TODO: Implement Windows Shell/WIC thumbnail retrieval with disk+memory cache
        logger.debug("get_thumbnail placeholder for {} size {}", path, size)
        return None

    def get_preview(self, path: str, max_side: int) -> Any:
        # TODO: Implement larger preview retrieval
        logger.debug("get_preview placeholder for {} size {}", path, max_side)
        return None
