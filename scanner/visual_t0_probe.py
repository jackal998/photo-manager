"""Measurement-only T0 pixel-signal probe wired into the real scan pipeline.

**This is a probe, not a feature.** Nothing it computes is persisted, no
schema changes, no scoring changes. It exists to answer one question the
isolated per-signal cost table cannot: *what does running T0 signals do to
the wall time of an actual scan?* Per-signal medians and end-to-end cost are
not additive — the scan is read-bound, the pools are GIL-bound, and the
existing hash stage already decodes every file once.

Enable with the environment variable, read ONCE at import:

    PM_VISUAL_T0=          off  (default — the OFF path adds one attribute
                                 lookup and one branch per file, nothing else;
                                 numpy / scipy / pywt are not even imported)
    PM_VISUAL_T0=lean      6 signals  (see LEAN_SIGNALS)
    PM_VISUAL_T0=all      19 signals  (the full T0 scalar registry)

Recipe provenance
-----------------
``decode_working`` and every signal function below are copied verbatim from
the feasibility study's probes so the two can be diffed line for line:

    <scratchpad>/probes/t0_common.py    -- decode_working, _box_downscale,
                                           _open_raw, _EXT_FORMAT, WORKING_LONG_EDGE
    <scratchpad>/probes/t0_signals.py   -- every signal function and constant

Function names, constants (TILE_GRID, TILE_TOP_FRAC, STRUCTURE_SIGMA,
REBLUR_SIZE) and formulas are unchanged. Two deliberate differences, both at
the CALL SITE rather than inside a function, both stated so the numbers can
be read correctly:

1. ``clipping_fractions`` is called ONCE per image and its 8 outputs are
   read from the returned dict. The study's ``SIGNALS`` registry wraps each
   clip value in ``_clip_getter``, which recomputes the whole 8-channel pass
   per value — an artifact of the registry's one-callable-per-signal shape,
   not of the recipe. Fusing it here measures what a production
   implementation would actually cost.
2. ``vol_global`` and ``vol_tile_topk`` still each compute their own
   Laplacian in ``all`` mode, exactly as the study does. That OVERSTATES a
   fused implementation by roughly one Laplacian pass. ``lean`` mode runs
   only ``vol_tile_topk``, so it is unaffected.

What the measured delta includes
--------------------------------
The probe decodes the bytes a SECOND time, at 1024 px on the long edge. It
cannot reuse the hash stage's image: ``_hashes_from_data`` calls
``draft("RGB", (256, 256))`` (``scanner/hasher.py``), and every operator in
the variance-of-Laplacian family scales strongly with decode scale, so a
256 px image is not a substitute. The measured ON-minus-OFF delta is
therefore an UPPER BOUND on a fused production build, which would decode
once at 1024 px and drop the 256 px decode. See the study's Section A for
why the working resolution is fixed rather than inherited.

Dependencies: numpy, scipy, pywt. Present in this machine's venv but NOT in
``requirements.txt`` — the imports are therefore guarded, and a missing
dependency disables the probe rather than breaking the scan.
"""

from __future__ import annotations

import atexit
import io
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Read once at import. In a ProcessPoolExecutor worker the module is
# re-imported in the child, which re-reads the inherited environment.
PROBE_MODE = os.environ.get("PM_VISUAL_T0", "")

WORKING_LONG_EDGE = 1024

# The 6 signals the study's cost table identifies as the cheap, defensible
# core. Counted as 6 even though clip_hi_max / clip_lo_max come from one call.
LEAN_SIGNALS = (
    "vol_tile_topk",
    "clip_hi_max",
    "clip_lo_max",
    "noise_sigma_mad",
    "mean_luminance",
    "rms_contrast",
)
LEAN_SIGNAL_COUNT = len(LEAN_SIGNALS)
# Full scalar registry: 5 sharpness + 1 noise + 8 clipping + 2 exposure
# + 1 contrast + 2 colour.
ALL_SIGNAL_COUNT = 19

# Constants copied from t0_signals.py — quoted, not re-derived.
TILE_GRID = 8
TILE_TOP_FRAC = 0.25
STRUCTURE_SIGMA = 2.0
REBLUR_SIZE = 9
_EPS = 1e-12

# Extension -> format bucket, from t0_common.py.
_EXT_FORMAT = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg", ".mpo": "jpeg",
    ".heic": "heic", ".heif": "heic",
    ".png": "other", ".webp": "other", ".tif": "other", ".tiff": "other",
    ".dng": "dng", ".cr2": "dng", ".cr3": "dng", ".nef": "dng",
    ".arw": "dng", ".raf": "dng", ".rw2": "dng",
}
_RAW_FORMATS = frozenset({"dng"})

