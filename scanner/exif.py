"""Batch EXIF date extraction via exiftool's -stay_open mode."""

from __future__ import annotations

import json
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


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
        )
        self._stderr_buf: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

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

    def execute(self, args: list) -> str:
        """Send args to exiftool, return stdout (with any stderr appended)
        up to the ``{ready}`` sentinel.
        """
        cmd = "\n".join(str(a) for a in args) + "\n-execute\n"
        self.proc.stdin.write(cmd)
        self.proc.stdin.flush()
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
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
