"""Tests for scripts/make_small_file_corpus.py — the NAS-probe corpus generator.

Covers the two pieces of real logic that can break silently: the size
distribution actually lands inside the requested ±spread window (every
generated file must be a valid, decodable JPEG a real read-path probe
would treat like a photo), and the refuse-on-nonempty-target guard.
Padding via COM-segment insertion is exercised indirectly through the
size-window assertion — a padding bug would show up as either an
undecodable file or a size outside the window.

Skipped intentionally: directory-tree shaping beyond a basic dirs-count
check (dev tool, not user-facing production code).
"""

from __future__ import annotations

import random

from PIL import Image

import scripts.make_small_file_corpus as gen


def test_pad_jpeg_to_size_hits_target_within_tolerance() -> None:
    """COM-segment padding lands within a few bytes of the exact target."""
    rng = random.Random(1)
    base = gen._base_jpeg_bytes(rng)
    target = len(base) + 20_000
    padded = gen._pad_jpeg_to_size(base, target)
    # May fall up to 3 bytes short (documented tolerance for the last segment).
    assert target - 3 <= len(padded) <= target
    # Still a valid, decodable JPEG.
    img = Image.open(__import__("io").BytesIO(padded))
    img.load()


def test_pad_jpeg_to_size_no_op_when_already_large_enough() -> None:
    rng = random.Random(2)
    base = gen._base_jpeg_bytes(rng)
    assert gen._pad_jpeg_to_size(base, len(base) - 10) == base


def test_sample_size_bytes_stays_within_spread_window() -> None:
    rng = random.Random(3)
    mean_bytes = 40_000.0
    spread = 0.5
    samples = [gen._sample_size_bytes(mean_bytes, spread, rng) for _ in range(200)]
    lo, hi = mean_bytes * (1 - spread), mean_bytes * (1 + spread)
    assert all(lo <= s <= hi for s in samples)


def test_generate_produces_valid_decodable_jpegs_within_size_window(tmp_path) -> None:
    target = tmp_path / "corpus"
    mean_kb, spread = 20.0, 0.5
    manifest = gen.generate(
        target, count=12, mean_kb=mean_kb, spread=spread,
        files_per_dir=4, tree_depth=1, seed=7,
    )

    assert len(manifest) == 12
    lo_bytes, hi_bytes = mean_kb * 1024 * (1 - spread), mean_kb * 1024 * (1 + spread)
    for entry in manifest:
        fpath = target / entry["path"]
        assert fpath.is_file()
        size = fpath.stat().st_size
        assert size == entry["size_bytes"]
        # Within tolerance of the requested window (padding can land a
        # few bytes short of target, see test above).
        assert lo_bytes - 5 <= size <= hi_bytes
        with Image.open(fpath) as img:
            img.load()  # raises on a corrupt/undecodable file

    # 12 files / 4 per dir => 3 directories.
    dirs = {p.parent for p in (target / e["path"] for e in manifest)}
    assert len(dirs) == 3


def test_main_refuses_nonempty_target(tmp_path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    (target / "leftover.txt").write_text("not ours")

    rc = gen.main(["make_small_file_corpus.py", "--target", str(target), "--count", "5"])

    assert rc == 2
    # Refused before writing anything new.
    assert list(target.iterdir()) == [target / "leftover.txt"]


def test_main_writes_manifest(tmp_path) -> None:
    target = tmp_path / "corpus"
    manifest_out = tmp_path / "manifest.json"

    rc = gen.main([
        "make_small_file_corpus.py", "--target", str(target),
        "--count", "6", "--files-per-dir", "3",
        "--manifest-out", str(manifest_out),
    ])

    assert rc == 0
    assert manifest_out.is_file()
    import json
    data = json.loads(manifest_out.read_text(encoding="utf-8"))
    assert data["summary"]["count"] == 6
    assert len(data["files"]) == 6
