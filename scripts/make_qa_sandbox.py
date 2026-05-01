"""Generate qa/sandbox/ fixtures for the /qa-explore QA agent.

Idempotent: re-running with no flags is a no-op when each subdir already
contains the expected file count. Pass --force to regenerate everything.

Imports save_jpg from scripts/make_qa_images.py (sibling module).

Subdirs and contents:
  empty/             — 0 files (+ .gitkeep so the empty dir is committable)
  unique/            — 10 distinct synthetic JPEGs, distinct EXIF dates
  near-duplicates/   — 5 JPEG re-saves of one base image at varying quality
  corrupted/         — 1 truncated JPEG (real SOI header, mid-stream cut)
  huge/              — 1 ~50 MP JPEG (8000x6300 gradient)
  formats/           — 5 non-JPEG images: heic, png, gif, webp, tiff
  exif-edge/         — 6 JPEGs probing EXIF edge cases (timezone, sub-second,
                       fallback to CreateDate, fallback to DateTime tag,
                       zero-date sentinel, dash sentinel)
  format-dup/        — 2 files: same scene as JPG + HEIC (FORMAT_DUPLICATE)
  multi-source-a/    — 2 JPEGs (used with multi-source-b for priority test)
  multi-source-b/    — 3 JPEGs: 1 byte-identical to a/, 1 near-dup, 1 unique
  walker-exclusions/ — 2 JPEGs + sidecar.json + Thumbs.db + desktop.ini
                       (probes walker skip rules)
  videos/            — 2 minimal video stub files: dummy.mp4, dummy.mov
                       (just an ftyp box; SHA-256 + extension routing only —
                       no actual decodable video stream)
  live-photo/        — IMG_0001.HEIC + IMG_0001.MOV pair
                       (probes Live Photo pairing logic in walker/dedup)

Usage:
  python scripts/make_qa_sandbox.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from make_qa_images import save_jpg  # noqa: E402

pillow_heif.register_heif_opener()

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX = REPO_ROOT / "qa" / "sandbox"

EXPECTED_COUNTS = {
    "empty": 0,
    "unique": 10,
    "near-duplicates": 5,
    "corrupted": 1,
    "huge": 1,
    "formats": 5,
    "exif-edge": 6,
    "format-dup": 2,
    "multi-source-a": 2,
    "multi-source-b": 3,
    "walker-exclusions": 5,
    "videos": 2,
    "live-photo": 2,
}


def _ensure_dir(name: str) -> Path:
    p = SANDBOX / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _content_files(p: Path) -> list[Path]:
    return [f for f in p.iterdir() if f.is_file() and f.name != ".gitkeep"]


def _is_complete(name: str) -> bool:
    p = SANDBOX / name
    if not p.is_dir():
        return False
    return len(_content_files(p)) == EXPECTED_COUNTS[name]


def _clear(p: Path) -> None:
    for f in _content_files(p):
        f.unlink()


def _gradient_rgb(seed: int, w: int, h: int) -> np.ndarray:
    """Deterministic distinctive RGB gradient for a given seed."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, size=(3,))
    fx = float(rng.uniform(0.5, 4.0))
    fy = float(rng.uniform(0.5, 4.0))
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        arr[..., c] = (
            base[c]
            + 60 * np.sin(2 * np.pi * fx * xx / w + c)
            + 60 * np.cos(2 * np.pi * fy * yy / h + c * 0.7)
        )
    return np.clip(arr, 0, 255).astype(np.uint8)


def make_empty(force: bool) -> None:
    p = _ensure_dir("empty")
    keep = p / ".gitkeep"
    if not keep.exists():
        keep.touch()
    if force:
        _clear(p)
    print("  empty/            -> 0 files (+ .gitkeep)")


def make_unique(force: bool) -> None:
    name = "unique"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/           -> already complete (10 files), skipping")
        return
    _clear(p)
    for i in range(10):
        arr = _gradient_rgb(seed=1000 + i, w=320, h=240)
        out = p / f"unique_{i:02d}.jpg"
        save_jpg(Image.fromarray(arr), out, f"2024:01:{i + 1:02d} 12:00:00")
    print(f"  {name}/           -> 10 files")


