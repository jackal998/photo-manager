"""SHA-256 and perceptual hash computation for all media formats.

Memory-footprint audit (#453)
-----------------------------

The hashing path holds the entire file in Python heap for image
formats (JPEG / PNG / HEIC / WebP / RAW) because all three downstream
operations — ``hashlib.sha256``, ``PIL.Image.open`` and
``rawpy.imread(io.BytesIO(...))`` — want a ``bytes`` buffer. This is
intentional: the single ``path.read_bytes()`` in ``compute_hashes`` was
the #446 fix that eliminated a double-read of every image and roughly
halved NAS scan time. Backing it out for RAW (e.g. mmap the SHA pass,
re-read for decode) would regress #446's gain — verified during the
#453 audit: ``rawpy`` has no zero-copy decode path and a fresh
``bytes(mm)`` materialisation costs the same RAM as ``read_bytes()``.

#591: the in-memory RAW path decodes those bytes via
``rawpy.imread(io.BytesIO(data))`` — rawpy 0.26.1 dropped the
module-level ``rawpy.open_buffer`` the older code called, so that call
dead-ended on ``AttributeError`` and silently re-read the file from disk
(3 touches per RAW). The single-read guarantee therefore holds for valid
camera RAW; a non-camera TIFF routed to the ``raw`` branch (#75) still
falls back to a path re-read (``_raw_preview_and_sizes(str(path))``) so it
skips cleanly rather than crashing.

#826: that in-memory decode now also yields ``raw.sizes`` off the SAME
LibRaw handle. The path used to open LibRaw twice per RAW file — once for
the embedded preview, once more purely for the sensor dimensions — which
cost 0.34-0.38 s per file on 124-129 MB ProRAW DNGs with the bytes already
in RAM, on the pipeline's most expensive pixel path.

The video path (``mp4`` / ``mov``) already streams via 64 KB chunks
in ``compute_sha256`` — peak heap is ~64 KB regardless of file size.
No code change needed for videos; mmap would not reduce the working
set further (both rely on the kernel page cache for the actual I/O).

Net memory ceiling per hash worker:
  - video:   ~64 KB (chunked)
  - image:   one full read (the file, plus a PIL decode buffer)

If the scan.workers spinner (#449) is pushed past 8 on a low-RAM box
with a RAW-heavy library, peak RAM during hashing scales linearly.
That headroom concern lives with #449's spinner cap (1-32), not with
the hasher implementation. See #453 closure note.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from scanner.dedup import HashResult
from scanner.exif import extract_pil_scoring_signals, parse_exif_date

if TYPE_CHECKING:
    from scanner.walker import FileRecord


@dataclass
class ReadFailure:
    """Carries the exception that prevented a file read in :func:`read_for_record`.

    Kept separate from :class:`HashFailure` so callers can distinguish "we
    never even opened the file" (I/O error on read) from "we read the bytes
    but couldn't hash them" (decode failure).  The dispatcher in the
    read→compute pipeline passes ``ReadFailure`` through as the ``data``
    argument to :func:`compute_from_bytes`, which maps it to a
    :class:`HashFailure` for the standard skip path.
    """

    exc_type: str
    exc_msg: str

try:
    from PIL import Image, ImageOps
    import imagehash
    _HASH_AVAILABLE = True
except ImportError:
    _HASH_AVAILABLE = False

try:
    import rawpy
    _RAWPY_AVAILABLE = True
except ImportError:
    _RAWPY_AVAILABLE = False

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    _HEIF_AVAILABLE = False


# #526 — version token for the per-file hash *recipe* (what
# ``compute_hashes`` / ``run_hash_for_record`` produce and how long it takes).
# Folded into ``scan_worker.hash_pool_fingerprint`` so the #486 auto-pool
# calibration cache invalidates when the recipe changes — otherwise a cached
# thread/process pick (and the grouping micro-rates) measured under an old
# recipe would silently mis-project. BUMP this whenever the per-file hash work
# changes in a way that shifts its cost: e.g. adding/removing a hash from the
# 7-tuple (sha256, phash, dhash, mean_color, raw_date, px_w, px_h — dHash was
# #517), changing decode/resize, or swapping the imagehash size. Purely a
# cache-keying token; the value is opaque, only equality matters.
# "2" (#569) — added Image.draft JPEG shrink-on-load before convert(); the
# decode resolution (and so the phash bits, marginally) changed.
# "3" (#786) — JPEG hash-stage now also extracts scoring signals
# (exif_date/exif_tag_count/gps_present/xmp_derived) from the already-open
# PIL image, added as an 8th return element; per-file JPEG cost shifts
# (adds EXIF/XMP parsing, removes the exiftool round-trip downstream).
# "4" (#822 + #826) — one bump for two recipe moves, so stored hashes are
# invalidated once rather than twice: EXIF Orientation is now applied before
# phash/dhash on every path that carries it (#822), and the RAW path opens
# LibRaw once and reduces the embedded preview to the JPEG path's working
# resolution before hashing (#826). Both shift hash values for the affected
# files; an Orientation-1 non-RAW file is bit-identical to recipe "3"
# (pinned by tests/test_hasher.py::TestHashRecipeParity).
HASH_RECIPE_VERSION = "4"

# Shrink-on-load target shared by every pixel path, so RAW and JPEG hash at the
# same working resolution (#569 chose it; #826 pointed the RAW path at it).
# For a 4032×3024 JPEG libjpeg's DCT scaler picks 1/8 → ~504 px on the long
# side; for a 8064×6048 ProRAW embedded preview, ~1008 px.
_DRAFT_TARGET = (256, 256)


def compute_sha256(path: Path) -> str:
    """Stream-compute SHA-256 of a file in 64 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hashes_from_data(
    path: Path, file_type: str, data: bytes
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[int], Optional[int], Optional[dict]]:
    """Derive ``(sha256, phash, dhash, mean_color, raw_date, px_w, px_h, inmemory_signals)``
    from bytes that have already been read off disk.

    This is the single implementation used by both :func:`compute_hashes`
    (which reads the bytes) and :func:`compute_from_bytes` (which receives
    them from the bounded read→compute queue).  Only call for non-video/gif/skip
    types; callers must stream video SHA separately via :func:`compute_sha256`.

    ``inmemory_signals`` (#786) is the scoring-signal dict from
    :func:`scanner.exif.extract_pil_scoring_signals` for formats in
    :data:`_INMEMORY_EXIF_TYPES`, else ``None`` — those formats route
    entirely around the exiftool queue in ``scan_runner._route_outcome``.
    """
    sha = hashlib.sha256(data).hexdigest()

    if not _HASH_AVAILABLE:
        return sha, None, None, None, None, None, None, None

    img: Optional[Image.Image] = None
    raw_date: Optional[str] = None
    px_w: Optional[int] = None
    px_h: Optional[int] = None
    inmemory_signals: Optional[dict] = None

    if file_type == "raw":
        # #826 — ONE LibRaw open yields both the embedded preview and the true
        # sensor dimensions. The old shape opened LibRaw twice per file (once
        # in _load_raw_preview_from_bytes, once more just for raw.sizes); on
        # this library's iPhone ProRAW DNGs (124–129 MB, 8064×6048) the two
        # opens alone cost 0.34–0.38 s per file with the bytes already in RAM.
        # #591 — the in-memory source is a fresh io.BytesIO over the bytes we
        # already read (rawpy 0.26.1 has no module-level open_buffer); no disk
        # re-read.
        img, px_w, px_h = _raw_preview_and_sizes(io.BytesIO(data))
        if img is None:
            # In-memory decode failed (genuine LibRaw error / non-camera TIFF
            # routed to 'raw', #75) — retry from the path so the file still
            # skips cleanly. Same recipe on both routes.
            img, fallback_w, fallback_h = _raw_preview_and_sizes(str(path))
            if px_w is None:
                px_w, px_h = fallback_w, fallback_h
        # True sensor dimensions come from rawpy metadata, never from the
        # embedded preview — on ProRAW the preview is the full 8064×6048 frame,
        # on other bodies it can be a small thumbnail; neither is authoritative.
        # RAW EXIF dates are not reliably readable via PIL — caller uses exiftool.
    else:
        try:
            with Image.open(io.BytesIO(data)) as pil_img:
                # Extract date BEFORE convert() — that creates a new image without EXIF.
                raw_date = _raw_exif_date(pil_img)
                # #786 — same constraint: the fuller scoring-signal pass
                # must also run before convert() destroys EXIF/XMP.
                if file_type in _INMEMORY_EXIF_TYPES:
                    inmemory_signals = extract_pil_scoring_signals(pil_img)
                # True dimensions — read BEFORE draft() mutates the reported size.
                px_w, px_h = pil_img.size
                # #569 — libjpeg DCT shrink-on-load: decode JPEG/MPO directly at
                # ~1/4 resolution (a no-op on PNG/WebP/HEIC/RAW). phash (32×32),
                # dhash (9×8) and the 1×1 mean-color all downsample far below
                # 256px, so this is pHash-safe — A/B on 597 real JPEGs: 0 over the
                # grouping threshold, 0 group-membership flips — while cutting JPEG
                # decode ~4×. The win lands on warm re-scans (~5–6×) and
                # compute-bound hardware; a read-bound first scan is unchanged.
                pil_img.draft("RGB", _DRAFT_TARGET)
                # #822 — apply EXIF Orientation BEFORE hashing. Without it a
                # copy whose rotation was baked into its pixels (an export, an
                # edit, a messenger re-save) hashes differently from its
                # EXIF-rotated original, so the pair misses the near-duplicate
                # tier entirely — a false negative that reports nothing. 19.4 %
                # of this library carries a non-identity Orientation tag.
                # Runs AFTER the EXIF reads above, which need the original tags.
                # ``in_place=True`` transposes the already-decoded buffer instead
                # of returning a copy, so an Orientation-1 file (the common case)
                # costs nothing extra against the #453 per-worker RAM ceiling.
                ImageOps.exif_transpose(pil_img, in_place=True)
                img = pil_img.convert("RGB")
                img.load()
        except (OSError, ValueError):
            img = None

    if img is None:
        return sha, None, None, None, raw_date, None, None, inmemory_signals
    try:
        tiny = img.resize((1, 1), Image.LANCZOS)
        mc = tiny.getpixel((0, 0))[:3]
        return (
            sha,
            str(imagehash.phash(img)),
            str(imagehash.dhash(img)),
            f"{mc[0]},{mc[1]},{mc[2]}",
            raw_date,
            px_w,
            px_h,
            inmemory_signals,
        )
    except (ValueError, TypeError):
        # #470 — preserve px_w / px_h measured before the phash compute attempt.
        # Wiping them to None would force scoring._score_resolution to 0.0 even
        # though the dimensions are known and valid; phash failure shouldn't
        # cascade into a fake resolution-zero penalty.
        return sha, None, None, None, raw_date, px_w, px_h, inmemory_signals


