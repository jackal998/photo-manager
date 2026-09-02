"""Tests for the measurement-only T0 pixel-signal probe (scanner/visual_t0_probe.py).

The bug these guard against is the only one that matters for a probe wired
into a production pipeline: **the probe changing what the scan produces.**
A probe that shifts a pHash, drops an EXIF date, or raises out of
``_hashes_from_data`` would silently corrupt a manifest, and the corruption
would look exactly like a real scan result.

No PIL / Qt / stdlib method is monkeypatched to force a branch. The probe is
switched the way the scan switches it — the ``PM_VISUAL_T0`` environment
variable plus a module reload, which is the real mechanism, not a stand-in
for it. The corrupt-bytes case uses genuinely truncated JPEG bytes, not a
stubbed decoder.
"""

from __future__ import annotations

import importlib
import io

import pytest
from PIL import Image

from scanner import hasher, visual_t0_probe


def _reload_probe(monkeypatch, mode: str):
    """Set PM_VISUAL_T0 and re-import the probe, as a worker process would.

    ``scanner.hasher`` holds a reference to the MODULE (not to a copied
    ``PROBE_MODE`` value), so reloading in place is enough for the hook to
    see the new mode — the same reason a spawned ProcessPoolExecutor worker
    picks the mode up from the inherited environment.
    """
    monkeypatch.setenv("PM_VISUAL_T0", mode)
    importlib.reload(visual_t0_probe)
    visual_t0_probe.reset_probe_stats()
    return visual_t0_probe


@pytest.fixture(autouse=True)
def _restore_probe_module():
    """Leave the probe OFF for every other test in the suite."""
    yield
    importlib.reload(visual_t0_probe)


@pytest.fixture
def jpeg_bytes() -> bytes:
    """A real JPEG with structure — flat colour would make several signals 0."""
    img = Image.new("RGB", (600, 400))
    pixels = img.load()
    for y in range(400):
        for x in range(600):
            pixels[x, y] = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# --- the invariant that matters: the scan's output must not move ---------


def test_probe_off_leaves_hash_output_identical(jpeg_bytes, tmp_path, monkeypatch):
    """With PM_VISUAL_T0 unset, the full 8-tuple is what it was before."""
    monkeypatch.delenv("PM_VISUAL_T0", raising=False)
    importlib.reload(visual_t0_probe)
    assert visual_t0_probe.PROBE_MODE == ""

    path = tmp_path / "off.jpg"
    path.write_bytes(jpeg_bytes)
    baseline = hasher._hashes_from_data(path, "jpg", jpeg_bytes)

    # Same call again — the OFF path must be deterministic and probe-free.
    assert hasher._hashes_from_data(path, "jpg", jpeg_bytes) == baseline
    assert visual_t0_probe.probe_stats() == {"files": 0, "failures": 0, "ns": 0}
    # sha / phash / dhash / mean_color must all be real, not None.
    assert baseline[0] and baseline[1] and baseline[2] and baseline[3]


def test_probe_lean_does_not_change_any_hash_field(jpeg_bytes, tmp_path, monkeypatch):
    """Turning the probe on must move nothing in the 8-tuple, and run 6 signals."""
    monkeypatch.delenv("PM_VISUAL_T0", raising=False)
    importlib.reload(visual_t0_probe)
    path = tmp_path / "lean.jpg"
    path.write_bytes(jpeg_bytes)
    baseline = hasher._hashes_from_data(path, "jpg", jpeg_bytes)

    probe = _reload_probe(monkeypatch, "lean")
    with_probe = hasher._hashes_from_data(path, "jpg", jpeg_bytes)

    assert with_probe == baseline, "the probe changed the hash stage's output"
    stats = probe.probe_stats()
    assert stats["files"] == 1
    assert stats["failures"] == 0
    assert stats["ns"] > 0


def test_probe_all_does_not_change_any_hash_field(jpeg_bytes, tmp_path, monkeypatch):
    monkeypatch.delenv("PM_VISUAL_T0", raising=False)
    importlib.reload(visual_t0_probe)
    path = tmp_path / "all.jpg"
    path.write_bytes(jpeg_bytes)
    baseline = hasher._hashes_from_data(path, "jpg", jpeg_bytes)

    probe = _reload_probe(monkeypatch, "all")
    assert hasher._hashes_from_data(path, "jpg", jpeg_bytes) == baseline
    assert probe.probe_stats()["files"] == 1


# --- the signal sets are the sizes the report will quote ----------------


def test_lean_computes_exactly_six_signals(jpeg_bytes, tmp_path, monkeypatch):
    probe = _reload_probe(monkeypatch, "lean")
    assert probe.compute_probe(jpeg_bytes, tmp_path / "x.jpg") == 6
    assert probe.LEAN_SIGNAL_COUNT == 6


def test_all_computes_exactly_nineteen_signals(jpeg_bytes, tmp_path, monkeypatch):
    probe = _reload_probe(monkeypatch, "all")
    count = probe.compute_probe(jpeg_bytes, tmp_path / "x.jpg")
    assert count == 19
    assert count == probe.ALL_SIGNAL_COUNT


def test_all_signal_values_are_finite(jpeg_bytes, tmp_path, monkeypatch):
    """A NaN would make the whole cost table meaningless without failing."""
    import math

    probe = _reload_probe(monkeypatch, "all")
    gray, rgb = probe.decode_working(tmp_path / "x.jpg", jpeg_bytes)
    values = probe._signals_all(gray, rgb)
    assert len(values) == 19
    bad = {k: v for k, v in values.items() if not math.isfinite(v)}
    assert not bad, f"non-finite signal values: {bad}"


