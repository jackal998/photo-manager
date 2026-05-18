"""Tests for scanner/phash_distance — the render-time Hamming helper.

This is the pure function the tree renderer calls (#253) to compute
distances against the *displayed* Ref winner without importing scanner
internals. The helper must:

  1. Return an integer Hamming distance when both phashes are valid.
  2. Return None on missing inputs (so callers fall back gracefully).
  3. Never raise — the renderer must never crash on a bad row.
"""
from __future__ import annotations

import pytest

from scanner.phash_distance import hamming_distance


class TestHammingDistanceBasics:
    def test_zero_distance_when_phashes_match(self):
        assert hamming_distance("0000000000000000", "0000000000000000") == 0

    def test_one_bit_difference(self):
        """Last hex char 0 → 1 flips a single bit; this anchors the
        #253 test fixture which uses these exact strings to drive a
        divergence-vs-anchor case in the renderer.
        """
        assert hamming_distance("0000000000000000", "0000000000000001") == 1

    def test_max_distance_for_64_bit_phash(self):
        """All-zero vs all-ones is the upper bound for 64-bit pHashes
        (16 hex chars). Pins the bit width — if a future change moves
        to a larger hash size the renderer's ``_hamming_to_pct``
        constants would need an update too.
        """
        assert hamming_distance("0000000000000000", "ffffffffffffffff") == 64


class TestHammingDistanceFallbacks:
    """Every fallback returns None so ``_file_similarity`` can drop back
    to the scanner's stored hamming_distance instead of rendering a
    placeholder. None is the contract — not 0, not 64, not raise."""

    @pytest.mark.parametrize("a,b", [
        (None, "0000000000000000"),
        ("0000000000000000", None),
        (None, None),
        ("", "0000000000000000"),
        ("0000000000000000", ""),
    ])
    def test_missing_inputs_return_none(self, a, b):
        assert hamming_distance(a, b) is None

    def test_malformed_hex_returns_none_not_raises(self):
        """A row with a corrupt phash (caused by a bad manifest write
        or hand-edit) must not crash the renderer. Caller falls back
        to the stored hamming_distance."""
        assert hamming_distance("not-hex-at-all", "0000000000000000") is None
