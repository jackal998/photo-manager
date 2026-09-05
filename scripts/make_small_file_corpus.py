"""Generate a small-file corpus for the NAS read-performance probes (H1/H2/H3).

Produces N valid, decodable JPEGs shaped like a real small-photo library:
a log-normal-ish size distribution around ``--mean-size-kb`` (clipped to
``±--size-spread``) spread across a directory tree so
``probe_walk_timing.py`` (H2) exercises realistic enumeration cost, not a
single flat directory.

Each file is a genuine JPEG (PIL-encoded noise image) so every read-path
probe (``Path.read_bytes()`` + PIL decode, exiftool) treats it exactly like
a photo — no synthetic byte blobs. Exact byte-size control is done by
inserting COM marker segments (0xFFFE — an officially-skippable segment
type; every JPEG decoder, including libjpeg/PIL, ignores it) right after
the SOI marker, padding the base-compressed image up to the sampled target
size. This is why the corpus is cheap to generate at 5000+ files: a small
noise image compresses in milliseconds and the byte-exact padding is a
pure bytes operation, no re-encoding needed.

Refuses to run if ``--target`` already exists and is non-empty — this is a
generator, not a merge tool.

CLI usage
---------

    # Default: ~5000 files, 40 KB mean, 50 dirs x 100 files/dir
    python scripts/make_small_file_corpus.py --target D:\\nas-probe-corpus \\
        --manifest-out D:\\nas-probe-corpus-manifest.json

    # Tiny local smoke corpus
    python scripts/make_small_file_corpus.py --target /tmp/corpus \\
        --count 50 --files-per-dir 10

JSON manifest schema (``--manifest-out``)
------------------------------------------

    {
      "summary": {"target": str, "count": int, "mean_size_kb_requested": float,
                  "size_spread_requested": float, "mean_bytes_actual": float,
                  "min_bytes": int, "max_bytes": int, "seed": int},
      "files": [{"path": str (relative to target), "size_bytes": int}, ...]
    }
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import statistics
import sys
from pathlib import Path

from PIL import Image

_DEFAULT_COUNT = 5000
_DEFAULT_MEAN_KB = 40.0
_DEFAULT_SPREAD = 0.5
_DEFAULT_FILES_PER_DIR = 100
_DEFAULT_TREE_DEPTH = 1
_DEFAULT_SEED = 42

# COM segment payload cap: the 2-byte length field (which includes itself)
# maxes out at 65535, so payload <= 65533. Stay comfortably under that.
_COM_MAX_PAYLOAD = 65000
# Base noise images are tiny (<= a few KB at these resolutions/quality), so
# they always have headroom under mean_size_kb * (1 - spread) for any
# sane --mean-size-kb (>= ~5 KB). Document rather than over-engineer.
_BASE_RESOLUTIONS = (32, 48, 64)
_BASE_QUALITY = 60


def _sample_size_bytes(mean_bytes: float, spread: float, rng: random.Random) -> int:
    """Log-normal-ish sample clipped to ``[mean*(1-spread), mean*(1+spread)]``."""
    lo = mean_bytes * (1.0 - spread)
    hi = mean_bytes * (1.0 + spread)
    mu = math.log(max(mean_bytes, 1.0))
    sigma = 0.35
    for _ in range(20):
        v = rng.lognormvariate(mu, sigma)
        if lo <= v <= hi:
            return int(v)
    # 20 rejections in a row (tight spread) — clip a fresh draw instead of
    # collapsing every remaining file onto one boundary value.
    return int(min(max(rng.lognormvariate(mu, sigma), lo), hi))


def _base_jpeg_bytes(rng: random.Random) -> bytes:
    """A small, valid, decodable JPEG (noise image) to pad up to size."""
    side = rng.choice(_BASE_RESOLUTIONS)
    img = Image.effect_noise((side, side), 40).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=_BASE_QUALITY)
    return buf.getvalue()


def _pad_jpeg_to_size(data: bytes, target_size: int) -> bytes:
    """Insert COM marker segment(s) after SOI to reach ``target_size`` bytes.

    Never shrinks — if ``data`` is already >= target_size it is returned
    unchanged (caller is responsible for picking a base image with headroom).
    Padding may fall up to 3 bytes short of the exact target when the
    remainder can't form a well-formed 4-byte-minimum COM header; negligible
    for a synthetic read-performance corpus.
    """
    if len(data) >= target_size:
        return data
    assert data[:2] == b"\xff\xd8", "expected a JPEG SOI marker"
    pad_needed = target_size - len(data)
    segments = bytearray()
    while pad_needed >= 4:
        seg_total = min(pad_needed, 4 + _COM_MAX_PAYLOAD)
        payload_len = seg_total - 4
        length_field = payload_len + 2  # length field counts itself
        segments += b"\xff\xfe"
        segments += length_field.to_bytes(2, "big")
        segments += b"\x00" * payload_len
        pad_needed -= seg_total
    return data[:2] + bytes(segments) + data[2:]


def _make_dir_tree(root: Path, n_dirs: int, depth: int) -> list[Path]:
    """``n_dirs`` distinct directories arranged in ``depth`` nested levels.

    Base-``branch`` positional numbering of ``i`` guarantees uniqueness for
    every ``i < branch**depth``, and ``branch`` is chosen so
    ``branch**depth >= n_dirs``.
    """
    if depth <= 1:
        return [root / f"dir_{i:04d}" for i in range(n_dirs)]
    branch = max(1, math.ceil(n_dirs ** (1.0 / depth)))
    dirs: list[Path] = []
    for i in range(n_dirs):
        idx = i
        parts = []
        for lvl in range(depth):
            parts.append(f"L{lvl}_{idx % branch:03d}")
            idx //= branch
        dirs.append(root.joinpath(*parts))
    return dirs


def generate(
    target: Path, count: int, mean_kb: float, spread: float,
    files_per_dir: int, tree_depth: int, seed: int,
) -> list[dict]:
    n_dirs = max(1, math.ceil(count / files_per_dir))
    dir_paths = _make_dir_tree(target, n_dirs, tree_depth)
    rng = random.Random(seed)
    mean_bytes = mean_kb * 1024.0

    manifest: list[dict] = []
    file_idx = 0
    for d in dir_paths:
        if file_idx >= count:
            break
        d.mkdir(parents=True, exist_ok=True)
        for _ in range(files_per_dir):
            if file_idx >= count:
                break
            target_size = _sample_size_bytes(mean_bytes, spread, rng)
            base = _base_jpeg_bytes(rng)
            attempts = 0
            while len(base) >= target_size and attempts < 5:
                base = _base_jpeg_bytes(rng)
                attempts += 1
            data = _pad_jpeg_to_size(base, target_size)
            fname = f"IMG_{file_idx:06d}.jpg"
            fpath = d / fname
            fpath.write_bytes(data)
            manifest.append({
                "path": str(fpath.relative_to(target)).replace("\\", "/"),
                "size_bytes": len(data),
            })
            file_idx += 1
    return manifest


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--target", required=True, help="Output directory (must be empty or absent)")
    p.add_argument("--count", type=int, default=_DEFAULT_COUNT)
    p.add_argument("--mean-size-kb", type=float, default=_DEFAULT_MEAN_KB)
    p.add_argument("--size-spread", type=float, default=_DEFAULT_SPREAD,
                    help="Fractional spread around the mean, e.g. 0.5 = +/-50%%")
    p.add_argument("--files-per-dir", type=int, default=_DEFAULT_FILES_PER_DIR)
    p.add_argument("--tree-depth", type=int, default=_DEFAULT_TREE_DEPTH,
                    help="Directory nesting levels (1 = flat dirs under --target)")
    p.add_argument("--seed", type=int, default=_DEFAULT_SEED)
    p.add_argument("--manifest-out", default=None)
    args = p.parse_args(argv[1:])

    target = Path(args.target)
    if target.exists() and any(target.iterdir()):
        print(f"ERROR: target dir {target} already exists and is non-empty; "
              "refusing to run (this tool does not merge into an existing corpus)",
              file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)

    manifest = generate(
        target, args.count, args.mean_size_kb, args.size_spread,
        args.files_per_dir, args.tree_depth, args.seed,
    )

    sizes = [m["size_bytes"] for m in manifest]
    summary = {
        "target": str(target),
        "count": len(manifest),
        "mean_size_kb_requested": args.mean_size_kb,
        "size_spread_requested": args.size_spread,
        "mean_bytes_actual": statistics.mean(sizes) if sizes else 0.0,
        "min_bytes": min(sizes) if sizes else 0,
        "max_bytes": max(sizes) if sizes else 0,
        "seed": args.seed,
    }
    print(f"generated {len(manifest)} files under {target}")
    print(json.dumps(summary, indent=2))

    if args.manifest_out:
        out = {"summary": summary, "files": manifest}
        Path(args.manifest_out).write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote manifest {args.manifest_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