def make_near_duplicates(force: bool) -> None:
    name = "near-duplicates"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/  -> already complete (5 files), skipping")
        return
    _clear(p)
    base = Image.fromarray(_gradient_rgb(seed=42, w=640, h=480))
    qualities = [95, 88, 80, 72, 65]
    for i, q in enumerate(qualities):
        exif = base.getexif()
        exif[36867] = f"2024:02:01 1{i}:00:00"
        out = p / f"neardup_{i:02d}_q{q}.jpg"
        base.save(str(out), "JPEG", quality=q, exif=exif.tobytes())
    print(f"  {name}/  -> 5 files (qualities {qualities})")


def make_corrupted(force: bool) -> None:
    name = "corrupted"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/        -> already complete (1 file), skipping")
        return
    _clear(p)
    arr = _gradient_rgb(seed=7, w=200, h=150)
    tmp = p / "_full.jpg"
    Image.fromarray(arr).save(str(tmp), "JPEG", quality=90)
    full = tmp.read_bytes()
    tmp.unlink()
    out = p / "corrupted_truncated.jpg"
    out.write_bytes(full[:1024])
    print(f"  {name}/        -> 1 file (truncated to {out.stat().st_size} bytes)")


def make_huge(force: bool) -> None:
    name = "huge"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/             -> already complete (1 file), skipping")
        return
    _clear(p)
    W, H = 8000, 6300
    x_grad = (np.arange(W) * 255 / W).astype(np.uint8)
    y_grad = (np.arange(H) * 255 / H).astype(np.uint8)
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    arr[..., 0] = x_grad[None, :]
    arr[..., 1] = y_grad[:, None]
    arr[..., 2] = 100
    out = p / "huge_50mp.jpg"
    save_jpg(Image.fromarray(arr), out, "2024:03:01 12:00:00")
    mp = (W * H) / 1e6
    mb = out.stat().st_size / 1e6
    print(f"  {name}/             -> 1 file ({W}x{H} = {mp:.1f}MP, {mb:.1f}MB)")


def _save_with_exif_date(img: Image.Image, path: Path, fmt: str,
                         date_str: str | None = None, **save_kwargs) -> None:
    """Save img in `fmt` (e.g. 'PNG', 'GIF', 'WEBP', 'TIFF', 'HEIF'),
    embedding DateTimeOriginal when date_str is provided. Some formats
    (GIF) don't support EXIF; for those we save without EXIF and the
    caller knows to expect None on read."""
    exif_bytes = b""
    if date_str is not None:
        exif = img.getexif()
        exif[36867] = date_str
        exif_bytes = exif.tobytes()
    if exif_bytes and fmt.upper() not in ("GIF",):
        img.save(str(path), format=fmt, exif=exif_bytes, **save_kwargs)
    else:
        img.save(str(path), format=fmt, **save_kwargs)


def make_formats(force: bool) -> None:
    name = "formats"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/         -> already complete (5 files), skipping")
        return
    _clear(p)
    base = Image.fromarray(_gradient_rgb(seed=2024, w=320, h=240))

    # HEIC — pillow-heif registered above
    _save_with_exif_date(base, p / "fmt_heic.heic", "HEIF",
                         date_str="2024:04:01 10:00:00")
    # PNG (lossless, supports EXIF)
    _save_with_exif_date(base, p / "fmt_png.png", "PNG",
                         date_str="2024:04:02 10:00:00")
    # GIF (no EXIF — palette mode)
    base.convert("P", palette=Image.Palette.ADAPTIVE).save(
        str(p / "fmt_gif.gif"), format="GIF")
    # WebP (supports EXIF)
    _save_with_exif_date(base, p / "fmt_webp.webp", "WEBP",
                         date_str="2024:04:04 10:00:00", quality=85)
    # TIFF (lossless, supports EXIF)
    _save_with_exif_date(base, p / "fmt_tiff.tif", "TIFF",
                         date_str="2024:04:05 10:00:00")
    print(f"  {name}/         -> 5 files (heic, png, gif, webp, tiff)")


