"""The GROUPING 11 structure gate on 128x128 grey signatures.

``_grey_identical`` passes when (a) at most GREY_TAIL_BUDGET (16) pixels differ
by more than GREY_TAIL_MAX (8) and (b) every 4x4 block's mean |diff| is at most
GREY_BLOCK_MAX (4.0). Every fixture starts from a flat grey of 100, so the
expected block means and tail counts are computed by hand in each comment.
"""

from __future__ import annotations

from pathlib import Path

from scanner.dedup import HashResult, _grey_identical, _pixels_identical
from scanner.grey_store import GREY_SIDE, GREY_SIG_BYTES, GreyBytes
from scanner.walker import FileRecord

BASE = bytes([100]) * GREY_SIG_BYTES


def _grey(delta_at) -> bytes:
    """Flat 100 plus ``delta_at(x, y)`` per pixel; x is the column, y the row."""
    return bytes(100 + delta_at(i % GREY_SIDE, i // GREY_SIDE) for i in range(GREY_SIG_BYTES))


def _block_patch(value: int, blocks: int, cols: int = 32) -> bytes:
    """One pixel at +value in each of the first ``blocks`` 4x4 blocks (row-major)."""
    spots = {((b % cols) * 4, (b // cols) * 4) for b in range(blocks)}
    return _grey(lambda x, y: value if (x, y) in spots else 0)


class TestGreyIdentical:
    def test_noise_of_zero_or_one_is_identical(self):
        noisy = _grey(lambda x, y: (x + y) % 2)
        assert _grey_identical(BASE, noisy) is True

    def test_12x12_patch_at_plus_60_is_not_identical(self):
        # 144 pixels over 8: fails the tail test and the block test.
        patch = _grey(lambda x, y: 60 if x < 12 and y < 12 else 0)
        assert _grey_identical(BASE, patch) is False

    def test_aligned_8x8_patch_at_plus_6_fails_the_block_test_only(self):
        # No pixel exceeds 8 (tail count 0), but each of the four 4x4 blocks
        # averages 6 > 4, so the block test refuses it.
        patch = _grey(lambda x, y: 6 if x < 8 and y < 8 else 0)
        assert _grey_identical(BASE, patch) is False

    def test_33_scattered_pixels_at_plus_60_fail_the_tail_test_only(self):
        # One pixel per block in 33 blocks: each block averages 60 / 16 = 3.75,
        # which passes the block test; 33 pixels over 8 exceed the budget of 16.
        assert _grey_identical(BASE, _block_patch(60, blocks=33)) is False


class TestGreyBoundaries:
    def test_block_mean_exactly_4_0_is_identical(self):
        # One aligned 4x4 block at +4: sum 64 / 16 = 4.0 <= 4.0.
        one_block = _grey(lambda x, y: 4 if x < 4 and y < 4 else 0)
        assert _grey_identical(BASE, one_block) is True

    def test_block_mean_4_0625_is_not_identical(self):
        # The same block with one pixel at +5: sum 65 / 16 = 4.0625 > 4.0.
        one_block = _grey(lambda x, y: 5 if (x, y) == (0, 0) else 4 if x < 4 and y < 4 else 0)
        assert _grey_identical(BASE, one_block) is False

    def test_16_scattered_pixels_at_plus_60_are_identical(self):
        # 16 pixels in 16 different blocks: budget is 16 (count > 16 fails).
        assert _grey_identical(BASE, _block_patch(60, blocks=16)) is True

    def test_17_scattered_pixels_at_plus_60_are_not_identical(self):
        assert _grey_identical(BASE, _block_patch(60, blocks=17)) is False

    def test_pixels_at_exactly_8_do_not_count_toward_the_tail(self):
        # d = 8 is not "> 8": 17 pixels at +8 leave the tail count at zero.
        assert _grey_identical(BASE, _block_patch(8, blocks=17)) is True

    def test_17_pixels_at_plus_9_are_not_identical(self):
        # d = 9 > 8: 17 such pixels exceed the budget of 16.
        assert _grey_identical(BASE, _block_patch(9, blocks=17)) is False


class _Broken:
    """A grey signature whose store read fails: the verdict must be unknown."""

    def bytes(self) -> bytes:
        raise OSError("store read failed")


def _hr(grey: bytes | None, rgb32: bytes | None = bytes(3072),
        width: int = 4000, height: int = 3000, broken: bool = False) -> HashResult:
    if broken:
        signature = _Broken()
    else:
        signature = None if grey is None else GreyBytes(grey)
    return HashResult(
        record=FileRecord(path=Path("/a/x.jpg"), source_label="s", file_type="jpeg"),
        sha256="x", phash="a", exif_date=None,
        rgb32=rgb32, pixel_width=width, pixel_height=height,
        grey128=signature,
    )


class TestPixelsIdentical:
    def test_missing_grey_on_either_side_is_unknown(self):
        assert _pixels_identical(_hr(None), _hr(BASE)) is None
        assert _pixels_identical(_hr(BASE), _hr(None)) is None

    def test_missing_rgb32_is_unknown(self):
        assert _pixels_identical(_hr(BASE, rgb32=None), _hr(BASE)) is None

    def test_unreadable_grey_store_is_unknown_never_exact(self):
        assert _pixels_identical(_hr(BASE, broken=True), _hr(BASE)) is None

    def test_different_dimensions_are_not_identical(self):
        assert _pixels_identical(_hr(BASE), _hr(BASE, width=3999)) is False

    def test_a_90_degree_swap_with_equal_signatures_is_identical(self):
        assert _pixels_identical(_hr(BASE), _hr(BASE, width=3000, height=4000)) is True

    def test_channel_shift_above_four_is_not_identical_even_with_equal_grey(self):
        # R +10 on every pixel: channel shift 10 > 4.
        shifted_rgb = bytes(10 if i % 3 == 0 else 0 for i in range(3072))
        assert _pixels_identical(_hr(BASE), _hr(BASE, rgb32=shifted_rgb)) is False
