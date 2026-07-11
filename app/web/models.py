"""HTTP boundary models and in-process task state for the web scan API."""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from core.app_service.cancel_token import _CancelToken
from core.app_service.dtos import ScanConfig
from core.app_service.scan_runner import hash_pool_fingerprint


# ---------------------------------------------------------------------------
# Phase 2B — review / decision / lock / settings / fs-browse models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 2C1 — file-mutating execute routes models
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    """Body for POST /api/execute."""

    manifest_path: str
    scope_paths: list[str] | None = None
    recycle: bool = True
    force_locked: bool = False


class RemoveRequest(BaseModel):
    """Body for POST /api/remove."""

    manifest_path: str
    file_paths: list[str]
    force_locked: bool = False


class PruneRequest(BaseModel):
    """Body for POST /api/prune."""

    manifest_path: str
    include_actioned: bool = False
    # Explicit-list prune (#686): when provided, prune EXACTLY these paths
    # (intersected with the current singletons, under-roots, unlocked) instead
    # of pruning by category. This mirrors the Qt desktop's
    # ``_apply_singleton_prune(to_prune)`` where the caller (the prune dialog /
    # lock-gate verdict) computes the precise set — e.g. "prune only this
    # Unlock&Applied locked singleton, keep the plain bucket". ``None`` keeps the
    # original category behaviour (all plain + optionally all actioned).
    paths: list[str] | None = None


class SaveRequest(BaseModel):
    """Body for POST /api/save."""

    manifest_path: str
    target_path: str | None = None


class RevealRequest(BaseModel):
    """Body for POST /api/reveal."""

    file_path: str


class ExecuteResult(BaseModel):
    """Response for POST /api/execute."""

    success_paths: list[str]
    failed: list[list[str]]   # list of [path, reason] pairs
    ignored: list[str]
    missing: list[str]
    log_path: str | None = None
    groups: list[Any]
    # Paths where the file was deleted from disk but the DB outcome write
    # failed.  The deletion succeeded (file is gone); the manifest row was
    # not updated.  These paths also appear in success_paths.
    db_write_failed: list[str] = []


class RemoveResult(BaseModel):
    """Response for POST /api/remove."""

    removed: int
    groups: list[Any]


class PruneResult(BaseModel):
    """Response for POST /api/prune."""

    pruned: list[str]
    locked_skipped: list[str]
    groups: list[Any]


class PruneCandidatesRequest(BaseModel):
    """Body for POST /api/prune/candidates."""

    manifest_path: str


class PruneCandidatesResult(BaseModel):
    """Response for POST /api/prune/candidates (#686).

    The current singletons classified into the three buckets the prune flow
    branches on. The web review view drops single-member groups, so the
    frontend asks the backend (which has the SQL) what singletons exist.
    """

    plain: list[str]
    actioned: list[str]
    locked: list[str]


class SaveResult(BaseModel):
    """Response for POST /api/save."""

    saved_to: str
    updated: int


class RevealResult(BaseModel):
    """Response for POST /api/reveal."""

    status: str


class DecisionItem(BaseModel):
    """One file-path → decision mapping in a batch decision update."""

    file_path: str
    decision: str


class DecisionUpdate(BaseModel):
    """Body for PATCH /api/decision."""

    manifest_path: str
    decisions: list[DecisionItem]
    force_locked: bool = False
    skip_locked: bool = False


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


class BulkDecideRequest(BaseModel):
    """Body for POST /api/action/bulk-decide."""

    manifest_path: str
    field: str
    pattern: str
    action: str
    force_locked: bool = False
    skip_locked: bool = False
    preview: bool = False


class BulkDecideResult(BaseModel):
    """Response for POST /api/action/bulk-decide."""

    matched: int
    affected_paths: list[str]
    action_applied: str
    groups: list[Any]


class ApplyBestCopyRequest(BaseModel):
    """Body for POST /api/action/apply-best-copy (#744)."""

    manifest_path: str
    group_number: int
    force_locked: bool = False
    skip_locked: bool = False


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
    # None = "server resolves from settings.json" (see resolved_with()); an
    # explicit value in the request is always honored untouched. This mirrors
    # the Qt ScanDialog default of "auto" hash-pool + settings-read exif
    # workers, rather than hardcoding a value at the HTTP boundary (#652).
    exif_workers: int | None = None
    hash_pool: str | None = None
    hash_pool_rates: dict | None = None
    auto_select_enabled: bool = False
    auto_select_aggressive_delete: bool = False
    autotune_read_knee: bool = False
    autotune_knees: dict = {}

    def resolved_with(self, settings) -> "WebScanRequest":
        """Return a NEW WebScanRequest with calibration fields read back from settings.

        Mirrors the Qt desktop's per-scan resolution (scan_dialog.py
        ``_resolve_hash_pool`` + the ``scan.exif_workers`` /
        ``scan.read_knee_cache`` reads in ``_start_scan``): the web scan path
        previously hardcoded ``hash_pool="thread"``, ``exif_workers=2``, and
        never read the hash-pool / read-knee calibration caches, so every web
        scan re-measured from scratch instead of reusing a prior scan's
        calibration (#652).

        Only fields left at their unset default (``None`` / empty dict) are
        resolved from ``settings``; any value explicitly supplied by the
        caller is returned unchanged. ``settings`` is any object exposing
        ``get`` (e.g. ``JsonSettings``).
        """
        hash_pool = self.hash_pool if self.hash_pool is not None else settings.get("scan.hash_pool", "auto")

        hash_pool_rates = self.hash_pool_rates
        if hash_pool_rates is None and hash_pool == "auto":
            fp = hash_pool_fingerprint(
                {label: Path(p) for label, p in self.sources.items()},
                dict(self.recursive_map),
                os.cpu_count() or 4,
            )
            cache = settings.get("scan.hash_pool_cache", {}) or {}
            hash_pool_rates = cache.get(fp)

        autotune_knees = self.autotune_knees or (settings.get("scan.read_knee_cache", {}) or {})

        exif_workers = self.exif_workers
        if exif_workers is None:
            exif_workers = settings.get("scan.exif_workers", 2)
            try:
                exif_workers = int(exif_workers)
            except (TypeError, ValueError):
                exif_workers = 2

        return self.model_copy(
            update={
                "hash_pool": hash_pool,
                "hash_pool_rates": hash_pool_rates,
                "autotune_knees": autotune_knees,
                "exif_workers": exif_workers,
            }
        )

    def to_config(self) -> ScanConfig:
        """Build a ScanConfig from this HTTP request.

        Path()-wraps sources values and output_path; lets ScanConfig
        __post_init__ clamp exif_workers. ``hash_pool``/``exif_workers``
        fall back to their Qt-parity defaults ("auto" / 2) when the request
        was never passed through ``resolved_with()`` (e.g. direct callers,
        unit tests constructing a WebScanRequest by hand).
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
            exif_workers=self.exif_workers if self.exif_workers is not None else 2,
            hash_pool=self.hash_pool if self.hash_pool is not None else "auto",
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
