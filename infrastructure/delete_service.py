from __future__ import annotations

from typing import Iterable, List, Optional

from loguru import logger
from send2trash import send2trash
from pathlib import Path
from datetime import datetime
import csv
import os

from core.models import PhotoRecord, PhotoGroup
from core.services.interfaces import DeleteResult, IDeleteService, DeletePlan, DeletePlanGroupSummary


class DeleteService(IDeleteService):
    def plan_delete(self, groups: Iterable[PhotoGroup], selected_paths: list[str]) -> DeletePlan:
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
        delete_paths: List[str] = [p for p in selected_paths if not lock_map.get(p, False)]

        summaries: List[DeletePlanGroupSummary] = []
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

    def execute_delete(self, groups: Iterable[PhotoGroup], plan: DeletePlan, log_dir: Optional[str] = None) -> DeleteResult:
        result = self.delete_to_recycle(plan.delete_paths)
        # Auto-write CSV log under %LOCALAPPDATA%/PhotoManager/delete_logs unless overridden
        try:
            base_dir = (
                os.path.expandvars(log_dir)
                if log_dir
                else os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "PhotoManager", "delete_logs")
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
            logger.info("Delete log written: {} ({} success, {} failed)", log_path, len(result.success_paths), len(result.failed))
        except Exception as ex:
            logger.error("Write delete log failed: {}", ex)
        return result