# --- dependency loading -------------------------------------------------
# Only paid for when the probe is ON. A missing scientific dependency makes
# the probe a no-op that counts failures; it never breaks a scan.

np = None
ndimage = None
pywt = None
Image = None
_DEPS_OK = False

if PROBE_MODE:
    try:
        import numpy as np  # type: ignore[no-redef]
        import pywt  # type: ignore[no-redef]
        from PIL import Image  # type: ignore[no-redef]
        from scipy import ndimage  # type: ignore[no-redef]

        _DEPS_OK = True
    except ImportError:  # pragma: no cover - deps are present in this venv
        _DEPS_OK = False

# pillow_heif's opener is registered by scanner/hasher.py at import; the probe
# is only ever reached through that module, so HEIC decodes without a second
# registration here.

# --- counters -----------------------------------------------------------
# Simplest thing that works under BOTH pools: plain module-level counters
# behind a Lock, flushed to stderr by an atexit handler.
#   * thread pool  -> every worker shares this process, so ONE line is printed
#                     at interpreter exit, totalling the whole scan.
#   * process pool -> each worker process has its own module state and prints
#                     its OWN line as it shuts down; the parent sums them.
# The pid is in the line precisely so the two cases are distinguishable.

_stats_lock = threading.Lock()
_stats = {"files": 0, "failures": 0, "ns": 0}


def probe_stats() -> dict:
    """Snapshot of this process's counters. Used by tests and the dump."""
    with _stats_lock:
        return dict(_stats)


def reset_probe_stats() -> None:
    """Zero this process's counters (tests only)."""
    with _stats_lock:
        _stats.update(files=0, failures=0, ns=0)


def format_stats_line(stats: Optional[dict] = None) -> str:
    """The one line each process emits at exit."""
    snap = probe_stats() if stats is None else stats
    return (
        f"[visual-t0-probe] pid={os.getpid()} mode={PROBE_MODE or 'off'}"
        f" deps_ok={_DEPS_OK}"
        f" files={snap['files']} failures={snap['failures']}"
        f" probe_s={snap['ns'] / 1e9:.3f}"
    )


def dump_probe_stats() -> None:
    """Print the counters to stderr if this process ran the probe at all."""
    snap = probe_stats()
    if snap["files"] == 0 and snap["failures"] == 0:
        return
    try:
        print(format_stats_line(snap), file=sys.stderr, flush=True)
    except (ValueError, OSError):  # pragma: no cover - stderr closed at exit
        pass


# Registered at most once per process. ``importlib.reload`` re-executes this
# module body against the SAME module dict, so without the sentinel a reload
# would stack a second handler and the process would print its summary line
# twice — which reads exactly like two workers.
if PROBE_MODE and not globals().get("_ATEXIT_REGISTERED"):
    atexit.register(dump_probe_stats)
    _ATEXIT_REGISTERED = True


# --- decode (verbatim from t0_common.py) --------------------------------


def format_of(path) -> str:
    """Bucket a path into ``jpeg`` / ``heic`` / ``dng`` / ``other``."""
    return _EXT_FORMAT.get(Path(path).suffix.lower(), "other")


def _box_downscale(img, long_edge: int):
    """Downscale so the long edge is ``long_edge``, area-averaged. Never up."""
    width, height = img.size
    current = max(width, height)
    if current <= long_edge:
        return img
    factor = current // long_edge
    if factor >= 2:
        img = img.reduce(factor)
        width, height = img.size
        current = max(width, height)
        if current <= long_edge:
            return img
    scale = long_edge / float(current)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    return img.resize(target, Image.BOX)


