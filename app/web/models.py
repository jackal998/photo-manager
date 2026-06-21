"""HTTP boundary models and in-process task state for the web scan API."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from core.app_service.cancel_token import _CancelToken
from core.app_service.dtos import ScanConfig


# ---------------------------------------------------------------------------
# Phase 2B — review / decision / lock / settings / fs-browse models
# ---------------------------------------------------------------------------


class DecisionItem(BaseModel):
    """One file-path → decision mapping in a batch decision update."""

    file_path: str
    decision: str


class DecisionUpdate(BaseModel):
    """Body for PATCH /api/decision."""

    manifest_path: str
    decisions: list[DecisionItem]


class LockItem(BaseModel):
    """One file-path → locked-bool mapping in a batch lock update."""

    file_path: str
    locked: bool


class LockUpdate(BaseModel):
    """Body for PATCH /api/lock."""

    manifest_path: str
    locks: list[LockItem]


class SettingsUpdate(BaseModel):
    """Body for PATCH /api/settings — dotted keys and their new values."""

    updates: dict[str, Any]


class WebScanRequest(BaseModel):
    """HTTP boundary model for POST /api/scan.

    All path values are plain strings here; ``.to_config()`` wraps them
    in Path objects and builds the ScanConfig dataclass.  The dataclass
    __post_init__ clamps exif_workers — no re-clamping needed here.
    """

    # Required: at least one source label → directory path.
    sources: dict[str, str]
    output_path: str

    # Optional pipeline tuning fields — defaults mirror ScanConfig.
    recursive_map: dict[str, bool] = {}
    source_priority: dict[str, int] | None = None
    threshold: int = 10
    mean_color_threshold: int = 30
    dhash_threshold: int = 10
    limit: int | None = None
    workers: int = 4
    exif_workers: int = 2
    hash_pool: str = "thread"
    hash_pool_rates: dict | None = None
    auto_select_enabled: bool = False
    auto_select_aggressive_delete: bool = False
    autotune_read_knee: bool = False
    autotune_knees: dict = {}

    def to_config(self) -> ScanConfig:
        """Build a ScanConfig from this HTTP request.

        Path()-wraps sources values and output_path; lets ScanConfig
        __post_init__ clamp exif_workers.
        """
        return ScanConfig(
            sources={label: Path(p) for label, p in self.sources.items()},
            output_path=Path(self.output_path),
            recursive_map=dict(self.recursive_map),
            source_priority=dict(self.source_priority) if self.source_priority else None,
            threshold=self.threshold,
            mean_color_threshold=self.mean_color_threshold,
            dhash_threshold=self.dhash_threshold,
            limit=self.limit,
            workers=self.workers,
            exif_workers=self.exif_workers,
            hash_pool=self.hash_pool,
            hash_pool_rates=dict(self.hash_pool_rates) if self.hash_pool_rates else None,
            auto_select_enabled=self.auto_select_enabled,
            auto_select_aggressive_delete=self.auto_select_aggressive_delete,
            autotune_read_knee=self.autotune_read_knee,
            autotune_knees=dict(self.autotune_knees),
        )


@dataclass
class ScanTask:
    """In-process state for one scan run.

    Two SEPARATE locks guard two SEPARATE mutable structures:
    - ``buffer_lock`` guards ``event_buffer`` + ``last_event_id``
    - ``subscriber_lock`` guards ``subscriber_queues``

    The deque is only ever iterated as ``list(event_buffer)`` while
    holding buffer_lock — never iterated live.

    NOTE: ``cancel_token`` is a _CancelToken (cooperative cancel via
    .request()/.is_set()), not a bare threading.Event.  The scan thread
    is a threading.Thread (not a Process) because SseScanBus holds an
    asyncio.Queue + loop and is not picklable.  Phase 2+ may migrate to
    a subprocess via a serialisable bus transport.
    """

    task_id: str
    status: str  # 'running' | 'finished' | 'failed' | 'cancelled'
    cancel_token: _CancelToken
    event_buffer: deque = field(default_factory=lambda: deque(maxlen=500))
    last_event_id: int = 0
    terminal_event: dict[str, Any] | None = None
    # Set by SseScanBus._set_terminal() the moment the task becomes terminal;
    # used by the registry reaper to enforce the TTL (float('inf') = not yet set).
    _terminal_at: float | None = field(default=None)
    buffer_lock: threading.Lock = field(default_factory=threading.Lock)
    subscriber_lock: threading.Lock = field(default_factory=threading.Lock)
    subscriber_queues: list[asyncio.Queue] = field(default_factory=list)
    loop: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
