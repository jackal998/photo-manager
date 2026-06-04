"""Batch EXIF date extraction via exiftool's -stay_open mode."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


# #465 — default timeout for individual stdout-line reads inside
# ``ExiftoolProcess.execute()``. 60s is generous — even a 500-file
# batch on NAS produces lines well before that window — but a wedged
# exiftool (corrupt input, dropped SMB session, kernel pipe stall)
# would otherwise block ``readline()`` indefinitely and hang the
# consumer thread for the rest of the scan. Caller catches
# ``ExiftoolTimeout`` and rotates to a fresh process.
_EXIFTOOL_READ_TIMEOUT_SECONDS = 60.0


class ExiftoolTimeout(Exception):
    """Raised by :meth:`ExiftoolProcess.execute` when the stdout reader
    thread doesn't produce a line within the timeout window. Signals a
    wedged exiftool — caller should ``close()`` the process (force-kill
    path) and rotate to a fresh instance.
    """

# Suppress the fresh-console window Windows allocates for a console-subsystem
# child (exiftool) spawned by a windowed-subsystem parent (PyInstaller
# ``--noconsole`` build, PR #420). On POSIX the constant is undefined, so we
# fall back to 0 — a no-op ``creationflags`` value on every platform.
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# #460 — Windows Job Object: when the parent process dies for ANY reason
# (graceful exit, crash, Task Manager force-kill, Explorer-freeze followed
# by user force-quit), the kernel closes the last handle to the job and
# terminates every process assigned to it. Without this, an ungraceful
# parent exit orphans the ``exiftool`` children — and on Windows, orphan
# exiftool processes holding file handles into a scanned tree are what
# triggers the user-reported Explorer freeze (the root cause of #460).
#
# Lazy + import-safe: the job handle is created once per Python process on
# first use. On POSIX or when pywin32 is unavailable (development checkout
# without optional deps installed), ``_get_kill_on_close_job()`` returns
# ``None`` and child-assignment becomes a no-op — preserving the pre-#460
# Popen behaviour exactly.
_KILL_ON_CLOSE_JOB = None  # type: ignore[var-annotated]


def _get_kill_on_close_job():
    """Return a process-wide Job Object with ``KILL_ON_JOB_CLOSE`` set, or
    ``None`` on non-Windows / when pywin32 is unavailable. The handle is
    intentionally leaked for the lifetime of the Python process — closing
    it would kill all assigned children prematurely.
    """
    global _KILL_ON_CLOSE_JOB
    if _KILL_ON_CLOSE_JOB is not None:
        return _KILL_ON_CLOSE_JOB
    if sys.platform != "win32":
        return None
    try:
        import win32job  # type: ignore[import-not-found]
    except ImportError:
        # pywin32 not installed in this checkout — fall back to the
        # legacy orphan-on-parent-death behaviour rather than refusing
        # to scan.
        return None
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # Any Win32 failure (rare — sandboxed CI, denied
        # SeAssignPrimaryToken, etc.) degrades to legacy behaviour
        # rather than failing the scan.
        return None
    _KILL_ON_CLOSE_JOB = job
    return job


def assign_pid_to_kill_job(pid: int) -> bool:
    """Assign process ``pid`` to the process-wide ``KILL_ON_JOB_CLOSE`` job so
    an ungraceful parent exit (crash / Task Manager force-kill) terminates it
    too. Returns ``True`` if assigned, ``False`` on no-op — POSIX, pywin32
    missing, no job, or any Win32 failure.

    #460 originally covered only the exiftool children (assigned inline in
    ``ExiftoolProcess.__init__``). #549(a) extracts that to this shared helper
    so the ``ProcessPoolExecutor`` hash workers — which read the source disks
    directly and otherwise orphan on a hard parent-kill — get the same guard.

    The parent process holds the sole job handle (intentionally leaked in
    ``_get_kill_on_close_job``), so assigning a child here keeps the
    last-handle-closes-on-parent-death semantics intact. Fail-open by design:
    a miss just means that one process orphans on a hard kill (pre-#460
    behaviour), never a scan abort.
    """
    job = _get_kill_on_close_job()
    if job is None:
        return False
    try:
        import win32api  # type: ignore[import-not-found]
        import win32job  # type: ignore[import-not-found]
        # PROCESS_ALL_ACCESS = 0x001F0FFF (documented Win32 constant) — needed
        # for AssignProcessToJobObject to transfer the process into the job.
        #
        # IMPORTANT: keep the PyHANDLE as a live local — do NOT wrap in int().
        # int(pyhandle) drops the PyHANDLE object, which closes the OS handle
        # immediately on GC, leaving AssignProcessToJobObject a stale handle
        # → ERROR_INVALID_HANDLE (6). This was the silent regression in #555:
        # assign always returned False, nothing was ever in the kill-on-close
        # job, and exiftool + process-pool workers orphaned on hard parent-kill.
        handle = win32api.OpenProcess(0x001F0FFF, False, pid)
        win32job.AssignProcessToJobObject(job, handle)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        # Already-in-another-job (pre-Win8 no-nesting), denied access, etc. —
        # leave the process unjailed rather than abort the scan.
        return False


def _process_in_any_job() -> bool:
    """Return ``True`` if this process already belongs to *any* Windows Job
    Object, ``False`` if it is job-free, and ``True`` (fail-safe) on POSIX /
    missing pywin32 / any Win32 error.

    #558 — gating exiftool's kill-on-close assignment on this is what makes
    re-enabling #460's hard-exit reaping SAFE. ``exiftool.exe`` is a PAR
    self-extracting Perl executable: it re-execs a child interpreter (a
    *grandchild* of this process) that does the actual tag extraction. When
    our ``KILL_ON_JOB_CLOSE`` job is assigned while we are ALREADY inside
    another job (the GitHub Actions runner, some console hosts, RDP /
    Task-Scheduler launches), our job nests under that outer job; the
    grandchild interpreter is then force-joined to the whole chain and the
    outer job's limits intersect onto it, corrupting the extended EXIF pass
    non-deterministically (#556: ``s42_scoring`` NULL ``exif_tag_count``).
    The process-pool hash workers never hit this — they are single leaf
    processes with no grandchild.

    Rather than fight the nesting (``CREATE_BREAKAWAY_FROM_JOB`` depends on the
    outer job permitting breakaway, which is undocumented and — empirically,
    on Win10 under a session host — unreliable), we simply DON'T jail when
    already nested. Nothing is lost by skipping: when the parent is in a job,
    that outer owner (e.g. the CI runner) already reaps the whole tree on its
    own teardown. Reaping is only *needed* on a bare desktop — exactly the
    case where we are NOT in a job.

    Fail-safe direction: any uncertainty returns ``True`` ("treat as already
    owned by someone else → don't add our own job"), which is the
    no-corruption choice.
    """
    if sys.platform != "win32":
        return True
    try:
        import win32api  # type: ignore[import-not-found]
        import win32job  # type: ignore[import-not-found]

        # Pass ``None`` as the job handle to ask "in ANY job?" rather than a
        # specific one (documented pywin32 / Win32 ``IsProcessInJob`` contract).
        return bool(win32job.IsProcessInJob(win32api.GetCurrentProcess(), None))
    except Exception:  # pylint: disable=broad-exception-caught
        return True


class ExiftoolProcess:
    """Persistent exiftool process for batch EXIF reads.

    Uses ``-stay_open True`` for performance — avoids subprocess overhead
    per file. Pattern originally from ``sync_takeout.py``; revised after
    photo-manager#145 to:

    * Separate stdout and stderr on **distinct OS pipes** (not merged via
      ``subprocess.STDOUT``). For any output large enough to fill the OS
      pipe buffer (~64 KB on Linux/Windows — i.e. several hundred files
      in one ``-stay_open`` batch) the kernel flushes stdout in chunks,
      and exiftool's progress messages on stderr can splice INTO stdout
      at the byte level. Concretely we have observed:
      ``"EXIF:DateTimeOriginal": "2 3360 image files read\\n024:..."``
      — the progress line spliced between bytes ``2`` and ``024:...`` of
      a date string. With the old line-positional parser this corrupted
      one date silently. Under JSON, it makes ``json.loads`` fail on the
      whole batch, returning empty results for every file. The fix on
      both ends: (a) read stderr on a daemon thread so exiftool never
      blocks on a full stderr pipe, and (b) keep the streams structurally
      separate so byte-level interleaving is impossible.
    * ``execute()`` appends any captured stderr to the returned string
      (after stdout, on a new line) so callers that grep for
      ``"error"`` / ``"warning"`` still see those words. JSON callers
      slice on ``[ ... ]`` so the trailing stderr text is harmless to
      them either way.
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["exiftool", "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
        # #558 — re-enable #460's hard-exit reaping for exiftool, but ONLY
        # when this process is not already inside another Job Object. Jailing a
        # live ``-stay_open`` exiftool while nested under an outer job (CI
        # runner / console host) corrupts its extended pass — see
        # ``_process_in_any_job`` for the PAR-grandchild mechanism (#556:
        # ``s42_scoring`` NULL ``exif_tag_count`` for a non-deterministic
        # subset of files). On a bare desktop (not in a job) we still want the
        # kill-on-close guard so an ungraceful parent exit (crash / Task
        # Manager force-kill) doesn't orphan exiftool and leave file handles
        # into the scanned tree — the Explorer-freeze #460 was filed for.
        # #561 already hard-kills exiftool on the *graceful* cancel/close path;
        # this gate covers the *hard*-exit path without the #556 corruption.
        if not _process_in_any_job():
            assign_pid_to_kill_job(self.proc.pid)
        self._stderr_buf: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()
        # #465 — stdout reader thread mirrors the stderr drain pattern.
        # ``queue.Queue`` is thread-safe; the ``execute()`` consumer uses
        # ``get(timeout=...)`` to detect wedges instead of blocking on
        # ``readline()`` indefinitely. A ``None`` enqueued by the reader
        # signals EOF / read error so ``execute()`` can exit cleanly.
        self._stdout_queue: queue.Queue[Optional[str]] = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, daemon=True
        )
        self._stdout_thread.start()

    def _drain_stderr(self) -> None:
        """Continuously pull lines off the stderr pipe so exiftool never
        blocks on a full pipe buffer. Lines are buffered in
        ``_stderr_buf`` for ``execute()`` to harvest.
        """
        while True:
            try:
                line = self.proc.stderr.readline()
            except Exception:  # pylint: disable=broad-exception-caught
                # The proc may have closed; exit the drain thread silently.
                break
            if not line:
                break
            with self._stderr_lock:
                self._stderr_buf.append(line)

    def _drain_stdout(self) -> None:
        """Continuously pull lines off the stdout pipe and queue them for
        :meth:`execute`. A ``None`` sentinel is enqueued on EOF / read
        error so ``execute()`` can detect process death without polling.
        """
        while True:
            try:
                line = self.proc.stdout.readline()
            except Exception:  # pylint: disable=broad-exception-caught
                # The proc may have closed; signal EOF and exit.
                self._stdout_queue.put(None)
                break
            if not line:
                self._stdout_queue.put(None)
                break
            self._stdout_queue.put(line)

    def execute(
        self, args: list, read_timeout: float = _EXIFTOOL_READ_TIMEOUT_SECONDS,
    ) -> str:
        """Send args to exiftool, return stdout (with any stderr appended)
        up to the ``{ready}`` sentinel.

        Raises :class:`ExiftoolTimeout` if the stdout reader thread
        doesn't yield a line within ``read_timeout`` seconds. Indicates
        a wedged exiftool process — the caller should ``close()`` it
        (force-kill path) and rotate to a fresh instance.
        """
        cmd = "\n".join(str(a) for a in args) + "\n-execute\n"
        self.proc.stdin.write(cmd)
        self.proc.stdin.flush()
        lines = []
        while True:
            try:
                line = self._stdout_queue.get(timeout=read_timeout)
            except queue.Empty:
                raise ExiftoolTimeout(
                    f"exiftool stdout idle for {read_timeout}s — process "
                    "appears wedged (corrupt input, dropped NAS, kernel "
                    "pipe stall, or stay_open deadlock). Caller should "
                    "close + rotate to a fresh ExiftoolProcess."
                )
            if line is None:
                # EOF / read error — process is dead. Break and return
                # whatever stdout we accumulated; stderr appended below.
                break
            stripped = line.rstrip("\n")
            if stripped == "{ready}":
                break
            lines.append(stripped)
        stdout_text = "\n".join(lines)
        with self._stderr_lock:
            err_text = "".join(self._stderr_buf)
            self._stderr_buf.clear()
        if err_text:
            # Append on a new line so JSON parsers slicing on ``[ ... ]``
            # remain unaffected, while text-grep callers still see
            # "error" / "warning" in the result.
            return stdout_text + "\n" + err_text.rstrip("\n")
        return stdout_text

    def close(self) -> None:
        try:
            self.proc.stdin.write("-stay_open\nFalse\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:  # pylint: disable=broad-exception-caught
            self.proc.kill()

    def kill(self) -> None:
        """Hard-kill the exiftool subprocess immediately, from any thread.

        Unlike :meth:`close` (graceful ``-stay_open False``), this terminates
        the process NOW. #561 — used by the scan cancel path: a consumer thread
        wedged inside a 500-file ``batch_read_extracts`` only checks the cancel
        flag between ``exif_queue.get`` calls, so it would otherwise hang the
        cancel ``join`` until the whole batch finished (and then orphan the
        process, since exiftool is un-jailed per #556). Killing the subprocess
        drops EOF onto the consumer's stdout queue, so its ``execute()`` returns
        promptly (the ``line is None`` break, not the 60s read-timeout) and the
        consumer exits on its next cancel check. ``subprocess.Popen.kill`` is
        safe to call from a thread other than the one using the process;
        best-effort / idempotent.
        """
        try:
            self.proc.kill()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def __enter__(self) -> "ExiftoolProcess":
        return self

    def __exit__(self, *_) -> None:
        self.close()


_EXIF_DATE_FMT = "%Y:%m:%d %H:%M:%S"
_VALID_SENTINEL = "-"
_ZERO_DATE = "0000:00:00 00:00:00"


def _parse_exiftool_json(output: str) -> list[dict]:
    """Extract the JSON array from a ``-j -G`` exiftool invocation.

    ``ExiftoolProcess.execute()`` appends any captured stderr after stdout,
    so the raw output may contain status messages (e.g. ``    3 image files
    read``) before or after the JSON blob. Slice by the outermost
    ``[ ... ]`` so leading/trailing non-JSON text doesn't break parsing,
    and fall back to an empty list on any malformation.

    Why JSON over the previous ``-s3 -f`` line-positional shape
    (photo-manager#145): each record carries its own ``SourceFile`` field,
    so records bind to paths by **identity** instead of position.
    Reordered records, missing records, extra records, inserted status
    messages — none can misalign the parser anymore. The drift bug class
    is structurally eliminated, not patched.
    """
    start = output.find("[")
    end = output.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(output[start:end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_exif_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw or raw == _VALID_SENTINEL or raw.startswith(_ZERO_DATE[:4] + ":"):
        return None
    # Strip timezone suffix if present ("2024:06:01 12:00:00+09:00" → drop "+09:00")
    raw = raw[:19]
    try:
        return datetime.strptime(raw, _EXIF_DATE_FMT)
    except ValueError:
        return None


_EXIF_CHUNK = 500  # files per exiftool call — avoids memory/command-line limits


def batch_read_dates(
    paths: list[Path],
    et: ExiftoolProcess,
    chunk_size: int = _EXIF_CHUNK,
) -> dict[Path, Optional[datetime]]:
    """Return {path: DateTimeOriginal} for all paths, chunked to avoid limits.

    Falls back to CreateDate / QuickTime:CreateDate when DateTimeOriginal is absent.
    Processes paths in chunks of chunk_size to keep each exiftool call manageable.
    """
    if not paths:
        return {}

    result: dict[Path, Optional[datetime]] = {}
    for offset in range(0, len(paths), chunk_size):
        chunk = paths[offset: offset + chunk_size]
        result.update(_read_chunk(chunk, et))
    return result


# JSON keys under ``-j -G`` (group-0 prefixed). Tried in order: prefer
# DateTimeOriginal (EXIF first, XMP fallback for PNG/GIF/WebP that carry
# date in XMP since some pipelines don't write EXIF for those formats),
# then CreateDate, then QuickTime:CreateDate (videos). Centralising the
# key strings here makes typos visible at module load.
_JSON_DATE_KEYS = (
    "EXIF:DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "EXIF:CreateDate",
    "XMP:CreateDate",
    "QuickTime:CreateDate",
)


# ── Extended batch for scoring system (#187) ───────────────────────────────
#
# The functions below extract a richer set of tags than ``batch_read_dates``:
# GPS, XMP provenance, user rating, and a census tag count needed by the
# scorer. ``-fast`` is *not* used because GPS and XMP segments live past the
# first IFD block that ``-fast`` would stop at. Latency cost on JPEG is ~3-5×
# higher per file; for a one-time scan this is acceptable for the precision
# gain.
#
# ``batch_read_dates`` is kept for backward compatibility with callers that
# only need dates. The scan pipeline (PR 4) switches to ``batch_read_extracts``.


# Census tags counted toward ``exif_tag_count``. Counting from the union
# of image + video censuses keeps the extraction file-type-agnostic; the
# scorer (PR 3) normalises the count against the appropriate baseline
# (image=16, video=9) when computing the EXIF-completeness sub-score.
_CENSUS_TAGS: frozenset = frozenset({
    # Image (EXIF/XMP namespace)
    "EXIF:DateTimeOriginal", "EXIF:Make", "EXIF:Model", "EXIF:ISO",
    "EXIF:FocalLength", "EXIF:ExposureTime", "EXIF:FNumber", "EXIF:Flash",
    "EXIF:Orientation", "EXIF:Software", "EXIF:LensModel",
    "EXIF:GPSLatitude", "EXIF:ColorSpace", "EXIF:WhiteBalance",
    "XMP:Rating", "XMP:Subject",
    # Video (QuickTime namespace)
    "QuickTime:CreateDate", "QuickTime:Duration", "QuickTime:VideoFrameRate",
    "QuickTime:ImageWidth", "QuickTime:ImageHeight",
    "QuickTime:AudioChannels", "QuickTime:AudioSampleRate",
    "QuickTime:CompressorName", "QuickTime:GPSCoordinates",
})

# Tags that, if present in the record, mark GPS as available.
_GPS_TAGS: tuple[str, ...] = (
    "EXIF:GPSLatitude",
    "EXIF:GPSLongitude",
    "QuickTime:GPSCoordinates",
)

# Tags that, if present, indicate the file is a derivative (Photoshop /
# Lightroom export, etc.). Under ``-G`` (group-0) the xmpMM:DerivedFrom
# tag surfaces under the ``XMP:`` group-0 prefix.
_XMP_DERIVED_TAGS: tuple[str, ...] = (
    "XMP:DerivedFrom",
    "XMP-xmpMM:DerivedFrom",   # in case caller uses -G1 / -G:1 in the future
)


def batch_read_extracts(
    paths: list[Path],
    et: "ExiftoolProcess",
    chunk_size: int = _EXIF_CHUNK,
) -> dict[Path, "MediaExtract"]:
    """Return {path: MediaExtract} populated with EXIF-side scoring signals.

    For each path the returned ``MediaExtract`` carries:
    * ``exif_date`` and ``exif_date_tag`` — parsed date + which exiftool
      tag produced it (e.g. ``"EXIF:DateTimeOriginal"``).
    * ``exif_tag_count`` — count of census tags present.
    * ``gps_present`` — True if any GPS tag is present, else False
      (*never None* after this function runs; that is the silent-dropout
      regression contract).
    * ``xmp_derived`` — True if ``xmpMM:DerivedFrom`` is present, else False.
    * ``xmp_rating`` — integer 0–5 if ``XMP:Rating`` is present, else None.
    * ``extracted_by = {"exiftool"}``.

    Paths that exiftool fails to return a record for still receive a
    ``MediaExtract`` with ``extracted_by={"exiftool"}`` and
    ``extraction_errors`` describing the missing record — they are *not*
    silently absent from the result dict. Downstream scoring treats the
    missing values as 'no signal'.
    """
    if not paths:
        return {}

    from scanner.media_extract import MediaExtract  # local import — avoid cycle

    result: dict[Path, MediaExtract] = {}
    for offset in range(0, len(paths), chunk_size):
        chunk = paths[offset: offset + chunk_size]
        result.update(_read_extract_chunk(chunk, et))
    return result


def _read_extract_chunk(
    paths: list[Path], et: "ExiftoolProcess"
) -> dict[Path, "MediaExtract"]:
    """Single ``-stay_open`` exiftool call for one chunk of paths.

    Tag selectors cover the scoring-system signals plus the existing date
    fallback chain. ``-fast`` is intentionally absent — GPS and XMP tags
    live in segments past the first IFD that ``-fast`` would skip
    (verified live against ``qa/sandbox/`` GPS-tagged JPEGs during #187
    research).
    """
    from scanner.media_extract import MediaExtract

    args = [
        "-j", "-G",
        # Date fallback chain (same as batch_read_dates).
        "-DateTimeOriginal", "-CreateDate", "-QuickTime:CreateDate",
        # GPS presence.
        "-GPSLatitude", "-GPSLongitude", "-QuickTime:GPSCoordinates",
        # XMP provenance and user metadata.
        "-XMP-xmpMM:DerivedFrom",
        "-XMP:Rating", "-XMP:Subject",
        # Image census (the rest of the 16-tag image baseline).
        "-EXIF:Make", "-EXIF:Model", "-EXIF:ISO", "-EXIF:FocalLength",
        "-EXIF:ExposureTime", "-EXIF:FNumber", "-EXIF:Flash",
        "-EXIF:Orientation", "-EXIF:Software", "-EXIF:LensModel",
        "-EXIF:ColorSpace", "-EXIF:WhiteBalance",
        # Video census (QuickTime:CreateDate already in date chain above).
        "-QuickTime:Duration", "-QuickTime:VideoFrameRate",
        "-QuickTime:ImageWidth", "-QuickTime:ImageHeight",
        "-QuickTime:AudioChannels", "-QuickTime:AudioSampleRate",
        "-QuickTime:CompressorName",
        # NB: NO -fast here.
    ]
    args += [str(p) for p in paths]
    output = et.execute(args)
    records = _parse_exiftool_json(output)

    by_path: dict[Path, dict] = {}
    for rec in records:
        src = rec.get("SourceFile")
        if isinstance(src, str):
            by_path[Path(src)] = rec

    result: dict[Path, MediaExtract] = {}
    for path in paths:
        rec = by_path.get(Path(str(path)))
        if rec is None:
            # File missing from output — emit a partial that documents the
            # exiftool pass ran (so the consumer knows we didn't forget)
            # but no signals were captured. extraction_errors records why.
            result[path] = MediaExtract(
                path=path,
                extracted_by={"exiftool"},
                extraction_errors=[
                    f"exiftool returned no record for SourceFile={path}"
                ],
            )
            continue
        result[path] = _record_to_extract(path, rec)

    return result


def _record_to_extract(path: Path, rec: dict) -> "MediaExtract":
    """Build a partial MediaExtract from one exiftool JSON record."""
    from scanner.media_extract import MediaExtract

    # Date with source-tag provenance — record which tag produced it so
    # the date_provenance scorer (PR 3) can weigh DateTimeOriginal vs.
    # CreateDate fallback differently if it wants.
    exif_date: Optional[datetime] = None
    exif_date_tag: Optional[str] = None
    for key in _JSON_DATE_KEYS:
        v = rec.get(key)
        if not isinstance(v, str) or not v:
            continue
        parsed = parse_exif_date(v)
        if parsed is not None:
            exif_date = parsed
            exif_date_tag = key
            break

    # GPS — explicit True/False (never None) so downstream callers can
    # tell "exiftool checked and there's no GPS" from "exiftool didn't run."
    gps_present: bool = any(rec.get(t) is not None for t in _GPS_TAGS)

    # XMP DerivedFrom — same explicit-True/False contract.
    xmp_derived: bool = any(rec.get(t) is not None for t in _XMP_DERIVED_TAGS)

    # XMP Rating — integer or None. Exiftool may emit it as int or string
    # depending on the file; coerce defensively.
    raw_rating = rec.get("XMP:Rating")
    xmp_rating: Optional[int] = None
    if raw_rating is not None:
        try:
            xmp_rating = int(raw_rating)
        except (ValueError, TypeError):
            xmp_rating = None

    # Census tag count — count any present tag in the union of image +
    # video censuses. The scorer normalises against the appropriate
    # baseline per file_type.
    exif_tag_count = sum(1 for tag in _CENSUS_TAGS if rec.get(tag) is not None)

    return MediaExtract(
        path=path,
        exif_date=exif_date,
        exif_date_tag=exif_date_tag,
        exif_tag_count=exif_tag_count,
        gps_present=gps_present,
        xmp_derived=xmp_derived,
        xmp_rating=xmp_rating,
        extracted_by={"exiftool"},
    )


def _read_chunk(paths: list[Path], et: ExiftoolProcess) -> dict[Path, Optional[datetime]]:
    # ``-j -G``: JSON output with group-0-prefixed keys. Each record carries
    # its own ``SourceFile`` field, so records bind to paths by identity
    # (Path equality) rather than position. This structurally eliminates the
    # drift bug class that the previous ``-s3 -f`` line-positional parser
    # had — see photo-manager#145 and google-album-metadata#5.
    #
    # ``-fast``: stop scanning after the first EXIF/metadata block — date
    # tags are always there for camera files, so this is safe and avoids
    # reading whole files over NAS. (MOV/MP4 with moov atom at file end
    # may lose their ``QuickTime:CreateDate`` under ``-fast``; they fall
    # back to CreateDate which is usually present anyway.)
    #
    # No ``-f``: under ``-j``, missing tags are absent from the record
    # (no ``-`` placeholder), which is cleaner than the sentinel.
    args = ["-j", "-G",
            "-DateTimeOriginal", "-CreateDate", "-QuickTime:CreateDate",
            "-fast"]
    args += [str(p) for p in paths]
    output = et.execute(args)
    records = _parse_exiftool_json(output)

    # Bind records to input paths by ``SourceFile``. ``pathlib`` normalises
    # forward/back slashes on Windows so dict equality holds regardless of
    # which separator exiftool emitted in its JSON output.
    by_path: dict[Path, dict] = {}
    for rec in records:
        src = rec.get("SourceFile")
        if isinstance(src, str):
            by_path[Path(src)] = rec

    result: dict[Path, Optional[datetime]] = {}
    for path in paths:
        rec = by_path.get(Path(str(path)))
        if rec is None:
            # Record missing (file not scanned, ghost-binding, etc.) →
            # None. Position-independence means a missing file at index N
            # does NOT cause every later file to drift.
            result[path] = None
            continue
        chosen: Optional[datetime] = None
        for key in _JSON_DATE_KEYS:
            v = rec.get(key)
            if not isinstance(v, str) or not v:
                continue
            parsed = parse_exif_date(v)
            if parsed is not None:
                chosen = parsed
                break
        result[path] = chosen

    return result