def make_exif_edge(force: bool) -> None:
    name = "exif-edge"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/       -> already complete (6 files), skipping")
        return
    _clear(p)

    def _seeded(seed: int) -> Image.Image:
        return Image.fromarray(_gradient_rgb(seed=seed, w=200, h=150))

    # 1. Timezone offset suffix (parser strips after first 19 chars)
    save_jpg(_seeded(101), p / "tz_offset.jpg", "2024:05:01 09:30:00+09:00")

    # 2. Sub-second precision (parser truncates to second)
    save_jpg(_seeded(102), p / "subsecond.jpg", "2024:05:02 09:30:00.500")

    # 3. CreateDate only (no DateTimeOriginal) — exiftool fallback chain
    img3 = _seeded(103)
    exif3 = img3.getexif()
    exif3[36868] = "2024:05:03 09:30:00"  # CreateDate (DateTimeDigitized)
    img3.save(str(p / "createdate_only.jpg"), "JPEG",
              quality=95, exif=exif3.tobytes())

    # 4. DateTime tag 306 only (hasher.py:107-120 fallback target)
    img4 = _seeded(104)
    exif4 = img4.getexif()
    exif4[306] = "2024:05:04 09:30:00"  # DateTime
    img4.save(str(p / "datetime_tag_only.jpg"), "JPEG",
              quality=95, exif=exif4.tobytes())

    # 5. Zero-date sentinel "0000:..." — exif.py treats as None
    save_jpg(_seeded(105), p / "zero_date_sentinel.jpg",
             "0000:00:00 00:00:00")

    # 6. Dash sentinel "-" — exif.py treats as None
    save_jpg(_seeded(106), p / "dash_sentinel.jpg", "-")
    print(f"  {name}/       -> 6 files "
          f"(tz, subsec, createdate-only, datetime-only, zero, dash)")


def make_format_dup(force: bool) -> None:
    name = "format-dup"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/      -> already complete (2 files), skipping")
        return
    _clear(p)
    base = Image.fromarray(_gradient_rgb(seed=555, w=400, h=300))
    save_jpg(base, p / "scene_a.jpg", "2024:06:15 12:00:00")
    _save_with_exif_date(base, p / "scene_a.heic", "HEIF",
                         date_str="2024:06:15 12:00:00")
    print(f"  {name}/      -> 2 files (jpg + heic of same scene)")


def make_multi_source(force: bool) -> None:
    a_complete = _is_complete("multi-source-a")
    b_complete = _is_complete("multi-source-b")
    if not force and a_complete and b_complete:
        print("  multi-source-a/, multi-source-b/ -> already complete, skipping")
        return

    pa = _ensure_dir("multi-source-a")
    pb = _ensure_dir("multi-source-b")
    _clear(pa)
    _clear(pb)

    base1 = Image.fromarray(_gradient_rgb(seed=777, w=300, h=200))
    base2 = Image.fromarray(_gradient_rgb(seed=888, w=300, h=200))

    # In source A: two photos
    save_jpg(base1, pa / "shared.jpg", "2024:07:01 10:00:00")
    save_jpg(base2, pa / "a_only.jpg", "2024:07:02 10:00:00")

    # In source B:
    #   - shared.jpg byte-identical to a/shared.jpg (EXACT_DUPLICATE)
    #   - shared_neardup.jpg same scene re-saved (REVIEW_DUPLICATE candidate)
    #   - b_only.jpg unique to b
    import shutil
    shutil.copy2(pa / "shared.jpg", pb / "shared.jpg")
    base1.save(str(pb / "shared_neardup.jpg"), "JPEG", quality=70,
               exif=_exif_with_date("2024:07:01 10:00:01"))
    save_jpg(Image.fromarray(_gradient_rgb(seed=999, w=300, h=200)),
             pb / "b_only.jpg", "2024:07:03 10:00:00")
    print("  multi-source-a/ -> 2 files; multi-source-b/ -> 3 files "
          "(1 shared exact, 1 near-dup, 1 unique)")


