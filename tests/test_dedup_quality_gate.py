"""Tests for the pHash-quality (contrast) grouping gate — scanner/dedup.py.

Motivation (dedup-core research 2026-11-05, class A): on the owner's real
library two 2020 eclipse frames and an unrelated 2023 photo of two lights in
the dark all hash to ``cccc3333cccc3333``. imagehash's median split gives
every photo popcount 32, so the #516 popcount guard cannot see the degenerate
hash, and a 42-member chained group formed through it. The quality scalar
(32×32 grey std-dev, recipe "5") is what separates them: 5.67 / 5.63 / 15.36
for the class-A files against 16.0 as the gate.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scanner.dedup import HashResult, _phash_quality_ok, classify
from scanner.walker import FileRecord

# The class-A degenerate pHash, and a dHash that agrees across all three.
DEGENERATE_PHASH = "cccc3333cccc3333"
AGREEING_DHASH = "0f0f0f0f0f0f0f0f"


def _dt() -> datetime:
    return datetime(2020, 6, 21, 16, 4, 39)


def _rows(result: list) -> dict:
    """Index ManifestRows by posix source_path (Windows-safe)."""
    return {Path(r.source_path).as_posix(): r for r in result}


def _hq(
    path: str,
    sha256: str,
    phash: str | None,
    quality: float | None,
    *,
    dhash: str | None = AGREEING_DHASH,
    mean_color: str | None = "120,110,100",
    source_label: str = "jdrive",
) -> HashResult:
    return HashResult(
        record=FileRecord(
            path=Path(path), source_label=source_label, file_type="jpeg",
        ),
        sha256=sha256,
        phash=phash,
        exif_date=_dt(),
        dhash=dhash,
        mean_color=mean_color,
        pixel_width=4000,
        pixel_height=3000,
        phash_quality=quality,
        rgb32=bytes(3072),  # constant signature: format dups read as same pixels
    )


def _class_a_trio(quality_a: float | None, quality_b: float | None, quality_c: float | None):
    """Three distinct files, identical degenerate pHash, mean colours within 30."""
    return [
        _hq("/eclipse/MVIMG_160439.jpg", "sha-1", DEGENERATE_PHASH, quality_a,
            mean_color="120,110,100"),
        _hq("/eclipse/MVIMG_160730.jpg", "sha-2", DEGENERATE_PHASH, quality_b,
            mean_color="125,112,101"),
        _hq("/lights/IMG_8511.JPG", "sha-3", DEGENERATE_PHASH, quality_c,
            mean_color="118,108,99"),
    ]


class TestPhashQualityGateUnit:
    """Value-domain checks on the gate predicate itself."""

    def test_quality_below_gate_fails(self):
        assert not _phash_quality_ok(_hq("/a.jpg", "a", DEGENERATE_PHASH, 5.6), 16.0)

    def test_quality_at_gate_passes(self):
        """Boundary: the gate is ``>=``, so exactly the minimum is kept."""
        assert _phash_quality_ok(_hq("/a.jpg", "a", DEGENERATE_PHASH, 16.0), 16.0)

    def test_quality_above_gate_passes(self):
        assert _phash_quality_ok(_hq("/a.jpg", "a", DEGENERATE_PHASH, 20.0), 16.0)

    def test_none_quality_passes(self):
        """Legacy manifests / video / failure carry no quality → unknown → pass."""
        assert _phash_quality_ok(_hq("/a.jpg", "a", DEGENERATE_PHASH, None), 16.0)

    def test_zero_min_disables_gate(self):
        assert _phash_quality_ok(_hq("/a.jpg", "a", DEGENERATE_PHASH, 0.0), 0)
        assert _phash_quality_ok(_hq("/a.jpg", "a", DEGENERATE_PHASH, 0.0), 0.0)


class TestPhashQualityGateClassify:
    """The gate's effect on grouping, on the three class-A frames."""

    def test_class_a_trio_not_grouped_at_default(self):
        """Reproduces the bug fixed: the three low-contrast frames that bridged
        unrelated photos must NOT form a pHash group at the default gate."""
        rows = _rows(classify(_class_a_trio(5.67, 5.63, 15.36)))
        for path, row in rows.items():
            assert row.action in ("", "UNDATED"), (path, row.action)
            assert row.group_id is None, (path, row.group_id)

    def test_class_a_trio_groups_when_quality_unknown(self):
        """Legacy manifest (no quality): behaves as before — the trio groups."""
        rows = _rows(classify(_class_a_trio(None, None, None)))
        assert {r.group_id for r in rows.values()} != {None}
        assert any(r.action in ("EXACT", "REVIEW_DUPLICATE") for r in rows.values())

    def test_class_a_trio_groups_when_quality_above_gate(self):
        rows = _rows(classify(_class_a_trio(20.0, 20.0, 20.0)))
        assert {r.group_id for r in rows.values()} != {None}

    def test_class_a_trio_groups_when_gate_disabled(self):
        """Reproduces the bug with the gate off (min_phash_quality=0)."""
        rows = _rows(classify(_class_a_trio(5.67, 5.63, 15.36), min_phash_quality=0))
        assert {r.group_id for r in rows.values()} != {None}

    def test_near_dup_pair_with_low_quality_side_has_no_edge(self):
        """A pHash near-dup (hamming 4) where one side is low-contrast must not
        produce an edge or a REVIEW_DUPLICATE. Control: same pair at quality
        40/40 does group, so the absence is the gate's doing."""
        base = "f0f0f0f0f0f0f0f0"
        near = "f0f0f0f0f0f0f0ff"  # hamming 4 from base
        low = [
            _hq("/n/a.jpg", "na", base, 5.0, dhash=None),
            _hq("/n/b.jpg", "nb", near, 40.0, dhash=None),
        ]
        rows = _rows(classify(low))
        assert rows["/n/a.jpg"].action != "REVIEW_DUPLICATE"
        assert rows["/n/b.jpg"].action != "REVIEW_DUPLICATE"
        assert rows["/n/a.jpg"].group_id is None
        assert rows["/n/b.jpg"].group_id is None

        control = [
            _hq("/n/a.jpg", "na", base, 40.0, dhash=None),
            _hq("/n/b.jpg", "nb", near, 40.0, dhash=None),
        ]
        ctrl = _rows(classify(control))
        assert ctrl["/n/b.jpg"].action == "REVIEW_DUPLICATE"

    def test_byte_identical_pair_still_exact_at_low_quality(self):
        """The gate must never weaken exact-SHA dedup."""
        recs = [
            _hq("/a/copy1.jpg", "same", DEGENERATE_PHASH, 5.0, source_label="src_a"),
            _hq("/b/copy2.jpg", "same", DEGENERATE_PHASH, 5.0, source_label="src_b"),
        ]
        rows = _rows(classify(recs, source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/a/copy1.jpg"].action == ""
        assert rows["/b/copy2.jpg"].action == "EXACT"
        assert rows["/a/copy1.jpg"].group_id == rows["/b/copy2.jpg"].group_id

    def test_quality_is_copied_onto_manifest_row(self):
        """Catches: _make_row dropping phash_quality — the value would never be
        written to the manifest, and the next load could not re-derive it."""
        rows = _rows(classify([_hq("/q/x.jpg", "qx", "0123456789abcdef", 16.4)]))
        assert rows["/q/x.jpg"].phash_quality == 16.4