def compute_hashes(
    path: Path, file_type: str
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[int], Optional[int], Optional[dict]]:
    """Single file read: ``(sha256, phash, dhash, mean_color, raw_exif_date, width, height, inmemory_signals)``.

    All values are derived from one in-memory read — no extra file open.
    ``dhash`` is a second, independent perceptual hash (gradient/brightness
    based, complementary to pHash's DCT) used by the dedup confidence vote
    (#517); ``None`` whenever ``phash`` is ``None``.
    ``width``/``height`` are the pixel dimensions of the image (or ``None``
    for video/skip and on decode failure).  For RAW files the true sensor
    dimensions are read from ``raw.sizes`` via rawpy (not the embedded thumbnail).
    ``mean_color`` is the average RGB via a 1×1 LANCZOS downscale.
    ``raw_date_str`` is ``None`` for RAW/video; callers pass those to exiftool.
    ``inmemory_signals`` (#786) is non-``None`` only for formats in
    :data:`_INMEMORY_EXIF_TYPES` (JPEG) — the scoring-signal dict that lets
    the scan pipeline skip the exiftool queue for those files entirely.
    For videos SHA-256 is streamed in 64 KB chunks so large files never load into RAM.
    """
    if file_type in ("mp4", "mov", "gif", "skip"):
        return compute_sha256(path), None, None, None, None, None, None, None

    # Single read: delegate all hash computation to _hashes_from_data.
    return _hashes_from_data(path, file_type, path.read_bytes())