def _open_raw(data: bytes):
    """Embedded JPEG preview first, full demosaic as fallback."""
    try:
        import rawpy
    except ImportError:  # pragma: no cover - rawpy is present in this venv
        return None
    try:
        with rawpy.imread(io.BytesIO(data)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = Image.open(io.BytesIO(thumb.data)).convert("RGB")
                    img.load()
                    return img
            except rawpy.LibRawNoThumbnailError:
                pass
            rgb = raw.postprocess(use_auto_wb=True, output_bps=8)
            return Image.fromarray(rgb).convert("RGB")
    except (OSError, ValueError, AttributeError, rawpy.LibRawError):
        return None


def decode_working(path, data: bytes, long_edge: int = WORKING_LONG_EDGE):
    """Bytes -> ``(gray float32 0-255, rgb uint8 HxWx3)`` at the working size.

    Raises on undecodable input; :func:`compute_probe` owns the try/except.
    """
    fmt = format_of(path) if path is not None else "other"
    if fmt in _RAW_FORMATS:
        img = _open_raw(data)
        if img is None:
            raise ValueError("raw decode failed")
    else:
        img = Image.open(io.BytesIO(data))
        if fmt == "jpeg":
            # Same shrink-on-load trick the app uses, asked for the working
            # resolution instead of 256. A no-op for HEIC/PNG/WebP.
            img.draft("RGB", (long_edge, long_edge))
        img = img.convert("RGB")
        img.load()

    small = _box_downscale(img, long_edge)
    rgb = np.asarray(small, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"unexpected array shape {rgb.shape}")
    gray = (
        0.299 * rgb[:, :, 0].astype(np.float32)
        + 0.587 * rgb[:, :, 1].astype(np.float32)
        + 0.114 * rgb[:, :, 2].astype(np.float32)
    ).astype(np.float32)
    return gray, rgb


# --- signals (verbatim from t0_signals.py) ------------------------------


def vol_global(gray) -> float:
    """Variance of Laplacian (LAPV). https://doi.org/10.1109/ICPR.2000.903548"""
    lap = ndimage.laplace(gray)
    return float(lap.var())


def vol_tile_topk(gray, grid: int = TILE_GRID, top_frac: float = TILE_TOP_FRAC) -> float:
    """Mean variance-of-Laplacian over the sharpest ``top_frac`` of tiles."""
    lap = ndimage.laplace(gray)
    rows = np.array_split(lap, grid, axis=0)
    variances = [
        float(block.var())
        for row in rows
        for block in np.array_split(row, grid, axis=1)
        if block.size > 1
    ]
    if not variances:
        return 0.0
    k = max(1, int(round(len(variances) * top_frac)))
    top = np.sort(np.asarray(variances))[-k:]
    return float(top.mean())


def tenengrad(gray) -> float:
    """Mean squared Sobel gradient magnitude (TENG).
    https://doi.org/10.1016/j.patcog.2012.11.011"""
    gx = ndimage.sobel(gray, axis=1)
    gy = ndimage.sobel(gray, axis=0)
    return float(np.mean(gx * gx + gy * gy))


def crete_roffet_blur(gray, size: int = REBLUR_SIZE) -> float:
    """Re-blur metric. https://doi.org/10.1117/12.702790 (higher = blurrier)"""
    blur_v = ndimage.uniform_filter1d(gray, size=size, axis=0, mode="nearest")
    blur_h = ndimage.uniform_filter1d(gray, size=size, axis=1, mode="nearest")
    d_f_v = np.abs(np.diff(gray, axis=0))
    d_f_h = np.abs(np.diff(gray, axis=1))
    d_b_v = np.abs(np.diff(blur_v, axis=0))
    d_b_h = np.abs(np.diff(blur_h, axis=1))
    var_v = np.maximum(d_f_v - d_b_v, 0.0)
    var_h = np.maximum(d_f_h - d_b_h, 0.0)
    s_f_v, s_v_v = float(d_f_v.sum()), float(var_v.sum())
    s_f_h, s_v_h = float(d_f_h.sum()), float(var_h.sum())
    b_v = (s_f_v - s_v_v) / s_f_v if s_f_v > _EPS else 1.0
    b_h = (s_f_h - s_v_h) / s_f_h if s_f_h > _EPS else 1.0
    return float(max(b_v, b_h))


def structure_anisotropy(gray, sigma: float = STRUCTURE_SIGMA) -> float:
    """Gradient-weighted structure-tensor coherence.
    https://doi.org/10.1023/A:1008009714131"""
    gx = ndimage.sobel(gray, axis=1)
    gy = ndimage.sobel(gray, axis=0)
    jxx = ndimage.gaussian_filter(gx * gx, sigma)
    jyy = ndimage.gaussian_filter(gy * gy, sigma)
    jxy = ndimage.gaussian_filter(gx * gy, sigma)
    half_trace = 0.5 * (jxx + jyy)
    root = np.sqrt(np.square(0.5 * (jxx - jyy)) + np.square(jxy))
    lam1 = half_trace + root
    lam2 = half_trace - root
    total = lam1 + lam2
    coherence = np.square((lam1 - lam2) / (total + _EPS))
    weight_sum = float(total.sum())
    if weight_sum <= _EPS:
        return 0.0
    return float((coherence * total).sum() / weight_sum)


def noise_sigma_mad(gray) -> float:
    """MAD-wavelet noise sigma. https://doi.org/10.1093/biomet/81.3.425"""
    _, (_, _, detail_dd) = pywt.dwt2(gray, "db1")
    return float(np.median(np.abs(detail_dd)) / 0.6745)


def clipping_fractions(rgb, low: int = 0, high: int = 255) -> dict:
    """Per-channel and max clipping fractions — 8 values from one pass."""
    out: dict = {}
    for idx, name in enumerate("rgb"):
        channel = rgb[:, :, idx]
        out[f"clip_lo_{name}"] = float(np.mean(channel <= low))
        out[f"clip_hi_{name}"] = float(np.mean(channel >= high))
    out["clip_lo_max"] = max(out[f"clip_lo_{c}"] for c in "rgb")
    out["clip_hi_max"] = max(out[f"clip_hi_{c}"] for c in "rgb")
    return out


def mean_luminance(gray) -> float:
    """``mean(0.299R + 0.587G + 0.114B)`` on 0-255."""
    return float(gray.mean())


def luminance_entropy(gray) -> float:
    """Shannon entropy (bits) of the 256-bin luma histogram."""
    quantised = np.clip(gray, 0, 255).astype(np.uint8)
    counts = np.bincount(quantised.ravel(), minlength=256).astype(np.float64)
    total = counts.sum()
    if total <= 0:
        return 0.0
    probability = counts[counts > 0] / total
    return float(-np.sum(probability * np.log2(probability)))


def rms_contrast(gray) -> float:
    """``std(I) / mean(I)`` on luma. https://doi.org/10.1364/JOSAA.7.002032"""
    mean = float(gray.mean())
    if abs(mean) <= _EPS:
        return 0.0
    return float(gray.std() / mean)


def colourfulness(rgb) -> float:
    """Hasler-Susstrunk colourfulness.
    https://infoscience.epfl.ch/record/33994/files/HaslerS03.pdf"""
    red = rgb[:, :, 0].astype(np.float32)
    green = rgb[:, :, 1].astype(np.float32)
    blue = rgb[:, :, 2].astype(np.float32)
    rg = np.abs(red - green)
    yb = np.abs(0.5 * (red + green) - blue)
    std = np.sqrt(rg.std() ** 2 + yb.std() ** 2)
    mean = np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    return float(std + 0.3 * mean)


def gray_world_cast(rgb) -> float:
    """Gray-world deviation.
    https://doi.org/10.1016/0016-0032(80)90058-7"""
    means = rgb.reshape(-1, 3).mean(axis=0).astype(np.float64)
    grand = float(means.mean())
    if grand <= _EPS:
        return 0.0
    return float(np.linalg.norm(means - grand) / grand)


# --- the probe entry point ----------------------------------------------


def _signals_lean(gray, rgb) -> dict:
    """The 6 lean signals. One clipping pass yields two of them."""
    clip = clipping_fractions(rgb)
    return {
        "vol_tile_topk": vol_tile_topk(gray),
        "clip_hi_max": clip["clip_hi_max"],
        "clip_lo_max": clip["clip_lo_max"],
        "noise_sigma_mad": noise_sigma_mad(gray),
        "mean_luminance": mean_luminance(gray),
        "rms_contrast": rms_contrast(gray),
    }


def _signals_all(gray, rgb) -> dict:
    """Every T0 scalar signal — 19 values."""
    out = dict(clipping_fractions(rgb))  # 8 values, one pass
    out["vol_tile_topk"] = vol_tile_topk(gray)
    out["vol_global"] = vol_global(gray)
    out["tenengrad"] = tenengrad(gray)
    out["crete_roffet_blur"] = crete_roffet_blur(gray)
    out["structure_anisotropy"] = structure_anisotropy(gray)
    out["noise_sigma_mad"] = noise_sigma_mad(gray)
    out["mean_luminance"] = mean_luminance(gray)
    out["luminance_entropy"] = luminance_entropy(gray)
    out["rms_contrast"] = rms_contrast(gray)
    out["colourfulness"] = colourfulness(rgb)
    out["gray_world_cast"] = gray_world_cast(rgb)
    return out


def compute_probe(data: bytes, path=None) -> int:
    """Decode ``data`` at the working resolution, compute the signal set,
    DISCARD the results, and return how many signals were computed.

    Returns 0 on any failure (and increments the failure counter). Never
    raises into the scan — a probe that can break a scan is not a probe.

    ``path`` is optional only so the documented ``compute_probe(data)`` shape
    stays callable; it is needed to bucket RAW vs PIL formats by extension,
    exactly as ``t0_common.decode_working`` does, so the pipeline always
    passes it.
    """
    if not _DEPS_OK:
        with _stats_lock:
            _stats["failures"] += 1
        return 0
    start = time.perf_counter_ns()
    try:
        gray, rgb = decode_working(path, data)
        values = _signals_all(gray, rgb) if PROBE_MODE == "all" else _signals_lean(gray, rgb)
        count = len(values)
    except Exception:  # noqa: BLE001 - a probe must never break a scan
        elapsed = time.perf_counter_ns() - start
        with _stats_lock:
            _stats["failures"] += 1
            _stats["ns"] += elapsed
        return 0
    elapsed = time.perf_counter_ns() - start
    with _stats_lock:
        _stats["files"] += 1
        _stats["ns"] += elapsed
    return count