def _exif_with_date(date_str: str) -> bytes:
    img = Image.new("RGB", (1, 1))
    exif = img.getexif()
    exif[36867] = date_str
    return exif.tobytes()


def make_walker_exclusions(force: bool) -> None:
    name = "walker-exclusions"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/ -> already complete (5 files), skipping")
        return
    _clear(p)

    # Two real photos that SHOULD be picked up
    save_jpg(Image.fromarray(_gradient_rgb(seed=311, w=200, h=150)),
             p / "real_photo_a.jpg", "2024:08:01 10:00:00")
    save_jpg(Image.fromarray(_gradient_rgb(seed=312, w=200, h=150)),
             p / "real_photo_b.jpg", "2024:08:02 10:00:00")

    # Things the walker should skip (per walker.py / test_walker.py)
    (p / "real_photo_a.jpg.json").write_text(
        '{"title":"Google Takeout sidecar","photoTakenTime":'
        '{"timestamp":"1722499200"}}', encoding="utf-8")
    (p / "Thumbs.db").write_bytes(b"\x00" * 64)  # placeholder
    (p / "desktop.ini").write_text(
        "[.ShellClassInfo]\nIconFile=icon.ico\n", encoding="utf-8")
    print(f"  {name}/ -> 5 files (2 photos + .json sidecar "
          "+ Thumbs.db + desktop.ini)")


def _minimal_mp4_box(brand: bytes) -> bytes:
    """Build a minimal valid ftyp box.

    Layout: size(4) + 'ftyp'(4) + major_brand(4) + minor_version(4) +
    compatible_brand(4) = 20 bytes. Enough for exiftool to identify
    the format; no actual video stream is included, so date reads
    return None — exactly the UNDATED-video edge case we want.
    """
    if len(brand) != 4:
        raise ValueError("brand must be exactly 4 bytes")
    body = brand + b"\x00\x00\x02\x00" + brand  # major + minor + 1 compat
    box_size = (4 + 4 + len(body)).to_bytes(4, "big")
    return box_size + b"ftyp" + body


def make_videos(force: bool) -> None:
    name = "videos"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/          -> already complete (2 files), skipping")
        return
    _clear(p)
    (p / "dummy.mp4").write_bytes(_minimal_mp4_box(b"isom"))
    (p / "dummy.mov").write_bytes(_minimal_mp4_box(b"qt  "))
    print(f"  {name}/          -> 2 files (minimal ftyp-only mp4 + mov)")


def make_live_photo(force: bool) -> None:
    name = "live-photo"
    p = _ensure_dir(name)
    if not force and _is_complete(name):
        print(f"  {name}/      -> already complete (2 files), skipping")
        return
    _clear(p)
    base = Image.fromarray(_gradient_rgb(seed=1234, w=320, h=240))
    _save_with_exif_date(base, p / "IMG_0001.HEIC", "HEIF",
                         date_str="2024:09:01 14:30:00")
    (p / "IMG_0001.MOV").write_bytes(_minimal_mp4_box(b"qt  "))
    print(f"  {name}/      -> 2 files (IMG_0001.HEIC + IMG_0001.MOV pair)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate qa/sandbox/ fixtures")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate all fixtures even if already present")
    args = parser.parse_args()

    SANDBOX.mkdir(parents=True, exist_ok=True)
    print(f"Writing fixtures to: {SANDBOX}")
    make_empty(args.force)
    make_unique(args.force)
    make_near_duplicates(args.force)
    make_corrupted(args.force)
    make_huge(args.force)
    make_formats(args.force)
    make_exif_edge(args.force)
    make_format_dup(args.force)
    make_multi_source(args.force)
    make_walker_exclusions(args.force)
    make_videos(args.force)
    make_live_photo(args.force)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
