"""Disposable near-duplicate cluster generator for the prune web scenarios
(s61 / s67, #686).

The web prune flow needs singleton groups, which only exist after a group is
collapsed to one member. To build a MIXED prune offer (a plain singleton AND an
actioned singleton at once) the scenario needs TWO independent near-duplicate
clusters — and the shared ``qa/sandbox/near-duplicates`` fixtures all collapse
into ONE dedup group. So, like the Qt desktop s61/s67 drivers, we GENERATE the
clusters: each is a random base image saved at two JPEG qualities (a guaranteed
within-cluster near-dup pair) and regenerated until its worst pairwise pHash
distance is under the scanner threshold. Two separately-generated bases are
cross-distinct, so they land in two different groups.

Files are written under ``qa/sandbox/_disposable/`` (the isolated, regenerated
sandbox), mirroring the desktop drivers' DESTRUCTIVE-COVERAGE GUARD home. The
caller copies them into a per-run tmpdir (so /api/remove's manifest_path guard
is satisfied — see the s54 pattern) and rmtree's it.
"""
from __future__ import annotations

import io
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

QUALITIES = (95, 65)        # 2 files per cluster — a guaranteed near-dup pair
_SCANNER_THRESHOLD = 10     # scanner/dedup.py default (see Qt s13/s61)
_REGEN_MAX_ATTEMPTS = 8


def _build_base(rng: np.random.Generator) -> Image.Image:
    base_color = rng.integers(0, 256, size=(3,))
    fx = float(rng.uniform(0.5, 4.0))
    fy = float(rng.uniform(0.5, 4.0))
    h, w = 480, 640
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        arr[..., c] = (
            base_color[c]
            + 60 * np.sin(2 * np.pi * fx * xx / w + c)
            + 60 * np.cos(2 * np.pi * fy * yy / h + c * 0.7)
        )
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _max_pairwise_phash(base: Image.Image) -> int:
    saved: list[Image.Image] = []
    for q in QUALITIES:
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=q)
        buf.seek(0)
        saved.append(Image.open(buf).copy())
    hashes = [imagehash.phash(im) for im in saved]
    return max(
        hashes[i] - hashes[j]
        for i in range(len(hashes))
        for j in range(i + 1, len(hashes))
    )


def _build_cluster_base() -> Image.Image:
    last_worst: int | None = None
    for _ in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_base(np.random.default_rng())
        worst = _max_pairwise_phash(candidate)
        if worst <= _SCANNER_THRESHOLD:
            return candidate
        last_worst = worst
    raise RuntimeError(
        f"Could not generate a clustering near-duplicate base after "
        f"{_REGEN_MAX_ATTEMPTS} attempts (last worst pHash distance {last_worst})."
    )


def write_cluster(dest_dir: Path, names: tuple[str, str], *, exif_month: int = 1) -> None:
    """Write a 2-file near-duplicate cluster ``names`` into ``dest_dir``.

    ``names`` is ``(keep_basename, drop_basename)`` saved at QUALITIES[0]/[1]
    respectively. Distinct EXIF DateTimeOriginal per cluster (``exif_month``)
    keeps the two clusters' timestamps apart, matching the desktop drivers.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = _build_cluster_base()
    for i, (q, name) in enumerate(zip(QUALITIES, names)):
        exif = base.getexif()
        exif[36867] = f"2024:{exif_month:02d}:01 1{i}:00:00"
        base.save(str(dest_dir / name), "JPEG", quality=q, exif=exif.tobytes())
