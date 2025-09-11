from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class DeleteResult:
    success_paths: list[str]
    failed: list[tuple[str, str]]  # (path, reason)
    log_path: Optional[str] = None


@dataclass
class DeletePlanGroupSummary:
    group_number: int
    selected_count: int
    total_count: int
    is_full_delete: bool


@dataclass
class DeletePlan:
    # Paths chosen for deletion (already filtered to skip locked)
    delete_paths: list[str]
    # Group-level summaries for confirmation UI
    group_summaries: List[DeletePlanGroupSummary]
