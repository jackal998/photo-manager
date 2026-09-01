"""Candidate generation + stratified sampling for the visual ground-truth study.

Reads the app's ``migration_manifest`` table READ-ONLY (SQLite URI with
``mode=ro&immutable=1`` — never any other way), derives candidate "moment"
groups from two sources, enriches only those candidates with a single
``exiftool -stay_open`` pass, and writes a deterministic stratified sample
to a JSON sidecar for :mod:`scripts.visual_gt.server` to serve.

Two candidate sources, both tagged in the output as ``source``:

``dup``
    Rows sharing a ``group_id``, 2..12 members after video passengers are
    dropped. These are the scanner's own near-duplicate components.

``burst``
    Rows with a non-null ``shot_date``, bucketed by (parent directory,
    file extension) — a camera-identity proxy, because ``Make``/``Model``
    are not persisted in the manifest — then split into runs wherever the
    gap to the previous row exceeds :data:`BURST_GAP_SECONDS`. Runs of
    2..12 members are kept.

Gaps are computed on **integer epoch seconds**. A float ``julianday``
difference carries ~5e-5 s of absolute error near 2.46e6, which silently
drops gaps of exactly 3 s (757 rows on the user's real manifest).

A burst run whose member set is identical to a dup group's is emitted
once, as ``dup``.
"""

from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from dataclasses import asdict, dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
from typing import Iterable, Optional, Sequence

# --- Candidate-generation constants -------------------------------------

BURST_GAP_SECONDS = 3
MIN_MEMBERS = 2
MAX_MEMBERS = 12
DEFAULT_SEED = 20260902
DEFAULT_SAMPLE_SIZE = 150

# Live Photo ``.mov`` passengers and plain videos are excluded — the study
# is about picking the best STILL of a moment.
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".avi"})

# --- Stratification constants -------------------------------------------

LONG_EXPOSURE_SECONDS = 0.25
BRACKET_RATIO = 4.0        # 2 stops = a 4x exposure-time ratio
BRACKET_WINDOW_SECONDS = 1  # "same shot second +/- 1 s"

STRATA: tuple[str, ...] = (
    "long_exposure",
    "bracket",
    "burst_id",
    "burst_len_2",
    "burst_len_3_5",
    "burst_len_6_12",
    "dup",
)

# --- exiftool enrichment ------------------------------------------------

# Group-qualified selectors, but no ``-G``: exiftool then keys the JSON by
# the bare tag name, which is what :func:`_enrichment_from_record` reads.
ENRICH_TAGS: tuple[str, ...] = (
    "-EXIF:ExposureTime",
    "-EXIF:ISO",
    "-EXIF:Make",
    "-EXIF:Model",
    "-EXIF:SubSecTimeOriginal",
    "-MakerNotes:BurstUUID",
    "-MakerNotes:ContentIdentifier",
    "-XMP-GCamera:BurstID",
)
ENRICH_CHUNK = 200


@dataclass(frozen=True)
class ManifestPhoto:
    """One still from the manifest, with only the columns this tool needs."""

    path: str
    group_id: Optional[str]
    shot_date: Optional[str]
    epoch: Optional[int]
    file_size_bytes: Optional[int]
    pixel_width: Optional[int]
    pixel_height: Optional[int]


@dataclass(frozen=True)
class Enrichment:
    """Per-file exiftool signals, all optional (older files carry none)."""

    exposure_time: Optional[float] = None
    iso: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    subsec: Optional[str] = None
    burst_uuid: Optional[str] = None
    content_identifier: Optional[str] = None
    burst_id: Optional[str] = None


@dataclass(frozen=True)
class CandidateGroup:
    """A candidate moment: an ordered, deduplicated tuple of member paths."""

    key: str
    source: str          # "dup" | "burst"
    paths: tuple[str, ...]
    epochs: tuple[Optional[int], ...]


# --- Manifest reading ---------------------------------------------------


def manifest_uri(manifest_path: str | os.PathLike[str]) -> str:
    """Read-only, immutable SQLite URI for ``manifest_path``.

    ``immutable=1`` is safe here only because the app is not running; it
    also stops SQLite from creating a ``-wal``/``-shm`` sidecar next to
    the user's database, which would be a write.
    """
    return f"file:{Path(manifest_path).as_posix()}?mode=ro&immutable=1"


