"""HEVC-to-H.264 transcode service for web video fallback.

Qt-free: shells out to ffmpeg via subprocess.  The only consumer is the
FastAPI media route (app/web/routes/media.py) which calls
``get_transcoded_path`` via ``run_in_executor`` so the blocking ffmpeg
sub-process never touches the event loop.

Cache design mirrors image_service.py:
- Versioned sub-directory under the configured base dir.
- Cache key = sha1(source_path | mtime_ns).hexdigest() + ".mp4".
- Atomic write: transcode to <key>.mp4.tmp, os.replace → <key>.mp4 on
  success; delete .tmp on failure so no half-written file survives.
- Two concurrency guards:
  (a) Per-cache-key Lock: serialises concurrent requests for the SAME
      source so the second caller finds the cache hit after the first
      finishes, never re-transcodes.
  (b) Module-level BoundedSemaphore(_TRANSCODE_SEM): limits the total
      number of concurrent ffmpeg sub-processes to 2.  Acquired only
      around the subprocess call; released in a finally block.  Cache
      hits bypass both guards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from loguru import logger

# Bump to invalidate the on-disk cache namespace.
TRANSCODE_RECIPE_VERSION = "1"

# Hard cap on concurrent ffmpeg processes.
_TRANSCODE_SEM = threading.BoundedSemaphore(2)

# Per-cache-key locks: serialise concurrent requests for the same source.
# The outer lock protects _KEY_LOCKS itself; inner locks protect individual
# transcode operations.
_KEY_LOCKS_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}

# Mirror exif.py's _CREATE_NO_WINDOW constant.
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class TranscodeUnavailable(Exception):
    """Raised when ffmpeg is not installed / not found on PATH.

    The media route maps this to HTTP 501.
    """


class TranscodeError(Exception):
    """Raised when ffmpeg exits non-zero or the output is missing."""


def _key_lock(cache_key: str) -> threading.Lock:
    """Return (creating if needed) the per-key Lock for ``cache_key``."""
    with _KEY_LOCKS_LOCK:
        if cache_key not in _KEY_LOCKS:
            _KEY_LOCKS[cache_key] = threading.Lock()
        return _KEY_LOCKS[cache_key]


def _compute_cache_key(source: Path) -> str:
    """Compute sha1(source_path | mtime_ns).hexdigest().

    mtime_ns invalidates the cache when the file changes on disk.
    """
    mtime_ns = source.stat().st_mtime_ns
    sig = f"{source}|{mtime_ns}".encode("utf-8", errors="ignore")
    return hashlib.sha1(sig).hexdigest()


class TranscodeService:
    """Lazy, cached HEVC-to-H.264 transcoder.

    Construct once in the FastAPI lifespan and store on
    ``app.state.transcode_service``.  All public methods are blocking —
    callers on the async path MUST use ``run_in_executor``.
    """

    def __init__(self, settings: Optional[object] = None) -> None:
        """Initialise cache directory and locate ffmpeg.

        Parameters
        ----------
        settings:
            A ``JsonSettings`` instance (or duck-typed equivalent).  Used
            to read ``video_transcode_cache_dir`` the same way
            ImageService reads ``thumbnail_disk_cache_dir``.  May be
            ``None`` (e.g. in tests) — the default platform path is used.
        """
        default_dir = str(
            Path.home() / "AppData" / "Local" / "PhotoManager" / "transcodes"
        )
        if settings is not None:
            raw = settings.get("video_transcode_cache_dir", default_dir)
            if isinstance(raw, str):
                default_dir = os.path.expandvars(raw)

        self._base_dir = Path(default_dir) / f"v{TRANSCODE_RECIPE_VERSION}"
        self._base_dir.mkdir(parents=True, exist_ok=True)

        # Resolve ffmpeg once at init.  None means "not installed".
        self._ffmpeg: Optional[str] = shutil.which("ffmpeg")
        if self._ffmpeg is None:
            logger.warning("ffmpeg not found on PATH — transcode fallback unavailable")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_transcoded_path(self, source: Path) -> Path:
        """Return the path to the cached H.264 MP4 for ``source``.

        Transcodes on cache-miss.  Raises:
        - ``TranscodeUnavailable`` if ffmpeg is not installed.
        - ``TranscodeError`` if ffmpeg exits non-zero or output is
          missing after a successful transcode.
        - ``OSError`` if the source file cannot be stat'd.

        Thread-safe: two concurrent requests for the same ``source``
        serialise on the per-key Lock; the second caller returns the
        cache hit without re-transcoding.  The total number of
        concurrent ffmpeg processes is capped at 2.
        """
        if self._ffmpeg is None:
            raise TranscodeUnavailable(
                "ffmpeg not found on PATH; cannot transcode video"
            )

        cache_key = _compute_cache_key(source)
        out_path = self._base_dir / f"{cache_key}.mp4"

        lock = _key_lock(cache_key)
        with lock:
            # Fast path: cache hit (checked inside the key-lock so a
            # concurrent first-call can't race past the rename).
            if out_path.exists():
                return out_path

            self._transcode(source, out_path)
            return out_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transcode(self, source: Path, out_path: Path) -> None:
        """Run ffmpeg to produce ``out_path`` from ``source``.

        Acquires ``_TRANSCODE_SEM`` only around the subprocess call so
        the concurrency cap covers only the CPU/IO-bound ffmpeg work,
        not the lock-wait or cache-check.
        """
        # Use a sibling .tmp file with the same stem so ffmpeg knows the
        # container format.  Explicitly pass -f mp4 as a belt-and-suspenders
        # guard against Windows builds that can't infer format from .tmp.
        tmp_path = out_path.parent / (out_path.stem + "_tmp.mp4")
        cmd = [
            self._ffmpeg,
            "-i", str(source),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart",
            "-f", "mp4",
            "-y",
            str(tmp_path),
        ]
        _TRANSCODE_SEM.acquire()
        try:
            result = subprocess.run(
                cmd,
                shell=False,
                timeout=300,
                check=False,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            # A hung/oversized encode must not leave a partial staging file
            # nor surface as a raw TimeoutExpired (route maps TranscodeError).
            self._remove_tmp(tmp_path)
            raise TranscodeError(
                f"ffmpeg timed out after 300s for {source!r}"
            ) from exc
        finally:
            _TRANSCODE_SEM.release()

        if result.returncode != 0:
            self._remove_tmp(tmp_path)
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise TranscodeError(
                f"ffmpeg exited {result.returncode} for {source!r}: {stderr[:400]}"
            )

        if not tmp_path.exists():
            # ffmpeg reported success but wrote nothing — matches the
            # "output is missing" contract in get_transcoded_path's docstring.
            raise TranscodeError(f"ffmpeg produced no output for {source!r}")

        # Atomic rename so a reader never observes a partial file.
        os.replace(tmp_path, out_path)
        logger.info("Transcoded {} → {}", source.name, out_path.name)

    @staticmethod
    def _remove_tmp(tmp_path: Path) -> None:
        """Best-effort removal of a partial staging file (never raises)."""
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
