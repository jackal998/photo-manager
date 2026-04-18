"""Background QThread that runs the deduplication scan pipeline."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


class ScanWorker(QThread):
    """Runs scan.py pipeline in a background thread.

    Signals:
        progress(str)  — one-line status update for the UI log
        finished(str)  — emitted with manifest_path on success
        failed(str)    — emitted with error message on failure
    """

    progress = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        sources: dict[str, str],   # label → path string
        output_path: str,
        threshold: int = 10,
        limit: int | None = None,
    ) -> None:
        super().__init__()
        self.sources = {k: Path(v) for k, v in sources.items() if v.strip()}
        self.output_path = Path(output_path)
        self.threshold = threshold
        self.limit = limit

    def run(self) -> None:
        try:
            self._run_pipeline()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.failed.emit(str(exc))

    def _emit(self, msg: str) -> None:
        self.progress.emit(msg)

    def _run_pipeline(self) -> None:
        from scanner.walker import scan_sources
        from scanner.hasher import compute_sha256, compute_phash
        from scanner.exif import ExiftoolProcess, batch_read_dates
        from scanner.dedup import HashResult, classify
        from scanner.manifest import write_manifest, print_summary
        import io
        from contextlib import redirect_stdout

        # --- 1. Walk sources ---
        self._emit(f"Scanning {len(self.sources)} source(s)…")
        records = []
        for label, root in self.sources.items():
            self._emit(f"  Walking {label}: {root} …")
            partial = scan_sources({label: root}, limit=self.limit)
            self._emit(f"  → {len(partial):,} files")
            records.extend(partial)
        self._emit(f"  Total: {len(records):,} media files")

        if not records:
            self.failed.emit("No media files found in the selected source folders.")
            return

        # --- 2. EXIF dates ---
        all_paths = [r.path for r in records]
        chunk_size = 500
        n_chunks = (len(all_paths) + chunk_size - 1) // chunk_size
        self._emit(f"Reading EXIF dates ({len(all_paths):,} files, {n_chunks} chunk(s))…")
        try:
            with ExiftoolProcess() as et:
                dates = {}
                for i in range(0, len(all_paths), chunk_size):
                    chunk = all_paths[i: i + chunk_size]
                    dates.update(batch_read_dates(chunk, et, chunk_size=chunk_size))
                    done = min(i + chunk_size, len(all_paths))
                    self._emit(f"  EXIF {done:,}/{len(all_paths):,}")
            found = sum(1 for v in dates.values() if v)
            self._emit(f"  EXIF done — {found:,} dates found")
        except FileNotFoundError:
            self._emit(
                "WARNING: exiftool not found on PATH — EXIF dates unavailable.\n"
                "Install from https://exiftool.org/ and add to PATH."
            )
            dates = {p: None for p in all_paths}

        # --- 3. Hash ---
        self._emit(f"Hashing {len(records):,} files…")
        hash_results: list[HashResult] = []
        for idx, record in enumerate(records):
            if self.isInterruptionRequested():
                self.failed.emit("Scan cancelled.")
                return
            sha256 = compute_sha256(record.path)
            phash = compute_phash(record.path, record.file_type)
            hash_results.append(HashResult(
                record=record,
                sha256=sha256,
                phash=phash,
                exif_date=dates.get(record.path),
            ))
            if (idx + 1) % 100 == 0 or (idx + 1) == len(records):
                self._emit(f"  Hashed {idx + 1:,}/{len(records):,}")

        # --- 4. Classify ---
        self._emit("Classifying…")
        rows = classify(hash_results, threshold=self.threshold)

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
