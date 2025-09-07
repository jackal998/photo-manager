from __future__ import annotations

from typing import Iterable

from loguru import logger
from send2trash import send2trash

from core.models import PhotoRecord
from core.services.interfaces import DeleteResult, IDeleteService


class DeleteService(IDeleteService):
    def delete_to_recycle(self, paths: list[str]) -> DeleteResult:
        success: list[str] = []
        failed: list[tuple[str, str]] = []
        for p in paths:
            try:
                send2trash(p)
                success.append(p)
            except Exception as ex:
                logger.error("Delete failed for {}: {}", p, ex)
                failed.append((p, str(ex)))
        return DeleteResult(success_paths=success, failed=failed)
