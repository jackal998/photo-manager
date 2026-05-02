"""Background QThread that runs the deduplication scan pipeline."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


class ScanWorker(QThread):
    """Runs scan.py pipeline in a background thread.

    Signals:
        progress(str)        — one-line status update for the UI log
        finished(str)        — emitted with manifest_path on success
        failed(str)          — emitted with error message on real failure
        completed_empty()    — scan ran cleanly but found 0 media files
                               (kept distinct from `failed` so the dialog
                               can avoid misclassifying a benign empty
                               input as an error)
    """

    progress = Signal(str)
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

    def run(self) -> None:
        try:
            self._run_pipeline()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.failed.emit(str(exc))

    def _emit(self, msg: str) -> None:
        self.progress.emit(msg)

    def _run_pipeline(self) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from scanner.walker import scan_sources
        from scanner.hasher import compute_hashes
        from scanner.exif import ExiftoolProcess, batch_read_dates, parse_exif_date
        from scanner.dedup import HashResult, classify
        from scanner.manifest import write_manifest, print_summary
        import io
        from contextlib import redirect_stdout

        self._emit("Read-only scan — no files will be moved or deleted.")
        self._emit("MOVE / SKIP / REVIEW in the results are planned actions only.")
        self._emit("")

        # --- 1. Walk sources ---
        self._emit(f"Scanning {len(self.sources)} source(s)…")
        records = []
        for label, root in self.sources.items():
            mode = "flat" if self.recursive_map.get(label) is False else "recursive"
            self._emit(f"  Walking {label} ({mode}): {root} …")
            partial = scan_sources(
                {label: root},
                limit=self.limit,
                recursive_map={label: self.recursive_map.get(label, True)},
            )
            self._emit(f"  → {len(partial):,} files")
            records.extend(partial)
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
        _EXIFTOOL_TYPES = frozenset(("heic", "raw", "mov", "mp4"))
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

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_hash_one, (i, r)): i for i, r in enumerate(records)}
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    cancel_flag.set()
                    pool.shutdown(wait=False, cancel_futures=True)
                    self.failed.emit("Scan cancelled.")
                    return
                idx, result = future.result()
                if result is not None:
                    hash_results[idx] = result
                done += 1
                if done % 100 == 0 or done == len(records):
                    self._emit(f"  Hashed {done:,}/{len(records):,}")

        # Remove any None slots (cancelled futures that didn't run, or skipped files)
        hash_results = [r for r in hash_results if r is not None]

        # Detect silent image-decode failures: compute_hashes returned without
        # raising but PIL couldn't produce a pHash — the file is truncated or
        # corrupt. Route to the same skip channel as exception failures so the
        # user sees them in the log instead of getting a misleading UNDATED row.
        _IMAGE_TYPES = frozenset(("jpeg", "heic", "raw", "png", "gif", "webp"))
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

        # --- 3. exiftool for HEIC / RAW / MOV / MP4 only ---
        # JPEG and PNG dates already populated from the PIL pass above.
        et_records = [r for r in hash_results if r.exif_date is None
                      and r.record.file_type in _EXIFTOOL_TYPES]
        if et_records:
            et_paths = [r.record.path for r in et_records]
            n_chunks = (len(et_paths) + chunk_size - 1) // chunk_size
            self._emit(f"EXIF via exiftool for {len(et_paths):,} non-JPEG files ({n_chunks} chunk(s))…")
            try:
                with ExiftoolProcess() as et:
                    dates: dict = {}
                    for i in range(0, len(et_paths), chunk_size):
                        chunk = et_paths[i: i + chunk_size]
                        dates.update(batch_read_dates(chunk, et, chunk_size=chunk_size))
                        done_et = min(i + chunk_size, len(et_paths))
                        self._emit(f"  EXIF {done_et:,}/{len(et_paths):,}")
                found = sum(1 for v in dates.values() if v)
                self._emit(f"  EXIF done — {found:,} dates found")
                for r in et_records:
                    r.exif_date = dates.get(r.record.path)
            except FileNotFoundError:
                self._emit(
                    "WARNING: exiftool not found on PATH — EXIF dates for HEIC/RAW/video unavailable.\n"
                    "Install from https://exiftool.org/ and add to PATH."
                )

        # --- 4. Classify ---
        self._emit("Classifying…")
        rows = classify(
            hash_results,
            threshold=self.threshold,
            mean_color_threshold=self.mean_color_threshold,
            source_priority=self.source_priority,
        )

        # Capture print_summary output and re-emit as progress
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_summary(rows)
        for line in buf.getvalue().splitlines():
            self._emit(line)

        # --- 5. Write manifest ---
        self._emit(f"Writing manifest → {self.output_path}")
        write_manifest(rows, self.output_path)
        self._emit("Done.")
        self.finished.emit(str(self.output_path))