# Formats where PIL is the primary decoder and a missing pHash unambiguously
# means decode-failure:
#   - GIF excluded: compute_hashes always returns phash=None for GIF
#     (intentional early-return at scanner/hasher.py:53), so flagging
#     phash=None as corruption false-positives 100% of the time (#75).
#   - RAW excluded: rawpy is the decoder, and rawpy fails on legitimate
#     non-camera-RAW TIFFs (Photoshop / scanner output) — flagging
#     those as corrupt drops real user files from the manifest (#75).
_IMAGE_TYPES = frozenset(("jpeg", "heic", "png", "webp"))

# #786 — formats whose scoring signals (exif_date/exif_tag_count/gps_present/
# xmp_derived) come from this in-memory PIL pass instead of exiftool.
# scan_runner._route_outcome checks HashResult.inmemory_signals (populated
# only for these types) to skip the exif_queue entirely for them. HEIC stays
# on exiftool: pillow-heif exposes no "xmp" info key for any real fixture in
# this repo's corpus (unlike the JPEG plugin), so xmp_derived parity can't be
# verified against real data — see the #786 PR discussion.
_INMEMORY_EXIF_TYPES = frozenset(("jpeg",))


@dataclass
class HashFailure:
    """Marker returned by :func:`run_hash_for_record` when a file cannot
    be hashed. Carries the exception type name + message so the caller
    can append to its ``skipped`` log without re-deriving the failure
    reason.

    Used for both raised exceptions (``compute_hashes`` failed) and
    silent decode failures (``compute_hashes`` returned with
    ``phash=None`` for an image-typed file). The latter is surfaced as
    ``exc_type="ImageDecodeError"`` so the user-visible log line stays
    distinguishable from a real Python exception trace.
    """

    exc_type: str
    exc_msg: str


