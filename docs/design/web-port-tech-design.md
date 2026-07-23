# Photo-Manager Web-Port -- Tech Design (Keystone Contracts)

**Status:** DRAFT v0.2 -- adversarially reviewed 2026-06-19 (see *Adversarial review -- decision record* at the end). NOT converged on first pass (judge tally: 17 load-bearing objections; **16 enumerated as binding corrections** below, one sub-claim folded), to fold into the contract bodies during Phase 0 before the contracts are locked.
**Base branch:** `docs/web-port-feasibility` (the web-port integration base; not merged to `master` until the whole program is complete).
**Companion:** [`docs/audits/web-port-feasibility-2026-06-19.md`](../audits/web-port-feasibility-2026-06-19.md) -- the "can/should we" verdict this doc builds on.
**Authoring:** drafted by a 6-agent workflow (5 Sonnet contract drafters + 1 reconcile pass), Opus-synthesised. Code-grounded -- cites current `file:line` (working tree == master architecture).

> **Citations note.** Line numbers are approximate and branch-relative; file names and structural claims are stable on master.

---

## 0. Purpose & scope

This document turns the feasibility verdict into **buildable contracts** for the five costly-to-reverse seams of the web port. It deliberately does **not** cover what is already settled:

- **In scope (the keystones):** the headless-core service API (the MVC seam), the realtime-progress + cancel protocol, the image-serving contract, the QA-harness architecture, and the eval gates.
- **Out of scope (already decided):** the frontend component design and theme tokens (the design prototype specifies them -- see feasibility report S9), and the stack choice (FastAPI + SSE backend, React + TanStack frontend, pywebview packaging -- feasibility S6-S7).

The decisive reason to design these five first: they are the contracts that **everything else depends on**, and the only ones expensive to change once parallel build starts. The service API in particular is the cross-platform seam -- get it right once and every view (Qt today, web next, native/mobile later) is a thin client of it.

---

## 1. Decided architecture (fixed inputs)

- **Backend:** FastAPI + uvicorn, single process. Scans run in a **dedicated worker process** (not inline in the uvicorn worker) to avoid GIL contention with HTTP serving.
- **Realtime:** Server-Sent Events (SSE) for progress; one HTTP POST for cancel.
- **Frontend:** React + Vite + TypeScript + TanStack Table v8 + TanStack Virtual + ShadCN/UI.
- **Packaging:** pywebview (WebView2 -> OS codec stack for HEVC).
- **Perf invariants:** `scanner/` + `core/` + `infrastructure/` (minus `image_service`) transfer unchanged; preserve the `_StageTracker` 1 Hz throttle; cancel via `threading.Event` replacing the ~15 `isInterruptionRequested()` sites; `image_service` rewritten `QImage`->bytes; WIC/Shell COM via a `CoInitializeEx`-STA thread pool; thumbnails served from the existing content-hashed JPEG disk cache.

---

## 2. The MVC seam (the keystone)

```
            +---------------------------------------------+
            |   Headless application core (Qt-free)        |
            |   core/  scanner/  infrastructure/           |
            |   + NEW  core/app_service/  -- AppService -- |
            |     scan_start / load_manifest / get_groups  |
            |     decide / execute / lock / get_image / .. |
            +---------------^--------------^---------------+
                            |              |
              +-------------+              +--------------+
        +-----+-------+                          +---------+--------+
        |  Qt adapter |  (today; thin)           |  Web adapter      |
        | app/views/  |  ScanWorker = thin Qt    | FastAPI routes -> |
        |             |  shell over run_pipeline | JSON + SSE + React |
        +-------------+                          +-------------------+
```

`AppService` (in `core/app_service/`) is the single Qt-free facade both views call. The current `ScanWorker` becomes a thin Qt shell over the extracted `run_pipeline()`; the web view is a thin FastAPI+React shell over the same surface. **This is the contract to get right.**

---

## 3. Shared conventions & cross-contract consistency

*Produced by the reconcile pass; pins the shared vocabulary the five contracts must speak. Where a contract's draft below conflicts with this section or with the real code, the **Binding reconciliation** (after the contracts) is authoritative.*

"## Cross-contract consistency & shared conventions\n\n### 1. Canonical DTO names\n\nAll five contracts must use these exact names. Deviations are bugs, not style choices.\n\n| Concept | Canonical name | Defined in | Notes |\n|---|---|---|---|\n| One photo row | `RecordDTO` | C1 | Pydantic `BaseModel`; field names mirror `PhotoRecord` (core/models.py) including the legacy `capture_date` field |\n| Group of rows | `GroupDTO` | C1 | `group_number: int`, `is_expanded: bool`, `items: list[RecordDTO]` |\n| Scan configuration | `ScanConfig` | C1 | Pydantic `BaseModel`; matches `ScanWorker.__init__` keyword args |\n| Scan task reference | `ScanHandle` | C1 | `cancel_token: _CancelToken` (not `threading.Event`) |\n| Delete/execute outcome | `DeleteResult` | `core/services/interfaces.py` | Fields: `success_paths`, `failed`, `log_path`. C1's `ExecuteResult` is a facade alias that must use these exact names. Do not introduce `deleted_paths`, `ignored_paths`, or `audit_log_path` as new field names. |\n| Image bytes response | `ImageBytes` | C1 | `data: bytes, content_type: str, width: int, height: int` |\n| Filesystem entry | `FsEntry` | C1 | `name: str, path: str, is_dir: bool, is_accessible: bool` |\n| Cancel token | `_CancelToken` | C2 | Wraps `threading.Event`; callable as `cancel_check`; `.request()` replaces 15 `isInterruptionRequested()` sites |\n\n### 2. Scan event names — single source of truth\n\nThe following table is the authoritative mapping across all layers. Any deviation between the multiprocessing.Queue message, the SSE event name, the ScanTask status, and the ScanProgressBus method is a bug.\n\n| Bus method | SSE event name | ScanTask.status | Qt signal analog |\n|---|---|---|---|\n| `on_log` | `log` | — | `progress` (Signal str) |\n| `on_stage_progress` | `stage` | — | `stage_progress` (Signal str, int, int, float) |\n| `on_finished` | `finished` | `finished` | `finished` (Signal str) |\n| `on_failed` | `failed` | `failed` | `failed` (Signal str) |\n| `on_completed_empty` | `empty` | `empty` | `completed_empty` (Signal) |\n| `on_calibration` | `hash_pool_measured` | — | `hash_pool_measured` (Signal dict) |\n| `on_read_knee` | `read_knee_measured` | — | `read_knee_measured` (Signal dict) |\n\n`cancelled` is a ScanTask.status that has no corresponding SSE event (the client already sent the cancel POST; the `failed` event with message `\"Scan cancelled.\"` is the terminal signal).\n\n### 3. SSE event payload schemas\n\nAll payloads are JSON objects. Field names are snake_case.\n\n```\nlog:               { msg: str }\nstage:             { stage: 'WALK'|'HASH'|'EXIFTOOL'|'CLASSIFY'|'SCORE'|'WRITE',\n                     done: int, total: int, fps: float }\nfinished:          { manifest_path: str }\nfailed:            { error: str }\nempty:             {}\nhash_pool_measured: { thread_per_file: float, process_per_file: float, spawn: float,\n                      group_per_pair: float, group_bk_per_candidate: float }\nread_knee_measured: { device: str, knee: int, sole_ramping: bool,\n                      ladder: list[int], levels: dict, current_permits: int,\n                      frozen: bool }\n```\n\n`hash_pool_measured` carries all 5 keys (the grouping micro-rates are mandatory; `_valid_hash_pool_rates` in the existing code validates only 3 — the fan-out layer must pass through all 5 without stripping).\n\n`read_knee_measured` fields come from `ReadKneeRamp.summary()` (scanner/autotune.py:226-237) augmented with `device` (device_key string) and `sole_ramping: bool` by the scan_worker at emit time. The exact summary keys (`ladder`, `levels`, `knee`, `current_permits`, `frozen`) are whatever `ReadKneeRamp.summary()` returns — do not hardcode them in the SSE schema independently.\n\n### 4. Error envelope\n\nAll non-2xx FastAPI responses return:\n\n```json\n{ \"error\": \"<human-readable message>\", \"code\": \"<machine-readable token>\", \"detail\": <optional> }\n```\n\nCanonical `code` values: `not_found`, `locked_paths`, `no_manifest`, `scan_running`, `scan_already_terminal`, `invalid_path`, `permission_denied`, `internal_error`.\n\nHTTP status mapping:\n- 400: `invalid_path`, `re.error`, `ValueError` from API inputs\n- 403: `permission_denied` (path traversal)\n- 404: `not_found`, `AppFileNotFoundError`\n- 409: `scan_running`, `scan_already_terminal`, `locked_paths` (when `force_locked=False`)\n- 422: FastAPI validation errors (Pydantic input schema failures — FastAPI default, do not override)\n- 500: `internal_error`\n\n### 5. ID schemes\n\n- **scan_id / task_id**: UUID4 string, generated by the FastAPI layer at `POST /api/scan/start`. Not the manifest path. Unique per scan invocation.\n- **group_id** (in testids and URLs): `str(group_number)` from the manifest (integer cast to string). Stable within a manifest session; not stable across re-scans.\n- **cache key**: `sha1(f'{path}|{int(size_key)}'.encode('utf-8', errors='ignore')).hexdigest()` — the existing `_compute_cache_key` function (image_service.py:128-131) is the single source of truth. Do not recompute this formula independently.\n- **ETag** for image responses: `sha1(f'{mtime_ns}:{size_bytes}'.encode()).hexdigest()[:16]` — derived from disk file metadata, NOT from the cache key.\n\n### 6. data-testid naming convention\n\nConvention: `{component}-{role}` in kebab-case. Set via React `data-testid` attribute.\n\n**Uniqueness rule**: `basename` alone is NOT a unique key. Use `row-file-{group_id}-{basename}` for row-level testids to prevent ambiguity when two groups contain files with the same filename. All sub-cell testids follow: `row-file-{group_id}-{basename}-action`, `row-file-{group_id}-{basename}-score`, etc.\n\nThe full testid surface is defined in C4. C4 is the single source of truth for testid strings; the React component developer reads C4, not the QA driver author.\n\n### 7. Settings persistence ownership\n\nThe single `JsonSettings` file (`settings.json`) is the canonical store. The FastAPI layer owns reading and writing it. The React frontend reads and writes settings through `GET /api/settings` (returns full settings object) and `PATCH /api/settings` (merges partial update). The browser never writes `settings.json` directly.\n\nCalibration persistence (`hash_pool_measured`, `read_knee_measured`) is persisted by the FastAPI layer immediately on receiving the corresponding `on_calibration` / `on_read_knee` bus events — NOT by waiting for the browser to POST them back. This eliminates the C2 open question: the browser is a display consumer of these events, not a persistence agent.\n\n### 8. WIC/Shell COM threading model\n\nThe current `_load_via_shell_thumbnail` uses `CoInitialize(None)` (MTA) per-call (image_service.py:692). The web rewrite uses a dedicated `_WIC_EXECUTOR = ThreadPoolExecutor(max_workers=2, initializer=_sta_initializer)` where `_sta_initializer` calls `CoInitializeEx(None, COINIT_APARTMENTTHREADED)` once per worker thread. FastAPI route handlers call `await asyncio.get_event_loop().run_in_executor(_WIC_EXECUTOR, ...)`. Do not copy the per-call CoInitialize pattern from the current code.\n\n### 9. Image cache clear lifecycle\n\n`AppService.load_manifest()` MUST call `image_service.clear_cache()` automatically before returning the loaded groups. This matches the existing Qt behavior (file_operations.py:406). The web API surface does not expose a separate `clear_image_cache` endpoint. The in-memory LRU cache is cleared; the disk cache under `thumbs/v1/` is preserved (content-hash-keyed, valid across manifests).\n\n### 10. Path encoding for API parameters\n\nWindows absolute paths (e.g., `C:\\Users\\J\\photo.jpg`) and UNC paths (e.g., `\\\\LINXIAOYUN\\photo\\img.heic`) are passed as-is in JSON string fields (not URL-encoded in POST bodies). In URL query parameters (`GET /api/image?path=...`), percent-encode the raw path string using standard URL encoding. The server decodes with `urllib.parse.unquote`. Backslashes are preserved — do not normalize to forward-slashes on the server side (Windows `Path` handles both)."

---

# The five contracts

---


## Contract 1 — Headless-Core Service API (MVC Seam)

### 1. Package home: `core/app_service/`

Proposed module tree:

```
core/
  app_service/
    __init__.py          # re-exports AppService, ScanHandle, all DTOs
    service.py           # AppService class
    dtos.py              # Pydantic models (GroupDTO, RecordDTO, ScanConfig, …)
    scan_runner.py       # headless scan entry-point (no QThread, no Qt)
```

**Why `core/app_service/` and not `service/` at the root or inside `infrastructure/`:**

- `core/` already contains the only two Qt-free domain layers (`core/models.py`, `core/services/`). Everything in `core/` is importable on a headless server (CI, web process) without Qt. Adding a sub-package there keeps the "zero Qt" invariant self-documenting: any import that resolves to `core.*` is safe in a FastAPI process.
- `infrastructure/` already contains I/O adapters (`ManifestRepository`, `DeleteService`, `ImageService`). The new module is a *coordinator* of those adapters, not another adapter. Placing it in `core/` signals that distinction.
- A top-level `service/` package would be ambiguous (Django convention) and would break the existing two-tier (`core/` domain + `infrastructure/` I/O) layout that the codebase already enforces.

`core/app_service/service.py` imports nothing from `PySide6`, nothing from `app.*`. The sole Qt escape valve is an optional `scan_runner.py` that accepts a `threading.Event` for cancellation — the same Event the Qt path already needs after replacing the ~15 `isInterruptionRequested()` call sites (per the feasibility report).

---

### 2. Python façade: `AppService`

All methods are typed. The service holds a single `_session` slot (see §4). Raises are enumerated below each signature.

```python
# core/app_service/dtos.py  (see §3 for full DTO definitions)
from __future__ import annotations
from pydantic import BaseModel

# core/app_service/service.py
import threading
from pathlib import Path
from core.app_service.dtos import (
    ScanConfig, ScanHandle,
    GroupDTO, RecordDTO,
    ExecuteResult, ImageBytes,
    FsEntry,
)
from core.app_service.exceptions import (
    NoManifestError, LockedRowError,
    FileNotFoundError as AppFileNotFoundError,
)
```

#### 2.1 Scan lifecycle

```python
def scan_start(self, config: ScanConfig) -> ScanHandle:
    """Launch the scanner pipeline in a dedicated worker process.

    Returns immediately. Progress is delivered via the event bus
    defined in Contract 2 (SSE / Qt signal, plug-in at construction).

    Implementation: starts scanner/scan_worker logic (extracted from
    app/views/workers/scan_worker.py:ScanWorker._run_pipeline) inside
    a multiprocessing.Process (or ProcessPoolExecutor on the web path)
    with a shared threading.Event cancel_token.

    Raises:
        ValueError  — config.sources is empty
        RuntimeError — a scan is already running (check handle.is_running())
    """
    ...

def scan_cancel(self, handle: ScanHandle) -> None:
    """Signal the running scan to stop cooperatively.

    Sets handle.cancel_token (threading.Event); the pipeline checks it
    at the same 15 sites that formerly called QThread.isInterruptionRequested().
    Returns immediately; caller polls handle.is_running() or subscribes
    to the 'scan_cancelled' event.

    No-op if handle.is_running() is False.
    """
    ...
```

#### 2.2 Manifest load / save

```python
def load_manifest(self, path: str) -> list[GroupDTO]:
    """Load a migration_manifest.sqlite and return grouped records.

    Wraps ManifestRepository.load() + MainVM._group_records() logic.
    Replaces the FileOperationsHandler._start_manifest_load /
    ManifestLoadWorker round-trip; here it is synchronous (callers
    that need async wrap it in an executor).

    Side-effect: stores path + groups in self._session.

    Raises:
        FileNotFoundError — path does not exist
        sqlite3.DatabaseError — file is not a valid SQLite manifest
    """
    ...

def save_manifest(self, path: str | None = None) -> int:
    """Persist current in-memory decisions to disk.

    path=None → save to the loaded manifest path (silent save,
    mirrors FileOperationsHandler.save_manifest_decisions_silent).
    path set   → checkpoint WAL + copy + save (mirrors
    FileOperationsHandler.save_manifest_decisions with copy logic
    from file_operations.py:456-509).

    Returns: number of rows updated.

    Raises:
        NoManifestError — no manifest loaded and path is None
        OSError — copy / write failure
    """
    ...
```

#### 2.3 Group + sort queries

```python
def get_groups(
    self,
    sort_keys: list[tuple[str, bool]] | None = None,
) -> list[GroupDTO]:
    """Return current in-memory groups, optionally re-sorted.

    sort_keys: list of (field_name, ascending) pairs matching
    PhotoRecord attribute names. None → return current order.
    Mirrors MainVM._group_records sort logic (core/services/sort_service.py).

    Raises:
        NoManifestError
    """
    ...
```

#### 2.4 Decisions

```python
def decide(
    self,
    file_paths: list[str],
    decision: str,                 # "" | "delete" | "ignore"
    force_locked: bool = False,    # True = unlock then apply (post-confirm)
) -> list[str]:
    """Set user_decision for the given paths in memory + SQLite.

    Mirrors FileOperationsHandler.set_decision (file_operations.py:934).
    Returns list of paths actually updated (excludes locked when
    force_locked=False).

    decision values: "" (canonical keep), "delete", "ignore".
    Lock / unlock are separate operations (see lock() / unlock()).

    Raises:
        NoManifestError
        LockedRowError — if any path is locked and force_locked=False;
            error carries .locked_paths for the UI to surface a confirm
    """
    ...

def bulk_decide(
    self,
    field: str,
    pattern: str,
    decision: str,
) -> list[str]:
    """Set user_decision for all records matching a regex / numeric pattern.

    Mirrors FileOperationsHandler.set_decision_by_regex (file_operations.py:1255).
    pattern shapes:
      - Plain regex (case-insensitive)
      - "__cmp__:OP:VALUE" threshold comparison
      - "__top_n__:N:asc|desc" top/bottom N per group
    Returns matched paths.

    Raises:
        NoManifestError
        re.error — invalid regex
        ValueError — malformed numeric pattern
    """
    ...
```

#### 2.5 Lock / unlock

```python
def lock(self, file_paths: list[str]) -> None:
    """Set is_locked=True for the given paths (memory + SQLite).

    Mirrors FileOperationsHandler.set_locked_state(locked=True).
    No lock-confirm needed — locking IS the freeze.

    Raises:
        NoManifestError
    """
    ...

def unlock(self, file_paths: list[str]) -> None:
    """Set is_locked=False. Mirrors set_locked_state(locked=False).

    Raises:
        NoManifestError
    """
    ...
```

#### 2.6 Execute (run planned deletions / ignores)

```python
def execute(
    self,
    recycle: bool = True,
) -> ExecuteResult:
    """Run all pending decisions that have user_decision != "".

    Wraps DeleteService.plan_delete + delete_to_recycle (or
    permanent delete via os.unlink when recycle=False).
    Writes finalize_outcome to DB (outcome='deleted'/'ignored').
    Removes executed paths from in-memory groups (calls
    MainVM.remove_deleted_and_prune + remove_from_list).

    Returns ExecuteResult with per-path outcomes + audit CSV path.

    Raises:
        NoManifestError
    """
    ...
```

#### 2.7 Remove-from-list / prune

```python
def remove_from_list(self, file_paths: list[str]) -> None:
    """Remove paths from the in-memory review list without touching disk.

    Mirrors MainVM.remove_from_list + ManifestRepository.remove_from_review.
    Writes outcome='ignored' to DB.

    Raises:
        NoManifestError
        LockedRowError — if any path is locked (caller must confirm)
    """
    ...

def prune_singletons(self) -> list[str]:
    """Drop groups that now have exactly 1 item.

    Returns list of pruned paths. Mirrors the _maybe_offer_singleton_prune
    logic (file_operations.py:767) minus the dialog — caller decides
    whether to prompt.

    Raises:
        NoManifestError
    """
    ...
```

#### 2.8 Image service

```python
def get_image(
    self,
    file_path: str,
    max_side: int,
) -> ImageBytes:
    """Return JPEG bytes for file_path scaled to max_side on longest side.

    Cache hierarchy (unchanged from current):
      1. In-memory LRU (_ByteBudgetLRUCache, keyed by sha1(path|size))
      2. Disk cache at thumbs/v1/<key>.jpg
      3. Load from source (PIL-HEIF → rawpy → QImageReader → Shell/WIC)

    The critical change from current infrastructure/image_service.py:
    _get_image returns a QImage; this method returns bytes (JPEG-encoded).
    The in-memory cache stores bytes (len(bytes) for budget accounting)
    instead of QImage objects, so the service is Qt-free.

    max_side ≤ 256  → thumbnail tier
    max_side > 256  → preview tier

    Returns:
        ImageBytes(data: bytes, content_type: str, width: int, height: int)

    Raises:
        AppFileNotFoundError — path does not exist on disk
    """
    ...
```

#### 2.9 Filesystem browse

```python
def browse_filesystem(
    self,
    path: str | None = None,
) -> list[FsEntry]:
    """Return directory entries for path (or filesystem roots if None).

    Used by the web UI's folder-picker (replaces QFileSystemModel /
    QFileDialog). No Qt dependency — uses os.scandir.

    Raises:
        PermissionError — path not accessible
        NotADirectoryError — path is a file
    """
    ...
```

#### 2.10 Reveal / open (localhost-only)

```python
def reveal_in_explorer(self, file_path: str) -> None:
    """Open the file's parent folder in Windows Explorer with the file
    selected. Localhost-only (web build: no-op / error on remote).

    Uses subprocess.Popen(["explorer", "/select,", path]).

    Raises:
        OSError — subprocess failed
    """
    ...

def open_with_default(self, file_path: str) -> None:
    """Open file_path in its default application (os.startfile on Windows).

    Localhost-only.

    Raises:
        OSError — os.startfile failed
        AppFileNotFoundError — path not on disk
    """
    ...
```

---

### 3. DTOs

#### 3.1 Why Pydantic

Pydantic is already a declared dependency (via FastAPI). The web layer needs JSON schemas for OpenAPI docs + SSE payloads. Using Pydantic BaseModel means `.model_json_schema()` for free, and `.model_dump()` replaces manual `asdict()` calls. The Qt path calls `.model_dump()` to get `dict` — zero extra work.

`core/models.PhotoRecord` and `PhotoGroup` stay as `dataclass(slots=True)` — they are the canonical domain objects that `ManifestRepository` produces and `SortService` consumes. DTOs are a thin translation layer at the service boundary.

#### 3.2 DTO definitions

```python
# core/app_service/dtos.py
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field
import threading


class RecordDTO(BaseModel):
    """Serialisable projection of core.models.PhotoRecord."""
    file_path: str
    folder_path: str
    group_number: int
    action: str                       # scanner action: KEEP/EXACT/REVIEW_DUPLICATE/…
    user_decision: str                # ""|"delete"|"ignore"
    is_locked: bool
    is_mark: bool
    file_size_bytes: int
    score: float | None
    phash: str | None
    hamming_distance: int | None
    shot_date: datetime | None
    creation_date: datetime | None
    modified_date: datetime | None
    pixel_width: int | None
    pixel_height: int | None

    @classmethod
    def from_record(cls, r: "PhotoRecord") -> "RecordDTO":  # noqa: F821
        return cls(**{f: getattr(r, f) for f in cls.model_fields})


class GroupDTO(BaseModel):
    group_number: int
    is_expanded: bool = False
    items: list[RecordDTO]


class ScanConfig(BaseModel):
    """Mirrors ScanWorker.__init__ parameters (scan_dialog.py:430-516)."""
    sources: dict[str, str]                    # label → absolute path
    output_path: str
    recursive_map: dict[str, bool] = Field(default_factory=dict)
    source_priority: dict[str, int] | None = None
    threshold: int = 10
    mean_color_threshold: int = 30
    dhash_threshold: int = 10
    limit: int | None = None
    workers: int = 4
    exif_workers: int = 2
    hash_pool: str = "thread"                  # "thread"|"process"|"auto"
    hash_pool_rates: dict | None = None
    auto_select_enabled: bool = False
    auto_select_aggressive_delete: bool = False
    autotune_read_knee: bool = False
    autotune_knees: dict = Field(default_factory=dict)


class ScanHandle(BaseModel):
    """Opaque reference to a running scan. Not serialised to JSON (server-side only)."""
    model_config = {"arbitrary_types_allowed": True}

    scan_id: str                               # uuid4 hex
    cancel_token: threading.Event              # set() to request stop
    output_path: str

    def is_running(self) -> bool:
        # Injected by AppService; placeholder here.
        ...

    def cancel(self) -> None:
        self.cancel_token.set()


class ExecuteResult(BaseModel):
    deleted_paths: list[str]
    ignored_paths: list[str]
    failed: list[tuple[str, str]]
    audit_log_path: str | None


class ImageBytes(BaseModel):
    data: bytes
    content_type: str = "image/jpeg"
    width: int
    height: int


class FsEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    is_accessible: bool = True
```

