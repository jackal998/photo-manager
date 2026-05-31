"""Deletion planning and execution service.

Provides a high-level API to plan deletions across photo groups, skip locked
records, and execute deletes by moving files to the recycle bin, while writing
an audit CSV log.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os

from loguru import logger
from send2trash import send2trash

from core.models import PhotoGroup
from core.services.interfaces import DeletePlan, DeletePlanGroupSummary, DeleteResult
from infrastructure.logging import write_delete_log


class DeleteService:
    """Coordinates delete operations and audit logging."""

    def __init__(self) -> None:
        self._handle_releaser: Callable[[], None] | None = None

    def set_handle_releaser(self, releaser: Callable[[], None] | None) -> None:
        """Register a callable to release UI-held file handles before deletion."""
        self._handle_releaser = releaser

    def plan_delete(self, groups: Iterable[PhotoGroup], selected_paths: list[str]) -> DeletePlan:
        """Compute a delete plan from selected paths.

        The caller is responsible for ensuring ``selected_paths`` does
        not include any locked rows the user did not explicitly opt
        into deleting — under photo-manager#182 every UI path that
        could pick locked rows routes the choice through
        ``LockedRowsConfirmDialog`` first. A defensive assertion
        below catches callers that forget; it logs the violating
        paths and excludes them from the plan rather than silently
        deleting locked files. (Previously this method silently
        filtered locked paths at line 50; the silent filter was the
        very asymmetry #182 retires.)
        """
        selected_set = set(selected_paths)
        per_group_selected: dict[int, int] = {}
        per_group_total: dict[int, int] = {}
        leaked_locked: list[str] = []
        for g in groups:
            per_group_total[g.group_number] = len(g.items)
            sel_count = 0
            for r in g.items:
                if r.file_path in selected_set:
                    sel_count += 1
                    if r.is_locked:
                        leaked_locked.append(r.file_path)
            per_group_selected[g.group_number] = sel_count

        if leaked_locked:
            logger.warning(
                "plan_delete: {} locked path(s) reached the planner "
                "without going through the lock-confirm dialog — "
                "excluding from plan. First few: {}",
                len(leaked_locked),
                leaked_locked[:3],
            )
        leaked_set = set(leaked_locked)
        delete_paths: list[str] = [
            p for p in selected_paths if p not in leaked_set
        ]

        summaries: list[DeletePlanGroupSummary] = []
        for g in groups:
            sel = per_group_selected.get(g.group_number, 0)
            tot = per_group_total.get(g.group_number, 0)
            summaries.append(
                DeletePlanGroupSummary(
                    group_number=g.group_number,
                    selected_count=sel,
                    total_count=tot,
                    is_full_delete=(tot > 0 and sel == tot),
                )
            )

        return DeletePlan(delete_paths=delete_paths, group_summaries=summaries)

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
                                    f"Multiple delete failures: {str(ex)}, {str(ex2)}, {str(ex3)}",
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
                failed.append((p, f"Unexpected error: {str(ex)}"))
        return DeleteResult(success_paths=success, failed=failed)

    def execute_delete(
        self, groups: Iterable[PhotoGroup], plan: DeletePlan, log_dir: str | None = None
    ) -> DeleteResult:
        """Execute the delete plan and write an audit CSV log.

        Args:
            groups: Groups used to map file path to group in the log.
            plan: The delete plan produced by `plan_delete`.
            log_dir: Optional directory to write the audit log; defaults to
                `%LOCALAPPDATA%/PhotoManager/delete_logs`.
        """
        result = self.delete_to_recycle(plan.delete_paths)
        # Build path -> group mapping, then delegate to the shared audit
        # writer so this path and the Execute Action dialog (#505) emit
        # one consistent delete_<ts>.csv format.
        path_to_group: dict[str, int] = {}
        for g in groups:
            for r in g.items:
                path_to_group[r.file_path] = g.group_number
        rows: list[tuple[int, str, bool, str]] = [
            (path_to_group.get(p, 0), p, True, "") for p in result.success_paths
        ]
        rows += [
            (path_to_group.get(p, 0), p, False, reason) for p, reason in result.failed
        ]
        log_path = write_delete_log(rows, log_dir)
        if log_path:
            result.log_path = log_path
        return result