def run_hash_for_record(
    idx: int, record: "FileRecord"
) -> tuple[int, Union[HashResult, HashFailure, None]]:
    """Pure compute path for one ``FileRecord``.

    Returns ``(idx, outcome)`` where ``outcome`` is one of:

    * :class:`HashResult` — happy path, record was hashed successfully
    * :class:`HashFailure` — ``compute_hashes`` raised OR returned
      ``phash=None`` for a format where that means decode-failure
      (see :data:`_IMAGE_TYPES`)
    * ``None`` — currently unused; reserved for caller-driven skip
      signals (e.g. cancel-flag short-circuit, which lives in the
      dispatch closure, not here)

    The ``idx`` is passed through unchanged so the caller can map
    out-of-order completions back to the original input ordering.
    This function is intentionally side-effect-free and picklable so
    it can be submitted to either a ``ThreadPoolExecutor`` (current
    use) or a ``ProcessPoolExecutor`` (planned, see follow-up to #486).
    """
    try:
        sha256, phash, dhash, mean_color, raw_date, px_w, px_h, inmemory_signals = (
            compute_hashes(record.path, record.file_type)
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # One bad file must never abort the whole scan — caller logs + skips.
        return idx, HashFailure(type(exc).__name__, str(exc))
    if record.file_type in _IMAGE_TYPES and phash is None:
        # Silent decode failure: PIL couldn't produce a pHash for an
        # image-typed file → truncated / corrupt. Caller routes this
        # to its skipped[] log alongside real exceptions.
        return idx, HashFailure(
            "ImageDecodeError",
            "image file could not be decoded (truncated or corrupt)",
        )
    pil_date = parse_exif_date(raw_date) if raw_date else None
    return idx, HashResult(
        record=record,
        sha256=sha256,
        phash=phash,
        dhash=dhash,
        mean_color=mean_color,
        exif_date=pil_date,
        pixel_width=px_w,
        pixel_height=px_h,
        inmemory_signals=inmemory_signals,
    )


def read_for_record(
    idx: int, record: "FileRecord"
) -> "tuple[int, FileRecord, bytes | None | ReadFailure]":
    """READ stage: load raw bytes for ``record`` without any decoding.

    Returns ``(idx, record, data)`` where ``data`` is:

    * ``None``              — video/gif/skip; no bytes to read (SHA will be
                             streamed from disk in :func:`compute_from_bytes`).
    * ``bytes``             — image data ready for :func:`_hashes_from_data`.
    * :class:`ReadFailure`  — I/O error; :func:`compute_from_bytes` converts
                             this to a :class:`HashFailure` for the skip path.

    The ``idx`` is passed through unchanged so the compute stage can scatter
    results back to ``hash_results[idx]`` — preserving the original walk order
    that :func:`~scanner.dedup.classify` relies on for deterministic group IDs.
    """
    if record.file_type in ("mp4", "mov", "gif", "skip"):
        return idx, record, None
    try:
        data = record.path.read_bytes()
        return idx, record, data
    except OSError as exc:
        return idx, record, ReadFailure(type(exc).__name__, str(exc))


def compute_from_bytes(
    idx: int, record: "FileRecord", data: "bytes | None | ReadFailure"
) -> "tuple[int, HashResult | HashFailure | None]":
    """COMPUTE stage: derive all hash fields from pre-read ``data``.

    Counterpart to :func:`read_for_record` in the bounded read→compute
    pipeline.  Mirrors the outcome contract of :func:`run_hash_for_record`:

    * :class:`HashResult`  — happy path.
    * :class:`HashFailure` — read failed, or decode failed on an image type.
    * ``None``             — (unused; reserved for caller-driven skip signals).

    Video/gif/skip files arrive with ``data=None`` and have their SHA
    streamed from disk here (same single-read guarantee as the fused path —
    the file is only opened once in the pipeline, just at a different stage).
    """
    if isinstance(data, ReadFailure):
        return idx, HashFailure(data.exc_type, data.exc_msg)

    try:
        if data is None:
            # video/gif/skip: stream SHA from path, no perceptual hashes.
            sha256 = compute_sha256(record.path)
            phash = dhash = mean_color = raw_date = px_w = px_h = inmemory_signals = None
        else:
            sha256, phash, dhash, mean_color, raw_date, px_w, px_h, inmemory_signals = (
                _hashes_from_data(record.path, record.file_type, data)
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return idx, HashFailure(type(exc).__name__, str(exc))

    if record.file_type in _IMAGE_TYPES and phash is None:
        return idx, HashFailure(
            "ImageDecodeError",
            "image file could not be decoded (truncated or corrupt)",
        )
    pil_date = parse_exif_date(raw_date) if raw_date else None
    return idx, HashResult(
        record=record,
        sha256=sha256,
        phash=phash,
        dhash=dhash,
        mean_color=mean_color,
        exif_date=pil_date,
        pixel_width=px_w,
        pixel_height=px_h,
        inmemory_signals=inmemory_signals,
    )


def _raw_exif_date(img: "Image.Image") -> Optional[str]:
    """Return the raw ``DateTimeOriginal`` string from a PIL image's EXIF, or None.

    Checks ExifIFD (sub-IFD 0x8769) first, then falls back to main IFD tag 306
    (DateTime).  No parsing — returns the raw "YYYY:MM:DD HH:MM:SS" string so
    that callers can use their own date-parsing logic.
    """
    try:
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)          # ExifIFD sub-IFD
        raw = exif_ifd.get(36867) or exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
        return str(raw) if raw else None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def compute_phash(path: Path, file_type: str) -> Optional[str]:
    """Compute a 64-bit perceptual hash for image files.

    Returns None for videos and on any loading failure.

    RAW files: try embedded JPEG preview first (fast), fall back to full decode.
    HEIC: requires pillow-heif registered (done at module import).

    Prefer ``compute_hashes()`` when SHA-256 is also needed — it reads the file
    only once instead of twice.
    """
    if not _HASH_AVAILABLE:
        return None
    if file_type in ("mp4", "mov", "gif", "skip"):
        return None

    img: Optional[Image.Image] = None
    if file_type == "raw":
        img = _load_raw_preview(path)
    else:
        try:
            with Image.open(path) as pil_img:
                img = pil_img.convert("RGB")
                img.load()
        except (OSError, ValueError):
            return None

    if img is None:
        return None
    try:
        return str(imagehash.phash(img))
    except (ValueError, TypeError):
        return None


def _load_raw_preview(path: Path) -> Optional[Image.Image]:
    """Load a PIL Image from a RAW file's embedded JPEG thumbnail (path-based).

    Falls back to full rawpy decode if no thumbnail is present.

    Returns the preview at FULL resolution and WITHOUT applying EXIF
    Orientation — that contract is relied on by ``compute_phash`` and by
    ``scripts/visual_gt/server.py`` (which sizes and orients the image itself).
    The hash path does not use this function: it goes through
    :func:`_raw_preview_and_sizes`, which opens LibRaw once (#826) and hashes
    an oriented, draft-reduced preview (#822).
    """
    if not _RAWPY_AVAILABLE:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = Image.open(io.BytesIO(thumb.data)).convert("RGB")
                    img.load()
                    return img
            except rawpy.LibRawNoThumbnailError:
                pass
            # Full decode fallback — slower but always works
            rgb = raw.postprocess(use_auto_wb=True, output_bps=8)
            return Image.fromarray(rgb).convert("RGB")
    except (OSError, ValueError, rawpy.LibRawError):
        return None


def _raw_preview_from_jpeg_bytes(thumb_data: bytes) -> Optional[Image.Image]:
    """Decode an embedded RAW preview JPEG at hash working resolution, oriented.

    #826 — the embedded preview is NOT the small thumbnail the old comment
    assumed: on this library's iPhone ProRAW DNGs it is the full sensor frame
    (8064×6048 = 48.8 MP, 21–22 MB), so hashing it as-is ran phash/dhash on
    ~260× the pixels of a drafted 12 MP JPEG for no gain (phash reduces to
    32×32 regardless). It now goes through the same :data:`_DRAFT_TARGET`
    shrink-on-load the JPEG path uses.

    #822 — the preview carries the camera's Orientation in its own EXIF while
    its pixels are stored unrotated (measured across the sampled DNGs:
    ``raw.sizes.flip`` equals the preview's EXIF Orientation on every file), so
    ``exif_transpose`` here is what applies ``sizes.flip``.
    """
    try:
        with Image.open(io.BytesIO(thumb_data)) as preview:
            preview.draft("RGB", _DRAFT_TARGET)
            ImageOps.exif_transpose(preview, in_place=True)
            img = preview.convert("RGB")
            img.load()
            return img
    except (OSError, ValueError):
        return None


def _raw_preview_and_sizes(
    source: "io.BytesIO | str",
) -> "tuple[Optional[Image.Image], Optional[int], Optional[int]]":
    """ONE LibRaw open → ``(hash-ready preview, sensor width, sensor height)``.

    #826 — replaces the old pair of opens (``_load_raw_preview_from_bytes`` for
    the preview, then a second ``rawpy.imread`` purely for ``raw.sizes``). The
    two opens alone cost 0.34–0.38 s per file on 124–129 MB ProRAW DNGs with
    the bytes already in RAM.

    ``source`` is either an ``io.BytesIO`` over already-read bytes (the normal
    single-read path — #446's single read, #591's ``imread``-over-BytesIO: rawpy
    0.26.1 has no module-level ``open_buffer``, and the dead call it replaced
    made the caller silently re-read the file from disk) or a ``str`` path (the
    degraded fallback, so both routes hash by the same recipe). Pass a FRESH
    ``io.BytesIO`` per call — a reused/non-zero-position buffer raises
    ``LibRawIOError``.

    Returns ``(None, None, None)`` on any LibRaw failure (incl.
    ``LibRawFileUnsupportedError`` for a non-camera TIFF routed to ``raw``,
    #75), so the caller can fall back or skip the file cleanly.

    The full-decode fallback needs no ``exif_transpose``: LibRaw applies
    ``sizes.flip`` inside ``postprocess`` itself (measured — a ``flip=6`` DNG
    returns a portrait 8064×6048 array from an 8064×6048 landscape sensor).
    """
    if not _RAWPY_AVAILABLE:
        return None, None, None
    try:
        with rawpy.imread(source) as raw:
            px_w, px_h = raw.sizes.width, raw.sizes.height
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = _raw_preview_from_jpeg_bytes(thumb.data)
                    if img is not None:
                        return img, px_w, px_h
            except rawpy.LibRawNoThumbnailError:
                pass
            # Full decode fallback — slower but always works.
            rgb = raw.postprocess(use_auto_wb=True, output_bps=8)
            return Image.fromarray(rgb).convert("RGB"), px_w, px_h
    except (OSError, ValueError, AttributeError, rawpy.LibRawError):
        return None, None, None
