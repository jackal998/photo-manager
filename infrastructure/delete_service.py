"""Recycle-bin delete primitive.

``DeleteService.delete_to_recycle`` sends files to the recycle bin and reports
per-path results. Callers own the lock and outcome decisions: the Execute path
goes through ``core.app_service.execute_service``.
"""

from __future__ import annotations

from collections.abc import Callable
import os

from loguru import logger
from send2trash import send2trash

from core.services.interfaces import DeleteResult
from infrastructure.winerror_reasons import decode_winerror


def _decoded_reason(fallback: str, *exceptions: BaseException) -> str:
    """Return the first decodable winerror reason among ``exceptions``.

    Checked in the given order (callers pass the most-recent/most-authoritative
    attempt first) so the final failure mode wins when several fallback
    attempts raised different errors. Falls back to ``fallback`` (the
    pre-#742 message) when none of the exceptions carry a known winerror —
    additive only, never changes behaviour for an unmapped/non-OSError case.
    """
    for exc in exceptions:
        if isinstance(exc, OSError):
            decoded = decode_winerror(exc)
            if decoded is not None:
                return decoded
    return fallback


class DeleteService:
    """Coordinates delete operations and audit logging."""

    def __init__(self) -> None:
        self._handle_releaser: Callable[[], None] | None = None

    def set_handle_releaser(self, releaser: Callable[[], None] | None) -> None:
        """Register a callable to release UI-held file handles before deletion."""
        self._handle_releaser = releaser

    def delete_to_recycle(self, paths: list[str]) -> DeleteResult:
        """Send files to recycle bin and report per-path results."""
        # Release any UI-held file handles (e.g., preview/video) before deleting
        try:
            if self._handle_releaser is not None:
                self._handle_releaser()
        except (AttributeError, RuntimeError, OSError):
            # Best-effort; do not block delete - UI cleanup can fail for various reasons
            pass

        success: list[str] = []
        failed: list[tuple[str, str]] = []
        for p in paths:
            try:
                # Normalize and validate path
                normalized_path = os.path.normpath(p)

                # Check if file exists and is accessible
                if not os.path.exists(normalized_path):
                    logger.error("File does not exist: {}", normalized_path)
                    failed.append((p, "File does not exist"))
                    continue

                # Try different approaches for non-ASCII paths
                # Method 1: Try with normalized path
                try:
                    send2trash(normalized_path)
                    success.append(p)
                except (UnicodeEncodeError, OSError) as ex:
                    logger.warning(
                        "Failed to delete with normalized path {}: {}", normalized_path, ex
                    )
                    # Method 2: Try with original path
                    try:
                        send2trash(p)
                        success.append(p)
                    except (UnicodeEncodeError, OSError) as ex2:
                        logger.warning("Failed to delete with original path {}: {}", p, ex2)
                        # Method 3: Try with absolute path
                        try:
                            abs_path = os.path.abspath(p)
                            send2trash(abs_path)
                            success.append(p)
                        except (UnicodeEncodeError, OSError) as ex3:
                            logger.error(
                                "All delete methods failed for {}: {} / {} / {}",
                                p,
                                ex,
                                ex2,
                                ex3,
                            )
                            failed.append(
                                (
                                    p,
                                    _decoded_reason(
                                        f"Multiple delete failures: {str(ex)}, {str(ex2)}, {str(ex3)}",
                                        ex3,
                                        ex2,
                                        ex,
                                    ),
                                )
                            )
                    except RuntimeError as ex2:
                        logger.error("Unexpected error with original path {}: {}", p, ex2)
                        failed.append((p, f"Unexpected error: {str(ex2)}"))
                except RuntimeError as ex:
                    logger.error(
                        "Unexpected error with normalized path {}: {}", normalized_path, ex
                    )
                    failed.append((p, f"Unexpected error: {str(ex)}"))

            except (UnicodeEncodeError, OSError, RuntimeError) as ex:
                logger.error("Unexpected error deleting {}: {}", p, ex)
                failed.append((p, _decoded_reason(f"Unexpected error: {str(ex)}", ex)))
        return DeleteResult(success_paths=success, failed=failed)