#### 3.3 Exceptions

```python
# core/app_service/exceptions.py
class AppServiceError(Exception): ...
class NoManifestError(AppServiceError): ...
class LockedRowError(AppServiceError):
    def __init__(self, locked_paths: list[str]) -> None:
        self.locked_paths = locked_paths
        super().__init__(f"{len(locked_paths)} locked rows need confirmation")
class FileNotFoundError(AppServiceError): ...  # distinct from builtin
```

---

### 4. State ownership

#### What the service owns (session scope)

```python
@dataclass
class _Session:
    manifest_path: str
    groups: list[PhotoGroup]      # mutable in-memory copy; the "working set"
    path_index: dict[str, tuple[int, int]]  # file_path → (g_idx, m_idx), lazy-rebuilt
    is_dirty: bool
    current_scan: ScanHandle | None
```

`AppService` holds one `_session: _Session | None`. When `_session is None`, all manifest-requiring calls raise `NoManifestError`.

> **IMPLEMENTATION NOTE (2026-07-24, accepted deviation).** The single `AppService`/`_Session` abstraction above was never built as one class. Session state and its concurrency ownership shipped **distributed**: `app/web/registry.py` (`ScanRegistry._lock`), the destructive-op `asyncio.Lock` created in `app/web/main.py` and held by `app/web/routes/execute.py`, `app/web/routes/review.py` (`_roots_lock`), and per-task subscriber/buffer locks in `app/web/models.py`. Each surface is individually lock-protected and unit-tested; no cross-resource transaction exists that would need the unified object. Recorded as accepted design-vs-implementation drift at cutover review — revisit only if a future feature needs an atomic multi-resource session mutation.

**What the service does NOT own:**

