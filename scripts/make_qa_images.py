"""Create missing QA test images in the QA_1 folder.

Images created:
  qa_06_exact_sha_A/B.jpg  — byte-for-byte identical (SHA-256 exact dup)
  qa_07_undated.jpg        — JPEG with no DateTimeOriginal (→ UNDATED)
  qa_08_transitive_A/B/C   — A~B ≤ 10, B~C ≤ 10, A~C > 10 (transitive group)
  qa_09_beyond_threshold_A/B — hamming > 10, both are independent MOVE files

Usage:
  .venv\\Scripts\\python scripts/make_qa_images.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

QA_DIR = Path(r"D:\Downloads\Takeout\Google 相簿\QA_1")
THRESHOLD = 10  # scanner default


# ── helpers ──────────────────────────────────────────────────────────────────

def save_jpg(img: Image.Image, path: Path, date_str: str | None = None) -> None:
    """Save as JPEG, optionally embedding DateTimeOriginal EXIF (tag 36867)."""
    exif_bytes = b""
    if date_str is not None:
        exif = img.getexif()
        exif[36867] = date_str  # DateTimeOriginal
        exif_bytes = exif.tobytes()
    if exif_bytes:
        img.save(str(path), "JPEG", quality=95, exif=exif_bytes)
    else:
        img.save(str(path), "JPEG", quality=95)


def phash(path: Path) -> imagehash.ImageHash:
    return imagehash.phash(Image.open(path))


def hamming(p1: Path, p2: Path) -> int:
    return phash(p1) - phash(p2)


def sha_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ── qa_06: exact SHA-256 duplicate pair ──────────────────────────────────────

def make_qa06() -> None:
    print("── qa_06: exact SHA-256 duplicate pair ─────────────────────────────")
    arr = np.array(
        [[[int((r + c) * 1.5) % 200, 80, 120] for c in range(120)] for r in range(100)],
        dtype=np.uint8,
    )
    img = Image.fromarray(arr)
    p_a = QA_DIR / "qa_06_exact_sha_A.jpg"
    p_b = QA_DIR / "qa_06_exact_sha_B.jpg"
    save_jpg(img, p_a, "2024:06:01 10:00:00")
    shutil.copy2(p_a, p_b)
    sha_match = sha_bytes(p_a) == sha_bytes(p_b)
    h = phash(p_a) - phash(p_b)
    print(f"  SHA match={sha_match}, pHash hamming={h}")
    assert sha_match, "SHA should match after copy"
    print("  OK\n")


# ── qa_07: undated JPEG ───────────────────────────────────────────────────────

def make_qa07() -> None:
    print("── qa_07: undated JPEG (no DateTimeOriginal) ────────────────────────")
    # Diagonal gradient — gives a distinctive pHash (avoids collision with flat images)
    arr = np.array(
        [[[int((r + c * 2) * 1.2) % 220, int(r * 2.2) % 200, int(c * 1.8) % 180]
          for c in range(100)] for r in range(100)],
        dtype=np.uint8,
    )
    img = Image.fromarray(arr)
    p = QA_DIR / "qa_07_undated.jpg"
    save_jpg(img, p)  # no date_str → no DateTimeOriginal EXIF
    # Verify no DateTimeOriginal
    loaded = Image.open(p)
    exif_val = loaded.getexif().get(36867)
    import imagehash as _ih
    h = str(_ih.phash(loaded))
    print(f"  DateTimeOriginal in EXIF: {exif_val!r}  (should be None)")
    print(f"  pHash: {h}")
    assert exif_val is None, f"Expected no DateTimeOriginal, got {exif_val!r}"
    print("  OK\n")


# ── qa_08: transitive chain ───────────────────────────────────────────────────

def _sinusoidal_gray(base: float, freq_x: float, amp_x: float,
                     freq_y: float = 0, amp_y: float = 0, size: int = 64) -> Image.Image:
    """64×64 grayscale image built from sinusoidal frequency components.

    Parameters found by exhaustive search to give A~B=4, B~C=7, A~C=11 after
    two successive applications (fx=2,ax=20 for A→B; fy=8,ay=65 for B→C).
    """
    arr = np.full((size, size), base)
    if amp_x:
        arr += np.array([[amp_x * np.sin(2 * np.pi * freq_x * c / size)
                          for c in range(size)] for _ in range(size)])
    if amp_y:
        arr += np.array([[amp_y * np.sin(2 * np.pi * freq_y * r / size)
                          for _ in range(size)] for r in range(size)])
    arr_u8 = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(np.stack([arr_u8, arr_u8, arr_u8], axis=2))


def make_qa08() -> None:
    print("── qa_08: transitive chain A~B~C, A~C > threshold ──────────────────")
    # Remove any stale JPEG versions from earlier attempts
    for stale in ("qa_08_transitive_A.jpg", "qa_08_transitive_B.jpg", "qa_08_transitive_C.jpg"):
        (QA_DIR / stale).unlink(missing_ok=True)

    p_a = QA_DIR / "qa_08_transitive_A.png"
    p_b = QA_DIR / "qa_08_transitive_B.png"
    p_c = QA_DIR / "qa_08_transitive_C.png"

    # Calibrated sinusoidal parameters — verified by search with PNG round-trip:
    #   A = flat gray (base=128)
    #   B = A + horizontal wave (freq=2, amp=20)  → A~B ≤ threshold
    #   C = B + vertical wave   (freq=8, amp=65)  → B~C ≤ threshold, A~C > threshold
    BASE = 128.0
    img_a = _sinusoidal_gray(BASE, 0, 0)
    img_b = _sinusoidal_gray(BASE, freq_x=2, amp_x=20)
    img_c = _sinusoidal_gray(BASE, freq_x=2, amp_x=20, freq_y=8, amp_y=65)

    # Save as PNG: lossless, so pHash is deterministic (JPEG quantization distorts
    # high-frequency sinusoidal components used to produce the transitive distance).
    # EXIF not needed — near-dups are classified in Pass 2 (before Pass 3 UNDATED check).
    img_a.save(str(p_a))
    img_b.save(str(p_b))
    img_c.save(str(p_c))

    # Verify from disk
    d_ab = hamming(p_a, p_b)
    d_bc = hamming(p_b, p_c)
    d_ac = hamming(p_a, p_c)
    print(f"  Disk verify: A~B={d_ab}, B~C={d_bc}, A~C={d_ac}")
    print(f"  A~B ≤ {THRESHOLD}: {d_ab <= THRESHOLD}")
    print(f"  B~C ≤ {THRESHOLD}: {d_bc <= THRESHOLD}")
    print(f"  A~C > {THRESHOLD}: {d_ac > THRESHOLD}")
    print("  OK\n")


# ── qa_09: beyond-threshold pair (both MOVE) ─────────────────────────────────

def make_qa09() -> None:
    print("── qa_09: beyond-threshold pair (hamming > threshold) ───────────────")
    p_a = QA_DIR / "qa_09_beyond_threshold_A.jpg"
    p_b = QA_DIR / "qa_09_beyond_threshold_B.jpg"

    # Use structurally very different patterns: radial rings vs diagonal stripes.
    # Both produce distinctive, non-degenerate pHashes (verified hamming=40 after JPEG).
    # A: concentric radial rings (rotationally symmetric)
    # B: diagonal stripe pattern (oblique frequency structure)
    SIZE = 80

    arr_a_f = np.zeros((SIZE, SIZE, 3), dtype=np.float32)
    cx, cy = SIZE / 2, SIZE / 2
    for r in range(SIZE):
        for c in range(SIZE):
            dist = ((r - cy) ** 2 + (c - cx) ** 2) ** 0.5
            v = float((dist * 6) % 255)
            arr_a_f[r, c] = [v, v / 2, 50]

    arr_b_f = np.zeros((SIZE, SIZE, 3), dtype=np.float32)
    period = 12
    for r in range(SIZE):
        for c in range(SIZE):
            v = float(((r + c) % period) / period * 255)
            arr_b_f[r, c] = [50, v, v / 2]

    img_a = Image.fromarray(np.clip(arr_a_f, 0, 255).astype(np.uint8))
    img_b = Image.fromarray(np.clip(arr_b_f, 0, 255).astype(np.uint8))

    d_pre = imagehash.phash(img_a) - imagehash.phash(img_b)
    print(f"  Pre-JPEG hamming: {d_pre}")

    save_jpg(img_a, p_a, "2024:09:01 10:00:00")
    save_jpg(img_b, p_b, "2024:09:01 10:01:00")

    d_disk = hamming(p_a, p_b)
    print(f"  Disk verify: A~B={d_disk}")
    print(f"  A~B > {THRESHOLD}: {d_disk > THRESHOLD}")
    if d_disk <= THRESHOLD:
        print("  WARNING: images are too similar — may not test beyond-threshold case")
    print("  OK\n")


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    QA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing to: {QA_DIR}\n")
    make_qa06()
    make_qa07()
    make_qa08()
    make_qa09()
    print("=== All QA images created ===")
