from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from app.viewmodels.photo_vm import PhotoVM


@dataclass
class GroupVM:
    group_number: int
    items: List[PhotoVM] = field(default_factory=list)
    is_expanded: bool = False