- UI selection state (which rows are highlighted, what's in the tree selection). Callers pass explicit `file_paths: list[str]` — the service never reads a selection model.
- Sort display preferences (the web client and Qt client each hold their own). Callers pass `sort_keys` explicitly to `get_groups`.
- The event bus / SSE stream (see §5 below).
- Image decode library availability flags — `ImageServiceAdapter` (the Qt-free rewrite of `infrastructure/image_service.py`) holds those.

#### Qt session vs web session

**Qt path:** One `AppService` instance is constructed at app startup (`main.py`) alongside `MainVM`. The Qt `MainWindow` becomes a thin wrapper that calls `service.decide(...)`, `service.load_manifest(...)`, etc., and forwards the returned DTOs to the existing `tree_model_builder`. The `_session` lives for the duration of the Qt process (same as today's `vm.groups`).

**Web path:** The FastAPI process holds one `AppService` singleton (process-local). Since the app is a single-user localhost tool (one browser tab), there is no per-request session isolation needed. The web process's `_session` is functionally identical to the Qt process's `_session`. If multi-user is ever needed, wrap `_session` in a `dict[session_id, _Session]` keyed by a cookie — but that is out of scope for the port.

---

### 5. Scan progress seam (boundary with Contract 2)

`AppService` accepts an `event_bus` at construction time via a Protocol:

```python
# core/app_service/events.py
from typing import Protocol

class ScanProgressBus(Protocol):
    """Event bus injected into AppService at construction.

    Contract 2 provides two concrete implementations:
      - SseScanBus    → writes SSE events to a FastAPI StreamingResponse queue
      - QtSignalBus   → wraps ScanWorker's existing Qt Signals
    """

    def on_log(self, scan_id: str, message: str) -> None:
        """Equivalent of ScanWorker.progress signal (scan_worker.py:388)."""
        ...

    def on_stage_progress(
        self,
        scan_id: str,
        stage: str,         # WALK / HASH / EXIFTOOL / CLASSIFY / SCORE / WRITE
        completed: int,
        total: int,
        files_per_sec: float,
    ) -> None:
        """Equivalent of ScanWorker.stage_progress signal (scan_worker.py:409)."""
        ...

    def on_finished(self, scan_id: str, manifest_path: str) -> None:
        """Equivalent of ScanWorker.finished signal."""
        ...

    def on_failed(self, scan_id: str, error: str) -> None:
        """Equivalent of ScanWorker.failed signal."""
        ...

    def on_completed_empty(self, scan_id: str) -> None:
        """Equivalent of ScanWorker.completed_empty signal."""
        ...

    def on_calibration(self, scan_id: str, rates: dict) -> None:
        """Equivalent of ScanWorker.hash_pool_measured signal (scan_worker.py:419)."""
        ...

    def on_read_knee(self, scan_id: str, summary: dict) -> None:
        """Equivalent of ScanWorker.read_knee_measured signal (scan_worker.py:426)."""
        ...
```

`scan_start()` creates a `ScanHandle`, starts `scan_runner.run_pipeline_in_process(config, cancel_token, bus)` in a `concurrent.futures.ProcessPoolExecutor` worker, and returns the handle. `_StageTracker` (scan_worker.py:331) is extracted to `core/app_service/scan_runner.py` unchanged — it has zero Qt dependency. The runner calls `bus.on_stage_progress(...)` at the same 1 Hz throttle rate (`_STAGE_EMIT_INTERVAL_SECONDS = 1.0`).

The **seam boundary** is: `scan_start` returns `ScanHandle`; everything that currently flows through Qt Signals flows through `ScanProgressBus` instead. Contract 2 owns the concrete bus implementations and the wire format (SSE JSON / Qt Signal wrapper).

---

### 6. Migration seam: Qt view → thin client

The strategy is **adapter injection**, not Qt code deletion. Three phases:

#### Phase A — Extract `_run_pipeline` from `ScanWorker` (scan_worker.py:717)

Create `core/app_service/scan_runner.py`:

```python
def run_pipeline(
    config: ScanConfig,
    cancel_token: threading.Event,    # replaces isInterruptionRequested()
    bus: ScanProgressBus,             # replaces Qt Signals
) -> str:                             # returns manifest_path on success
    ...
    # All pipeline logic from ScanWorker._run_pipeline, but:
    #   self.isInterruptionRequested() → cancel_token.is_set()
    #   self._emit(msg)               → bus.on_log(scan_id, msg)
    #   self._emit_stage(...)         → bus.on_stage_progress(...)
    #   self.finished.emit(path)      → bus.on_finished(scan_id, path)
    #   self.failed.emit(err)         → bus.on_failed(scan_id, err)
    #   self.completed_empty.emit()   → bus.on_completed_empty(scan_id)
    #   self.hash_pool_measured.emit  → bus.on_calibration(scan_id, rates)
    #   self.read_knee_measured.emit  → bus.on_read_knee(scan_id, summary)
```

#### Phase B — Keep `ScanWorker` as a thin Qt adapter

```python
class ScanWorker(QThread):
    progress = Signal(str)
    stage_progress = Signal(str, int, int, float)
    finished = Signal(str)
    failed = Signal(str)
    completed_empty = Signal()
    hash_pool_measured = Signal(dict)
    read_knee_measured = Signal(dict)

    def run(self) -> None:
        from core.app_service.scan_runner import run_pipeline
        from core.app_service.events import ScanProgressBus

        class _QtBus:  # inline adapter — no new file needed
            def __init__(self, worker: ScanWorker, scan_id: str) -> None:
                self._w = worker
                self._scan_id = scan_id
            def on_log(self, scan_id, msg): self._w.progress.emit(msg)
            def on_stage_progress(self, scan_id, stage, completed, total, fps):
                self._w.stage_progress.emit(stage, completed, total, fps)
            def on_finished(self, scan_id, path): self._w.finished.emit(path)
            def on_failed(self, scan_id, err): self._w.failed.emit(err)
            def on_completed_empty(self, scan_id): self._w.completed_empty.emit()
            def on_calibration(self, scan_id, rates): self._w.hash_pool_measured.emit(rates)
            def on_read_knee(self, scan_id, summary): self._w.read_knee_measured.emit(summary)

        bus = _QtBus(self, "qt-scan")
        cancel = threading.Event()
        # Bridge QThread interruption to threading.Event
        # Poll every 50 ms (matches existing exif_queue.get(timeout=0.5) cadence)
        def _poll() -> None:
            while not self.isInterruptionRequested():
                time.sleep(0.05)
            cancel.set()
        threading.Thread(target=_poll, daemon=True).start()

        try:
            run_pipeline(self._config, cancel, bus)
        except Exception as exc:
            logger.exception("Scan pipeline failed: {}", exc)
            self.failed.emit(str(exc))
```

#### Phase C — Replace `MainVM` + `FileOperationsHandler` with `AppService` calls

Each Qt handler method becomes a one-liner:

```python
# FileOperationsHandler (becomes a thin adapter)
def set_decision(self, items, new_decision, incremental=True):
    try:
        self._service.decide([it["path"] for it in items if it.get("type") == "file"],
                             new_decision)
    except LockedRowError as e:
        # surface LockedRowsConfirmDialog with e.locked_paths
        ...
    if incremental:
        self._refresh_cells(...)  # Qt-only: tree_controller.update_decision_cells
```

The Qt handler **still exists** during the port; it wraps `AppService` instead of manipulating `ManifestRepository` and `MainVM` directly. This means both UI stacks can coexist: the Qt `MainWindow` talks to `AppService`, the FastAPI routes talk to `AppService`. The `MainVM` class can be retired once the Qt handler is a thin wrapper — but that is Phase C, not Phase A/B.

---

### Appendix: Current call-site to service method mapping

| Current Qt call site | Service method |
|---|---|
| `ManifestLoadWorker` → `ManifestRepository.load()` | `load_manifest()` |
| `FileOperationsHandler.save_manifest_decisions_silent()` | `save_manifest()` |
| `FileOperationsHandler.set_decision()` | `decide()` |
| `FileOperationsHandler.set_decision_by_regex()` | `bulk_decide()` |
| `FileOperationsHandler.set_locked_state(locked=True)` | `lock()` |
| `FileOperationsHandler.set_locked_state(locked=False)` | `unlock()` |
| `FileOperationsHandler.remove_items_from_list()` | `remove_from_list()` |
| `FileOperationsHandler._maybe_offer_singleton_prune()` | `prune_singletons()` |
| `FileOperationsHandler.execute_action()` → `DeleteService` | `execute()` |
| `MainVM.get_groups` / `SortService.sort()` | `get_groups(sort_keys)` |
| `ScanWorker._run_pipeline()` | `scan_start()` + `scan_runner.run_pipeline()` |
| `ImageService.get_preview()` / `get_thumbnail()` → `QImage` | `get_image()` → `ImageBytes` |
| `QFileDialog` / `QFileSystemModel` | `browse_filesystem()` |
| `subprocess explorer /select` | `reveal_in_explorer()` |
| `os.startfile` | `open_with_default()` |


---

## CONTRACT 2 — Realtime Progress + Cancellation Protocol

### 1. SSE Event Schema

Each current Qt signal maps to one named SSE event type. The connection is
`GET /api/scan/{id}/events` (text/event-stream). Every event carries a JSON
`data:` field.

#### 1.1 Signal → SSE mapping

| Qt Signal | SSE `event:` name | JSON payload |
|---|---|---|
| `progress(str)` | `log` | `{"msg": "<text>"}` |
| `stage_progress(str, int, int, float)` | `stage` | `{"stage": "WALK"\|"HASH"\|"EXIFTOOL"\|"CLASSIFY"\|"SCORE"\|"WRITE", "done": 0, "total": 0, "fps": 0.0}` — `total==0` means indeterminate |
| `finished(str)` | `finished` | `{"manifest_path": "<abs path>"}` |
| `failed(str)` | `failed` | `{"error": "Scan cancelled."\|"<exception text>"}` |
| `completed_empty()` | `empty` | `{}` |
| `hash_pool_measured(dict)` | `hash_pool_measured` | `{"thread_per_file": 0.0, "process_per_file": 0.0, "spawn": 0.0, "group_per_pair": 0.0, "group_bk_per_candidate": 0.0}` |
| `read_knee_measured(dict)` | `read_knee_measured` | `{"device": "<device_key>", "knee": 2, "sole_ramping": true, …ramp summary fields}` |

Terminal events are `finished`, `failed`, and `empty`. After any terminal event
the server closes the SSE stream (sends a blank line then no more data).

Full SSE wire example for one stage tick:

```
id: 42
event: stage
data: {"stage":"HASH","done":1200,"total":3400,"fps":87.3}

```

#### 1.2 The `_StageTracker` throttle — where it lives now and where it moves

**Now (Qt):** `_StageTracker` lives in
`app/views/workers/scan_worker.py:331-382`. One instance is constructed per
stage inside `_run_pipeline` and calls `self._emit_stage(tracker, done,
total)` at each progress point. `should_emit` returns `True` only on (a) first
call, (b) `done >= total`, or (c) ≥1 s elapsed since last emit
(`_STAGE_EMIT_INTERVAL_SECONDS = 1.0`, line 22). The throttle is entirely
worker-side; the Qt signal queue absorbs it with no further gating.

**After the port:** `_StageTracker` moves, unchanged in logic, into the
headless pipeline runner that executes in the dedicated worker process. Instead
of calling `self.stage_progress.emit(...)` it pushes a `("stage", {...})`
tuple onto the cross-process `multiprocessing.Queue`. The FastAPI router reads
that queue in a background thread and broadcasts to all active SSE connections
for that task-id. The 1 Hz throttle stays worker-side — the multiprocessing
queue, like the Qt signal queue, is a FIFO and does not need a second
throttle on the reader side.

---

### 2. Scan-Task Registry

#### 2.1 Task-id allocation

`POST /api/scan/start` receives the scan parameters, allocates a UUID4
`task_id`, registers a `ScanTask` record in the server-side registry, spawns
the worker process, and returns immediately.

```
POST /api/scan/start
Content-Type: application/json

{
  "sources": {"label": "/path"},
  "output_path": "/path/out.sqlite",
  "recursive_map": {"label": true},
  "source_priority": null,
  "threshold": 10,
  "mean_color_threshold": 30,
  "dhash_threshold": 10,
  "workers": 4,
  "exif_workers": 2,
  "hash_pool": "auto",
  "hash_pool_rates": null,
  "auto_select_enabled": false,
  "auto_select_aggressive_delete": false,
  "autotune_read_knee": true,
  "autotune_knees": {}
}

201 Created
{"task_id": "550e8400-e29b-41d4-a716-446655440000"}
```

#### 2.2 Task lifetime decoupled from SSE connection

The `ScanTask` object persists in the registry **independently of any SSE
connection**. A `ScanTask` holds:

```python
@dataclass
class ScanTask:
    task_id: str
    status: Literal["running", "finished", "failed", "empty", "cancelled"]
    created_at: float          # time.monotonic()
    cancel_token: _CancelToken # see §3
    event_buffer: deque        # ring buffer — see §2.3
    last_event_id: int         # monotone counter, starts at 0
    terminal_event: dict | None  # the finished/failed/empty payload, or None
```

The registry is a module-level `dict[str, ScanTask]` protected by a
`threading.Lock`. Tasks are inserted at `POST /api/scan/start` and
**never removed during a scan**. A background reaper thread prunes tasks whose
`created_at` is older than 4 hours (configurable) to prevent unbounded growth.

#### 2.3 Server-side ring buffer for resume

Every event the worker process emits is appended to `ScanTask.event_buffer`
(a `collections.deque(maxlen=500)`) as `(event_id: int, event_name: str, data:
dict)`. The `event_id` is a task-scoped monotone integer incremented on every
push. This buffer allows a reconnecting client to replay events it missed.

#### 2.4 `GET /api/scan/{id}/events` — SSE endpoint

```
GET /api/scan/{id}/events
Accept: text/event-stream
Last-Event-ID: 41          (optional — browser sets this on reconnect)

200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

On connect the server:

1. Looks up the `ScanTask`. Returns `404` if unknown, `410 Gone` if the task
   is pruned (>4 h).
2. If `Last-Event-ID` is present, replays all buffered events with
   `event_id > Last-Event-ID` before switching to live streaming.
3. If the task is already in a terminal state (`finished`/`failed`/`empty`)
   AND the terminal event is in the buffer, it replays the replay window and
   then the terminal event, then closes the stream — browser refresh after
   completion sees the final state without a stale hanging connection.
4. Enters a blocking read loop on the worker queue, forwarding new events to
   the HTTP response body as they arrive.

The SSE connection is **read-only with respect to cancel**. Client disconnect
does NOT cancel the scan (§5 covers this).

FastAPI implementation uses `StreamingResponse` with an async generator that
awaits an `asyncio.Queue` fed by a sync thread that reads the
`multiprocessing.Queue`.

#### 2.5 `POST /api/scan/{id}/cancel`

```
POST /api/scan/{id}/cancel

200 OK
{"status": "cancel_requested"}
```

Returns `404` if unknown, `409 Conflict` if the task is already terminal.
The cancel is fire-and-forget on the HTTP side — the scan may take up to ~1s
to observe the token and emit a `failed` event with `{"error": "Scan
cancelled."}`.

---

### 3. Cancel-Token Contract

#### 3.1 `_CancelToken` — replaces `isInterruptionRequested`

```python
class _CancelToken:
    """threading.Event wrapper that also satisfies the cancel_check Callable[[], bool] contract."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        """Called from the HTTP handler (main process) via IPC or from the worker
        process's own teardown. Thread-safe and idempotent."""
        self._event.set()

    def __call__(self) -> bool:          # satisfies Callable[[], bool]
        return self._event.is_set()

    def is_set(self) -> bool:            # satisfies cancel_flag.is_set pattern
        return self._event.is_set()
```

The `_CancelToken` replaces **every** `isInterruptionRequested()` call site in
`scan_worker.py`. In the headless pipeline, the `ScanWorker` class is stripped
of `QThread` and receives a `_CancelToken` at construction time. Every site
that previously called `self.isInterruptionRequested()` instead calls
`self._cancel_token()`. The `cancel_check=self.isInterruptionRequested`
injection into `scan_sources` (walker.py:76) becomes
`cancel_check=self._cancel_token`.

#### 3.2 Enumeration of all 11 live call sites in `scan_worker.py`

(Comments-only mentions excluded. Line numbers reference the current master
tree.)

| Line | Stage / Context | Role |
|------|-----------------|------|
| 801 | WALK — `cancel_check=` arg to `scan_sources` | polled per rglob tick inside `scanner/walker.py:103,225` |
| 840 | post-WALK gate | stop pipeline if cancelled during walk |
| 985 | exif-queue cooperative put loop | break put loop so parent can reach cancel teardown (#594) |
| 1180 | process-pool HASH drain loop (as_completed) | gate for process-branch cancel |
| 1531 | thread-branch HASH drain loop (out_q.get inner loop) | gate for thread-branch cancel |
| 1601 | post-HASH exif consumer join loop | kill exiftool on dialog-close during exif drain (#607) |
| 1613 | post-HASH exif final gate | stop pipeline after EXIF post-drain cancel |
| 1669 | pre-CLASSIFY gate | stop pipeline before expensive classify pass |
| 1693 | pre-SCORE gate | stop pipeline before scoring pass |
| 1727 | pre-AUTO-SELECT gate | stop pipeline before auto-select pass |
| **1752** | **pre-WRITE gate** | **dangerous — see §3.3** |

#### 3.3 The WRITE-stage gate — the dangerous site

Line 1752 is the critical guard:

```python
# scan_worker.py:1752-1755
if self.isInterruptionRequested():
    logger.warning("Scan cancelled by user before manifest write")
    self.failed.emit("Scan cancelled.")
    return
```

If cancel arrives between this check and the `write_manifest(rows,
self.output_path)` call at line 1759, `write_manifest` overwrites whatever is
at `output_path` with a partial manifest. The Qt version accepted this race
because the window is ~1 ms. The web version must handle it explicitly:

**Contract:** The headless pipeline MUST check the cancel token
**after** `write_manifest` completes (not before), OR write to a temp file and
atomically rename. The atomic-rename option is safer:

```python
# headless pipeline replacement
tmp = Path(str(output_path) + ".tmp")
write_manifest(rows, tmp)
if not cancel_token.is_set():
    tmp.replace(output_path)   # atomic on POSIX; near-atomic on Win32 (NTFS)
else:
    tmp.unlink(missing_ok=True)
    raise CancelledError("Scan cancelled.")
```

This means a mid-WRITE cancel leaves the previous manifest intact — a safe
fail. The old pre-write gate at line 1752 is still needed to avoid writing
at all when the user cancels just before write, but the post-write check
prevents the partial-overwrite race.

#### 3.4 How cancel crosses the process boundary

```
HTTP POST /api/scan/{id}/cancel
        │
        ▼
FastAPI handler (main process)
  → ScanTask.cancel_token.request()      # sets threading.Event in main process
  → puts ("cancel",) onto control_queue  # multiprocessing.Queue
        │
        ▼
Worker process — control reader thread
  → reads ("cancel",) from control_queue
  → sets its LOCAL threading.Event (the one ScanWorker holds)
        │
        ▼
ScanWorker._cancel_token.is_set() → True
```

Two separate `threading.Event` objects exist: one in the main process
(for registry state) and one in the worker process (for the pipeline).
They are linked by a `multiprocessing.Queue` pair: `event_queue` (worker
→ main, carries progress/terminal events) and `control_queue` (main →
worker, carries cancel requests). The worker process has a lightweight
daemon thread that reads `control_queue` in a `get(timeout=0.1)` loop
and calls the local cancel token on receipt.

---

### 4. Dedicated-Worker-Process Boundary

#### 4.1 Architecture

```
┌─────────────────────────────────────────┐
│  Main Process (uvicorn + FastAPI)        │
│                                          │
│  task_registry: dict[str, ScanTask]     │
│                                          │
│  POST /api/scan/start                   │
│    ├─ allocate task_id                  │
│    ├─ create event_queue (mp.Queue)     │
│    ├─ create control_queue (mp.Queue)   │
│    ├─ spawn _scan_process(...)          │
│    └─ store ScanTask in registry        │
│                                          │
│  GET /api/scan/{id}/events              │
│    └─ async reads event_queue           │
│         ↓ feeds SSE response            │
│                                          │
│  POST /api/scan/{id}/cancel             │
│    └─ control_queue.put(("cancel",))    │
└────────────┬────────────────────────────┘
             │  multiprocessing.Queue (two)
┌────────────▼────────────────────────────┐
│  Worker Process (_scan_process)          │
│                                          │
│  cancel_token = _CancelToken()          │
│  control_thread: reads control_queue    │
│    └─ cancel_token.request() on cancel  │
│                                          │
│  _HeadlessScanRunner(                   │
│    ..., cancel_token, event_queue       │
│  ).run()                                │
│    emits → event_queue.put((...))       │
└─────────────────────────────────────────┘
```

`_scan_process` is the `multiprocessing.Process` target — a plain top-level
function (not a method, to remain picklable on Windows spawn). It receives
all scan parameters plus the two queues as arguments.

#### 4.2 Process-group reaping — Linux equivalent of Windows Job Object

The Windows code uses `KILL_ON_JOB_CLOSE` (scan_worker.py:300-328) to assign
pool workers to a job so an ungraceful parent exit reaps them. Linux/macOS
equivalents:

**Option A — `os.setpgrp()` in the worker process initializer:**

```python
# worker_process_entry.py
import os, signal

def _worker_process_main(event_q, control_q, params):
    # Create a new process group so kill(-pgid, SIGTERM) from the parent
    # reaps this process AND any ProcessPoolExecutor children it spawns.
    os.setpgrp()          # become process group leader
    _run_scan(event_q, control_q, params)
```

The main process stores the worker PID and, on ungraceful exit (SIGTERM
handler or atexit), calls:

```python
import os, signal
os.killpg(os.getpgid(worker_pid), signal.SIGTERM)
```

**Option B — `PR_SET_PDEATHSIG` (Linux only):**

Each child of the worker process calls `prctl(PR_SET_PDEATHSIG, SIGTERM)` so
the kernel kills them automatically when their parent (the worker) dies. This
is the Linux kernel-native equivalent of `KILL_ON_JOB_CLOSE` but applies only
one level down (grandchildren of the worker are not covered unless they also
call `prctl`).

**Recommendation:** Use Option A (process-group kill) because it covers all
descendants, works on macOS as well as Linux, and does not require per-child
setup. On Windows, retain the existing `_assign_process_pool_to_kill_job`
logic — the headless runner detects platform and branches.

**Graceful cancel sequence** (replaces `requestInterruption()` + `wait(3000)`):

```python
# main process, cleanup path
control_queue.put(("cancel",))
worker_process.join(timeout=5)
if worker_process.is_alive():
    # escalate
    os.killpg(os.getpgid(worker_process.pid), signal.SIGTERM)  # Linux/macOS
    # Windows: win32api.TerminateProcess(worker_handle, 1)
    worker_process.join(timeout=2)
```

---

### 5. Edge Cases

#### 5.1 Client disconnect mid-scan — must NOT cancel

SSE connections are read-only listeners. The `StreamingResponse` generator
catches `asyncio.CancelledError` (triggered when the client disconnects) and
simply exits — no cancel signal is sent to the worker process. The scan
continues. The event buffer (ring, maxlen=500) continues to accumulate events
so a reconnecting client can replay what it missed via `Last-Event-ID`.

```python
# FastAPI SSE generator (sketch)
async def _sse_stream(task_id: str, last_event_id: int):
    task = registry[task_id]
    try:
        # replay missed events
        for eid, name, data in task.event_buffer:
            if eid > last_event_id:
                yield f"id: {eid}\nevent: {name}\ndata: {json.dumps(data)}\n\n"
        # live stream
        async for event in task.live_queue:
            yield f"id: {event.id}\nevent: {event.name}\ndata: {json.dumps(event.data)}\n\n"
            if event.name in ("finished", "failed", "empty"):
                return
    except asyncio.CancelledError:
        # client disconnected — do not cancel the scan
        return
```

#### 5.2 Double-cancel

`_CancelToken.request()` calls `threading.Event.set()` which is idempotent.
`control_queue.put(("cancel",))` from a second HTTP cancel request is
harmless — the control reader thread already set the token; the second
`("cancel",)` is read and `request()` is called again (no-op). The HTTP
handler returns `200` on the first cancel and `409 Conflict` on any subsequent
call when `task.status` is already `"cancelled"` or any terminal state.

#### 5.3 Scan already finished

`POST /api/scan/{id}/cancel` checks `task.status`. If the status is
`"finished"`, `"failed"`, or `"empty"`, the handler returns:

```
409 Conflict
{"error": "scan_already_terminal", "status": "finished"}
```

No cancel signal is sent to the (already-exited) worker process.

#### 5.4 Browser refresh resume

On browser refresh, the `EventSource` API automatically sets the
`Last-Event-ID` header to the id of the last event it received. The
`GET /api/scan/{id}/events` handler replays all buffered events with
`event_id > Last-Event-ID`, then continues live streaming. If the scan
already finished and the terminal event is within the buffer, the client sees
the complete history including the terminal event and the stream closes
normally. If the terminal event has aged out of the 500-event ring buffer, the
handler checks `task.terminal_event` (stored separately on `ScanTask`) and
sends it after the replay window, guaranteeing the client always receives the
terminal event on reconnect regardless of how long the scan took or how long
the client was disconnected.

#### 5.5 Scan task unknown / expired

`GET /api/scan/{id}/events` returns `404` for unknown task-ids and
`410 Gone` for tasks that have been pruned by the 4-hour reaper.
The browser's `EventSource` does not retry on non-2xx responses by default,
so the UI must handle these status codes explicitly (show an error state, do
not loop forever on reconnect).

---

### 6. Sequence Diagram — Happy Path

```
Browser          FastAPI (main)          Worker process
  │                   │                       │
  │ POST /start       │                       │
  │──────────────────►│ allocate task_id      │
  │                   │ spawn worker process──►│ _scan_process starts
  │◄──────────────────│ 201 {task_id}         │
  │                   │                       │ WALK events → event_q
  │ GET /events       │                       │ HASH events → event_q
  │──────────────────►│ SSE stream opened     │ ...
  │◄──────────────────│ id:1 event:stage ...  │
  │◄──────────────────│ id:2 event:log ...    │
  │                   │      [1 Hz throttle]  │ WRITE → finished → event_q
  │◄──────────────────│ id:N event:finished   │ process exits
  │                   │ stream closed         │
```

### 7. Cross-cutting Implementation Notes

- `_StageTracker` is moved to `scanner/progress.py` (new file) so it is
  importable by both `app/views/workers/scan_worker.py` (Qt path) and the
  headless `_HeadlessScanRunner`, without creating an import cycle through
  `app/views/`.
- The `multiprocessing` start method is explicitly set to `"spawn"` at process
  start for Windows compatibility (PySide6 is not fork-safe). Linux defaults
  to `"fork"` but `"spawn"` is correct here too because the worker must not
  inherit the uvicorn file descriptor set.
- The `event_queue` uses `multiprocessing.Queue` (not `Pipe`) so multiple
  SSE connections can drain the same event stream — each connection reads from
  a per-connection `asyncio.Queue` that is fed by a single reader thread
  pulling from `event_queue` and fanning out to all registered per-connection
  queues for that task. This fan-out is managed in the `ScanTask` object.


---


## CONTRACT 3 — Image-Serving Contract (QImage → bytes rewrite)

**Base:** `infrastructure/image_service.py` (878 lines), `app/views/image_tasks.py`, `app/views/image_tasks_helpers.py`, `app/views/preview_pane.py`

---

### 1. HTTP Endpoint Shape

#### 1.1 Unified endpoint

```
GET /api/image
  ?path=<url-encoded absolute path>
  &size=<int>          # longest-side cap; 0 = full-res
  [&v=1]               # optional recipe version hint for cache-busting
```

**Single endpoint, not two.** Size=0 is the full-res signal; any positive integer is a thumbnail/preview cap. This mirrors the existing `_get_image(path, requested_side)` dispatch exactly (`image_service.py:337`).

#### 1.2 Content-Type

`image/jpeg` for all responses (the disk cache under `thumbs/v1/` already stores JPEG at quality=85; `image_service.py:371`). For the full-res path (`size=0`) the same JPEG re-encode policy applies by default; see §3 for the Range-streaming alternative.

Error responses:
- `400 Bad Request` — path not provided, or path traversal detected (see §5 security note)
- `403 Forbidden` — path resolves outside the allowed root set (manifest roots + configured scan dirs)
- `404 Not Found` — file does not exist on disk (do not leak whether it exists — return 404 for both)
- `500 Internal Server Error` — decode failed after all fallbacks; body is JSON `{"error": "decode_failed"}`

#### 1.3 Cache key

The existing `_compute_cache_key` (`image_service.py:128–131`) uses:

```python
sig = f"{path}|{int(size_key)}".encode("utf-8", errors="ignore")
key = hashlib.sha1(sig).hexdigest()
```

**This is a path-based key, not a content-hash key.** For the web port, preserve this exactly. The disk-cached file at `thumbs/v1/{key}.jpg` is already present on the user's machine from Qt-app usage; the web server reads from the same directory, so there is zero cold-start penalty on first launch of the web UI for a library that was already browsed via the Qt app.

**ETag:** derive from the disk-cache file's mtime + size (not from the SHA1 key). The SHA1 key is a cache-miss signal, not a freshness signal:

```python
stat = disk_file.stat()
etag = f'"{stat.st_mtime_ns}-{stat.st_size}"'
```

**HTTP caching headers for thumbnails (size > 0):**

```
Cache-Control: public, max-age=86400, stale-while-revalidate=604800
ETag: "<mtime_ns>-<size>"
Vary: (none)
```

**HTTP caching headers for full-res (size = 0):**

```
Cache-Control: public, max-age=3600
ETag: "<mtime_ns>-<size>"
```

The shorter TTL on full-res accounts for the larger memory cost browser-side and the fact that full-res may be re-encoded from rawpy each time unless the disk cache holds the result.

#### 1.4 Conditional request handling

If `If-None-Match` header matches the ETag, return `304 Not Modified` with no body. FastAPI / Starlette's `FileResponse` does this automatically when serving from disk; for bytes produced in-memory, implement it explicitly in the route handler.

#### 1.5 device_key / source alignment

The existing Qt implementation has **no device_key in the image cache key** (`image_service.py:128–131`). The disk-cache fingerprint (`AUTOTUNE_RECIPE_VERSION` keying in the scanner) is unrelated to image caching. Do not add device_key to the image cache key — it would invalidate the existing on-disk cache and is unnecessary because the key's uniqueness comes from the absolute path (which encodes the device implicitly via the drive letter / UNC prefix).

---

### 2. QImage → bytes rewrite

#### 2.1 Cache entry type change

| | Qt app (current) | Web app (new) |
|---|---|---|
| In-memory cache value | `QImage` object (`_MemCacheItem.image: QImage`) | `bytes` (JPEG-encoded) |
| Byte budget metric | `image.sizeInBytes()` (`image_service.py:172`) | `len(jpeg_bytes)` |
| Cache class | `_ByteBudgetLRUCache[QImage]` | `_ByteBudgetLRUCache[bytes]` |

The `_ByteBudgetLRUCache` class itself (`image_service.py:146–193`) is unchanged except that `_MemCacheItem.image: QImage` becomes `_MemCacheItem.data: bytes` and `byte_size = image.sizeInBytes()` becomes `byte_size = len(data)`. The thread-safety model (single `threading.Lock`) carries over unchanged; the web server's thread pool hits the same shared cache.

**Budget sizing:** `_compute_cache_budgets()` (`image_service.py:111–125`) continues using the RAM probe — `min(256 MB, total_RAM // 32)` split 25/75 thumb/preview. JPEG bytes are smaller than raw QImage pixels (roughly 8–20× smaller at quality=85 for real photos), so effective capacity in item-count terms increases substantially. No change needed to the budget formula.

#### 2.2 Removed code

- `_pil_to_qimage()` (`image_service.py:496–518`) — deleted entirely. Its only callers are `_load_via_pillow` and `_try_rawpy_embedded_thumb`.
- `QImage`/`QImageReader`/`QPixmap` imports — removed from `infrastructure/image_service.py`.
- `QColor`, `QSize`, `Qt` imports — removed.
- The `memory_probe` hook at `image_service.py:376–382` (`track_qt_alloc("QImage", ...)`) — removed (probe is Qt-specific).

#### 2.3 Decode pipeline rewrite (per format)

Each `_load_from_source` path returns `bytes` (JPEG) instead of `QImage`. The fallback chain is preserved; only the terminal conversion changes.

**Pillow path (HEIC/HEIF, general images):**

```python
def _load_via_pillow(self, path: str, requested_side: int) -> bytes | None:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)           # orientation correction
        if requested_side > 0:
            im.thumbnail((requested_side, requested_side), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        rgb = im.convert("RGB")                    # JPEG requires RGB, not RGBA
        rgb.save(out, format="JPEG", quality=85, optimize=False)
        return out.getvalue()
```

**rawpy embedded-thumb path (DNG fast path):**

The existing `_try_rawpy_embedded_thumb` (`image_service.py:568–645`) already calls `_pil_to_qimage(pil_im)` after EXIF transpose. Replace that call:

```python
# after ImageOps.exif_transpose(pil_im):
if requested_side > 0:
    pil_im.thumbnail((requested_side, requested_side), Image.Resampling.LANCZOS)
out = io.BytesIO()
pil_im.convert("RGB").save(out, format="JPEG", quality=85)
return out.getvalue()   # bytes, not QImage
```

**rawpy full-decode path (DNG postprocess fallback):**

```python
rgb = raw.postprocess(...)     # numpy ndarray (H, W, 3) uint8
pil_img = Image.fromarray(rgb, mode="RGB")
if requested_side > 0:
    pil_img.thumbnail((requested_side, requested_side), Image.Resampling.LANCZOS)
out = io.BytesIO()
pil_img.save(out, format="JPEG", quality=85)
return out.getvalue()
```

**QImageReader path (existing JPEG, PNG, etc.):** QImageReader is eliminated. Replace with Pillow for everything except the WIC fallback. Pillow handles JPEG, PNG, TIFF, BMP, GIF, WebP natively. The cases where QImageReader was the only option in the Qt app were HEIC (now covered by pillow-heif) and DNG (now rawpy). The generic Pillow path for non-HEIC non-DNG files:

```python
def _load_via_pillow_generic(self, path: str, requested_side: int) -> bytes | None:
    with Image.open(path) as im:
        try:
            im = ImageOps.exif_transpose(im)
        except (OSError, ValueError, AttributeError):
            pass
        if requested_side > 0:
            im.thumbnail((requested_side, requested_side), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.convert("RGB").save(out, format="JPEG", quality=85)
        return out.getvalue()
```

**WIC/Shell ctypes path** — stays Windows-only. See §4 for the COM STA threading contract. The HBITMAP → pixel buffer conversion already produces a raw ARGB32 buffer. Replace the `QImage(buf, ...)` + `.copy()` step with Pillow:

```python
# where the existing code does:
#   qi = QImage(bytes(buf), width, height, row_bytes, QImage.Format_ARGB32)
#   img_local = qi.convertToFormat(QImage.Format_RGB32)
#   return img_local.copy()
# replace with:
pil_img = Image.frombytes("RGBA", (width, height), bytes(buf), "raw", "BGRA")
if requested_side > 0:
    pil_img.thumbnail((requested_side, requested_side), Image.Resampling.LANCZOS)
out = io.BytesIO()
pil_img.convert("RGB").save(out, format="JPEG", quality=85)
return out.getvalue()
```

(WIC GetDIBits with `biBitCount=32` returns BGRA-ordered bytes on Windows; Pillow's "raw"/"BGRA" decoder handles this correctly.)

**Disk cache:** replace `img.save(str(disk_file), "JPEG", quality=85)` (`image_service.py:371`) with:

```python
disk_file.write_bytes(jpeg_bytes)
```

Reading from disk: replace `QImage(str(disk_file))` with `disk_file.read_bytes()`.

**Placeholder for decode failure:** replace the grey `QImage(64, 64, ...)` sentinel with a pre-generated 64×64 grey JPEG stored as a module-level constant `_PLACEHOLDER_JPEG: bytes` — generated once at module import via Pillow, so there is no runtime cost.

#### 2.4 Decode coverage table (preserved / changed)

| Format | Qt app path | Web app path | Platform |
|---|---|---|---|
| HEIC/HEIF | pillow-heif → Pillow → WIC Shell | pillow-heif → Pillow → WIC Shell (Windows only) | Windows full; Linux/macOS loses WIC fallback |
| DNG ProRAW | rawpy embedded JPEG → rawpy postprocess → QImageReader → WIC Shell | rawpy embedded JPEG → rawpy postprocess → WIC Shell | Windows full; Linux/macOS loses WIC |
| JPEG/PNG/TIFF/BMP/WebP | QImageReader (or Pillow for HEIC) | Pillow (direct) | All platforms |
| CR2/CR3/NEF/ARW/RAF/RW2 | QImageReader → WIC Shell | Pillow (partial) → WIC Shell (Windows) | Linux/macOS: best-effort via Pillow; no WIC |
| Video (MP4/MOV) | Not decoded by image_service (video player handles) | Not decoded; thumbnail = first frame via a separate ffprobe call (out of scope for this contract) | — |

**Coverage lost on non-Windows:** any exotic RAW or HEIC variant that rawpy/pillow-heif cannot decode has no WIC fallback. This is an accepted limitation (§8 of feasibility report); the endpoint returns the 64×64 placeholder JPEG with a `X-Decode-Fallback: placeholder` response header so the client can distinguish "valid small image" from "decode failed".

---

### 3. Full-res strategy for 50–130 MB ProRAW DNGs

#### 3.1 The tradeoff

| Strategy | Latency | Quality | Server memory | Bandwidth |
|---|---|---|---|---|
| JPEG re-encode (quality=92) | 2–8 s decode on first request; ~0 on cache hit | Lossy (visible at 100% zoom for pixel-peeping) | ~30 MB for one file (JPEG bytes) | ~8–25 MB per view |
| Range streaming of decoded bytes (raw RGB) | Same decode cost; Range splits the HTTP response | Lossless | 300–400 MB (full decoded numpy array stays alive) | 300–450 MB per view |

For a single-user desktop app where the server and client are the same machine, **bandwidth is free and memory is the constraint**. The feasibility report flags this as "the one genuine perf regression" (§3.3) for pixel-peeping review.

#### 3.2 Chosen contract: two-tier with a quality knob

**Default (size=0, quality=95):** re-encode to JPEG at quality 95 (visibly identical to lossless at normal 1:1 zoom for most photographers; only distinguishable on synthetic gradients or at 200%+ zoom). The endpoint writes the re-encoded JPEG into the disk cache under a dedicated `full/` sub-directory (`thumbs/v1/full/{sha1_of_path}.jpg`), keyed on the path SHA1 alone (no size component, since size=0 is always full). Subsequent loads for the same file are instant cache hits.

```
GET /api/image?path=<enc>&size=0
→ 200 image/jpeg (quality=95)
  Cache-Control: public, max-age=3600
  ETag: "<mtime_ns>-<size>"
  X-Image-Mode: reencoded-jpeg
  X-Original-Dims: <w>x<h>
```

**Optional lossless path (size=0&raw=1):** reserved for a future "pixel-peep" mode. Not in Phase 0. Endpoint returns `415 Unsupported Media Type` with `{"error": "raw_mode_not_implemented"}` until explicitly built. This reserves the query-parameter contract without blocking Phase 0.

**Tradeoff knob — `quality` parameter:**

```
GET /api/image?path=<enc>&size=0&quality=75   # faster encode, smaller transfer
GET /api/image?path=<enc>&size=0&quality=95   # default
```

`quality` is clamped to [60, 97]. Values outside this range return 400. This is the concrete lever for users who want faster first-load at the cost of fidelity.

**Memory budget:** the `_ByteBudgetLRUCache` for full-res (preview tier, 75% of combined budget) holds the full-res JPEG bytes. A 130 MB DNG re-encoded to quality=95 produces ~20–35 MB JPEG. The existing 192 MB preview-tier default comfortably holds 5–9 concurrent full-res DNGs. This is the correct tradeoff for a solo desktop user.

---

### 4. COM STA threading for WIC/Shell ctypes

#### 4.1 The problem

The existing `_load_via_shell_thumbnail` (`image_service.py:648–857`) calls `ole32.CoInitialize(None)` / `CoUninitialize()` inline on whatever thread calls it (image_service.py:692, 854). In the Qt app this is a QThreadPool worker thread that happens to be STA-clean. In a FastAPI async context, the call originates on an asyncio event loop thread, which must never block, and on which `CoInitialize` with no apartment type means COM chooses arbitrarily — a latent hang risk.

#### 4.2 The pattern

```python
import concurrent.futures, ctypes

def _sta_initializer() -> None:
    """Called once per ThreadPoolExecutor worker thread at thread creation."""
    # COINIT_APARTMENTTHREADED = 0x2 (STA)
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)

# Module-level singleton — created once when image_service imports
_WIC_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None

def _get_wic_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _WIC_EXECUTOR
    if _WIC_EXECUTOR is None:
        _WIC_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="wic-sta",
            initializer=_sta_initializer,
        )
    return _WIC_EXECUTOR
```

**Why max_workers=2:** WIC Shell thumbnails are I/O-bound (shell cache lookup) and are only needed for the WIC fallback path — HEIC/HEIF and most DNGs never reach it. Two workers is enough for the concurrent thumbnail requests a user can realistically generate; more workers would each hold their own COM STA apartment, which has non-trivial overhead.

**Why `CoInitializeEx(None, 0x2)` (STA) not `0x0` (COINIT_MULTITHREADED):** The existing code calls `ole32.CoInitialize(None)` which defaults to STA (`image_service.py:692`). `IShellItemImageFactory::GetImage` is a shell COM object that must be called on an STA thread. MTA violates this contract and produces sporadic `RPC_E_WRONG_THREAD` errors.

**`CoUninitialize` placement:** The existing code wraps each call in a try/finally with `CoUninitialize()` (`image_service.py:854`). With the executor pattern, `CoInitializeEx` is called once per thread lifetime (via `initializer`), so `CoUninitialize` moves to a thread-level atexit, not per-call. The executor's `shutdown(wait=True)` on process exit is the cleanup trigger.

**FastAPI async integration:**

```python
async def _load_via_shell_thumbnail_async(
    self, path: str, side: int
) -> bytes | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _get_wic_executor(),
        self._load_via_shell_thumbnail_sync,  # the existing logic, returns bytes
        path,
        side,
    )
```

All other decode paths (Pillow, rawpy) are CPU-bound and run on the default `ThreadPoolExecutor` via `run_in_executor(None, ...)` — not the WIC executor. Only the WIC path needs the STA-initialised pool.

**Windows-only guard:** wrap the entire `_get_wic_executor()` and `_load_via_shell_thumbnail_async` in `if sys.platform == "win32":` checks. On non-Windows, the WIC path returns `None` (same as current behaviour when `windll` is absent).

---

### 5. Headless operability — Qt-free path

#### 5.1 Decision: eliminate QApplication entirely from image_service

The feasibility report correctly identifies `infrastructure/image_service.py` as "the only backend Qt leak" and the `QImage`/`QImageReader` imports as the coupling to remove. The decision is **full Qt removal** — do not accept even a headless `QApplication` with `-platform offscreen`.

**Rationale:**
- A `QApplication(sys.argv, platform="offscreen")` requires the Qt platform plugin (`qoffscreen.dll`) to be present, adds ~50 MB to the installed footprint, and forces uvicorn workers to carry Qt's event-loop machinery for a purely I/O-bound image-encode task.
- Pillow covers every format QImageReader handled except exotic RAW variants, which rawpy handles. There is no functional gap that requires Qt in the image path.
- The WIC/Shell path never used Qt; it calls ctypes directly and was always Qt-free in execution (it just happened to return a QImage which was then stored in the Qt-typed cache).
- Headless operability is binary: if QApplication is absent, the server starts; if it is present, any pytest run that imports `image_service` without a display will fail or require `-platform offscreen` plumbing.

#### 5.2 What is removed vs what is added

**Removed from `infrastructure/image_service.py`:**

```python
# All of these go:
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader

# Removed methods:
_pil_to_qimage()
_load_via_shell_thumbnail → QImage portions (HBITMAP → QImage(buf, ...) block)
_looks_like_placeholder() → QColor pixel probe (now uses Pillow pixel access)
```

**Added to `infrastructure/image_service.py`:**

```python
import io
import sys
```

Pillow and rawpy are already imported. No new dependencies.

#### 5.3 Qt adapter shim for the existing Qt app (Phase 0 backward compat)

The Qt app (`app/views/image_tasks.py`) expects `ImageService.get_thumbnail()` and `get_preview()` to return a `QImage`. During Phase 0 the rewritten service returns `bytes`. The Qt adapter is a two-line shim:

```python
# In app/views/image_tasks.py (Qt side only, not in infrastructure/):
from PySide6.QtGui import QImage

def _bytes_to_qimage(jpeg: bytes) -> QImage:
    q = QImage()
    q.loadFromData(jpeg)       # Qt can decode JPEG natively
    return q
```

`_ImageTask.run()` calls `_bytes_to_qimage(img)` before emitting `imageLoaded(token, path, image)`. This keeps all Qt objects in `app/views/` and makes `infrastructure/image_service.py` fully Qt-free. The `QImage.loadFromData` path does not honour EXIF orientation — but EXIF rotation has already been applied by Pillow's `exif_transpose` during encoding, so the bytes are already correctly oriented.

#### 5.4 `_looks_like_placeholder` without Qt

Replace the `QColor(img.pixel(x, y))` probe (`image_service.py:860–877`) with Pillow:

```python
def _looks_like_placeholder(self, jpeg: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(jpeg)) as im:
            if im.width != 64 or im.height != 64:
                return False
            rgb = im.convert("RGB")
            def near(px, target=220): return abs(px - target) <= 5
            for xy in [(0,0), (31,31), (63,63)]:
                r, g, b = rgb.getpixel(xy)
                if not (near(r) and near(g) and near(b)):
                    return False
            return True
    except Exception:
        return False
```

---

### 6. FastAPI Route Handler (complete signature)

```python
# app/web/routes/image.py

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import Response as FastResponse
import asyncio, hashlib, sys
from pathlib import Path
from infrastructure.image_service import ImageService

router = APIRouter()

@router.get("/api/image")
async def get_image(
    request: Request,
    path: str = Query(..., description="Absolute file path (URL-encoded)"),
    size: int = Query(0, ge=0, le=65535, description="Max longest side; 0=full-res"),
    quality: int = Query(95, ge=60, le=97, description="JPEG quality (full-res only)"),
) -> Response:
    svc: ImageService = request.app.state.image_service

    # Security: path must be an absolute path within allowed roots
    resolved = _validate_path(path, request.app.state.allowed_roots)
    if resolved is None:
        return FastResponse(status_code=403)

    # Fetch bytes (mem cache → disk cache → decode)
    jpeg: bytes | None = await asyncio.get_event_loop().run_in_executor(
        None, svc.get_image_bytes, str(resolved), size
    )

    if jpeg is None or len(jpeg) == 0:
        return FastResponse(status_code=500, content=b'{"error":"decode_failed"}',
                            media_type="application/json",
                            headers={"X-Decode-Fallback": "placeholder"})

    # ETag from content hash of the bytes (stable, cheap for cached responses)
    etag = f'"{hashlib.sha1(jpeg[:512]).hexdigest()}"'   # first 512 bytes for speed
    if request.headers.get("if-none-match") == etag:
        return FastResponse(status_code=304)

    max_age = 86400 if size > 0 else 3600
    headers = {
        "Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age*7}",
        "ETag": etag,
        "Content-Type": "image/jpeg",
        "X-Image-Mode": "reencoded-jpeg",
    }
    if size == 0:
        headers["X-Image-Mode"] = "reencoded-jpeg"
    return FastResponse(content=jpeg, media_type="image/jpeg", headers=headers)
```

`svc.get_image_bytes(path, size)` is the renamed public entry point (previously `get_thumbnail`/`get_preview` collapsed into one):

```python
def get_image_bytes(self, path: str, requested_side: int) -> bytes:
    """Return JPEG bytes for path, from cache or decoded. Never raises."""
    ...
```

---

### 7. Security note on path parameter

The `path` query parameter is a raw filesystem path supplied by the browser. **Path traversal is a real risk.**

```python
def _validate_path(raw: str, allowed_roots: list[Path]) -> Path | None:
    try:
        p = Path(raw).resolve()
    except Exception:
        return None
    for root in allowed_roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    return None
```

`allowed_roots` is set at startup from the loaded manifest's source directories plus any configured scan roots. Requests for paths outside these roots return 403.

> **Security addendum (2026-07-11, #662).** The original note above ended "this is the
> only security gate needed for a localhost app — the server is not accessible from the
> network." That waved off the browser-borne surface; the hardening pass closed it and
> settled the trust boundary:
>
> 1. **Origin/Host CSRF guard (shipped, `app/web/security.py`).** Every mutating method
>    (anything but GET/HEAD/OPTIONS) is refused with 403 unless the `Host` hostname is
>    loopback (DNS-rebinding guard — a rebound request arrives with the attacker's
>    hostname) AND any browser-supplied `Origin` (or, failing that, `Referer`) matches
>    the server's own `host:port` exactly (literal `Origin: null` — the sandboxed-iframe
>    vector — is refused; the Vite dev origins are allowed only under
>    `PHOTO_MANAGER_DEV_MODE=1`). Headerless non-browser clients pass: a browser cannot
>    strip its own Origin on a cross-origin write. Deliberate side effect: an instance
>    someone binds beyond loopback serves reads but refuses all mutations.
> 2. **Manifest-root trust boundary (decided: manifest-derived roots stay).**
>    `allowed_roots` continues to derive from the loaded manifest's own folders. The
>    threat model is explicit: opening a manifest is a trusted act by the single local
>    user, in the same trust class as opening a document or running a script — a
>    fully-crafted hostile manifest is out of scope. An OS-configured independent
>    allow-list would add real friction for zero realistic gain on this deployment
>    model; revisit ONLY if the server is ever deliberately exposed beyond loopback.
> 3. **TOCTOU / symlink race (accepted-low).** `is_under_roots` resolves symlinks at
>    check time; the residual check-then-op race requires a hostile local process, at
>    which point the attacker already runs code as the user. No `O_NOFOLLOW` recheck.


---

## Contract 4 — QA-Harness Architecture (UIA → Playwright)

### 1. Playwright Harness Architecture

#### 1.1 Lifecycle: `_batch.py` → `_web_batch.py`

The existing `_batch.py` (`qa/scenarios/_batch.py`) orchestrates five steps per scenario: configure → launch subprocess → wait for window → drive → close → wait for exit. The web harness replaces steps 2–5 wholesale; step 1 (configure) is kept intact because it writes `qa/settings.json`, which the FastAPI server reads on startup identically to how the Qt app reads it.

```
# Current Qt flow (qa/scenarios/_batch.py:522–607)
configure(name)            # writes qa/settings.json  ← KEEP AS-IS
Popen([PY, "main.py"])     # launches PySide6 app
_wait_for_main_window()    # Win32 EnumWindows poll
subprocess.run([PY, "-m", f"qa.scenarios.{name}"])  # driver (UIA)
_close_window()            # WM_CLOSE + Leave dialog
proc.wait()                # OS cleanup

# New web flow (qa/web/_batch.py)
configure(name)            # UNCHANGED
_start_server(name)        # uvicorn subprocess: PHOTO_MANAGER_HOME=qa python -m app.web.main
_wait_for_server()         # HTTP poll GET /api/health until 200, max 8s
playwright.chromium.launch()
page = browser.new_page()
page.goto("http://localhost:8765")
subprocess.run([PY, "-m", f"qa.web.scenarios.{name}"], env={"PW_PAGE_URL": ...})
page.close(); browser.close()
server_proc.terminate(); server_proc.wait(timeout=5)
```

The server port is fixed at `8765` for QA runs (configurable via `PHOTO_MANAGER_QA_PORT`). The `_wait_for_server()` poll is `GET /api/health → {"status": "ok"}` with 100 ms intervals and an 8 s hard ceiling — the same contract as `_wait_for_main_window()`.

**Key departure from Qt batch:** the browser process is long-lived per scenario (not per step), matching Playwright's `Browser → BrowserContext → Page` model. The page is created fresh per scenario and torn down after the driver subprocess exits.

#### 1.2 Page-Object / Helper Library: `qa/web/_pw.py`

`_pw.py` replaces `_uia.py` (72 functions). The mapping is function-for-function where semantics are equivalent; functions whose only job was Win32 plumbing are dropped entirely.

**Driving model:** drivers import `from qa.web import _pw` and receive a Playwright `Page` object, not a `UIAWrapper`. The harness injects the page URL into the driver's environment; the driver calls `_pw.connect(page)` to get a `PWContext` dataclass that caches the page reference and exposes all helper methods.

```python
# qa/web/_pw.py  —  public surface
@dataclass
class PWContext:
    page: Page

    # --- Connection ---
    @classmethod
    def connect(cls, page: Page) -> "PWContext": ...

    # --- Navigation ---
    def open_scan_dialog(self) -> "ScanDialogCtx": ...
    def open_execute_dialog(self) -> "ExecuteDialogCtx": ...
    def open_action_dialog(self) -> "ActionDialogCtx": ...

    # --- Main-window probes ---
    def read_result_rows(self) -> list[dict]: ...
    # Returns [{"group": str, "filename": str, "action": str, "score": str, ...}]
    def read_tree_row_order(self) -> list[str]: ...          # filename column only
    def read_selected_basenames(self) -> list[str]: ...
    def read_status_text(self) -> str: ...
    def read_main_window_state(self) -> dict: ...           # empty_state_visible, tree_visible, status_text
    def read_column_headers(self) -> list[str]: ...

    # --- Menu ops ---
    def menu_path(self, menu: str, item: str) -> None: ...
    def probe_menu_items(self, menu: str) -> list[tuple[str, bool]]: ...

    # --- Tree interactions ---
    def left_click_row(self, basename: str) -> None: ...
    def ctrl_click_row(self, basename: str) -> None: ...
    def right_click_row(self, basename: str) -> None: ...
    def double_click_row(self, basename: str) -> None: ...
    def click_column_header(self, header_text: str) -> None: ...

    # --- Wait helpers ---
    def wait_for_status(self, regex: str, timeout: float = 5.0) -> bool: ...
    def wait_for_element(self, testid: str, timeout: float = 5.0) -> None: ...
    def assert_no_dialog_within(self, testid: str, seconds: float = 1.0) -> bool: ...

    # --- Scan dialog (returned from open_scan_dialog) ---
    # ScanDialogCtx exposes:
    #   .add_source(path: str) -> None
    #   .read_sources() -> list[str]
    #   .remove_source(idx: int) -> None
    #   .toggle_recursive(idx: int) -> None
    #   .remove_all() -> None
    #   .start_scan() -> None
    #   .wait_for_done(timeout: float) -> str   # returns log text
    #   .close_and_load() -> None

    # --- Dropped (no web analog, or collapsed into page API) ---
    # list_process_windows, find_popup, _focus, connect_by_handle,
    # close_window_by_hwnd, list_top_level_windows, list_explorer_windows,
    # close_new_shell_windows, force_foreground, _key_down/_key_up,
    # _find_native_dialog_action_button, _find_filename_edit,
    # save_manifest_via_native_dialog, open_manifest_via_native_dialog
    # (native file dialogs → <input type="file"> + fetch; no OS chrome)
```

#### 1.3 `data-testid` Naming Convention

The convention maps Qt `objectName` suffixes to `data-testid` values. The rule is: take the camelCase `objectName` string, prefix with the component scope. Where Qt used dotted automation IDs (`QApplication.ScanDialog.QSplitter.QPlainTextEdit`), the web uses flat `data-testid` strings.

**Naming rule:** `{component}-{role}` in kebab-case. Component is the dialog or surface name; role is the widget's functional name.

```
Qt objectName / UIA auto_id                → data-testid
──────────────────────────────────────────────────────────────────────────────
# Main window
(result tree, no objectName)               → main-result-tree
(status bar, no objectName)                → main-status-bar
(empty state label)                        → main-empty-state
(menu bar)                                 → main-menu-bar
(menu item by text)                        → menu-{kebab(text)}   e.g. menu-file-scan-sources
(column header by text)                    → col-header-{kebab(text)}

# Decision table rows
(group header row)                         → row-group-{group_id}
(file row)                                 → row-file-{basename}
(action cell within file row)              → row-file-{basename}-action
(score cell)                               → row-file-{basename}-score
(lock cell)                                → row-file-{basename}-lock

# Scan dialog
SCAN_AID_LOG  (QPlainTextEdit)             → scan-log
SCAN_AID_OUTPUT_PATH  (QLineEdit)          → scan-output-path
SCAN_AID_SOURCE_TABLE  (QTableWidget)      → scan-source-table
SCAN_AID_TREE_PATH_FIELD  (QLineEdit)      → scan-path-field
"Start Scan" (QPushButton)                 → scan-btn-start
"Close & Load"                             → scan-btn-close-load
"+ Add"                                    → scan-btn-add
"Browse…"                                  → scan-btn-browse
"Remove All"                               → scan-btn-remove-all
scan progress frame (objectName=scanProgressFrame) → scan-progress-frame
per-source row remove button               → scan-source-{idx}-remove
per-source recursive checkbox              → scan-source-{idx}-recursive
scan autotune checkbox (s66)               → scan-autotune-checkbox
scan error message (s38 inline)            → scan-path-error

# Execute Action dialog
(tree inside dialog)                       → execute-tree
"Execute" button                           → execute-btn-execute
"Select by Field/Regex…"                   → execute-btn-select-by
"Execute selected"                         → execute-btn-execute-selected
type-filter combo (objectName=executeDialogTypeFilterCombo) → execute-type-filter
all-delete banner                          → execute-all-delete-banner
per-row decision cells (same pattern as main tree, scoped to dialog)
                                           → execute-row-file-{basename}-action

# Set Action by Field dialog (ActionDialog / SelectDialog)
regexFieldCombo                            → action-field-combo
regexLineEdit                              → action-regex-input
regexSimpleRow                             → action-simple-row
regexSimpleOpCombo                         → action-simple-op
regexSimpleText                            → action-simple-text
regexSimpleDisabledNote                    → action-simple-disabled-note
regexRegexRow                              → action-regex-row
regexValidationIcon                        → action-validation-icon
regexValidationError                       → action-validation-error
regexMatchCounter                          → action-match-counter
regexActionCombo                           → action-action-combo
regexApplyButton                           → action-btn-apply
regexPreviewList                           → action-preview-list
regexPreviewTruncated                      → action-preview-truncated
numericConditionRow                        → action-numeric-row
numericModeThreshold                       → action-numeric-mode-threshold
numericModeTopN                            → action-numeric-mode-topn
numericCmpCombo                            → action-numeric-cmp
numericValueEdit                           → action-numeric-value
numericThresholdError                      → action-numeric-error
numericOrderCombo                          → action-numeric-order
numericNSpinBox                            → action-numeric-n

# LockedRowsConfirmDialog
(dialog root)                              → lock-confirm-dialog
"Unlock & Apply to All" / context variants → lock-confirm-btn-unlock-apply
"Apply to Unlocked Only"                   → lock-confirm-btn-unlocked-only
"Cancel"                                   → lock-confirm-btn-cancel

# DeleteRegexConfirmDialog
(dialog root)                              → delete-confirm-dialog
confirm button (variable label)            → delete-confirm-btn-confirm
"Cancel"                                   → delete-confirm-btn-cancel

# SingletonPruneConfirmDialog (s61, s67)
(dialog root)                              → prune-confirm-dialog

# Preview pane
preview_single_label (objectName)          → preview-single-image
info_label                                 → preview-info

# Full-res viewer (s68)
(dialog root)                              → fullres-dialog
(image element)                            → fullres-image
```

#### 1.4 Sharding / Parallelism

The Qt batch uses 5 shards (sorted-stride, CI matrix). The `s23a`/`s23b` unit pair is kept together. Playwright enables true parallelism but the web app is a single-process server; running multiple browsers against one server simultaneously would interleave state (especially manifests written by destructive scenarios). The sharding strategy stays **sequential within a shard**, but the number of shards can increase from 5 to 10 because per-scenario overhead drops from ~15 s (UIA launch + settle + close) to ~4 s (navigate + assert + navigate away).

```python
# qa/web/_batch.py — sharding keeps the same select_shard() function verbatim
# The s23a/s23b pair constraint still applies (s23b reads s23a's persisted state)
# New: --total-shards default is 10 instead of 5
# Parallelism is across shards (CI matrix), not within a shard (sequential)
```

---

### 2. Scenario Migration Contract

#### 2.1 Mapping Table Structure

Each of the 68 entries in `ALL_SCENARIOS` (_batch.py:35–291) maps to one of three buckets. The table is codified in `qa/web/MIGRATION.md` (one row per scenario):

```
| Scenario | Bucket | Migration notes | New testid(s) exercised |
|---|---|---|---|
| s01_happy_path | clean | ... | main-result-tree, main-status-bar |
...
```

The three buckets:

**Bucket A — Clean (~50 scenarios, ~74%):** Direct translation. The scenario opens the browser app, drives the same flows using `_pw` helpers, and makes equivalent assertions. No semantic changes.

**Bucket B — Rework (~10 scenarios, ~15%):** The flow exists in the web app but the assertion mechanism changes because the observation channel differs. Examples:

- `s39_window_geometry_persist` — The QSettings `window_state.ini` round-trip has no web analog. Rework: browser `localStorage`/IndexedDB persistence is tested via `page.evaluate("localStorage.getItem('windowGeometry')")` after a simulated resize; the "re-launch" step becomes `page.reload()`.
- `s22_language_switch` / `s58_language_switch_preserves_manifest` — i18n switch via a dropdown still exists; the "restart required" Qt pattern becomes a live re-render. Rework: assert the DOM text changed without a reload.
- `s47_column_layout_persist` — column drag-to-reorder: TanStack Table handles this in-browser. Rework: `page.drag_and_drop()` on column headers; assert `localStorage` column order after reload.
- `s48_dialog_geometry_persist` — dialog size persistence: if dialogs use `localStorage`, assert via `page.evaluate`; if dialogs are always full-viewport on web, this becomes a no-op test of the always-remembered concept (i.e. the web equivalent is "dialogs remember their content state across open/close").
- `s45_sort_persistence` — column-sort persisted across manifest reload: assert `localStorage['sortState']` survives `page.reload()`.

**Bucket C — No-analog (~4–8 scenarios, ~6–12%):** The tested behavior is pure OS/desktop and has no web equivalent. These scenarios are **not ported**; they are replaced by API-level / property tests in `tests/` (Layer 1 or Layer 2).

> **AS-SHIPPED NOTE (2026-07-24).** The predicted set below drifted during porting; the authority is `qa/web/scenario_map.yml` (`status: skip`, each entry carries its full rationale). The final skip set is **s18** (no web Log-menu surface — deliberate MenuBar deferral), **s28** (browser close cannot host a 3-button Save/Leave/Back dialog; the close-during-scan guard is covered by s63), **s48** (web dialogs have no user-resizable geometry to persist — moved here from Bucket B), and **s59** (the Qt tree-desync class is architecture-eliminated by the single Zustand source of truth; the data assertion lives in s30). Of the four predicted firm cases, only s18 held: s19, s26, and s41 were ported after all via Bucket-B rework.

The firm no-analog cases:
- `s19_context_menu_open_folder` — Opens `explorer.exe` via `os.startfile`/`subprocess.Popen(["explorer", ...])`. The web app has no right-click "Open Folder in Explorer" action (the path is displayed but no OS shell integration fires). Migration: delete the scenario; the unit test for the URL-encode / path-format helper covers the contract. File a follow-up issue for "web: copy path to clipboard" as a replacement UX.
- `s18_log_menu` — Opens Notepad/log-viewer via `os.startfile`. The web app serves log content at `GET /api/logs/latest` as JSON; the "open in Notepad" action does not exist. Migration: replace with an API-level pytest test (`tests/test_web_log_api.py`) asserting the response shape and content.
- `s26_keyboard_navigation` — Alt+F mnemonic, Tab-cycle inside a native OS dialog, Esc-dismiss. The web app has its own keyboard semantics (Tab focus, Esc closes dialogs). Rework to B: rewrite using `page.keyboard.press("Escape")` and `page.keyboard.press("Tab")`; the mnemonic test becomes a `aria-keyshortcuts` attribute probe.
- `s41_empty_state_action_buttons` — Tests "native file picker opens" via `QFileDialog.getOpenFileName`. The web equivalent is a custom file-path input (no OS-native picker is driven). Rework to B: assert the custom path-input panel appears; omit the "picker opens" assertion.

Ambiguous (lean B): `s24_stale_manifest_paths` (stale-path UX exists in the web app, needs a rework of how we inject staleness), `s40_results_tree_double_click` (double-click row: web has the same interaction but drives it with `page.dblclick()`).

#### 2.2 Soft-Probe Migration

Qt soft-probes use `print("probe_status: ok=True field=Action")` embedded in drivers (`qa/scenarios/sNN_*.py`). The mechanism is stdout scraping by the batch runner.

Web equivalent — two forms:

**Form 1 (console.log):** The React components emit `console.log("probe_status: ok=true field=Action")` in development mode. The driver captures this via Playwright's `page.on("console", handler)` and writes to a `probe_log` list. Post-scenario, the batch runner scans `probe_log` for `probe_status:` lines and prints them.

```python
# qa/web/_pw.py — soft-probe capture
probe_log: list[str] = []
page.on("console", lambda msg: probe_log.append(msg.text) if msg.text.startswith("probe_status:") else None)
```

**Form 2 (test-event endpoint):** For probes that inspect application state rather than UI structure (e.g. "is the match_fn None?"), the service layer exposes `GET /api/debug/state` (dev/QA mode only, gated by `PHOTO_MANAGER_QA_MODE=1`). The driver calls this via `page.evaluate("fetch('/api/debug/state').then(r=>r.json())")` and asserts on the returned dict.

Both forms preserve the `probe_status:` tag so the existing batch-runner log-scraping logic works unchanged.

#### 2.3 Static Probes: Unchanged

`tests/test_ui_probes.py` runs as pytest in CI and inspects AST / YAML / Python import surfaces — none of which are web-specific. The probes remain in `tests/test_ui_probes.py` with zero changes:

- `test_probe_select_dialog_exposes_every_filterable_tree_column` — imports `dialog_handler_helpers.default_action_dialog_fields()` (Qt-free), compares against `constants.COL_*`. The web frontend must expose the same field list in its dropdown; the probe validates the source of truth, not the rendered DOM.
- Translation-passthrough probes (compare `en.yml` vs `zh_TW.yml`) — YAML-only, no Qt.
- Bridge-gap probes (AST walk of `action_handlers.py` and `context_menu.py`) — source-text analysis, Qt-free.
- `test_uia_label_coupling.py` — references `_uia.py` constants but only reads them as strings; the constants can be moved to a shared `qa/constants.py` that both `_uia.py` and the web harness import.

The only static probe that needs adjustment is any probe that calls `app.views.*` constructors (those will fail once the Qt view layer is removed). The one confirmed case is `build_model` in `test_ui_probes.py:37` (imports `app.views.tree_model_builder`). This probe should be migrated to `tests/test_web_tree_contract.py` using the headless service layer rather than the Qt model builder. File as a follow-up issue.

---

### 3. Three-Layer Mapping for the Web App

```
Layer | What | Tool | Runs in CI | Changes from Qt version
──────────────────────────────────────────────────────────────────────────────
1 — Unit       | Pure logic, service contracts,   | pytest          | Yes       | UNCHANGED — scanner/, core/,
                 API route schemas, SSE event       |                |           | infrastructure/ tests carry
                 types, serialization              |                |           | over verbatim. New: tests for
                                                   |                |           | FastAPI route contracts in
                                                   |                |           | tests/test_api_*.py
──────────────────────────────────────────────────────────────────────────────
2 — Integration | Real exiftool, real send2trash,  | pytest          | Local only| UNCHANGED — same boundary
                 real rawpy/pillow-heif,            | @mark.          |           | set, same on-demand policy.
                 real SSE stream shape              | integration    |           | New: tests/integration/
                                                   |                |           | test_sse_scan_events.py
                                                   |                |           | (one long-running SSE stream
                                                   |                |           | against real scanner)
──────────────────────────────────────────────────────────────────────────────
3 — QA/E2E     | User flows, label drift,          | Playwright      | Yes (new) | REPLACES qt qa-batch.
                 state transitions, UX regressions  | (qa/web/)      |           | ~50 scenarios port clean,
                                                   |                |           | ~10 rework, ~4-8 no-analog
──────────────────────────────────────────────────────────────────────────────
Probe          | Structural invariants             | pytest          | Yes       | MOSTLY UNCHANGED.
                 (field-vs-column drift, label      | (static)       |           | One probe migrates off Qt
                 uniqueness, bridge gaps)            | Playwright     |           | model builder. New live
                                                   | (live DOM)     |           | probes: DOM-structure checks
                                                   |                |           | via page.locator('[data-testid]')
```

**API-level tests for no-analog scenarios (Bucket C):** These live in `tests/test_web_*.py` (Layer 1) and assert via `httpx.AsyncClient` against the FastAPI app in-process (no browser needed):

```python
# tests/test_web_log_api.py  — replaces s18_log_menu
async def test_get_latest_log_returns_json():
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get("/api/logs/latest")
    assert r.status_code == 200
    assert "lines" in r.json()
```

---

### 4. Directory Layout and Coexistence

```
photo-manager/
├── qa/
│   ├── scenarios/          # EXISTING Qt drivers — kept intact during migration
│   │   ├── _batch.py       # UNCHANGED (runs Qt batch)
│   │   ├── _uia.py         # UNCHANGED
│   │   ├── _config.py      # UNCHANGED
│   │   ├── _invariants.py  # UNCHANGED
│   │   ├── s01_happy_path.py  ... (all 68 existing drivers)
│   └── web/                # NEW — Playwright harness
│       ├── _batch.py       # Web equivalent of qa/scenarios/_batch.py
│       ├── _pw.py          # Page-object helpers (replaces _uia.py)
│       ├── _config.py      # Imports + re-exports qa/scenarios/_config.py unchanged
│       ├── _invariants.py  # Web-specific invariant helpers (status-bar regex → DOM)
│       ├── MIGRATION.md    # 68-row mapping table (bucket A/B/C, notes)
│       └── scenarios/
│           ├── s01_happy_path.py   # Playwright driver
│           ├── s13_execute_action.py
│           ... (one file per Bucket-A/B scenario)
```

**Coexistence rule during migration:** Both `qa/scenarios/_batch.py` (Qt) and `qa/web/_batch.py` (Playwright) gate CI until Playwright coverage reaches parity with Qt at ≥50 scenarios. The CI matrix runs:

```yaml
# .github/workflows/qa-batch.yml
strategy:
  matrix:
    include:
      # Qt harness (existing)
      - runner: windows-latest
        shard: 1
        total_shards: 5
        harness: qt
      ...
      # Playwright harness (new)
      - runner: ubuntu-latest
        shard: 1
        total_shards: 10
        harness: web
      ...
```

The `harness: qt` matrix entries are removed one by one as each scenario graduates to Playwright. The final removal of the Qt harness CI entries happens when all Bucket-A/B scenarios are green in the web harness for 3 consecutive PR runs.

**Settings file:** `qa/settings.json` is shared — both harnesses write/read the same file (they cannot run simultaneously in CI anyway). No change needed.

---

### 5. Data-testid Instrumentation — Frontend Contract

This section is the contract the **web frontend (Contract 1) must honor**. Every `data-testid` listed here must be present in the rendered DOM when its parent component is mounted. Static probes in `tests/test_web_dom_probes.py` verify the presence of required testids against the app's static HTML/JS output via Playwright's `page.locator('[data-testid]').all()`.

> **Staleness note (2026-07-10).** This §5 inventory captures the *original* design contract and has since drifted from what shipped. The **authoritative sources are: `frontend/src/testids.ts`** (generated from `qa/web/testid_constants.py`) for the exact shipped `data-testid` strings, and **`docs/features.md`** for shipped behaviour/controls. Known drift: (a) the literal id convention was reworked from `{surface}-btn-{action}` to `{surface}-{action}[-button]`, so many example strings below no longer match the DOM; (b) several controls listed here were deliberately **not shipped** or deferred and are tracked as such — e.g. the List/Log top-menus and File→Save-Manifest (see the intentional-omission comment in `frontend/src/components/MenuBar.tsx`), and ~~`ctx-apply-best-copy` (§5.7), which shipped only as a no-op stub~~ (**correction below** — this claim was itself wrong). Treat any mismatch between this section and `testids.ts`/`features.md` as this doc being stale, not the code.
>
> **CORRECTION (2026-07-16, verified — the note above's `ctx-apply-best-copy` claim was false, not the code).** `ctx-apply-best-copy` was implemented fully the day after this staleness note was written: commits `a829b95`/`3c530b9` "feat(web): apply best-copy decisions to a group via context menu (#744)" (2026-07-11) wire the testid to `store.applyBestCopy(groupNumber)`, which calls `core/app_service/action_service.py:266` `apply_best_copy()` — a real, non-stub function (highest-scoring row becomes keeper, classifier-confirmed duplicates get `user_decision="delete"`). `qa/web/scenario_map.yml` s72 (`status: done`) asserts this end-to-end, including a durable-SQLite-write proof across reload+reopen. Re-verified by the 2026-07-16 goal audit (`docs/audits/web-port-goal-audit-2026-07-16.md`) — see that report's P-1 finding. This §5.7 example block below predates the fix and is stale on this one point only; the rest of the staleness note's guidance (defer to `testids.ts`/`features.md`) still holds.

#### 5.1 Main Window Surface

```html
<!-- Decision table container -->
<div data-testid="main-result-tree" role="grid">
  <!-- Group header rows -->
  <div data-testid="row-group-{group_id}" role="row" aria-expanded="true|false">
    ...
  </div>
  <!-- File rows -->
  <div data-testid="row-file-{basename}" role="row">
    <div data-testid="row-file-{basename}-action">delete|keep|remove_from_list|—</div>
    <div data-testid="row-file-{basename}-score">0.48|—</div>
    <div data-testid="row-file-{basename}-lock">locked|—</div>
    <div data-testid="row-file-{basename}-similarity">Ref|85%|—</div>
  </div>
</div>

<!-- Column headers (TanStack Table th elements) -->
<th data-testid="col-header-file-name">File Name</th>
<th data-testid="col-header-action">Action</th>
<th data-testid="col-header-score">Score</th>
<th data-testid="col-header-size-bytes">Size (Bytes)</th>
<th data-testid="col-header-group-count">Group Count</th>
<th data-testid="col-header-creation-date">Creation Date</th>
<th data-testid="col-header-shot-date">Shot Date</th>
<th data-testid="col-header-similarity">Similarity</th>
<th data-testid="col-header-resolution">Resolution</th>
<th data-testid="col-header-folder">Folder</th>
<th data-testid="col-header-lock">Lock</th>

<!-- Status bar -->
<div data-testid="main-status-bar" aria-live="polite">Ready</div>

<!-- Empty state (shown when no manifest loaded) -->
<div data-testid="main-empty-state">
  <button data-testid="empty-btn-scan">Scan Sources…</button>
  <button data-testid="empty-btn-open">Open Manifest…</button>
</div>

<!-- Menu bar items — must carry data-testid for probe navigation -->
<nav data-testid="main-menu-bar">
  <button data-testid="menu-file">File</button>      <!-- dropdown trigger -->
  <button data-testid="menu-action">Action</button>
  <button data-testid="menu-list">List</button>
  <button data-testid="menu-log">Log</button>
  <button data-testid="menu-view">View</button>
</nav>
<!-- Menu items within each dropdown -->
<li data-testid="menu-file-scan-sources">Scan Sources…</li>
<li data-testid="menu-file-open-manifest">Open Manifest…</li>
<li data-testid="menu-file-save-manifest">Save Manifest Decisions…</li>
<li data-testid="menu-action-set-action-by-field">Set Action by Field…</li>
<li data-testid="menu-action-execute-action">Execute Action…</li>
<li data-testid="menu-action-execute-action-selected">Execute Action (only selected)…</li>
<li data-testid="menu-list-remove-from-list">Remove from List</li>
```

#### 5.2 Scan Dialog

```html
<dialog data-testid="scan-dialog">
  <textarea data-testid="scan-log" aria-readonly="true"></textarea>
  <input  data-testid="scan-output-path" type="text" />
  <input  data-testid="scan-path-field" type="text" placeholder="absolute folder path" />
  <span   data-testid="scan-path-error" role="alert"></span>   <!-- hidden until error -->
  <table  data-testid="scan-source-table">
    <tr>
      <td data-testid="scan-source-0-path">…</td>
      <td><input data-testid="scan-source-0-recursive" type="checkbox" /></td>
      <td><button data-testid="scan-source-0-remove">×</button></td>
    </tr>
    <!-- index increments per row -->
  </table>
  <button data-testid="scan-btn-add">+ Add</button>
  <button data-testid="scan-btn-browse">Browse…</button>
  <button data-testid="scan-btn-remove-all">Remove All</button>
  <button data-testid="scan-btn-start">Start Scan</button>
  <button data-testid="scan-btn-close-load" disabled>Close & Load</button>
  <div    data-testid="scan-progress-frame" hidden>
    <!-- progress bar, ETA, stage label -->
  </div>
  <label><input data-testid="scan-autotune-checkbox" type="checkbox" /> Auto-tune read knee</label>
</dialog>
```

#### 5.3 Execute Action Dialog

```html
<dialog data-testid="execute-dialog">
  <div data-testid="execute-all-delete-banner" hidden>
    All files in group <a data-testid="execute-all-delete-jump-{group_id}">…</a> will be deleted.
  </div>
  <select data-testid="execute-type-filter">
    <option>All</option>
    <option>Delete only</option>
    <option>Remove only</option>
  </select>
  <div data-testid="execute-tree" role="grid">
    <!-- Same row pattern as main-result-tree but scoped:
         data-testid="execute-row-file-{basename}-action" etc. -->
  </div>
  <button data-testid="execute-btn-select-by">Select by Field/Regex…</button>
  <button data-testid="execute-btn-execute">Execute</button>
  <button data-testid="execute-btn-execute-selected">Execute selected</button>
  <!-- Preview pane (s51) -->
  <div data-testid="execute-preview-pane">
    <img data-testid="execute-preview-image" src="" />
  </div>
</dialog>
```

#### 5.4 Set Action by Field Dialog (ActionDialog / SelectDialog)

```html
<dialog data-testid="action-dialog">
  <select data-testid="action-field-combo"></select>

  <!-- Simple mode panel -->
  <div data-testid="action-simple-row">
    <select data-testid="action-simple-op"></select>
    <input  data-testid="action-simple-text" type="text" />
    <span   data-testid="action-simple-disabled-note"></span>
  </div>

  <!-- Regex mode panel -->
  <div data-testid="action-regex-row">
    <input data-testid="action-regex-input" type="text" />
    <span  data-testid="action-validation-icon"></span>
    <span  data-testid="action-validation-error" role="alert"></span>
    <!-- Cheatsheet chips -->
    <button data-testid="action-cheatsheet-{token}"></button>
  </div>

  <!-- Numeric condition panel (s43, s50) -->
  <div data-testid="action-numeric-row">
    <button data-testid="action-numeric-mode-threshold"></button>
    <button data-testid="action-numeric-mode-topn"></button>
    <select data-testid="action-numeric-cmp"></select>
    <input  data-testid="action-numeric-value" type="number" />
    <span   data-testid="action-numeric-error" role="alert"></span>
    <select data-testid="action-numeric-order"></select>
    <input  data-testid="action-numeric-n" type="number" />
  </div>

  <!-- Match counter + action selector + Apply -->
  <span   data-testid="action-match-counter"></span>
  <select data-testid="action-action-combo"></select>
  <button data-testid="action-btn-apply">Apply</button>

  <!-- Preview list -->
  <ul data-testid="action-preview-list"></ul>
  <span data-testid="action-preview-truncated" hidden></span>
</dialog>
```

#### 5.5 Confirm Dialogs

```html
<!-- LockedRowsConfirmDialog -->
<dialog data-testid="lock-confirm-dialog">
  <button data-testid="lock-confirm-btn-unlock-apply">Unlock & Apply to All</button>
  <button data-testid="lock-confirm-btn-unlocked-only">Apply to Unlocked Only</button>
  <button data-testid="lock-confirm-btn-cancel">Cancel</button>
</dialog>

<!-- DeleteRegexConfirmDialog -->
<dialog data-testid="delete-confirm-dialog">
  <button data-testid="delete-confirm-btn-confirm">Mark N files for deletion</button>
  <button data-testid="delete-confirm-btn-cancel">Cancel</button>
</dialog>

<!-- SingletonPruneConfirmDialog (s61, s67) -->
<dialog data-testid="prune-confirm-dialog">
  <!-- three verdict buttons carry their own testids per the label constants -->
</dialog>

<!-- All-files-deleted confirmation (s13 equivalent) -->
<dialog data-testid="execute-all-delete-confirm">
  <button data-testid="execute-all-delete-confirm-yes">Yes</button>
  <button data-testid="execute-all-delete-confirm-no">No</button>
  <p><!-- body must contain a digit: N files will be deleted --></p>
</dialog>
```

#### 5.6 Preview Pane and Full-Res Viewer

```html
<!-- Main-window preview pane -->
<div data-testid="preview-pane">
  <img data-testid="preview-single-image" src="" />
  <div data-testid="preview-info"></div>
</div>

<!-- FullResViewerDialog (s68) -->
<dialog data-testid="fullres-dialog">
  <img data-testid="fullres-image" src="" />
  <!-- title must contain the filename -->
</dialog>
```

#### 5.7 Context Menu (right-click)

```html
<!-- Appears as a floating element, not a dialog -->
<ul data-testid="context-menu" role="menu">
  <li data-testid="ctx-set-action-keep">Keep</li>
  <li data-testid="ctx-set-action-delete">Delete</li>
  <li data-testid="ctx-set-action-remove">Remove from List</li>
  <li data-testid="ctx-open-folder">Open Folder</li>
  <li data-testid="ctx-lock">Lock</li>
  <li data-testid="ctx-unlock">Unlock</li>
  <li data-testid="ctx-apply-best-copy">Apply best-copy decisions to this group</li>
</ul>
```

---

### Invariant: DOM vs. Constants Parity

The static probe `tests/test_web_dom_probes.py::test_all_required_testids_present` runs Playwright headlessly against the bundled app in a fixture state, calls `page.locator('[data-testid]').all()`, extracts all `data-testid` values, and asserts the required set is a subset. This closes the drift gap that the Qt harness detected via `test_uia_label_coupling.py` — a label change in the app source that wasn't matched in `_uia.py` constants.

```python
# tests/test_web_dom_probes.py
REQUIRED_TESTIDS = {
    "main-result-tree", "main-status-bar", "main-empty-state",
    "main-menu-bar", "menu-file", "menu-action", "menu-list",
    "scan-dialog", "scan-log", "scan-btn-start", "scan-btn-close-load",
    "execute-dialog", "execute-tree", "execute-btn-execute",
    "action-dialog", "action-field-combo", "action-btn-apply",
    "lock-confirm-dialog", "delete-confirm-dialog", "prune-confirm-dialog",
    "preview-pane", "fullres-dialog",
    # ... full enumeration of the static (non-parameterized) testids
}

@pytest.mark.web_probe
def test_all_required_testids_present(pw_empty_state_page):
    present = {
        el.get_attribute("data-testid")
        for el in pw_empty_state_page.locator("[data-testid]").all()
    }
    missing = REQUIRED_TESTIDS - present
    assert not missing, f"Missing testids: {missing}"
```


---


## Contract 5 — Eval Gates: Perf A/B + QA-Parity Counter

**Base:** `origin/master` (architecturally identical working tree). All file:line citations reference the working tree at time of writing.

---

### 1. Perf A/B Harness

#### 1.1 What to Measure

Two independent metrics, measured separately (they stress different subsystems):

| Metric | Unit | Collection point |
|---|---|---|
| **Scan throughput** | files/s (not bytes/s — size-insensitive to walk-order clustering bias; same rationale as `autotune.py:24`) | from the `_StageTracker` "Hashed N/M" progress line already emitted by `ScanWorker` |
| **Thumbnail p50 / p95 latency** | ms (wall-clock from HTTP request to first byte of JPEG response) | new: measured client-side by the bench harness against the `/api/image?size=thumb` endpoint |

Do NOT measure bytes/s for scan — the existing bench at `scripts/bench_autotune_604.py` already captures this correctly (n_files_hashed / wall_s gives files/s, line 307–309 of that file) and the autotune module documents the anti-pattern at `scanner/autotune.py:24–26`.

#### 1.2 Baseline (Qt) vs Target (Web)

The Qt baseline is the EXISTING bench infrastructure. The web target adds one new dimension (thumbnail latency). Do not re-implement what already exists.

**Scan A/B:** Reuse `bench_autotune_604.py`'s `run_one_scan()` skeleton (file `scripts/bench_autotune_604.py:154–323`) but parametrise the backend rather than the autotune arm:

```python
# scripts/bench_web_port.py  — new file
@dataclass
class ScanBenchResult:
    backend: str          # "qt" | "web"
    pair_idx: int
    sources: list[str]
    wall_s: float
    n_files_hashed: int
    files_per_s: float    # n_files_hashed / wall_s
    cancelled: bool = False
    error: str = ""
```

- **Qt arm:** calls `ScanWorker.run()` via the existing `run_one_scan()` pattern (same QThread + DirectConnection signal capture).
- **Web arm:** calls the new `run_pipeline()` function extracted in Phase 0 directly (no Qt, no HTTP round-trip for the scan bench — the pipeline itself is what we're validating, not the HTTP layer).

**Thumbnail latency bench:** new, separate:

```python
# scripts/bench_thumbnail_latency.py  — new file
def measure_thumbnail_latency(
    server_url: str,
    image_paths: list[str],
    *,
    warmup: int = 20,
    measured: int = 100,
    concurrency: int = 4,
) -> dict:
    """
    Returns:
      {
        "warmup_n": int,
        "measured_n": int,
        "concurrency": int,
        "p50_ms": float,
        "p95_ms": float,
        "p99_ms": float,
        "max_ms": float,
        "server_url": str,
        "image_paths_sample": list[str],   # first 5
      }
    """
```

The harness issues `GET {server_url}/api/image?path=<encoded>&size=thumb` from N concurrent threads, records wall-clock per request (not TTFB — we want the full JPEG payload delivery time since the cache budget metric is `len(jpeg_bytes)`, mirroring `image_service` where sizeInBytes is replaced by `len(bytes)` per the feasibility report §11). Warmup requests are discarded; stats are computed over the measured set.

#### 1.3 Corpus

Two corpora, used for two distinct purposes:

| Corpus | Location | Size | Purpose |
|---|---|---|---|
| **qa sandbox** | `qa/sandbox/` (built by `scripts/make_qa_sandbox.py`) | ~50–100 files across sub-fixtures | Determinism gate: results must be bit-identical to the Qt baseline; catches correctness regressions |
| **Synthetic large** | `scripts/make_qa_large_source.py` already exists | configurable N; target 2000 files (matches `--limit 2000` from bench_autotune_604.py CLI convention) | Throughput signal: wall-clock comparison between backends |

The dev rig (D: HDD + J: NAS) is a **correctness checkpoint, not the target**, consistent with the project's measure-first culture (MEMORY.md: "Global-opt, dev rig = checkpoint"). The bench harness must be runnable on CI with the synthetic corpus.

#### 1.4 Pass Threshold

**Scan throughput:** `web_files_per_s / qt_files_per_s >= 0.95` (web within 5% of Qt). Rationale: the pipeline is unchanged Python; the only overhead is the `queue.put()` replacing `Signal.emit()` and the cross-process IPC for scan-in-dedicated-worker. 5% tolerance absorbs process-startup and IPC overhead while catching any catastrophic regression.

**Thumbnail latency:** `p95_ms <= 200` on the qa sandbox corpus served from localhost (no network hop). This is an absolute gate rather than a Qt-relative ratio because Qt served thumbnails via Qt Signals (no HTTP round-trip at all), so a ratio would be comparing incomparable things. 200 ms p95 at localhost is generous enough to accommodate the JPEG disk cache lookup + one HTTP round-trip while still catching a pathological cache-miss regime (a cold cache that re-encodes every thumbnail inline would blow past 200 ms on a 4 MB HEIC).

#### 1.5 Noise Defeat Protocol

Mirrors `bench_autotune_604.py`'s proven methodology:

1. **Alternating pairs** (`--pairs 3` default): Qt → Web → Qt → Web → Qt → Web. Monotonic drift (OS cache warming, background I/O) hits both arms equally.
2. **Warm cache for thumbnail bench**: 1 warmup pass over all corpus images before measurement (`warmup=20` default).
3. **Median of N**, not mean: same as `bench_autotune_604.py:410–414` (`statistics.median(walls)`).
4. **Pre-scan assertion** (mandatory, same as bench_autotune_604.py:172–183): print `device_key`, `is_remote_drive`, `hash_workers_for_root` before each scan arm so a confound (like the #604/#605 device_key guard bypass) is visible in the artifact JSON.
5. **JSON artifact output** with same structure as bench_autotune_604.py's `out` dict — the user pastes the JSON back to confirm the gate passed.
6. **Repetitions:** 3 pairs minimum for scan; 100 measured requests minimum for thumbnail latency (p95 is unstable below ~50 samples).

---

### 2. QA-Parity Counter

#### 2.1 Scenario Inventory

The current batch has **64 UIA scenarios** in `ALL_SCENARIOS` (`qa/scenarios/_batch.py:35–291`). The 4 internal helpers (`_batch.py`, `_close_window_helper.py`, `_config.py`, `_uia.py`, `configure.py`) are infrastructure, not scenarios.

Canonical count from `_batch.py` `ALL_SCENARIOS` list: **64 scenarios** (s01–s68, with s46, s62, and one gap at s02 skipped — actual scenario names are the authoritative source; do not infer from numbering).

#### 2.2 Mapping Categories

Each scenario maps to exactly one of three Playwright categories. This is a static, reviewable declaration maintained in a new file `qa/web/scenario_map.yml`:

```yaml
# qa/web/scenario_map.yml
# Authoritative mapping of the 64 UIA scenarios to Playwright equivalents.
# Categories: clean_port | needs_rework | no_web_analog
# For each scenario:
#   playwright_module: the target Playwright file (once created)
#   status: todo | in_progress | done
#   notes: (optional) what rework means or why no analog
scenarios:
  s01_happy_path:
    category: clean_port
    playwright_module: tests/web/test_s01_happy_path.spec.ts
    status: todo

  s02_empty_folder:
    category: clean_port
    playwright_module: tests/web/test_s02_empty_folder.spec.ts
    status: todo

  # ... (all 64)

  s19_context_menu_open_folder:
    category: no_web_analog
    playwright_module: tests/web/test_s19_open_folder.spec.ts  # API-level wiring test
    notes: >
      CabinetWClass Explorer-window detection is Win32-only UIA.
      Web equivalent: POST /api/reveal returns 200 and the path is valid.
    status: todo

  s39_window_geometry_persist:
    category: needs_rework
    playwright_module: tests/web/test_s39_layout_persist.spec.ts
    notes: >
      QSettings restoreGeometry -> localStorage. Assertion weakens from
      screen-pixel position to "panel widths survive page reload".
    status: todo

  s22_language_switch:
    category: needs_rework
    playwright_module: tests/web/test_s22_language_switch.spec.ts
    notes: >
      Qt app restart for locale change -> in-page i18n context switch.
      No subprocess; manifest must still be loaded after switch.
    status: todo

  s11_video_live:
    category: no_web_analog   # or needs_rework if pywebview HEVC is testable
    playwright_module: tests/web/test_s11_video_live.spec.ts
    notes: >
      QMediaPlayer HEVC via WMF. pywebview/WebView2 can play it but
      Playwright-driven WebView2 has no media-event API.
      Downgrade to: video element is present + src attribute is set.
    status: todo
```

The mapping is maintained as a machine-readable YAML, not a prose table, so `scripts/check_qa_parity.py` (below) can parse it.

#### 2.3 Initial Count (from feasibility report §4.2)

| Category | Count | Playwright contract |
|---|---|---|
| `clean_port` | ~50 | Full functional equivalence — same preconditions, same assertions on app state (SQLite / HTTP API), different driver API |
| `needs_rework` | ~10 | Functional equivalence on the *observable business state* (manifest SQLite, API responses); the weaker web-native mechanism replaces the stronger OS-level assertion |
| `no_web_analog` | ~4 | Downgrade to API-level or property assertion; explicitly documented in `scenario_map.yml` with `notes:` |

These counts are estimates from the feasibility report. The **authoritative counts** come from `scripts/check_qa_parity.py` reading `qa/web/scenario_map.yml`.

#### 2.4 Parity Definition

**"Parity" for a `clean_port` scenario:** The Playwright test exercises the same user action flow and asserts the same post-condition on the manifest SQLite (via `better-sqlite3` or a backend API call from the test) or the HTTP API response. Mechanism changes (pywinauto UIA click → Playwright `page.click('[data-testid="..."]')`) are invisible to the gate; only the observable outcome matters.

**"Parity" for a `needs_rework` scenario:** The Playwright test covers the functional behavior at the level the web architecture can observe. Document the weakened assertion explicitly in the test's top-of-file docstring and in `scenario_map.yml notes:`. Example: s39 asserts `localStorage.getItem('panelWidths')` survives `page.reload()`, not pixel coordinates.

**"Parity" for a `no_web_analog` scenario:** The Playwright test exists (status: `done`) and asserts the closest available web-observable property. Count it as "done" even with the weaker assertion. The `notes:` in `scenario_map.yml` is the audit trail.

#### 2.5 Per-Phase Parity Targets

| Phase | Playwright scenarios `status: done` | Count target | Blocking? |
|---|---|---|---|
| Phase 0 | 0 | 0 | N/A — no frontend yet |
| Phase 1 | Scan flow: s01, s02, s03, s04, s06, s07, s09, s10, s37, s38, s66 | ≥ 11 | Advisory (shadow gate) |
| Phase 2 | + decision tree, execute, regex, lock, scoring (all `clean_port` core) | ≥ 40 of 50 `clean_port` | Advisory until Phase 3 merge |
| Phase 3 | + remaining dialogs, OS integration | ≥ 58 (all `clean_port` + all `needs_rework`) | Blocking gate for Phase 3 exit |
| Phase 4 | All 64 (60 functional + 4 `no_web_analog` with downgraded assertions) | 64 (= 100% minus nothing — all have a Playwright test) | Blocking — Playwright becomes primary CI |

"Blocking" means the phase's PR cannot merge until `scripts/check_qa_parity.py` reports the phase's count threshold is met.

---

### 3. CI Integration

#### 3.1 Principle: Supply Inputs, Don't Duplicate Enforcement

The existing `pr-gates.yml` enforces `docs_guard` and `qa_scenario_guard`. `tests.yml` enforces pytest + per-file coverage. `news-gate.yml` enforces changelog fragments. `qa-batch.yml` runs the UIA layer-3 suite (non-required).

The eval gates for the web port are **new, additive jobs** — they supply pass/fail signals without duplicating or weakening anything that already exists.

#### 3.2 New Script: `scripts/check_qa_parity.py`

```python
# scripts/check_qa_parity.py
"""Machine-checkable QA-parity counter.

Reads qa/web/scenario_map.yml and reports per-category counts and
whether the current phase's target is met.

Exit codes:
  0 — phase target met
  1 — below threshold (gate fails)
  2 — scenario_map.yml missing or malformed

Usage:
  python scripts/check_qa_parity.py --phase 2
  python scripts/check_qa_parity.py --phase 4 --require-all
"""
import argparse, sys, yaml
from pathlib import Path

PHASE_TARGETS = {0: 0, 1: 11, 2: 40, 3: 58, 4: 64}

def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--phase", type=int, required=True, choices=[0,1,2,3,4])
    p.add_argument("--require-all", action="store_true",
                   help="Fail unless ALL 64 scenarios are done (Phase 4 full gate)")
    args = p.parse_args(argv[1:])

    map_path = Path(__file__).resolve().parent.parent / "qa/web/scenario_map.yml"
    if not map_path.exists():
        print(f"ERROR: {map_path} not found", file=sys.stderr)
        return 2

    data = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", {})
    done = [name for name, meta in scenarios.items() if meta.get("status") == "done"]
    n_done = len(done)
    n_total = len(scenarios)

    threshold = 64 if args.require_all else PHASE_TARGETS[args.phase]
    print(f"QA parity: {n_done}/{n_total} scenarios done (phase {args.phase} threshold: {threshold})")

    if n_done < threshold:
        print(f"FAIL: need {threshold}, have {n_done} ({threshold - n_done} remaining)")
        return 1

    print(f"PASS")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

#### 3.3 New Workflow: `.github/workflows/web-eval-gates.yml`

```yaml
name: web-eval-gates
# Advisory gate during Phases 1–3; REQUIRED at Phase 4 cutover.
# Controls whether the eval gate is blocking via a repo var WEB_PORT_PHASE.
on:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  qa-parity-counter:
    runs-on: ubuntu-latest
    timeout-minutes: 3
    env:
      WEB_PORT_PHASE: ${{ vars.WEB_PORT_PHASE || '0' }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install pyyaml
      - name: QA parity counter
        run: python scripts/check_qa_parity.py --phase ${{ env.WEB_PORT_PHASE }}
        # Non-blocking until Phase 4: continue-on-error: true
        # At Phase 4 cutover: remove continue-on-error

  scan-bench-sanity:
    # Runs bench_web_port.py on the qa sandbox (synthetic corpus, no real disks)
    # Advisory only: validates the bench script itself runs without error.
    # The real A/B (dev rig or NAS) is a local manual checkpoint per the
    # "dev rig = checkpoint" principle. This job catches import errors and
    # schema mismatches in the bench output.
    runs-on: windows-latest
    timeout-minutes: 10
    if: ${{ vars.WEB_PORT_PHASE >= '1' }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: requirements.txt
      - run: pip install -r requirements.txt -r dev-requirements.txt
      - name: Build qa sandbox
        run: python scripts/make_qa_sandbox.py
      - name: Scan bench sanity (qa corpus only)
        run: >
          python scripts/bench_web_port.py
          --sources qa/sandbox/near-duplicates
          --pairs 1
          --limit 50
          --output .bench_artifacts/scan_sanity.json
        # Advisory: failures here surface in the job log but don't block merge
        continue-on-error: true
      - name: Upload bench artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: scan-bench-sanity
          path: .bench_artifacts/scan_sanity.json
          if-no-files-found: ignore
          retention-days: 7
```

`WEB_PORT_PHASE` is a **GitHub repository variable** (not a secret), set by the developer as each phase completes. This decouples the gate promotion from code changes — no PR needed to promote the parity gate from advisory to blocking.

#### 3.4 What Is NOT Replicated

- `docs_guard` / `qa_scenario_guard` enforcement: unchanged; those gates already fire on every PR. When a web-port PR adds `web/` routes that are user-facing, `docs_guard` will require a `docs/features.md` update. That is correct behaviour — supply the update, don't bypass.
- `news-gate.yml`: unchanged; every web-port PR needs a news fragment or `[skip-news:]`.
- `tests.yml` coverage gate: the Phase-0 service-extraction changes will show up in `coverage.json`; the 70%-per-file floor applies. New web-port modules under `service/` need unit tests to clear it.
- The real-hardware A/B (dev rig + NAS) is a **manual checkpoint**, not a CI gate — consistent with bench_autotune_604.py's design decision (file header lines 56–59: "No mocking — drives the real disks ... this script is the real-rig analogue").

---

### 4. Per-Phase Exit Criteria Table

| Phase | Name | Concrete measurable gate | Blocking? |
|---|---|---|---|
| **0** | Service extraction | (a) `pytest` green + per-file coverage ≥ 70% for new `service/` modules. (b) `scripts/bench_web_port.py --pairs 1 --limit 50` runs without error on qa sandbox and emits valid JSON with `files_per_s > 0` for the web arm. (c) Qt `qa-batch` remains green (zero regression). `WEB_PORT_PHASE=0`. | Yes (pytest + qa-batch) |
| **1** | Scan API + SSE + QA parallel start | (a) `GET /api/scan/start`, `GET /api/scan/{id}/events` (SSE), `POST /api/scan/{id}/cancel` all return correct shapes on qa sandbox (validated by a new `tests/web/test_scan_api.py` pytest module, runs in CI without a browser). (b) `scripts/check_qa_parity.py --phase 1` passes (≥ 11 Playwright scenarios `status: done` covering the scan flow). (c) `scan-bench-sanity` CI job passes. (d) `qt qa-batch` still green. `WEB_PORT_PHASE=1`. | Pytest + parity counter advisory; qa-batch blocking |
| **2** | Frontend core | (a) `scripts/check_qa_parity.py --phase 2` passes (≥ 40 Playwright scenarios done). (b) `bench_thumbnail_latency.py` on qa sandbox returns `p95_ms <= 200`. (c) `bench_web_port.py --pairs 3` on synthetic-2000 returns `on_over_off_ratio` (web/Qt files_per_s) in range [0.95, 1.20] — upper bound catches a synthetic "fast" reading from a warm-cache confound. (d) Qt qa-batch still green. `WEB_PORT_PHASE=2`. | Parity counter + thumbnail gate advisory; qt qa-batch blocking |
| **3** | Remaining dialogs + OS integration | (a) `scripts/check_qa_parity.py --phase 3` passes (≥ 58 done — all `clean_port` + all `needs_rework`). (b) All 4 `no_web_analog` scenarios have a Playwright file with a `status: todo` → `in_progress` minimum (proof that a downgraded test is in flight). (c) `bench_thumbnail_latency.py` p95 ≤ 200 (re-run with a broader corpus including HEIC if available in qa fixtures). (d) Qt qa-batch still green. `WEB_PORT_PHASE=3`. | All gates blocking at Phase 3 exit |
| **4** | Cutover | (a) `scripts/check_qa_parity.py --phase 4 --require-all` passes (every runtime-derived scenario ported — `status: done`, or documented no-analog `status: skip`; the total comes from `qa/scenario_ids.py` at runtime, not a frozen count). (b) Playwright `web-scenario-batch` CI job green (shipped job name in `web-eval-gates.yml`; early drafts here called it `web-playwright-batch` — the Playwright equivalent of `qa-batch.yml`, same `fail-fast: false`, running on `ubuntu-latest` headless). (c) `bench_web_port.py --pairs 3` ratio ∈ [0.95, 1.20] on synthetic-2000. (d) `bench_thumbnail_latency.py` p95 ≤ 200. (e) `WEB_PORT_PHASE=4` set in repo vars. (f) `qt qa-batch` removed from required status checks (replaced by `web-scenario-batch`). | All gates blocking; no merging without all 6 |

#### Phase Exit Checklist (machine-checkable per phase)

For Phase N to be declared complete, all of the following commands must exit 0:

```bash
# Phase 0
python -m pytest                                    # Layer-1 tests + coverage
python scripts/check_coverage_per_file.py          # 70% per-file floor
python -m qa.scenarios._batch                      # Qt qa-batch (local)

# Phase 1 (adds)
python scripts/check_qa_parity.py --phase 1
pytest tests/web/test_scan_api.py                  # New: scan API contract tests

# Phase 2 (adds)
python scripts/check_qa_parity.py --phase 2
python scripts/bench_web_port.py --pairs 3 --limit 2000 --output bench_p2.json
python scripts/bench_thumbnail_latency.py --server http://localhost:8000 --measured 100

# Phase 3 (adds)
python scripts/check_qa_parity.py --phase 3

# Phase 4 (final)
python scripts/check_qa_parity.py --phase 4 --require-all
npx playwright test tests/web/                     # Full Playwright suite
python scripts/bench_web_port.py --pairs 3 --limit 2000 --output bench_p4.json
python scripts/bench_thumbnail_latency.py --server http://localhost:8000 --measured 100
```

---

### Appendix: Existing Bench Patterns Reused

| Pattern | Source | Reuse in this contract |
|---|---|---|
| `ScanResult` dataclass with `files_per_s` | `bench_autotune_604.py:84–99` | Extended to `ScanBenchResult` with `backend` field |
| Alternating OFF/ON pairs with `statistics.median` | `bench_autotune_604.py:376–414` | Reused verbatim; "backend" replaces "autotune arm" |
| Pre-scan `probe_device()` assertion | `bench_autotune_604.py:102–116` | Mandatory in `bench_web_port.py` — the #604/#605 confound lesson |
| JSON artifact output + `summary` key | `bench_autotune_604.py:403–428` | Same shape; add `backend_comparison.ratio` key |
| `sys.path.insert(0, repo_root)` bootstrap | `bench_autotune_604.py:75` | Identical |
| `--pairs`, `--limit`, `--output`, `--per-scan-timeout` CLI | `bench_autotune_604.py:327–346` | Identical CLI in `bench_web_port.py` + `--backend {qt,web,both}` |
| `exiftool_orphans_post_scan` smoke test | `bench_autotune_604.py:280–289` | Included in `bench_web_port.py` web arm (process-based scan worker) |


---

# Binding reconciliation -- corrections that override the drafts
The drafts above were written independently. Where a draft conflicts with the **real code** or with **another contract**, the resolution here is authoritative and must be applied during the build. Caught by the cross-contract reconcile pass:

| Sev | Between | Problem (abridged) | Binding resolution |
|---|---|---|---|
| **high** | C1 (RecordDTO) ↔ core/models.py (PhotoRecord) | C1's RecordDTO omits `capture_date` (the legacy EXIF date field that core/models.py:18 carries as a mandatory non-optional field). PhotoRecord has four date fields: `capture_date` (mandatory, not None-typed at position), `modified_date`, `creation_date`, and `shot_date`. C1 lists only `shot_date`, `creation_date`, `... | Add `capture_date: datetime \| None` to RecordDTO. Mark it deprecated in a comment but carry it so the Qt adapter never needs a schema fork. Alternatively, canonically alias `capture_date` → `shot_date` in the AppService mapping layer and document the merge rule. |
| **high** | C1 (ExecuteResult) ↔ core/services/interfaces.py (DeleteResult) | C1 defines `ExecuteResult.deleted_paths` + `ignored_paths` + `failed` + `audit_log_path`. The real `DeleteResult` (core/services/interfaces.py:13-24) uses `success_paths` (not `deleted_paths`), has no `ignored_paths` field, and has `log_path` (not `audit_log_path`). C4's test-id surface uses `execute-row-file-{basen... | Pin `ExecuteResult` to the canonical `DeleteResult` field names: `success_paths`, `failed`, `log_path`. Drop `ignored_paths` (no analog in the current model; 'remove_from_list' paths are tracked on the VM not the result object). If C1 needs `deleted_paths` as an alias, document it as a property shim on the AppServic... |
| **high** | C1 (ScanHandle.cancel_token type) ↔ C2 (_CancelToken class) | C1 defines `ScanHandle.cancel_token: threading.Event` (a plain stdlib Event). C2 defines `_CancelToken` as a distinct class with `request()`, `__call__()`, and `is_set()` methods — satisfying both the `cancel_check: Callable[[], bool]` pattern (via `__call__`) AND the `cancel_flag.is_set()` pattern. These are incomp... | C1 should declare `ScanHandle.cancel_token: _CancelToken` (not `threading.Event`), importing the type from C2's module. `_CancelToken` wraps a `threading.Event` internally and is the canonical replacement for `isInterruptionRequested()`. C1's `scan_cancel()` calls `handle.cancel_token.request()`, not `.set()`. |
| **high** | C3 (WIC/Shell COM threading model) ↔ infrastructure/image_service.py:692 (current implementation) | C3 specifies a module-level `ThreadPoolExecutor` with `CoInitializeEx(None, 0x2)` STA initializer (`_get_wic_executor() -> ThreadPoolExecutor`, `_sta_initializer()`). The current code calls `ole32.CoInitialize(None)` (the deprecated MTA form) inline in `_load_via_shell_thumbnail` at line 692, with `ole32.CoUninitial... | C3's `_sta_initializer` / `_get_wic_executor` design is the correct fix and must be treated as a new implementation, not a refactor of the existing per-call pattern. The FastAPI route handler calls `await loop.run_in_executor(_WIC_EXECUTOR, ...)` — the executor thread is already STA-initialized, so no per-call CoIni... |
| **medium** | C2 (hash_pool_measured SSE event schema) ↔ scan_worker.py:666-672 (actual emit) | C2 defines the `hash_pool_measured` SSE event payload as `{thread_per_file, process_per_file, spawn, group_per_pair, group_bk_per_candidate}`. This matches the actual dict emitted (scan_worker.py:666-672). However C2's interface definition in its INTERFACES section lists the SSE event as `{thread_per_file: float, pr... | Explicitly enumerate all 5 keys in the `hash_pool_measured` SSE event schema in C2. Update `_valid_hash_pool_rates` (or its web equivalent) to treat grouping keys as optional-but-preserved, not stripped. C1's `ScanProgressBus.on_calibration(rates: dict)` must pass through the full 5-key dict. |
| **medium** | C3 (image cache clear trigger) ↔ C1 (AppService.load_manifest contract) | C1's open question asks 'Does AppService need explicit clear_image_cache() or does it clear on load_manifest()?' The existing Qt code clears explicitly (file_operations.py:406 calls `clear_image_cache()` after manifest load; main_window.py:571-572 also clears explicitly). C3 defines `_ByteBudgetLRUCache.clear()` but... | Make `AppService.load_manifest()` call `image_service.clear_cache()` automatically (matching the existing Qt behavior at file_operations.py:406). Remove `clear_image_cache()` from C1's public surface; it becomes an internal implementation detail called from within `load_manifest`. C3 should note this in its lifecycl... |
| **medium** | C4 (data-testid 'row-file-{basename}') ↔ C1 (RecordDTO.file_path) | C4 keys all row-level test IDs on `basename` (e.g., `row-file-photo.jpg`). C1 exposes `file_path` (the full absolute path) in RecordDTO, not basename separately. If two groups contain files with the same basename from different directories (a realistic dedup scenario — the app's core purpose), `row-file-photo.jpg` i... | Change the test-id convention to use a deterministic path-derived slug rather than basename: e.g., `row-file-{sha1(file_path)[:8]}` or `row-file-{group_id}-{basename}`. Update all C4 testid surface definitions consistently. The `data-testid` attribute value can be set by the React component from `RecordDTO.file_path`. |
| **medium** | C5 (bench_web_port.py 'qt' backend) ↔ C1 (run_pipeline extraction status) | C5 defines `bench_web_port.py --backend {qt,web,both}` with the `qt` arm calling `run_pipeline()` directly from Python. But `run_pipeline()` does not exist yet — it is the planned extraction from `ScanWorker._run_pipeline()` (a private method in a QThread subclass). Until Phase 0 of the migration lands, the `qt` ben... | C5 must explicitly state it depends on C1's `run_pipeline()` being available (Phase 0 prerequisite). The bench should fail with a clear ImportError and a 'run Phase 0 first' message if the function is not yet extracted. Add this dependency to C5's preconditions. |
| **medium** | C4 (file-save/open dialog handling) ↔ C1 (AppService.browse_filesystem + AppService.save_manifest) | C4 lists `scan-output-path`, `scan-path-field`, and `scan-btn-browse` testids implying the scan dialog has a browse button for paths. C4's open question asks 'Does the web app use <input type=file> or a custom path-input widget?' C1 exposes `AppService.browse_filesystem(path)` as the backend-side directory listing. ... | Pin to the custom path-input approach backed by `GET /api/fs/browse`: the React scan dialog has a text input + a browse button that opens a tree popover calling the browse API. `<input type=file>` is rejected because it returns browser-sandboxed File objects without server-accessible paths. C4's `scan-path-field` te... |
| **low** | C2 (SSE event name 'empty') ↔ C1 (ScanProgressBus.on_completed_empty) | C1 names the bus method `on_completed_empty(scan_id)`. C2 names the SSE event `empty`. The Qt signal is named `completed_empty` (scan_worker.py:406). The C2-defined `ScanTask.status` Literal includes `'empty'`. These are three different names for the same terminal condition. The fan-out mapping must be explicit: `on... | Add an explicit event-name ↔ bus-method ↔ ScanTask.status mapping table to the shared conventions section. Canonical SSE event names: `log`, `stage`, `finished`, `failed`, `empty`, `hash_pool_measured`, `read_knee_measured`. ScanTask terminal statuses: `finished`, `failed`, `empty`, `cancelled`. |

## Unassigned concerns (gaps) -- resolve in Phase 0
Concerns no single contract owns yet. Each becomes a Phase-0 decision/issue before the dependent code is written:

- CORS policy: no contract assigns responsibility for the `CORSMiddleware` configuration. On localhost the default is to block requests from `file://` or a non-matching port (e.g., Vite dev server on :5173 vs FastAPI on :8000). C2 never specifies `allow_origins`. Needs an explicit decision: `allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173']` in dev; `allow_origins=['*']` inside pywebview (same-origin by construction). Must be documented before any browser-to-API call is coded.
- Error envelope shape: no contract defines the JSON error body that non-2xx responses return. C4's test helpers `wait_for_status()` and QA assertions operate on UI state, not raw HTTP. But every React `fetch()` call needs to parse errors. Without a canonical shape (`{error: string, code: string, detail: any?}`) each endpoint implementor invents their own format. FastAPI's default `detail` key is a start but not pinned.
- Settings persistence for the web client: `JsonSettings` (infrastructure/settings.py) is the existing store. C1's `ScanConfig` includes `autotune_read_knee` and `hash_pool`. C2's open question asks whether `hash_pool_measured` / `read_knee_measured` should be persisted by the FastAPI layer or POSTed back by the browser. No contract assigns ownership of `GET /api/settings` and `POST /api/settings` endpoints, nor the JSON shape of settings that the React UI needs to read/write (scan defaults, density, language, column layout, sort order).
- i18n delivery to the frontend: `infrastructure/i18n.py` provides the backend translation system (YAML-based, `t(key)`). The React frontend needs its own translation mechanism. No contract specifies how translations reach the browser: options are (a) `GET /api/i18n/{locale}` returning a flat key→string JSON object, (b) bundling `en.yml`/`zh_TW.yml` as static assets served by Vite, or (c) i18next with a backend plugin. The current locale-switch requires an app restart (i18n.py:15); C4's open question asks whether the web app does a live re-render or a page reload for s22/s58.
- Session lifecycle / singleton enforcement: C1 specifies `AppService` as a singleton per process. The web app has no authentication, but multiple browser tabs or concurrent Playwright test runs could share one AppService instance and race on manifest state (e.g., two tabs both calling `load_manifest()` concurrently). No contract defines whether the AppService is truly process-global with a single active manifest, or whether it supports concurrent sessions. The feasibility report (web-port-feasibility-2026-06-19.md) does not address this.
- Path security / allowed-roots enforcement: C3 defines `_validate_path(raw, allowed_roots)` but leaves the `allowed_roots` set at startup undefined when no manifest is loaded. No contract specifies what paths the image endpoint can serve before a manifest is loaded (e.g., during the scan dialog's preview panel). The `browse_filesystem` endpoint (C1) also traverses arbitrary paths — no contract defines its traversal root or depth limit. On localhost this is low-risk but needs a documented policy.
- UNC / NAS path handling in SSE and bench: C2's open question asks whether atomic temp-file rename works on UNC shares. C5's bench uses `--sources` which may be UNC paths. No contract defines how `\\LINXIAOYUN\photo` (the user's actual NAS path, from MEMORY.md) is encoded in JSON responses (backslash vs forward-slash, URL-encoding in `GET /api/image?path=`). The image endpoint's `path` query parameter must handle both Windows-style absolute paths and UNC paths without double-encoding.
- Pywebview integration contract: no contract specifies the pywebview bootstrap (which process owns uvicorn, which port, how the webview window is sized/titled, how the webview communicates back to the Python process for OS integration calls like reveal-in-Explorer). C1's `reveal_in_explorer` and `open_with_default` are implemented server-side, but pywebview's `create_window` / JS bridge may offer a shorter path. This is a packaging decision but affects how C4's s19 analog is implemented.
- Video streaming endpoint: C4 lists no testids for a video player. The feasibility report confirms HEVC video needs a `<video>` tag with a range-capable endpoint. No contract defines `GET /api/video?path=` with `Accept-Ranges: bytes` support, the MIME type negotiation, or how s11_video_live (HEVC scenario) maps to a Playwright test.
- Manifest upload/download for file-save dialog: C1 has `AppService.save_manifest(path)` and `load_manifest(path)` but on a web frontend the manifest file is on the server filesystem, not accessible via browser's `<a download>` directly. No contract defines whether the manifest dialog uses `GET /api/manifest/download` (returns file bytes) or a server-side path picker. C4's missing testids for 'file-open manifest' and 'file-save manifest' are a gap.

---

# Appendix A -- consolidated open questions (-> Phase 0 issues)

**C1 Service-API**

- Should `run_pipeline()` run in a `multiprocessing.Process` or a `ProcessPoolExecutor` worker on the web path? -- *why:* ProcessPoolExecutor reuses workers across calls (cheaper respawn) but its shutdown semantics on cancel are tricky — scan_worker.py:1143-1144 explicitly avoids `with pool:` because `shutdown(wait=True)` blocks on cancel. A bare `multiprocessing.Process` gives clean cancel semantics (terminate()) but pays respawn cost per scan. The Qt path keeps `QThread` so this only affects the web path.
- How does `get_image()` handle the WIC/Shell COM calls (`_load_via_shell_thumbnail` in image_service.py) when running in a FastAPI worker thread that was not CoInitialised? -- *why:* The feasibility report mandates `ThreadPoolExecutor + CoInitializeEx initializer` for WIC/Shell calls. The exact initializer shape (which thread, which coinit flag, whether to use a dedicated executor per `get_image()` call vs a shared one) must be specified before implementing `ImageServiceAdapter`.
- Does `AppService` need an explicit `clear_image_cache()` call, or does it clear automatically on `load_manifest()`? -- *why:* file_operations.py:406 calls `ui_updater.clear_image_cache()` after every manifest load (#616). If the service auto-clears, the Qt adapter gets it for free. If it does not auto-clear, the Qt adapter must call it explicitly — and the web route must remember to call it too.
- What is the `ScanHandle.is_running()` implementation — polling a `multiprocessing.Process.is_alive()`, or a `threading.Event` flipped by the `on_finished`/`on_failed` callbacks? -- *why:* The SSE route needs to know when to close the stream. If `is_running()` polls `Process.is_alive()` across the process boundary it may return False before `on_finished` has been delivered, creating a race where the client sees stream-closed before the manifest path arrives.
- Should `bulk_decide()` expose the numeric pseudo-patterns (`__cmp__:OP:VALUE`, `__top_n__:N:asc|desc`) through the service API, or should those be resolved to plain `file_paths: list[str]` by the caller before calling `decide()`? -- *why:* Exposing pseudo-patterns makes the service API aware of a UI convention from `app/views/dialogs/select_dialog.py`. Pushing resolution to the caller (web route or Qt handler) keeps the service leaner but duplicates the `_matched_paths_for_pattern` logic. The current `FileOperationsHandler.set_decision_by_regex` (file_operations.py:1255) owns the resolution today.

**C2 Realtime/Cancel**

- Should hash_pool_measured and read_knee_measured events trigger settings persistence in the FastAPI layer, or should the browser POST them back to a /api/settings endpoint? -- *why:* In the Qt app scan_dialog._on_hash_pool_measured (scan_dialog.py:1089) and _on_read_knee_measured (scan_dialog.py:1101) persist calibration data to settings.json immediately on signal receipt. In the web app settings.json lives server-side. The simplest path is to have the server persist on receipt of these events from the worker queue. But if the browser is the settings owner (e.g. via /api/settings GET/PUT), a round-trip is required and the persistence is delayed until the browser acknowledges. This decision affects whether the calibration cache survives a server restart mid-scan.
- What is the maximum task_id ring size and retention policy for the task registry? -- *why:* A 4-hour TTL is specified here but not derived from any existing constraint. Long NAS scans can exceed 1 hour. If the TTL is too short, a browser refreshing after a long scan gets 410 Gone. The right value depends on the longest expected scan duration plus the longest expected browser-away time.
- Does the atomic temp-file rename on Windows work correctly when output_path is on a UNC share (J: NAS)? -- *why:* Path.replace() on Windows uses MoveFileExW with MOVEFILE_REPLACE_EXISTING, which works on NTFS but may fail across UNC shares if the source and destination are on different volumes. If output_path is on J: NAS, the temp file must also be on J: NAS (same parent directory), not on a local drive. The contract specifies writing tmp to the same directory as output_path, but this must be verified at implementation time against the Synology SMB stack.
- How is the SSE fan-out implemented when the FastAPI process runs behind a reverse proxy that buffers SSE (e.g. nginx without X-Accel-Buffering: no)? -- *why:* The contract specifies X-Accel-Buffering: no in the SSE response headers. If the proxy ignores this or is not nginx (e.g. Caddy), SSE events may be buffered and delivered in bursts rather than in real time, breaking the 1 Hz progress feel. pywebview's WebView2 backend on Windows talks directly to uvicorn with no proxy in the packaged app, so this is a deployment concern rather than a development concern — but it should be documented.

**C3 Image-serving**

- Should the full-res disk-cache sub-directory use the same key as thumbnails (sha1(path+'|'+0)) or a separate sha1(path)-only key under thumbs/v1/full/? -- *why:* Using sha1(path+'|'+0) is consistent with _compute_cache_key but means full-res entries share the eviction domain with thumbnails. A separate full/ sub-dir makes it easy to purge or size-limit full-res independently. The current code uses the same key space for all sizes, but full-res JPEGs are 20-35 MB each vs 20-80 KB for thumbnails — mixing them in one directory without sub-bucketing makes eviction less targeted.
- What is the allowed_roots set at startup when no manifest is loaded yet (app first launch, scan not yet run)? -- *why:* The path validation gate (_validate_path) blocks /api/image requests until allowed_roots is populated. If a user opens the web UI before loading a manifest, every thumbnail request returns 403. The route needs a fallback policy: either accept any absolute path on Windows drives when no manifest is loaded (trusting the localhost boundary), or show a 'load a manifest first' error in the UI.
- The Qt-side shim (QImage.loadFromData) does not honour EXIF orientation — is this acceptable given that Pillow's exif_transpose already bakes orientation into the JPEG bytes? -- *why:* This is the same design PR #624 used for the embedded DNG thumb path (image_service.py:588-609 comment block). It is correct IF the encode path always calls exif_transpose before saving. But the rawpy postprocess path (full decode) does not pass through exif_transpose — rawpy's postprocess does not auto-rotate. An iPhone ProRAW DNG shot in portrait grip would render 90-deg rotated in the Qt preview if the postprocess path is hit and exif_transpose is not applied afterward. Needs an explicit test case.
- Should _WIC_EXECUTOR be shut down on app exit, and if so, via which FastAPI lifecycle hook? -- *why:* CoUninitialize is not called on the WIC worker threads with the initializer pattern — the threads hold a COM STA apartment for their lifetime. On a clean process exit this is a no-op (process exit releases COM). But if uvicorn uses graceful shutdown with thread joining, the executor.shutdown(wait=True) call must precede process exit to avoid late COM calls after the runtime begins tearing down. The right hook is FastAPI's lifespan context manager.
- What is the strategy for the resolution read (_read_resolution in preview_pane.py:54-88) in the web port? It calls QImageReader(path).size() and rawpy — both of which are acceptable headless, but QImageReader must be removed. -- *why:* The preview info panel shows 'WxH' dimensions. In the Qt app, _read_resolution (preview_pane.py:54) uses QImageReader for non-RAW formats. In the web port this must become Pillow (Image.open().size) for general formats. This is a small but completeness-sensitive change — if not explicitly noted in the resolution endpoint contract, it will be missed during Phase 0 implementation.

**C4 QA-harness**

- Does the web app use a native browser <dialog> element or a React portal div for each modal dialog? -- *why:* Playwright's page.locator('dialog[data-testid=...]') works for native <dialog>; portals appended to document.body require locator('[data-testid=...]') without the element tag. The testid surface contract works either way, but the probe implementation differs. The ShadCN/UI dialog component uses Radix Portal (div at body root) — the contract spec above uses <dialog> as a label but implementors should use <div role='dialog'>.
- How are file-save / file-open dialogs handled? Does the web app use <input type='file'> (browser-native, Playwright-friendly) or a custom path-input widget? -- *why:* If <input type='file'> is used, Playwright's page.set_input_files() drives it cleanly and s12/s16 (save/open manifest) port to Bucket A. If a custom path widget is used (text input + server-side path resolution), the driver calls page.fill('[data-testid=save-path-input]', path) instead — still clean, but the testid must be added to the contract.
- What is the web analog for window geometry persistence (s39) and dialog geometry persistence (s48)? -- *why:* If the web app stores column widths and dialog sizes in localStorage, the Playwright driver asserts via page.evaluate(). If the web app is always full-viewport (common SPA pattern), s39 and s48 are no-analog (Bucket C) and downgrade to localStorage-write unit tests. This decision changes two scenarios from Bucket B to Bucket C.
- Is the language-switch (s22, s58) a live re-render or a page reload? -- *why:* If it is a live re-render (React i18n context), the driver can assert the new text appeared without a reload — cleaner. If it requires a reload (SSR locale negotiation), the driver must call page.reload() and re-assert state persistence, matching the existing s23a/s23b split pattern.
- Does the web app need a file-system browser for the Scan Sources dialog (tree panel showing local directories)? -- *why:* The Qt ScanDialog has a folder-tree panel and a path-field. On a localhost web app, the browser cannot enumerate the local filesystem without an OS file picker or server-side API. If the web app implements GET /api/fs/list?path=X, the scan-path-field testid works for typed paths; if it omits the tree panel, s17_scan_dialog_widgets loses the 'browse tree and select folder' sub-path (still Bucket B but with a narrower test surface).

**C5 Eval-gates**

- Which of the 64 scenarios map to which category (clean_port / needs_rework / no_web_analog)? -- *why:* The feasibility report gives ~50/~10/~4 estimates (§4.2), but the authoritative mapping must be done scenario-by-scenario before Phase 1 starts. The per-phase parity targets (11/40/58/64) are calibrated against those estimates; if the actual rework count is 15 instead of 10, Phase 2's target of 40 may need adjustment. This mapping is the first deliverable of Phase 1.
- s11_video_live (HEVC): clean_port, needs_rework, or no_web_analog? -- *why:* The feasibility report lists it as 'no_web_analog' under the HEVC/QMediaPlayer category (§4.2, §6), but pywebview/WebView2 can play HEVC. If Playwright can drive WebView2 and detect media-play events, this upgrades to needs_rework. The answer changes Phase 3's deliverable scope.
- Does the synthetic large corpus (make_qa_large_source.py) generate files that trigger the ByteBudget back-pressure path (DNG >= 30 MB)? -- *why:* The most dangerous OOM regression (scan_oom_proraw_regression in MEMORY.md) is a DNG-heavy library. If the synthetic corpus only generates small JPEGs, the bench cannot detect a ByteBudget regression in the web arm. The corpus generator may need a --include-large-raw flag.
- What is the correct way to run the web arm scan bench without starting a uvicorn server — call run_pipeline() directly from Python, or spawn it in a subprocess? -- *why:* The Phase 0 extraction produces run_pipeline() as a standalone function. If bench_web_port.py calls it directly (in-process), it can capture progress via queue.get() and reuse the existing ScanResult signal-capture pattern. If it spawns a subprocess + uvicorn, the bench adds HTTP IPC overhead that conflates the pipeline perf with the server overhead. The contract above specifies direct call; this should be confirmed against the Phase 0 extraction design (Contract 1).
- Should the thumbnail latency bench use the warm disk cache (JPEG already on disk) or measure cold-cache encode time? -- *why:* The image_service rewrite produces JPEG bytes and caches them. On first request (cold cache) it must re-encode; on subsequent requests it serves from disk. The p95 <= 200ms threshold is calibrated for the warm-cache case. If the bench only runs warm, it cannot detect a regression in the encoding path. A separate cold-cache sub-run (or a --cold-cache flag that clears the thumbs/ directory before measuring) may be needed.

---

# Appendix B -- /adversarial-review targets (next quality gate)
The costly-to-reverse decisions to pressure-test before locking the contracts:

1. **AppService singleton + multiprocessing.Process IPC boundary (C1 + C2 keystone)**
   - *Failure mode to attack:* State mutation races: the AppService holds a single ManifestRepository reference. If the scan worker process sends `on_finished` while the HTTP layer is concurrently serving `get_groups()`, the manifest reload and the group-list read race on the same repository. The IPC fan-out (multiprocessing.Queue → asyncio.Queue) inserts an event loop hop, but the AppService state mutation (triggered by `on_finished` calling `load_manifest`) still happens on the main asyncio thread — without an explicit lock, a concurrent `decide()` call can interleave. Pressure-test: concurrent scan finish + decide() requests; verify the manifest is either fully-old or fully-new, never partially-replaced.
2. **_CancelToken replacing 15 isInterruptionRequested() sites (C1 + C2)**
   - *Failure mode to attack:* The WRITE stage cancel gate is the most dangerous: a missed substitution at scan_worker.py's WRITE stage could allow a partial manifest overwrite after cancel. The current code checks `isInterruptionRequested()` at multiple points including inside the WRITE stage's atomic-rename path. The new `_CancelToken.__call__()` must be substituted at all 15 sites including the one at scan_worker.py:840, 985, and the WRITE-stage gate. Pressure-test: cancel during WRITE stage; verify the output manifest is either the pre-scan version (untouched) or the complete new version — never a partial write.
3. **STA thread pool for WIC/Shell COM (C3)**
   - *Failure mode to attack:* Shell thumbnail providers (IShellItemImageFactory) are apartment-threaded COM objects. If the FastAPI asyncio event loop thread calls into the WIC path without the executor (e.g., during startup cache warm-up or via a code path that bypasses `run_in_executor`), it will CoInitialize MTA (asyncio's implicit apartment) and the HEIC/DNG Shell thumbnail calls will fail silently or deadlock. The existing `_sta_initializer` design is correct but fragile — any direct call to `_load_via_shell_thumbnail` from outside the executor will violate the STA contract. Pressure-test: request a HEIC thumbnail from three concurrent HTTP requests; verify all three resolve and the process does not hang or return placeholder silently.
4. **Full-resolution RAW serving (C3) — the JPEG re-encode tradeoff**
   - *Failure mode to attack:* A 100–130 MB ProRAW DNG decoded by rawpy yields a ~60 MP image. JPEG re-encode at quality=95 (C3's default) consumes ~500–800 MB of peak RAM in the FastAPI process (numpy array + PIL buffer + JPEG bytes simultaneously). The feasibility report flags this as 'the one genuine regression risk'. C3's `size=0` returns JPEG re-encoded at quality=95 with no streaming — the full bytes are buffered before the HTTP response starts. On a scan of 200+ ProRAW files with concurrent thumbnail requests, the FastAPI process could OOM. Pressure-test: 5 concurrent `GET /api/image?size=0` requests on 100 MB DNG files; measure peak RSS; verify byte budget does not accumulate across requests.
5. **row-file-{basename} testid uniqueness assumption (C4)**
   - *Failure mode to attack:* C4's testid convention uses basename (e.g., `row-file-IMG_0001.jpg`) which is non-unique across groups (the dedup use-case explicitly produces groups where the same filename appears in different directories). If two groups each contain `IMG_0001.jpg`, `page.locator('[data-testid=row-file-IMG_0001.jpg]')` returns multiple elements and Playwright's strict mode raises. ~50 ported scenarios use row selection as their primary assertion mechanism; a non-unique testid key makes the entire harness unreliable. Pressure-test: run s02 (the basic dedup scenario) with a fixture containing two groups each having a file named `photo.jpg`; verify the scenario correctly selects from group 1 only.
6. **Scan bench qt-arm dependency on run_pipeline() extraction (C5 + C1 Phase 0)**
   - *Failure mode to attack:* C5's `bench_web_port.py --backend qt` calls `run_pipeline()` directly. This function does not exist in the current codebase — it must be extracted from `ScanWorker._run_pipeline()` as Phase 0 of the migration. If C5 is implemented before C1's Phase 0 is complete, the bench will import-fail. More subtly, `_run_pipeline` currently calls `self.isInterruptionRequested()` (a QThread method) at 15 sites — after extraction, these become `cancel_token.is_set()`. If the bench constructs a `_CancelToken` that is never set (no cancellation path), and a bug in the extraction left one `isInterruptionRequested()` call untouched, the bench will deadlock silently waiting for an interruption that never fires. Pressure-test: run the qt bench arm with a deliberate mid-scan cancel at the 50% mark; verify the scan stops cleanly and returns a partial result, not a hang.

---

## Next steps (sequence 1 -> 3 -> 2)

1. **This doc (drafted).** -> run `/adversarial-review` on Appendix B, fold resolutions in, lift status to v1.0.
2. **Open Phase 0-4 issues** (step 3) from the contracts + open questions + gaps, with the per-phase eval-gate exit criteria from C5.
3. **Phase 0 spike** (step 2): extract `run_pipeline()` + the `_CancelToken` (15 sites), decouple `image_service` from `QImage`, stand up the `core/app_service/` skeleton -- reversible, and it improves the current Qt app too.

---

# Adversarial review — decision record (binding corrections)
Run 2026-06-19 via `/adversarial-review` (Opus + Sonnet **independent** peers; round-1 blind → round-2 cross-attack → judge adjudicating each objection against the source). Full transcript: workflow `wf_5c13f791-614`.

**Verdict: NOT CONVERGED on first pass** — 19 objections raised, 17 load-bearing, **17 unrebutted (real)** (16 enumerated as individual binding corrections below; one sub-claim folded into another), 2 refuted. This is the method working as intended: real, costly-to-reverse defects caught **before any build**. The corrections below are **binding** — apply them to the contract bodies during Phase 0. Where a contract draft conflicts with a correction here, **this section wins** (same precedence as §Binding reconciliation).

**LEAD spot-verification (guarding against shared hallucination — failure mode #7).** Independently re-confirmed the highest-stakes claims against source: the cancel path is **dual-signal** (`isInterruptionRequested` ×15 *plus* a module-local `cancel_flag` ×26 that the daemon worker threads actually poll — `scan_worker.py:873/985/1016/1034/1353/1417/1465/1477`, `cancel_flag.set()` only inside the `isInterruptionRequested` branches); `write_manifest` is **already crash-atomic** (`tmp.sqlite` + `os.replace`, `scanner/manifest.py:68/132`, #464) so the design's "partial-overwrite" premise was false; the post-write `apply_auto_select_decisions` runs **outside** that atomic window (`scan_worker.py:1759`→`1786`); and the dup-basename fixture exists (`qa/sandbox/multi-source-{a,b}/shared.jpg`). The scenario count is genuinely ambiguous across the doc and my own checks (52 / 65 / 67) — which itself proves the "derive at runtime, never hardcode" fix.

> **Judge decision:** NOT CONVERGED — 17 real, unrebutted load-bearing objections remain; fold the contract changes below into docs/design/web-port-tech-design.md before lifting status above DRAFT v0.1. Two objections were REFUTED against the artifact's own text (not by peer persuasion): T3-DCL (the sole call site of _get_wic_executor is run_in_executor's first arg, evaluated on the event-loop thread with no await between None-check and assignment — no cross-thread interleave, design lines 1499-1508) and T1-SSE-terminal-event-as-stated (the §5.4 terminal_event fallback at 1170-1174 and §2.4 step-3 replay at 868-871 DO exist; only a minor first-connection write-ordering precision gap survives, overlapping open question 2740). SHIP-BLOCKER TIER (data-integrity / harness-wide): T5-testid-contradiction (C4 body still bare basename in §1.3/§5.1/§5.3 vs the group_id mandate in §6/§2712/AppendixB — C4 is the source the React dev reads, so the broken key ships) and its T5-Playwright-regression twin (verified dup shared.jpg fixture + lenient Qt _row_anchor → strict-mode raise on ~50 ported scenarios); T2-apply_auto_select (post-write SQLite block outside the atomic window → incoherent manifest); T4-OOM (unbounded run_in_executor(None) on 300-800MB ProRAW decodes, no semaphore); NEW-ETag/stale-cache (path-only key + 3 conflicting ETag formulas → stale-forever); NEW-scenario-count (real count 67; C4 says 68, C5 says 64 with a FALSE 's02 skipped' claim; PHASE_TARGETS hard-codes 64 → 3-scenario silent Phase-4 hole). STRONG TIER: T2-cancel_flag (two-tier teardown unmodeled), T1-session-race (no _Session lock + 'functionally identical to Qt' is false + line-628 cancel-type fork), T1-SSE-iteration (deque iterated under concurrent append), T3-STA-unenforced (no apartment guard/probe), T4-bench-warm-cache (gate never runs the cold size=0 path it guards), T6-deadlock-mechanism (AttributeError not deadlock + no zero-surviving-site exit check). MINOR: T2-write-premise (write_manifest already atomic per manifest.py:52-132; the 'buggier double-rename' sub-claim was refuted, but the premise is wrong + wrapper redundant), T2-site-count (15-vs-11 reconciliation), T3-CoUninitialize (atexit claim imprecise). The DRAFT status (line 3) and the explicit Appendix-B attack-target list + Unassigned-concerns tail are good practice and correctly pre-flag many of these — but several (T5 testid body not propagated, T2 post-write block, NEW scenario-count miscounts, NEW ETag triple-formula) are concrete contradictions to fix, not just open questions to defer.

## Refuted objections (recorded so they are not re-raised)
- **T1** — T1-SSE-terminal-event: the SSE generator has NO fallback to terminal_event when live_queue exhausts, so a queue drained before on_finished is buffered causes a silent stream-close and an EventSource error-reconnect loop / 410.
  - *Why refuted:* As stated ('NO fallback exists') this is false. §5.4 (design lines 1170-1174) explicitly specifies: 'the handler checks task.terminal_event (stored separately on ScanTask) and sends it after the replay window, guaranteeing the client always receives the terminal event on reconnect.' §2.4 step 3 (868-871) replays the terminal event when the task is already terminal and the event is in the buffer. terminal_event is a ScanTask field (834). The objection attacked the labeled 'sketch' (1123-1138) and ignored the prose contract that completes it. The narrow valid residual — the design never states the write-ordering invariant that the reader thread pushes the terminal event into the buffer BEFORE flipping task.status to terminal, leaving a first-CONNECTION (not reconnection) window — is minor and already overlaps the doc:2740 is_running()-vs-on_finished open question. Not load-bearing.
- **T3** — T3-DCL: _get_wic_executor()'s lazy None-check has no lock; two concurrent requests on two OS threads both see None, create+leak two executors (double-checked-locking failure).
  - *Why refuted:* Verified the sole call site: design line 1504 calls _get_wic_executor() as the FIRST ARGUMENT to loop.run_in_executor(_get_wic_executor(), self._load_via_shell_thumbnail_sync, ...) inside the async coroutine _load_via_shell_thumbnail_async (1499-1508). Python evaluates that argument on the single asyncio event-loop thread BEFORE run_in_executor dispatches to any pool thread. The function body (1481-1486) is pure synchronous Python with no await between the None-check and the assignment, so under cooperative asyncio scheduling no second coroutine interleaves there; the pool threads only ever run the 2nd arg (_load_via_shell_thumbnail_sync), never _get_wic_executor. The leaked-executor race cannot occur on the design's own code path. Peer 1's R2 refutation is correct. Residual risk (a future dev submitting a warm-up that calls _get_wic_executor from inside a pool thread) is hypothetical, not a property of the current design.

## Real load-bearing objections — binding corrections
Apply each to the named contract during Phase 0. Grouped by target.

### T2

**Objection:** T2-cancel_flag: §3.2's 11-site table treats cancel as a single token to substitute, but the real HASH teardown is two-tier — a local cancel_flag = threading.Event() polled by daemon threads, set only inside isInterruptionRequested branches. cancel_flag is never named in the design.

**Verdict (grounded):** Verified at source: scan_worker.py:873 defines cancel_flag = threading.Event() local to _run_pipeline; daemon-thread loops poll it at 1016/1034/1353/1417/1465/1477; cancel_flag.set() is called ONLY inside isInterruptionRequested() branches at 1181/1532/1602; line 985 is the dual-check (not cancel_flag.is_set() AND not self.isInterruptionRequested()). The design's §3.2 table (lines 934-946) enumerates 11 isInterruptionRequested sites and never names cancel_flag. A naive s/isInterruptionRequested/_cancel_token/ leaves cancel_flag an orphaned second Event with its own set/poll lattice and the #594 ordering hazard (set-before-drain) unmodeled. Ship-blocker overstates: _CancelToken.is_set() (design line 917) is API-compatible with cancel_flag.is_set(), so the merge is mechanically straightforward once named — both peers settled on strong.

**Binding fix:** In §3.2, add a row/paragraph stating: the local cancel_flag = threading.Event() at scan_worker.py:873 is replaced by the SAME injected _cancel_token reference (not a separate Event); preserve the #594 ordering — the outer token must be observed before the bounded exif_queue put-loops (985, 1465) drain, and daemon threads poll _cancel_token in place of cancel_flag. Document the two-tier→one-tier collapse explicitly so the migrator does not create a three-way signal lattice.

**Objection:** T2-write-premise: §3.3 claims write_manifest 'overwrites whatever is at output_path with a partial manifest' (lines 960-963); this is false because write_manifest already writes to a sibling tmp.sqlite then os.replace (the #464 fix). The proposed outer wrapper is a redundant double-rename.

**Verdict (grounded):** manifest.py:52-132 confirms the existing atomic dance: tmp_path = output.with_name(output.name + '.tmp.sqlite') (line 68), sqlite3.connect(tmp_path) (92), WAL checkpoint TRUNCATE (126), conn.close() (128), os.replace(tmp_path, output) (132). Docstring 63-65 guarantees 'A partial write never reaches the destination — the live manifest stays consistent.' So the design's premise is factually inaccurate (it echoes a stale scan_worker comment at 1750-1752), and the proposed tmp = output_path + '.tmp'; write_manifest(rows, tmp); tmp.replace(output_path) wrapper is doubly redundant. The peer's 'buggier double-rename' SUB-claim is refuted — both peers agreed the end result is correct, just needlessly complex (os.replace is atomic on NTFS; the inner write checkpoints away sidecars). Real as a doc-accuracy + redundant-design fix, not a corruption bug.

**Binding fix:** Rewrite §3.3's premise to acknowledge write_manifest is already crash-safe via tmp.sqlite+os.replace (#464, manifest.py:52-132). Drop the redundant outer-wrapper proposal. The actual residual gap is post-write cancel (see apply_auto_select_decisions ruling), not a partial-overwrite of output_path. The correct contract: call write_manifest directly to output_path (already atomic); on a cancel observed after write, the new manifest is complete and valid, so emit finished, not a partial-overwrite race.

**Objection:** T2-apply_auto_select: the atomic-rename contract protects write_manifest but NOT the post-write apply_auto_select_decisions block, which does additional SQLite writes on the already-replaced manifest. A cancel landing there leaves a structurally-complete but semantically-incoherent manifest (keepers unlocked).

**Verdict (grounded):** Verified at scan_worker.py: write_manifest(rows, self.output_path) at 1759, then a SEPARATE post-write block (the '5.5 post-write keep+lock' section) gated by if keepers: imports apply_auto_select_decisions and calls apply_auto_select_decisions(str(self.output_path), keepers, non_keepers) — additional SQLite writes on the already-written manifest, only then self.finished.emit(). The design's §3.3 sketch (lines 969-978) wraps ONLY write_manifest in the tmp→replace and raises on cancel; it stops modeling at write_manifest. A cancel after the rename but during apply_auto_select_decisions yields a manifest that is complete-looking yet has keepers unlocked / non-keepers undecided, while a finished event could fire. Real data-integrity window when auto_select_enabled produces keepers.

**Binding fix:** Extend §3.3's WRITE-stage contract to cover the post-write block: either (a) fold apply_auto_select_decisions inside the same atomic critical section, or (b) since the manifest is already valid after rename, treat a cancel observed during the auto-select block as a 'finished' outcome (skip the rest, emit finished — not failed/cancelled), or (c) explicitly document the incoherence window as an accepted rare opt-in. Name scan_worker.py's post-write keep+lock block as the second WRITE-stage gate site.

**Objection:** T2-site-count: the doc says '~15 isInterruptionRequested sites' in 9 places but the §3.2 table enumerates 11; one peer asserted the table is 'self-consistent (11)' without reading the 4 excluded lines.

**Verdict (grounded):** grep -c isInterruptionRequested scan_worker.py = 15. Verified 11 executable (801/840/985/1180/1531/1601/1613/1669/1693/1727/1752) + 4 comment-only (956/976/1590/1665, confirmed each is a # or docstring line). The §3.2 table correctly lists 11 executable; but lines 29, 60, 90, 2781, 2782, 2790 and the §6 conventions all say '15'. An implementer told '15 sites' will hunt 4 phantom substitutions or edit comment lines. Peer 2's R1 'self-consistent' characterization was mis-evidenced (it did not read the 4 excluded lines); peer 1's R2 reconciliation (15 raw = 11 exec + 4 comment) is the correct grounding. Minor.

**Binding fix:** Add one reconciling sentence near §3.2: '15 raw occurrences = 11 executable call sites (substituted) + 4 comment-only mentions (956/976/1590/1665, left as-is).' Update the '~15 sites' phrasings (lines 29, 60, 90, Appendix B items 2 and 6) to match, so the count is unambiguous.

### T1

**Objection:** T1-session-race: _Session holds mutable groups/path_index with no lock; on_finished's load_manifest mutates it on the asyncio thread while get_groups()/decide() read it on executor threads; the 'functionally identical to Qt' claim (line 555) papers over the concurrency delta.

**Verdict (grounded):** Confirmed: _Session (design 533-540) has groups: list and lazy path_index with no synchronization; the registry dict gets a threading.Lock (837-838) but _Session does not. Line 555 asserts the web _session 'is functionally identical to the Qt process's _session' and line 553 says 'no per-request session isolation needed' — false on the concurrency axis: Qt mutates _session only on the main thread (queued signal delivery), whereas the web path mutates from the asyncio on_finished callback and reads from run_in_executor worker threads (1620 confirms executor use; 159-160 says load_manifest callers 'wrap it in an executor'). The design routes this to Phase 0 (2724 + Appendix B item 1 at 2780), which is a legitimate DRAFT posture, but the 'functionally identical' equivalence claim is the misleading part. Additionally the Phase-A signature run_pipeline(config, cancel_token: threading.Event, bus) at line 628 contradicts the Binding Reconciliation (2708: ScanHandle.cancel_token: _CancelToken) and §1 conventions — an unreconciled cancel-type fork inside the keystone. Ship-blocker overstates for a DRAFT explicitly pending review; strong is right.

**Binding fix:** Correct line 553-555: state the web _session is NOT concurrency-equivalent to Qt's (Qt = single-thread; web = asyncio callback + executor reads). Specify the Phase-0 contract: either pin all _session mutation+reads to a single-threaded executor / the event loop with no await in the critical section, or guard _session with an explicit lock / copy-on-write swap of groups+path_index in on_finished. Also reconcile line 628's run_pipeline cancel_token type to _CancelToken (per row 2708), or state it is the wrapped threading.Event inside _CancelToken.

**Objection:** T1-SSE-iteration: the SSE generator sketch iterates task.event_buffer (a bare deque(maxlen=500)) directly while a reader thread appends concurrently — a CPython concurrent-iteration hazard.

**Verdict (grounded):** Confirmed at design line 1128: 'for eid, name, data in task.event_buffer' with no list() snapshot and no lock held; event_buffer is collections.deque(maxlen=500) (832/845) fed by 'a sync thread that reads the multiprocessing.Queue' (878-880). CPython deque.append is GIL-atomic, but iterating a deque under concurrent mutation can raise RuntimeError('deque mutated during iteration') or skip/double-visit — exactly the replay window. §5.4 (1162-1174) builds the resume-correctness contract on this iteration, so the hazard is load-bearing, not incidental. Labeled a 'sketch' (1123), which slightly mitigates but does not resolve.

**Binding fix:** In the SSE replay block (§2.3 / the sketch at 1128), mandate a snapshot before iteration: 'for eid, name, data in list(task.event_buffer)' (or hold the registry lock across the buffer append and the replay read). State this as a binding requirement, not sketch-level pseudocode, since §5.4 resume correctness depends on it.

### T3

**Objection:** T3-CoUninitialize: the design says 'CoUninitialize moves to a thread-level atexit; shutdown(wait=True) is the cleanup trigger' — but ThreadPoolExecutor.shutdown() runs no per-thread teardown and stdlib has no thread-atexit, so STA apartments leak on executor replacement.

**Verdict (grounded):** Design line 1494 states the atexit/shutdown claim. Correct that ThreadPoolExecutor.shutdown() sets the shutdown flag and joins workers but invokes no user per-thread teardown, and the stdlib has no thread-local atexit, so CoUninitialize is never called on the WIC STA threads. Benign on clean process exit (OS reclaims COM), but leaks STA apartments on uvicorn graceful restart / executor replacement. The design's OWN open question at 2755 already flags this ('Should _WIC_EXECUTOR be shut down on app exit, and via which lifecycle hook'). Minor — both peers agree.

**Binding fix:** Correct line 1494: ThreadPoolExecutor.shutdown() does not run per-thread CoUninitialize. Specify the cleanup mechanism explicitly — submit a Future to each of the max_workers=2 threads that calls CoUninitialize before return, OR a FastAPI lifespan context manager that drains+shuts the executor; wire it to the lifespan, resolving open question 2755 in the contract.

**Objection:** T3-STA-unenforced: the STA-executor invariant is a convention guarded by reviewer vigilance — _load_via_shell_thumbnail_sync stays directly callable with no apartment/thread-name guard; a direct call from the MTA event-loop thread yields RPC_E_WRONG_THREAD; no xfail-strict probe is added.

**Verdict (grounded):** Confirmed: design keeps the sync function callable as _load_via_shell_thumbnail_sync (1505), the current sync fn remains directly importable (image_service.py:648-857), and §4.1 notes MTA → RPC_E_WRONG_THREAD (1464/1492). Appendix B item 3 (2784) explicitly admits 'any direct call to _load_via_shell_thumbnail from outside the executor will violate the STA contract' and proposes NO guard. No assertion, thread-name check, or private-by-construction wrapper at the COM entry point. Per the project's probe-layer pattern (MEMORY feedback_probe_layer_pattern), this structural-invariant class is normally caught with an xfail-strict probe; the design adds none. Strong.

**Binding fix:** Add a runtime guard at the COM entry point: assert threading.current_thread().name.startswith('wic-sta') (the thread_name_prefix from line 1484) at the top of _load_via_shell_thumbnail_sync, so an out-of-executor call fails loud instead of silently CoInitialize-ing MTA. Additionally specify a tests/test_ui_probes.py AST/static probe asserting the WIC sync fn is only invoked via run_in_executor(_WIC_EXECUTOR, ...).

### T4

**Objection:** T4-OOM: C3 bounds CACHE-retention bytes (LRU at put) but not PEAK-DECODE bytes; size=0 decodes run on the unbounded default executor with no semaphore; N concurrent 60MP ProRAW decodes OOM the process. The scan pipeline's #587 byte-budget back-pressure is not imported.

**Verdict (grounded):** Confirmed: route handler runs the decode via run_in_executor(None, svc.get_image_bytes, ...) (design 1620) — the default ThreadPoolExecutor (min(32, cpu+4) workers), no semaphore. image_service.py's only threading.Lock (161) guards the LRU cache dict; rawpy.postprocess (541) is ungated; _compute_cache_key path-based, no decode gate. The 'Memory budget' paragraph (1456) reasons only about cached JPEG bytes (20-35MB in the 192MB LRU tier) and never the transient decode peak — yet the doc's OWN tradeoff table (1428) states the decode numpy array alone is '300-400 MB'. N concurrent size=0 = N×~300-800MB, gated by neither the cache LRU nor any concurrency limiter. The scan-OOM-ProRAW regression (MEMORY) is confirmed prior art for this exact class. Self-flagged at 1430 and Appendix B item 4 (2786). Ship-blocker overstates for a DRAFT that flags the risk; strong.

**Binding fix:** Add a concurrency/in-flight-bytes bound to C3's §3.2 full-res path: an asyncio.Semaphore (or max_workers=2 dedicated executor) limiting concurrent size=0 rawpy decodes, OR an in-flight byte budget that back-pressures before postprocess (mirroring scan_worker.py's #587 reader-side byte budget). Make explicit that the _ByteBudgetLRUCache caps retention, not peak decode.

**Objection:** T4-bench-warm-cache: the thumbnail latency bench measures size=thumb from warm cache (warmup=20) and the p95<=200ms gate never exercises the cold size=0 ProRAW decode path that triggers the OOM, so the eval gate cannot catch the regression it is meant to guard.

**Verdict (grounded):** Confirmed: bench_thumbnail_latency (design 2344-2370) issues GET /api/image?...&size=thumb with warmup:int=20, and the gate (2387) is p95_ms<=200 on warm cache. It never runs size=0 on uncached ProRAW. The OOM risk (T4) is entirely the cold size=0 decode path. The design's own open question 2772 acknowledges this gap ('if the synthetic corpus only generates small JPEGs, the bench cannot detect a ByteBudget regression') and 2770 notes the corpus may need --include-large-raw. So the gate that should catch the OOM does not run the triggering scenario. Strong.

**Binding fix:** Add a cold-cache, size=0, large-DNG sub-run to the eval gates: a bench mode that clears thumbs/v1/full/, issues N concurrent GET ...&size=0 against ProRAW-sized synthetic files (gated by make_qa_large_source --include-large-raw), and asserts peak RSS stays under a budget. Without it, the OOM bound from T4 is untested. Resolve open questions 2770/2772 in the C5 contract.

**IMPLEMENTATION NOTE (2026-06-29, measured — supersedes the "N concurrent postprocess" sub-run above).** Both T4 defenses shipped: (1) the route's 40 MiB source-size cap (`app/web/routes/image.py:24,69-82`) returns **413 before any decode** for `size=0` on a >40 MiB source; (2) `_FULLRES_DECODE_SEM = BoundedSemaphore(2)` (`infrastructure/image_service.py:74`, acquired at :595-596) caps concurrent `postprocess`. But measurement on real iPhone ProRAW DNGs (70-120 MB) shows the feared **"N concurrent 60 MP postprocess → OOM" regime is unreachable through the web route**: (a) every real ProRAW exceeds 40 MiB → 413, never decoded; (b) for `size=0` the embedded-thumb fast path (`image_service.py:627-665`) returns the DNG's **embedded full-res JPEG** (measured: IMG_3700 → 13.4 MB JPEG in 0.5 s) and **never calls `postprocess`** — so the semaphore is not even reached. The postprocess+semaphore path fires only for a RAW with **no** embedded thumb that is also <40 MiB (atypical — e.g. #75 non-camera TIFF). Therefore the design's "N concurrent ProRAW postprocess sub-run with peak-RSS budget" would test a regime the real path avoids = synthetic (the *validation-must-match-real-workload* anti-pattern). The shipped gate (`scripts/bench_thumbnail_latency.py`) instead tests the defenses that actually fire: **(A) cap → 413** for >40 MiB at `size=0`; **(B) full-res serve of a real ProRAW (`get_image_bytes(path,0)`) stays under a peak-RSS budget** — measured 627 MB delta for the 3 fixtures, embedded-JPEG path, bounded; **(C) warm-cache `size=thumb` p95 ≤ 200 ms**. The semaphore stands as correct defense-in-depth for the rare no-embedded-thumb case; the bench does not fake a path to it. Real RAW can't be synthesised (rawpy is read-only), so the fixtures are real ProRAW DNGs placed in the gitignored `qa/fixtures/raw_local/` (GPS-stripped, no people); `make_qa_large_source.py --include-large-raw` validates/reports them; the OOM-bound check skips when the dir is empty (CI has no RAW codecs — it is a dev-rig checkpoint).

**Objection:** NEW-ETag/stale-cache: the path-only disk cache key and the ETag disagree on identity; an in-place file edit serves a stale image forever while 304ing. The doc also gives three different ETag formulas.

**Verdict (grounded):** Verified: cache key is path+size sha1 with no mtime/content (image_service.py:128-131; design says 'preserve this exactly' at 1256-1263); the full/ disk entry is keyed on path sha1 ALONE, no mtime (design 1434). The ETag appears in THREE incompatible forms: §5 conventions (line 60: sha1(mtime_ns:size_bytes)[:16]), §3.2 HTTP example (1440: '<mtime_ns>-<size>'), and the actual route handler (1630: sha1(jpeg[:512]) of the SERVED bytes). With a path-keyed disk entry, an in-place re-export (same path, new bytes) returns the OLD JPEG, and the content-hash ETag (1630) is stable against the stale bytes → the client 304s forever; there is no auto-incrementing cache-bust (the &v= hint at 1240 is manual). The Qt app sidesteps it (no HTTP layer; clears in-mem cache on every manifest load, file_operations.py:406) so the staleness was latent-harmless there. Strong.

**Binding fix:** Pin ONE ETag scheme and make it freshness-bearing: derive the ETag (and/or the full/ disk-cache key) from the source file's mtime_ns+size, so an in-place edit changes both key and ETag. Remove the contradictory route-handler sha1(jpeg[:512]) form (1630) and the §3.2 example divergence (1440); reconcile to the §5 conventions formula. State that the path-only key is only safe because the ETag carries mtime — or add mtime to the full/ key.

### T5

**Objection:** T5-testid-contradiction: §6/§2712/Appendix-B mandate row-file-{group_id}-{basename}, but C4 — the declared single source of truth the React dev reads — still specifies bare row-file-{basename} in §1.3, the §5.1 DOM contract, and §5.3 execute dialog. A reconciliation note that contradicts the named source-of-truth body is a latent merge conflict that ships the broken key.

**Verdict (grounded):** Directly verified internal contradiction. Mandating group_id: §6 (line 60: 'Use row-file-{group_id}-{basename}'), reconciliation row 2712, Appendix B item 5 (2787-2788). C4 body still bare basename: §1.3 table lines 1798-1801 (row-file-{basename}, -action, -score, -lock), §5.1 DOM contract lines 2061-2065 (<div data-testid='row-file-{basename}' role='row'> + sub-cells), §5.3 execute dialog line 2151 (execute-row-file-{basename}-action). Line 60 §6 declares 'C4 is the single source of truth for testid strings; the React component developer reads C4, not the QA driver.' A dev implementing from §5.1/§5.3 ships the non-unique key the reconciliation was supposed to kill. Note group_id is already in the C4 surface (row-group-{group_id} at 1797), so the fix is mechanically consistent. Strong (defeats ~50 ported scenarios' primary row-selection).

**Binding fix:** Propagate the reconciliation into the C4 body: rewrite §1.3 table (1798-1801) and the §5.1 DOM contract (2061-2065) to row-file-{group_id}-{basename} (and -action/-score/-lock/-similarity sub-cells), and §5.3 (2150-2151) to execute-row-file-{group_id}-{basename}-action. The reconciliation note alone is insufficient because C4 is the named source the React dev reads.

**Objection:** T5-Playwright-regression: dup basenames are guaranteed by the multi-source fixture (shared.jpg in two roots); under Playwright strict mode the bare-basename selector matches 2 and RAISES, whereas the current Qt _row_anchor lenient first-match silently returns the first — a real first-match→must-be-unique semantic regression the 'parity' claim hides.

**Verdict (grounded):** Verified: qa/sandbox/multi-source-a/shared.jpg and multi-source-b/shared.jpg both exist (ls confirmed); s10_multi_source scans both and is a Phase-1 parity target (design 2494). qa/scenarios/_uia.py:2262-2284 _row_anchor is a lenient first-match loop — iterates TreeItems, returns on the FIRST whose text == basename, never raises on a second match. Playwright locator is strict by default (1.14+): 2+ matches throws. So a selector that worked (leniently, first-match) in Qt becomes a hard failure in Playwright, and the design's Phase-1 'parity' for s10 hides this row-selection semantic change. Strong.

**Binding fix:** In C4/§2, note the row-selection semantics CHANGE from Qt's lenient first-match (_uia._row_anchor) to Playwright strict must-be-unique, and that this is precisely why row testids MUST be path/group-derived. Add s10_multi_source to the explicit list of scenarios whose row selectors require the group_id-keyed testid before Phase 1, so the strict-mode failure is designed out rather than discovered at port time.

### NEW

**Objection:** NEW-scenario-count: C4 says 68 entries; C5 says 64 ('s02 skipped'); the parity gate hard-codes 64 against the real list, so Phase 4 --require-all passes with scenarios unported.

**Verdict (grounded):** AST-verified: len(ALL_SCENARIOS) in qa/scenarios/_batch.py = 67 (first s01_happy_path, last s68_full_res_viewer_double_click). C4 says '68 entries' (1893) and 'all 68 existing drivers' (2007) — wrong by 1. C5 says '64 UIA scenarios' / 'canonical count: 64' / 's02 ... skipped' (2406-2408) — wrong by 3, AND the s02 claim is FALSE (s02_empty_folder is present, verified). PHASE_TARGETS={4:64} (2532), --require-all hard-codes 64 (2552), exit gate says 'all 64 scenarios done' (2649/2678). Running --require-all sets threshold=64 against a 67-entry scenario_map.yml → the Phase-4 cutover gate passes with 3 scenarios unported (silent hole). Peer 1's claimed denominator '68' is itself wrong; peer 2's '67' + the false-s02 catch is correct ground truth. Strong (cutover gate correctness depends on the count).

**Binding fix:** Reconcile every count to the authoritative 67: fix C4 1893/2007 (68→67), C5 2406-2408 (64→67 and delete the false 's02 skipped' clause — s02 IS present; the real gaps are s46 and s62), PHASE_TARGETS[4] (2532: 64→67), the --require-all help/threshold (2537-2538/2552: 64→67), and exit-gate 2649/2678. Better: derive the Phase-4 total from len(ALL_SCENARIOS) at runtime in check_qa_parity.py rather than hard-coding, so the gate cannot silently diverge.

### T6

**Objection:** T6-CI-noop: the C5 Qt-arm measurement can't run in CI — the Qt arm needs a live QApplication/event loop for QThread+DirectConnection, but scan-bench-sanity has no QApplication bootstrap and continue-on-error swallows the failure; the exit gate validates only the web arm.

**Verdict (grounded):** Confirmed: Qt arm 'calls ScanWorker.run() via the existing run_one_scan() pattern (same QThread + DirectConnection signal capture)' (design 2339); ScanWorker(QThread) requires a live QCoreApplication for DirectConnection signal capture. The scan-bench-sanity CI job (2596-2632) runs on windows-latest with no QApplication bootstrap shown and continue-on-error: true (2623), so a QThread/QApplication-absent failure surfaces only in the log and does not block. The Phase 0 exit gate (b) at 2649 checks only 'files_per_s > 0 for the web arm' — never the Qt baseline. A perf A/B whose baseline arm can silently no-op is not a gate. Minor: the bench is a perf tool not a correctness gate, the phase dependency is stated (2713, Appendix B item 6 at 2789), and a missing qt_arm field in the JSON is developer-observable. Both peers rated minor.

**Binding fix:** State in C5 that the Qt arm requires a QApplication (offscreen) bootstrap and that the scan-bench-sanity job must assert qt-arm files_per_s>0 (remove continue-on-error for the Qt arm at Phase 4, or split the Qt arm into its own non-swallowed step). Otherwise document that CI validates web-arm liveness only and the Qt A/B baseline is a local manual checkpoint.

**Objection:** T6-deadlock-mechanism: a surviving isInterruptionRequested() in the extracted (QThread-stripped) pipeline would 'deadlock silently' (Appendix B 2790) — but the real failure is AttributeError (isInterruptionRequested is a QThread method), and there is no Phase-0 exit-check asserting zero surviving sites.

**Verdict (grounded):** ScanWorker(QThread) confirmed at scan_worker.py:385; the design strips QThread in extraction (922-924). isInterruptionRequested() is a QThread instance method, so a surviving self.isInterruptionRequested() in the extracted standalone pipeline raises AttributeError at that line — a loud crash-as-failure, not the silent hang Appendix B item 6 (2790) describes. The AttributeError is actually a BETTER (louder) failure than the feared deadlock, so the deadlock framing is wrong. The load-bearing residual is real and unaddressed: the ImportError guard (2713) only catches the function-absent case, not a PARTIAL extraction (10 of 11 sites done); there is no Phase-0 exit-check (the C5/Phase-0 criteria at 2649/2660/2713 contain no grep/AST gate asserting zero surviving isInterruptionRequested() in the extracted module, though test_ui_probes.py AST probes exist in CI per 1944). Strong on the missing exit-check; the deadlock-mechanism correction itself is a doc fix.

**Binding fix:** Correct Appendix B item 6 (2790): a surviving isInterruptionRequested() in the QThread-stripped pipeline raises AttributeError, not a silent deadlock. Add a Phase-0 exit-checklist item (and a tests/test_ui_probes.py AST probe) asserting ZERO isInterruptionRequested()/QThread-method references remain in core/app_service/scan_runner.py after extraction — the ImportError guard does not catch a partial extraction.

## Status after this review

The contracts are **stronger but not yet locked**. Convergence was not reached on the first pass; the binding corrections above must be folded into the contract bodies (and several into the Phase-0 build itself — the cancel two-tier teardown, the testid key, the full-res decode semaphore, the scenario-count derivation). A second adversarial pass is warranted only if a correction materially reshapes a contract; the mechanical folds do not need re-debate.
