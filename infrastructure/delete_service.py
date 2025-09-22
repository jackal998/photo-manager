"""Deletion planning and execution service.

Provides a high-level API to plan deletions across photo groups, skip locked
records, and execute deletes by moving files to the recycle bin, while writing
an audit CSV log.
"""

from __future__ import annotations

from collections.abc import Iterable
import csv
from datetime import datetime
import os
from pathlib import Path

from loguru import logger
from send2trash import send2trash

from core.models import PhotoGroup
from core.services.interfaces import DeletePlan, DeletePlanGroupSummary, DeleteResult


class DeleteService:
    """Coordinates delete operations and audit logging."""

    def plan_delete(self, groups: Iterable[PhotoGroup], selected_paths: list[str]) -> DeletePlan:
        """Compute a delete plan from selected paths, skipping locked items."""
        # Build a quick lookup of path -> (group, record, locked)
        selected_set = set(selected_paths)
        per_group_selected: dict[int, int] = {}
        per_group_total: dict[int, int] = {}
        lock_map: dict[str, bool] = {}
        for g in groups:
            per_group_total[g.group_number] = len(g.items)
            sel_count = 0
            for r in g.items:
                lock_map[r.file_path] = bool(r.is_locked)
                if r.file_path in selected_set:
                    sel_count += 1
            per_group_selected[g.group_number] = sel_count

        # Skip locked in final delete list
        delete_paths: list[str] = [p for p in selected_paths if not lock_map.get(p, False)]

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
        # Auto-write CSV log under %LOCALAPPDATA%/PhotoManager/delete_logs unless overridden
        try:
            base_dir = (
                os.path.expandvars(log_dir)
                if log_dir
                else os.path.join(
                    os.path.expandvars("%LOCALAPPDATA%"), "PhotoManager", "delete_logs"
                )
            )
            Path(base_dir).mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = os.path.join(base_dir, f"delete_{ts}.csv")
            with open(log_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["GroupNumber", "FilePath", "Success", "Reason"])
                # Build path -> group mapping for summary
                path_to_group: dict[str, int] = {}
                for g in groups:
                    for r in g.items:
                        path_to_group[r.file_path] = g.group_number
                for p in result.success_paths:
                    writer.writerow([path_to_group.get(p, 0), p, 1, ""])
                for p, reason in result.failed:
                    writer.writerow([path_to_group.get(p, 0), p, 0, reason])
            result.log_path = log_path
            logger.info(
                "Delete log written: {} ({} success, {} failed)",
                log_path,
                len(result.success_paths),
                len(result.failed),
            )
        except (OSError, ValueError) as ex:
            logger.error("Write delete log failed: {}", ex)
        return result
