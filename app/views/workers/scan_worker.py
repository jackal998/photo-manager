"""Background QThread that runs the deduplication scan pipeline."""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from loguru import logger

# #424 — rolling throughput sampling window used to compute files/sec.
# 5s matches the issue's acceptance criterion ("ETA appears once ≥ 5s of
# throughput samples are available"). Wide enough that an SMB blip
# doesn't crash the rate to zero, narrow enough that a real stall
# surfaces within ~5s instead of getting smoothed out over a minute.
_THROUGHPUT_WINDOW_SECONDS = 5.0

# Minimum interval between two stage_progress emits inside a streaming
# loop. Per-second cadence keeps the UI feeling live without burning
# Qt event loop on every single file in a 100k-file scan.
_STAGE_EMIT_INTERVAL_SECONDS = 1.0

# Canonical stage names (#424). Receiver localises for display via
# translations[scan_dialog.stage_<name_lower>]; raw string passes
# through the Qt signal so the worker stays UI-agnostic.
STAGE_WALK = "WALK"
STAGE_HASH = "HASH"
STAGE_EXIFTOOL = "EXIFTOOL"
STAGE_CLASSIFY = "CLASSIFY"
STAGE_SCORE = "SCORE"
STAGE_WRITE = "WRITE"


class _StageTracker:
    """Worker-side throughput accumulator + per-second emit throttle.

    One instance per stage. Records `(timestamp, completed_count)`
    samples in a deque trimmed to the last :data:`_THROUGHPUT_WINDOW_SECONDS`
    on every update, then reports throughput as
    `(latest_completed - oldest_completed) / (latest_ts - oldest_ts)` —
    zero when the deque collapses to a single sample or the dt is
    too small for a stable rate.

    The throttle prevents per-file emits in the hot HASH / EXIFTOOL
    loops; ``should_emit()`` returns True only on (a) the first call
    for a stage, (b) the boundary (completed == total), or (c) when
    ≥ ``_STAGE_EMIT_INTERVAL_SECONDS`` has elapsed since the last emit.
    """

    def __init__(self, stage_name: str) -> None:
        self.stage_name = stage_name
        self._samples: deque[tuple[float, int]] = deque()
        self._last_emit_at: float = 0.0
        self._first_emit_done = False

    def record(self, completed: int) -> None:
        now = time.monotonic()
        self._samples.append((now, completed))
        cutoff = now - _THROUGHPUT_WINDOW_SECONDS
        while len(self._samples) > 1 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def throughput(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        t0, c0 = self._samples[0]
        t1, c1 = self._samples[-1]
        dt = t1 - t0
        if dt < 0.1:
            return 0.0
        return max(0.0, (c1 - c0) / dt)

    def should_emit(self, completed: int, total: int) -> bool:
        now = time.monotonic()
        if not self._first_emit_done:
            self._first_emit_done = True
            self._last_emit_at = now
            return True
        if total > 0 and completed >= total:
            self._last_emit_at = now
            return True
        if now - self._last_emit_at >= _STAGE_EMIT_INTERVAL_SECONDS:
            self._last_emit_at = now
            return True
        return False


class ScanWorker(QThread):
    """Runs scan.py pipeline in a background thread.

    Signals:
        progress(str)        — one-line status update for the UI log
        stage_progress(str, int, int, float)
                              — #424 typed per-stage progress: stage
                                name, completed-in-stage, total-in-stage,
                                files-per-second over the last
                                :data:`_THROUGHPUT_WINDOW_SECONDS`.
                                ``total == 0`` marks an atomic stage
                                (receiver should render indeterminate).
                                ``files_per_sec == 0`` indicates either
                                a stall or insufficient samples — ETA
                                hides until the rate stabilises.
        finished(str)        — emitted with manifest_path on success
        failed(str)          — emitted with error message on real failure
        completed_empty()    — scan ran cleanly but found 0 media files
                               (kept distinct from `failed` so the dialog
                               can avoid misclassifying a benign empty
                               input as an error)
    """

    progress = Signal(str)
    stage_progress = Signal(str, int, int, float)
    finished = Signal(str)
    failed = Signal(str)
    completed_empty = Signal()

    def __init__(
        self,
        sources: dict[str, str],                    # label → path string
        output_path: str,
        recursive_map: dict[str, bool] | None = None,
        source_priority: dict[str, int] | None = None,
        threshold: int = 10,
        mean_color_threshold: int = 30,
        limit: int | None = None,
        workers: int = 4,
        auto_select_enabled: bool = False,
        auto_select_aggressive_delete: bool = False,
    ) -> None:
        super().__init__()
        self.sources = {k: Path(v) for k, v in sources.items() if v.strip()}
        self.output_path = Path(output_path)
        self.recursive_map = recursive_map or {}
        self.source_priority = source_priority   # None → auto-inferred in classify()
        self.threshold = threshold
        self.mean_color_threshold = mean_color_threshold
        self.limit = limit
        self.workers = workers
        # #212 — when True, promote the top-scored row in each duplicate
        # group to action="KEEP" before writing the manifest. The scan
        # dialog persists the corresponding setting; defaults False so
        # callers that don't opt in get the pre-#212 behaviour.
        self.auto_select_enabled = auto_select_enabled
        # #393 — when True (and auto_select_enabled also True), set
        # user_decision='delete' on every non-keeper row in scored
        # groups so the user opens Execute Action with the full triage
        # pre-populated. Off by default because it's destructive-leaning;
        # the user still confirms via the standard ExecuteAction flow.
        self.auto_select_aggressive_delete = auto_select_aggressive_delete

    def run(self) -> None:
        try:
            self._run_pipeline()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Log with traceback so the rotating app_<date>.log captures the
            # full forensic context — the dialog log box clears on close.
            logger.exception("Scan pipeline failed: {}", exc)
            self.failed.emit(str(exc))

    def _emit(self, msg: str) -> None:
        # Forward every progress line to loguru so the rotating app_<date>.log
        # has a persistent record for users reporting "the scan stopped" —
        # the dialog log box is transient and disappears on close.
        logger.info("scan: {}", msg)
        self.progress.emit(msg)

    def _emit_stage(
        self,
        tracker: _StageTracker,
        completed: int,
        total: int,
        *,
        force: bool = False,
    ) -> None:
        """#424 — emit a stage_progress signal with throttling.

        ``force`` bypasses the per-second throttle for stage boundaries
        (start / end) where the receiver must update the label even if
        the throttle hasn't elapsed. The throughput value rides on the
        tracker's rolling deque; samples are recorded unconditionally
        so a slow loop's rate stays accurate even when emits are
        throttled away.
        """
        tracker.record(completed)
        if force or tracker.should_emit(completed, total):
            self.stage_progress.emit(
                tracker.stage_name, completed, total, tracker.throughput()
            )

    def _run_pipeline(self) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from scanner.walker import scan_sources
        from scanner.hasher import compute_hashes
        from scanner.exif import ExiftoolProcess, batch_read_extracts, parse_exif_date
        from scanner.dedup import HashResult, classify
        from scanner.manifest import write_manifest, print_summary
        from scanner.scoring import apply_scoring_to_rows
        import io
        from contextlib import redirect_stdout

        # #425 — was "no files will be moved or deleted"; reworded to
        # match scan_dialog.notice and stop implying a file operation
        # the read-only scan never performs.
        self._emit("Read-only scan — no files on disk are changed.")
        self._emit("")

        # --- 1. Walk sources ---
        self._emit(f"Scanning {len(self.sources)} source(s)…")
        records = []
        walk_tracker = _StageTracker(STAGE_WALK)
        total_sources = len(self.sources)
        self._emit_stage(walk_tracker, 0, total_sources, force=True)
        for idx, (label, root) in enumerate(self.sources.items()):
            mode = "flat" if self.recursive_map.get(label) is False else "recursive"
            self._emit(f"  Walking {label} ({mode}): {root} …")
            partial = scan_sources(
                {label: root},
                limit=self.limit,
                recursive_map={label: self.recursive_map.get(label, True)},
            )
            self._emit(f"  → {len(partial):,} files")
            records.extend(partial)
            # #424 — WALK reports folder-count progress (not per-file)
            # because scan_sources is synchronous per source and we
            # don't know the per-source total until it returns. Force
            # emit on each source-boundary so the bar advances visibly
            # even on a single-source scan that completes in <1s.
            self._emit_stage(walk_tracker, idx + 1, total_sources, force=True)
        self._emit(f"  Total: {len(records):,} media files")

        if not records:
            # Empty input is a benign success, not a failure: the user picked
            # folders that simply have no media. Log a neutral terminator and
            # signal the dialog to re-enable Start Scan without a red modal.
            self._emit("Done. No media files found — nothing to scan.")
            self.completed_empty.emit()
            return

        # --- 2. Hash + PIL EXIF (parallel) ---
        # One file read per image: SHA-256, pHash, and EXIF date for JPEG/PNG
        # are extracted from the same in-memory buffer.
        chunk_size = 500
        cancel_flag = threading.Event()
        skipped: list[tuple[Path, str, str]] = []  # (path, exc type, exc msg)

        def _hash_one(idx_record: tuple) -> tuple:
            idx, record = idx_record
            if cancel_flag.is_set():
                return idx, None
            try:
                sha256, phash, mean_color, raw_date, px_w, px_h = compute_hashes(record.path, record.file_type)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # One bad file must never abort the whole scan — log + skip.
                skipped.append((record.path, type(exc).__name__, str(exc)))
                return idx, None
            pil_date = parse_exif_date(raw_date) if raw_date else None
            return idx, HashResult(
                record=record, sha256=sha256, phash=phash, mean_color=mean_color,
                exif_date=pil_date, pixel_width=px_w, pixel_height=px_h,
            )

        self._emit(f"Hashing {len(records):,} files (workers={self.workers})…")
        hash_results: list[HashResult] = [None] * len(records)  # type: ignore[list-item]
        done = 0
        hash_tracker = _StageTracker(STAGE_HASH)
        self._emit_stage(hash_tracker, 0, len(records), force=True)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_hash_one, (i, r)): i for i, r in enumerate(records)}
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    cancel_flag.set()
                    pool.shutdown(wait=False, cancel_futures=True)
                    logger.warning("Scan cancelled by user during hashing pass")
                    self.failed.emit("Scan cancelled.")
                    return
                idx, result = future.result()
                if result is not None:
                    hash_results[idx] = result
                done += 1
                if done % 100 == 0 or done == len(records):
                    self._emit(f"  Hashed {done:,}/{len(records):,}")
                # #424 — per-second-throttled stage_progress emit
                # alongside the existing per-100 log line. The tracker
                # records every iteration so throughput stays accurate
                # even when the emit is throttled away.
                self._emit_stage(hash_tracker, done, len(records))

        # Remove any None slots (cancelled futures that didn't run, or skipped files)
        hash_results = [r for r in hash_results if r is not None]

        # Detect silent image-decode failures: compute_hashes returned without
        # raising but PIL couldn't produce a pHash — the file is truncated or
        # corrupt. Route to the same skip channel as exception failures so the
        # user sees them in the log instead of getting a misleading UNDATED row.
        #
        # Restricted to formats where PIL is the primary decoder and a missing
        # pHash unambiguously means decode-failure:
        #   - GIF excluded: compute_hashes always returns phash=None for GIF
        #     (intentional early-return at scanner/hasher.py:53), so flagging
        #     phash=None as corruption false-positives 100% of the time (#75).
        #   - RAW excluded: rawpy is the decoder, and rawpy fails on legitimate
        #     non-camera-RAW TIFFs (Photoshop / scanner output) — flagging
        #     those as corrupt drops real user files from the manifest (#75).
        _IMAGE_TYPES = frozenset(("jpeg", "heic", "png", "webp"))
        corrupt_paths: set[Path] = set()
        for r in hash_results:
            if r.record.file_type in _IMAGE_TYPES and r.phash is None:
                skipped.append((
                    r.record.path,
                    "ImageDecodeError",
                    "image file could not be decoded (truncated or corrupt)",
                ))
                corrupt_paths.add(r.record.path)
        if corrupt_paths:
            hash_results = [r for r in hash_results if r.record.path not in corrupt_paths]

        if skipped:
            self._emit(f"  Skipped {len(skipped):,} unreadable file(s):")
            for p, exc_type, exc_msg in skipped[:10]:
                self._emit(f"    {p}  [{exc_type}: {exc_msg}]")
            if len(skipped) > 10:
                self._emit(f"    … and {len(skipped) - 10:,} more")

        # --- 3. exiftool for ALL non-skip files ---
        # Previously this ran only for HEIC/RAW/MOV/MP4 (formats whose dates
        # PIL cannot extract). For the #187 scoring system every file needs
        # a full census tag count + GPS / xmpMM:DerivedFrom presence, so
        # the exiftool pass now covers everything (except file_type="skip").
        # PIL dates from the hash pass are preserved — exiftool dates only
        # fill in when PIL didn't find one.
        et_records = [r for r in hash_results if r.record.file_type != "skip"]
        extracts: dict = {}
        if et_records:
            et_paths = [r.record.path for r in et_records]
            n_chunks = (len(et_paths) + chunk_size - 1) // chunk_size
            self._emit(
                f"EXIF + scoring signals via exiftool for {len(et_paths):,} files"
                f" ({n_chunks} chunk(s))…"
            )
            exif_tracker = _StageTracker(STAGE_EXIFTOOL)
            self._emit_stage(exif_tracker, 0, len(et_paths), force=True)
            try:
                with ExiftoolProcess() as et:
                    for i in range(0, len(et_paths), chunk_size):
                        chunk = et_paths[i: i + chunk_size]
                        extracts.update(batch_read_extracts(chunk, et, chunk_size=chunk_size))
                        done_et = min(i + chunk_size, len(et_paths))
                        self._emit(f"  EXIF {done_et:,}/{len(et_paths):,}")
                        # #424 — per-chunk stage_progress emit; the
                        # throttle in _emit_stage drops sub-second
                        # repeats so a fast local SSD scan doesn't
                        # spam the dialog.
                        self._emit_stage(exif_tracker, done_et, len(et_paths))
                found_dates = sum(1 for e in extracts.values() if e.exif_date is not None)
                with_gps = sum(1 for e in extracts.values() if e.gps_present)
                self._emit(f"  EXIF done — {found_dates:,} dates, {with_gps:,} with GPS")
                # Backfill exif_date for records where PIL didn't find one.
                for r in et_records:
                    if r.exif_date is None:
                        extract = extracts.get(r.record.path)
                        if extract is not None:
                            r.exif_date = extract.exif_date
            except FileNotFoundError:
                self._emit(
                    "WARNING: exiftool not found on PATH — EXIF dates for HEIC/RAW/video"
                    " and scoring signals (GPS, EXIF census, XMP provenance) unavailable.\n"
                    "Install from https://exiftool.org/ and add to PATH."
                )

        # --- 4. Classify ---
        # #424: classify() is opaque from the worker's view (single
        # call into scanner/dedup.py). Surface start + end emits with
        # total=0 so the receiver renders the bar as indeterminate
        # ("CLASSIFY — working…") instead of stuck at 0%. Pattern
        # repeats for SCORE and WRITE below.
        self._emit("Classifying…")
        classify_tracker = _StageTracker(STAGE_CLASSIFY)
        self._emit_stage(classify_tracker, 0, 0, force=True)
        rows = classify(
            hash_results,
            threshold=self.threshold,
            mean_color_threshold=self.mean_color_threshold,
            source_priority=self.source_priority,
        )
        self._emit_stage(classify_tracker, 1, 1, force=True)

        # --- 4.5: score within each duplicate group (#187) ---
        # Mutates rows in place: copies exif_tag_count / gps_present /
        # xmp_derived from extracts into ManifestRow, then assigns
        # compute_score(...) per group. Isolated rows (group_id is None)
        # stay unscored — no peers to compete with.
        score_tracker = _StageTracker(STAGE_SCORE)
        self._emit_stage(score_tracker, 0, 0, force=True)
        apply_scoring_to_rows(rows, extracts)
        self._emit_stage(score_tracker, 1, 1, force=True)

        # --- 4.6: optional auto-select keepers (#212, #393) ---
        # When enabled in the scan dialog, the top-scored row in each
        # duplicate group is promoted to action="KEEP" so the manifest
        # loads with keepers already chosen — the user does not have
        # to open the Selection dialog manually. Other duplicates keep
        # their classifier action (REVIEW_DUPLICATE / EXACT / MOVE) so
        # the user still confirms deletions explicitly.
        #
        # #393 layered on top: keepers also receive user_decision=""
        # (canonical "keep" state — empty string, NOT the literal "keep")
        # AND is_locked=1 (written post-write_manifest via the
        # repo's batch_update_* methods, since ManifestRow has neither
        # field — those live on the DB and PhotoRecord). The lock gives
        # a visible tree badge; user_decision="" composes with #182
        # LockedRowsConfirmDialog if the user later applies bulk-regex.
        # #425 — Previously this wrote the literal "keep" string, which
        # then leaked into the tree's Action column as raw "keep" text
        # instead of an empty cell. Canonical convention everywhere else
        # in the codebase (settable_decisions, set_decision via right-
        # click) is empty string for keep; auto-select now matches.
        #
        # #393 (c) — optional aggressive mode: every non-keeper row in
        # a scored group gets user_decision='delete'. Off by default
        # (destructive-leaning); the user still confirms via the
        # standard ExecuteAction flow before any file moves.
        keepers: set[str] = set()
        if self.auto_select_enabled:
            from core.services.auto_select import top_score_path_per_group
            keepers = top_score_path_per_group(rows)
            if keepers:
                for row in rows:
                    if row.source_path in keepers:
                        row.action = "KEEP"
                self._emit(f"Auto-select: marked {len(keepers):,} keeper(s) per group.")

        # Capture print_summary output and re-emit as progress
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_summary(rows, skipped=len(skipped))
        for line in buf.getvalue().splitlines():
            self._emit(line)

        # --- 5. Write manifest ---
        self._emit(f"Writing manifest → {self.output_path}")
        write_tracker = _StageTracker(STAGE_WRITE)
        self._emit_stage(write_tracker, 0, 0, force=True)
        write_manifest(rows, self.output_path)
        self._emit_stage(write_tracker, 1, 1, force=True)

        # --- 5.5: post-write keep+lock (and aggressive delete) (#393) ---
        # Runs only when auto_select_enabled fired and produced keepers.
        # The helper composes the repo's batch_update_* primitives — see
        # core/services/auto_select.py::apply_auto_select_decisions for
        # the write contract. Both writes are tiny (≤N rows per scan)
        # so the cost is negligible compared to the scan itself.
        if keepers:
            from core.services.auto_select import apply_auto_select_decisions
            non_keepers: set[str] | None = None
            if self.auto_select_aggressive_delete:
                # Non-keepers in scored groups: rows with both
                # ``group_id`` AND ``score`` (i.e. ranked peers) but
                # NOT picked as the keeper. ``score=None`` peers (Live
                # Photo MOV passengers, all-MOV groups) are excluded —
                # they aren't candidates for an explicit delete.
                non_keepers = {
                    row.source_path for row in rows
                    if row.group_id is not None
                    and row.score is not None
                    and row.source_path not in keepers
                }
                self._emit(
                    f"Auto-select aggressive: marked {len(non_keepers):,}"
                    f" non-keeper(s) for delete."
                )
            apply_auto_select_decisions(
                str(self.output_path), keepers, non_keepers
            )
            self._emit(
                f"Auto-select: locked {len(keepers):,} keeper(s);"
                f" decisions written."
            )

        self._emit("Done.")
        self.finished.emit(str(self.output_path))