def parse_epoch(shot_date: Optional[str]) -> Optional[int]:
    """``'YYYY-MM-DDTHH:MM:SS'`` -> integer epoch seconds, or None.

    The manifest stores whole seconds with no timezone, so the absolute
    offset is arbitrary; only differences are used. ``calendar.timegm``
    keeps it exact and integral.
    """
    if not shot_date:
        return None
    try:
        parsed = dt.datetime.strptime(shot_date[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return calendar.timegm(parsed.timetuple())


def is_still(path: str) -> bool:
    """True for anything that is not a walked video format."""
    return Path(path).suffix.lower() not in VIDEO_EXTENSIONS


def read_manifest(manifest_path: str | os.PathLike[str]) -> list[ManifestPhoto]:
    """Load every still row from ``migration_manifest``, read-only."""
    conn = sqlite3.connect(manifest_uri(manifest_path), uri=True)
    try:
        rows = conn.execute(
            "SELECT source_path, group_id, shot_date, file_size_bytes, "
            "       pixel_width, pixel_height "
            "FROM   migration_manifest"
        ).fetchall()
    finally:
        conn.close()
    photos = []
    for path, group_id, shot_date, size, width, height in rows:
        if not path or not is_still(path):
            continue
        photos.append(
            ManifestPhoto(
                path=path,
                group_id=group_id or None,
                shot_date=shot_date or None,
                epoch=parse_epoch(shot_date),
                file_size_bytes=size,
                pixel_width=width,
                pixel_height=height,
            )
        )
    return photos


def existing_paths(paths: Sequence[str], workers: int = 32) -> set[str]:
    """Subset of ``paths`` that still exists on disk.

    The manifest is a snapshot: on the user's real library 9 224 of 24 508
    candidate files had been deleted (a Google Takeout staging tree) by the
    time this ran. Offering those groups for labelling would show the user
    a screen of broken thumbnails, so they are dropped before sampling.
    Stat'ing is I/O-bound on SMB, hence the thread pool.
    """
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    with ThreadPoolExecutor(max_workers=workers) as pool:
        flags = list(pool.map(lambda p: Path(p).exists(), paths))
    return {path for path, ok in zip(paths, flags) if ok}


# --- Candidate generation -----------------------------------------------


def _group_key(source: str, paths: Sequence[str]) -> str:
    """Stable key: source tag + a digest of the member set (order-free)."""
    digest = hashlib.sha1("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
    return f"{source}:{digest[:12]}"


def _make_group(source: str, members: Sequence[ManifestPhoto]) -> CandidateGroup:
    ordered = sorted(members, key=lambda m: (m.epoch if m.epoch is not None else 0, m.path))
    paths = tuple(m.path for m in ordered)
    return CandidateGroup(
        key=_group_key(source, paths),
        source=source,
        paths=paths,
        epochs=tuple(m.epoch for m in ordered),
    )


def dup_groups(photos: Iterable[ManifestPhoto]) -> list[CandidateGroup]:
    """Duplicate-group candidates: rows sharing a ``group_id``.

    The 2..12 size window is applied AFTER video passengers are dropped
    (they never reach this function), so a 3-member group that is
    JPG+HEIC+MOV arrives here as a 2-member still group.
    """
    by_gid: dict[str, list[ManifestPhoto]] = defaultdict(list)
    for photo in photos:
        if photo.group_id:
            by_gid[photo.group_id].append(photo)
    groups = []
    for gid in sorted(by_gid):
        members = by_gid[gid]
        if MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
            groups.append(_make_group("dup", members))
    return groups


def burst_runs(
    photos: Iterable[ManifestPhoto], gap_seconds: int = BURST_GAP_SECONDS
) -> list[CandidateGroup]:
    """Burst candidates: time-adjacent runs within one directory + extension.

    A run breaks when the gap to the previous row **exceeds**
    ``gap_seconds`` — a gap of exactly ``gap_seconds`` keeps the run
    going. Comparison is on integer seconds, so that boundary is exact.
    """
    # Buckets carry the epoch alongside the row so the gap arithmetic below
    # operates on a plain ``int``. Reading ``photo.epoch`` again after the
    # None guard would leave it ``int | None`` — the guard narrows the loop
    # variable, not the field, and a None reaching the subtraction raises.
    buckets: dict[tuple[str, str], list[tuple[int, ManifestPhoto]]] = defaultdict(list)
    for photo in photos:
        epoch = photo.epoch
        if epoch is None:
            continue
        parent = Path(photo.path).parent.as_posix().lower()
        buckets[(parent, Path(photo.path).suffix.lower())].append((epoch, photo))

    groups: list[CandidateGroup] = []
    for bucket_key in sorted(buckets):
        rows = sorted(buckets[bucket_key], key=lambda r: (r[0], r[1].path))
        run: list[ManifestPhoto] = [rows[0][1]]
        for (previous_epoch, _), (current_epoch, current) in zip(rows, rows[1:]):
            if current_epoch - previous_epoch > gap_seconds:
                _append_run(groups, run)
                run = [current]
            else:
                run.append(current)
        _append_run(groups, run)
    return groups


def _append_run(groups: list[CandidateGroup], run: Sequence[ManifestPhoto]) -> None:
    if MIN_MEMBERS <= len(run) <= MAX_MEMBERS:
        groups.append(_make_group("burst", run))


def build_candidate_pool(photos: Sequence[ManifestPhoto]) -> list[CandidateGroup]:
    """dup groups + burst runs, with identical member sets emitted once as ``dup``."""
    dups = dup_groups(photos)
    seen = {frozenset(group.paths) for group in dups}
    pool = list(dups)
    for run in burst_runs(photos):
        members = frozenset(run.paths)
        if members in seen:
            continue
        seen.add(members)
        pool.append(run)
    return pool


# --- exiftool enrichment ------------------------------------------------


def _to_float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> Optional[int]:
    number = _to_float(value)
    return None if number is None else int(number)


def _to_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _enrichment_from_record(record: dict) -> Enrichment:
    return Enrichment(
        exposure_time=_to_float(record.get("ExposureTime")),
        iso=_to_int(record.get("ISO")),
        make=_to_str(record.get("Make")),
        model=_to_str(record.get("Model")),
        subsec=_to_str(record.get("SubSecTimeOriginal")),
        burst_uuid=_to_str(record.get("BurstUUID")),
        content_identifier=_to_str(record.get("ContentIdentifier")),
        burst_id=_to_str(record.get("BurstID")),
    )


def normalise_path(path: str) -> str:
    """Key form for enrichment lookups: forward slashes, case as given.

    ``ExiftoolProcess.execute`` returns ``SourceFile`` with forward
    slashes even on Windows (``H:/Photos/a.DNG``), while the manifest
    stores backslashes (``H:\\Photos\\a.DNG``). Keying either dict by the
    raw string makes every lookup miss silently — the enrichment is
    collected, the stratifier sees nothing, and the sample comes back
    with three empty strata and no error anywhere.
    """
    return path.replace("\\", "/")


def _parse_exiftool_json(text: str) -> list[dict]:
    """Decode the JSON array at the head of exiftool's output.

    ``ExiftoolProcess.execute`` appends captured stderr after the JSON, so
    slicing to the LAST ``]`` swallows the warning text and fails the whole
    batch. ``raw_decode`` stops at the end of the array and ignores the
    rest, which is what loses 200 records at a time otherwise.
    """
    start = text.find("[")
    if start < 0:
        return []
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def enrich_paths(
    paths: Sequence[str], chunk_size: int = ENRICH_CHUNK
) -> tuple[dict[str, Enrichment], Optional[str]]:
    """Run one ``exiftool -stay_open`` pass over ``paths``.

    Returns ``(by_path, warning)``. ``warning`` is non-None when exiftool
    could not be used at all — the caller keeps going with empty
    enrichment rather than failing, but must say so.
    """
    if not paths:
        return {}, None
    try:
        from scanner.exif import ExiftoolProcess  # noqa: PLC0415 — optional at import time
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {}, f"scanner.exif unavailable ({exc}); enrichment skipped"

    try:
        proc = ExiftoolProcess()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {}, f"exiftool could not be started ({exc}); enrichment skipped"

    by_path: dict[str, Enrichment] = {}
    try:
        for start in range(0, len(paths), chunk_size):
            batch = list(paths[start : start + chunk_size])
            args = ["-j", "-n", "-charset", "filename=utf8", *ENRICH_TAGS, *batch]
            for record in _parse_exiftool_json(proc.execute(args)):
                source = record.get("SourceFile")
                if source:
                    by_path[normalise_path(str(source))] = _enrichment_from_record(record)
            print(
                f"  enriched {min(start + chunk_size, len(paths))}/{len(paths)} files",
                file=sys.stderr,
            )
    finally:
        proc.close()
    return by_path, None


def coverage_counts(enrichment: dict[str, Enrichment]) -> dict[str, int]:
    """How many enriched files carry each signal at all."""
    values = list(enrichment.values())
    return {
        "files_enriched": len(values),
        "burst_uuid": sum(1 for e in values if e.burst_uuid),
        "burst_id": sum(1 for e in values if e.burst_id),
        "content_identifier": sum(1 for e in values if e.content_identifier),
        "subsec_time_original": sum(1 for e in values if e.subsec),
        "exposure_time": sum(1 for e in values if e.exposure_time is not None),
        "make_model": sum(1 for e in values if e.make or e.model),
    }


# --- Stratification -----------------------------------------------------


def enrichment_for(enrichment: dict[str, Enrichment], path: str) -> Enrichment:
    """The single lookup site — always through :func:`normalise_path`."""
    return enrichment.get(normalise_path(path), Enrichment())


def _exposures(group: CandidateGroup, enrichment: dict[str, Enrichment]) -> list[float]:
    out = []
    for path in group.paths:
        value = enrichment_for(enrichment, path).exposure_time
        if value is not None and value > 0:
            out.append(value)
    return out


def is_long_exposure(group: CandidateGroup, enrichment: dict[str, Enrichment]) -> bool:
    """Any member exposed for at least :data:`LONG_EXPOSURE_SECONDS`."""
    return any(value >= LONG_EXPOSURE_SECONDS for value in _exposures(group, enrichment))


def is_bracket(group: CandidateGroup, enrichment: dict[str, Enrichment]) -> bool:
    """Exposure spread of >= 2 stops inside one shot second (+/- 1 s)."""
    exposures = _exposures(group, enrichment)
    if len(exposures) < 2:
        return False
    epochs = [e for e in group.epochs if e is not None]
    if len(epochs) >= 2 and max(epochs) - min(epochs) > 2 * BRACKET_WINDOW_SECONDS:
        return False
    return max(exposures) / min(exposures) >= BRACKET_RATIO


def has_burst_id(group: CandidateGroup, enrichment: dict[str, Enrichment]) -> bool:
    """Any member carries a camera-assigned burst identifier."""
    return any(
        enrichment_for(enrichment, path).burst_uuid or enrichment_for(enrichment, path).burst_id
        for path in group.paths
    )


def stratum_of(group: CandidateGroup, enrichment: dict[str, Enrichment]) -> str:
    """Assign exactly one stratum, first match in :data:`STRATA` order."""
    if is_long_exposure(group, enrichment):
        return "long_exposure"
    if is_bracket(group, enrichment):
        return "bracket"
    if has_burst_id(group, enrichment):
        return "burst_id"
    if group.source == "dup":
        return "dup"
    size = len(group.paths)
    if size == 2:
        return "burst_len_2"
    if size <= 5:
        return "burst_len_3_5"
    return "burst_len_6_12"


def allocate_quotas(pool_sizes: dict[str, int], total: int) -> dict[str, int]:
    """Spread ``total`` across the strata, degrading when a stratum is short.

    Round-robin in :data:`STRATA` order: each pass hands every stratum an
    equal share of what is left, capped by what that stratum actually has.
    A stratum that runs dry drops out and its share goes to the rest.
    """
    quotas = {name: 0 for name in STRATA}
    active = [name for name in STRATA if pool_sizes.get(name, 0) > 0]
    remaining = total
    while remaining > 0 and active:
        share = max(1, remaining // len(active))
        progressed = False
        for name in list(active):
            if remaining <= 0:
                break
            take = min(share, pool_sizes[name] - quotas[name], remaining)
            if take <= 0:
                active.remove(name)
                continue
            quotas[name] += take
            remaining -= take
            progressed = True
        if not progressed:
            break
    return quotas


def stratified_sample(
    pool: Sequence[CandidateGroup],
    enrichment: dict[str, Enrichment],
    total: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
) -> tuple[list[CandidateGroup], dict[str, int], dict[str, int]]:
    """Deterministic stratified sample, interleaved so any prefix is balanced.

    Returns ``(sample, pool_counts, sample_counts)``.
    """
    by_stratum: dict[str, list[CandidateGroup]] = {name: [] for name in STRATA}
    for group in pool:
        by_stratum[stratum_of(group, enrichment)].append(group)
    for name in STRATA:
        by_stratum[name].sort(key=lambda g: g.key)

    pool_counts = {name: len(by_stratum[name]) for name in STRATA}
    quotas = allocate_quotas(pool_counts, total)

    rng = random.Random(seed)
    picked: dict[str, list[CandidateGroup]] = {}
    for name in STRATA:
        candidates = list(by_stratum[name])
        rng.shuffle(candidates)
        picked[name] = candidates[: quotas[name]]

    interleaved: list[CandidateGroup] = []
    position = 0
    while len(interleaved) < sum(quotas.values()):
        for name in STRATA:
            if position < len(picked[name]):
                interleaved.append(picked[name][position])
        position += 1
    sample_counts = {name: len(picked[name]) for name in STRATA}
    return interleaved, pool_counts, sample_counts


# --- CSV resume ---------------------------------------------------------


def read_last_rowset_sizes(csv_path: str | os.PathLike[str]) -> dict[str, int]:
    """Per group key, how many rows its LAST contiguous row-set holds.

    One submission writes one contiguous run of rows sharing a group key
    AND a ``labelled_at`` stamp, one row per member. A hard kill part-way
    through that append leaves a short (or truncated) final run whose
    ``group_key`` still parses — counting presence would then mark the
    group done and resume would hide it forever. The caller compares this
    size against the group's member count instead.

    The append-only log means the LAST COMPLETE row-set per key wins.
    Leading ``#`` lines carry that rule for human readers and are skipped
    here. Splitting on lines before the CSV parse is safe because Windows
    paths cannot contain a newline.
    """
    path = Path(csv_path)
    if not path.exists():
        return {}
    import csv as _csv  # noqa: PLC0415 — only needed on the resume path

    with path.open("r", newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]

    sizes: dict[str, int] = {}
    current: Optional[tuple[str, Optional[str]]] = None
    for row in _csv.DictReader(lines):
        key = row.get("group_key")
        if not key:
            continue
        marker = (key, row.get("labelled_at"))
        if marker == current:
            sizes[key] += 1
        else:
            sizes[key] = 1
            current = marker
    return sizes


def read_labelled_keys(csv_path: str | os.PathLike[str]) -> set[str]:
    """Group keys that appear in the label CSV at all (empty when absent).

    Presence only — see :func:`read_last_rowset_sizes` for the completeness
    check the server's resume actually uses.
    """
    return {key for key, count in read_last_rowset_sizes(csv_path).items() if count}


# --- Serialisation ------------------------------------------------------


def _member_payload(
    photo_by_path: dict[str, ManifestPhoto],
    enrichment: dict[str, Enrichment],
    path: str,
) -> dict:
    photo = photo_by_path.get(path)
    payload = {
        "path": path,
        "name": Path(path).name,
        "shot_date": photo.shot_date if photo else None,
        "epoch": photo.epoch if photo else None,
        "file_size_bytes": photo.file_size_bytes if photo else None,
        "pixel_width": photo.pixel_width if photo else None,
        "pixel_height": photo.pixel_height if photo else None,
    }
    payload.update(asdict(enrichment_for(enrichment, path)))
    return payload


def summarise(members: Sequence[dict]) -> str:
    """One-liner shown above the thumbnails: camera, exposure range, burst id."""
    cameras = sorted({f"{m.get('make') or ''} {m.get('model') or ''}".strip() for m in members})
    cameras = [c for c in cameras if c] or ["camera unknown"]
    exposures = [m["exposure_time"] for m in members if m.get("exposure_time")]
    if exposures:
        low, high = min(exposures), max(exposures)
        exposure = f"{low:g}s" if low == high else f"{low:g}-{high:g}s"
    else:
        exposure = "exposure unknown"
    burst = "burst id present" if any(m.get("burst_uuid") or m.get("burst_id") for m in members) else "no burst id"
    return f"{'/'.join(cameras)} | {exposure} | {burst}"


def build_document(
    sample: Sequence[CandidateGroup],
    photo_by_path: dict[str, ManifestPhoto],
    enrichment: dict[str, Enrichment],
    *,
    manifest_path: str,
    seed: int,
    pool_counts: dict[str, int],
    sample_counts: dict[str, int],
    coverage: dict[str, int],
    warning: Optional[str],
) -> dict:
    """The JSON sidecar Phase 2 recomputes against."""
    groups = []
    for group in sample:
        members = [_member_payload(photo_by_path, enrichment, path) for path in group.paths]
        groups.append(
            {
                "key": group.key,
                "source": group.source,
                "stratum": stratum_of(group, enrichment),
                "summary": summarise(members),
                "members": members,
            }
        )
    return {
        "schema": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "manifest": manifest_path,
        "seed": seed,
        "pool_counts": pool_counts,
        "sample_counts": sample_counts,
        "enrichment_coverage": coverage,
        "enrichment_warning": warning,
        "groups": groups,
    }


def write_document(document: dict, out_path: str | os.PathLike[str]) -> Path:
    """Write the sidecar atomically, as UTF-8 bytes (no line-ending rewrite)."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)
    return path


def _print_table(title: str, pool_counts: dict[str, int], sample_counts: dict[str, int]) -> None:
    print(f"\n{title}")
    print(f"  {'stratum':<16} {'pool':>8} {'sample':>8}")
    for name in STRATA:
        print(f"  {name:<16} {pool_counts.get(name, 0):>8} {sample_counts.get(name, 0):>8}")
    print(f"  {'TOTAL':<16} {sum(pool_counts.values()):>8} {sum(sample_counts.values()):>8}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.visual_gt.bootstrap",
        description="Derive candidate moment groups from the manifest and sample them.",
    )
    parser.add_argument("--manifest", required=True, help="path to migration_manifest.sqlite (opened read-only)")
    parser.add_argument("--out", required=True, help="JSON sidecar to write")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--n", type=int, default=DEFAULT_SAMPLE_SIZE, help="groups to sample")
    parser.add_argument("--no-exiftool", action="store_true", help="skip enrichment (faster, loses stratification)")
    parser.add_argument(
        "--keep-missing",
        action="store_true",
        help="keep rows whose file is no longer on disk (default: drop them)",
    )
    args = parser.parse_args(argv)

    photos = read_manifest(args.manifest)
    print(f"manifest: {args.manifest}")
    print(f"still rows: {len(photos)}")

    if not args.keep_missing:
        present = existing_paths([photo.path for photo in photos])
        dropped = len(photos) - len(present)
        photos = [photo for photo in photos if photo.path in present]
        print(f"dropped {dropped} rows whose file is no longer on disk; {len(photos)} remain")

    pool = build_candidate_pool(photos)
    by_source: dict[str, int] = defaultdict(int)
    for group in pool:
        by_source[group.source] += 1
    print(f"candidate groups: {len(pool)} (dup {by_source['dup']}, burst {by_source['burst']})")

    candidate_paths = sorted({path for group in pool for path in group.paths})
    print(f"candidate files: {len(candidate_paths)}")

    if args.no_exiftool:
        enrichment, warning = {}, "enrichment skipped (--no-exiftool)"
    else:
        print("running exiftool over candidates ...", file=sys.stderr)
        enrichment, warning = enrich_paths(candidate_paths)
    if warning:
        print(f"WARNING: {warning}")

    sample, pool_counts, sample_counts = stratified_sample(pool, enrichment, args.n, args.seed)
    _print_table(f"stratum counts (seed {args.seed}, n={args.n})", pool_counts, sample_counts)

    coverage = coverage_counts(enrichment)
    print("\nenrichment coverage (candidate files only)")
    for name, count in coverage.items():
        print(f"  {name:<22} {count:>8}")

    photo_by_path = {photo.path: photo for photo in photos}
    document = build_document(
        sample,
        photo_by_path,
        enrichment,
        manifest_path=str(args.manifest),
        seed=args.seed,
        pool_counts=pool_counts,
        sample_counts=sample_counts,
        coverage=coverage,
        warning=warning,
    )
    written = write_document(document, args.out)
    print(f"\nwrote {len(document['groups'])} groups -> {written}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
