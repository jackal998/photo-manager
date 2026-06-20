"""ScanConfig — picklable plain-data bag for the scan pipeline.

Keeping the config as a plain dataclass (no Qt, no PySide6) means it
can cross a ProcessPoolExecutor boundary in Phase 1 without patching.
ScanWorker.__init__ builds one and passes it straight to run_pipeline().
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScanConfig:
    """All inputs the scan pipeline needs, fully resolved at construction.

    Field meanings mirror ScanWorker.__init__ parameters; see that
    docstring for per-field rationale.

    ``exif_workers`` is clamped by ``__post_init__`` to
    ``max(1, min(4, cpu_count // 2))`` so a direct ``ScanConfig(exif_workers=64)``
    from the first web-API caller doesn't spawn 64 ExiftoolProcess instances.
    ScanWorker.__init__ clamps before constructing ScanConfig — the dataclass
    clamp is idempotent when the value is already in-range.
    """

    # Source roots — label → resolved Path.
    sources: dict[str, Path]
    # Destination for the SQLite manifest.
    output_path: Path
    # Per-source walk depth: {label: False} = flat, missing/True = recursive.
    recursive_map: dict[str, bool] = field(default_factory=dict)
    # Per-source tie-break priority for classify(); None = auto-inferred.
    source_priority: dict[str, int] | None = None

    # Duplicate-detection thresholds (passed through to classify()).
    threshold: int = 10
    mean_color_threshold: int = 30
    dhash_threshold: int = 10

    # Walker per-source file-count cap; None = unlimited.
    limit: int | None = None

    # Hash-stage executor sizing.
    workers: int = 4
    # Number of parallel ExiftoolProcess instances.  Clamped by __post_init__
    # to [1, min(4, cpu_count // 2)] — the dataclass owns the enforcement so
    # any caller (Qt dialog, web API) gets the same safety guarantee.
    exif_workers: int = 2

    # Executor selector: "thread" | "process" | "auto".
    hash_pool: str = "thread"
    # Pre-measured calibration rates from the dialog's fingerprint cache.
    # When present and hash_pool=="auto", re-projection skips re-measurement.
    hash_pool_rates: dict | None = None

    # Auto-select: promote top-scored keeper in each dup group.
    auto_select_enabled: bool = False
    # Auto-select aggressive: also mark non-keepers delete.
    auto_select_aggressive_delete: bool = False

    # Read-knee autotune (#551): ramp per-device read concurrency to a measured knee.
    autotune_read_knee: bool = False
    # Pre-cached per-device knees {device_key: {"knee": int, "recipe": str}}.
    autotune_knees: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Clamp exif_workers at the dataclass boundary so every caller —
        # ScanWorker, web API, test — gets the same safety guarantee.
        # The cap mirrors ScanWorker.__init__: max(1, min(4, cpu // 2)).
        cpu = os.cpu_count() or 4
        cap = max(1, min(4, cpu // 2))
        self.exif_workers = max(1, min(self.exif_workers, cap))
