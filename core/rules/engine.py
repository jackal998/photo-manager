from __future__ import annotations

from typing import Iterable, Any

from core.models import PhotoGroup


class RuleEngine:
    def execute(self, groups: Iterable[PhotoGroup], rule: dict) -> Any:
        raise NotImplementedError("Rule engine will be implemented in later milestones.")