def test_decode_working_honours_the_fixed_long_edge(tmp_path, monkeypatch):
    """1024 px on the long edge, and never upscaled — the study's recipe."""
    probe = _reload_probe(monkeypatch, "lean")
    big = Image.new("RGB", (4000, 3000), (120, 130, 140))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    gray, rgb = probe.decode_working(tmp_path / "big.png", buf.getvalue())
    assert max(gray.shape) == 1024
    assert rgb.shape[:2] == gray.shape

    small = Image.new("RGB", (320, 240), (10, 20, 30))
    buf2 = io.BytesIO()
    small.save(buf2, format="PNG")
    gray_s, _ = probe.decode_working(tmp_path / "small.png", buf2.getvalue())
    assert gray_s.shape == (240, 320), "a small image must not be upscaled"


# --- real failure modes -------------------------------------------------


def test_truncated_bytes_count_a_failure_and_leave_the_hash_path_intact(
    jpeg_bytes, tmp_path, monkeypatch
):
    """A truncated JPEG is the real corruption mode on a NAS scan.

    The probe must count it as a failure and stay silent; the hash stage must
    still return its normal result for those bytes (sha always, decode fields
    as PIL manages) rather than propagating the probe's exception.
    """
    monkeypatch.delenv("PM_VISUAL_T0", raising=False)
    importlib.reload(visual_t0_probe)
    truncated = jpeg_bytes[: len(jpeg_bytes) // 3]
    path = tmp_path / "truncated.jpg"
    path.write_bytes(truncated)
    baseline = hasher._hashes_from_data(path, "jpg", truncated)

    probe = _reload_probe(monkeypatch, "lean")
    with_probe = hasher._hashes_from_data(path, "jpg", truncated)

    assert with_probe == baseline, "the probe changed the corrupt-file result"
    assert with_probe[0], "sha256 must still be computed for a truncated file"
    stats = probe.probe_stats()
    assert stats["failures"] == 1
    assert stats["files"] == 0


def test_non_image_bytes_count_a_failure_not_a_crash(tmp_path, monkeypatch):
    probe = _reload_probe(monkeypatch, "lean")
    assert probe.compute_probe(b"this is not an image at all", tmp_path / "x.jpg") == 0
    assert probe.probe_stats()["failures"] == 1


def test_empty_bytes_count_a_failure(tmp_path, monkeypatch):
    probe = _reload_probe(monkeypatch, "lean")
    assert probe.compute_probe(b"", tmp_path / "x.jpg") == 0
    assert probe.probe_stats()["failures"] == 1


def test_unreadable_raw_bytes_do_not_raise(tmp_path, monkeypatch):
    """A .dng extension routes to rawpy; garbage bytes must fail closed."""
    probe = _reload_probe(monkeypatch, "lean")
    assert probe.compute_probe(b"\x00" * 4096, tmp_path / "broken.dng") == 0
    assert probe.probe_stats()["failures"] == 1


# --- the counters the parent reads off the A/B run ----------------------


def test_counters_accumulate_across_files(jpeg_bytes, tmp_path, monkeypatch):
    probe = _reload_probe(monkeypatch, "lean")
    for i in range(3):
        probe.compute_probe(jpeg_bytes, tmp_path / f"{i}.jpg")
    probe.compute_probe(b"garbage", tmp_path / "bad.jpg")
    stats = probe.probe_stats()
    assert stats["files"] == 3
    assert stats["failures"] == 1
    assert stats["ns"] > 0


def test_stats_line_carries_pid_mode_and_totals(jpeg_bytes, tmp_path, monkeypatch):
    """The A/B run reads these numbers off stderr — the shape is load-bearing."""
    import os

    probe = _reload_probe(monkeypatch, "all")
    probe.compute_probe(jpeg_bytes, tmp_path / "x.jpg")
    line = probe.format_stats_line()
    assert f"pid={os.getpid()}" in line
    assert "mode=all" in line
    assert "files=1" in line
    assert "failures=0" in line
    assert "probe_s=" in line


def test_dump_is_silent_when_the_probe_never_ran(monkeypatch, capsys):
    probe = _reload_probe(monkeypatch, "lean")
    probe.dump_probe_stats()
    assert capsys.readouterr().err == ""


def test_dump_writes_one_line_to_stderr_after_a_run(jpeg_bytes, tmp_path, monkeypatch, capsys):
    probe = _reload_probe(monkeypatch, "lean")
    probe.compute_probe(jpeg_bytes, tmp_path / "x.jpg")
    probe.dump_probe_stats()
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[visual-t0-probe]" in err
    assert "files=1" in err


def test_format_of_buckets_raw_and_pil_routes(monkeypatch):
    probe = _reload_probe(monkeypatch, "lean")
    assert probe.format_of("a.DNG") == "dng"
    assert probe.format_of("a.CR2") == "dng"
    assert probe.format_of("a.jpeg") == "jpeg"
    assert probe.format_of("a.heic") == "heic"
    assert probe.format_of("a.png") == "other"
    assert probe.format_of("a.unknown") == "other"


def test_deps_missing_disables_the_probe_without_raising(jpeg_bytes, tmp_path, monkeypatch):
    """The probe module ships outside requirements.txt (scipy / pywt).

    On a machine without them the import guard leaves ``_DEPS_OK`` False, and
    the scan must still run — the probe just counts every file as a failure.
    This asserts the fail-closed contract on the real flag rather than
    uninstalling a package.
    """
    probe = _reload_probe(monkeypatch, "lean")
    monkeypatch.setattr(probe, "_DEPS_OK", False)
    assert probe.compute_probe(jpeg_bytes, tmp_path / "x.jpg") == 0
    assert probe.probe_stats()["failures"] == 1
