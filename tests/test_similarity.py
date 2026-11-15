"""scanner/similarity.py — how alike two pictures look (rule C).

The pictures are synthetic (seeded), so every expectation below is a
property a person would state about the pair, not a number copied from a
photo set: an identical copy is 1, a re-toned copy keeps most of its credit,
a different picture keeps little, a crop is worth the share it covers.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance

from scanner.similarity import SHOWN_CAP, SIGNATURE_SIDE, Signature, ciede2000, percent, similarity


def _scene(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A textured colour picture with hard-edged blocks."""
    rng = np.random.default_rng(seed)
    base = Image.fromarray(rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)).resize(size, Image.BICUBIC)
    texture = Image.fromarray(rng.integers(0, 256, (60, 80), dtype=np.uint8)).resize(size, Image.BILINEAR)
    im = Image.blend(base, Image.merge("RGB", [texture] * 3), 0.35)
    draw = ImageDraw.Draw(im)
    for _ in range(12 if min(size) > 130 else 0):
        x, y = int(rng.integers(0, size[0] - 60)), int(rng.integers(0, size[1] - 60))
        w, h = int(rng.integers(20, 120)), int(rng.integers(20, 120))
        draw.rectangle([x, y, x + w, y + h], fill=tuple(int(v) for v in rng.integers(0, 256, 3)))
    return im


def _sim(a: Image.Image, b: Image.Image, pixel_ratio: float | None = None) -> float:
    return similarity(Signature.from_image(a), Signature.from_image(b), pixel_ratio)


class TestColourDifference:
    # Sharma, Wu and Dalal (2005), table 1: the pairs chosen for the
    # branches an implementation gets wrong (hue wrap-around, grey axis,
    # the blue rotation term) plus four large differences.
    @pytest.mark.parametrize(
        "lab1, lab2, expected",
        [
            ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
            ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
            ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
            ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
            ((50.0, 2.49, -0.001), (50.0, -2.49, 0.0009), 7.1792),
            ((50.0, 2.49, -0.001), (50.0, -2.49, 0.0011), 7.2195),
            ((50.0, -0.001, 2.49), (50.0, 0.0009, -2.49), 4.8045),
            ((50.0, -0.001, 2.49), (50.0, 0.0011, -2.49), 4.7461),
            ((50.0, 2.5, 0.0), (50.0, 0.0, -2.5), 4.3065),
            ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
            ((50.0, 2.5, 0.0), (61.0, -5.0, 29.0), 22.8977),
            ((50.0, 2.5, 0.0), (56.0, -27.0, -3.0), 31.9030),
            ((50.0, 2.5, 0.0), (58.0, 24.0, 15.0), 19.4535),
        ],
    )
    def test_matches_the_published_pairs(self, lab1, lab2, expected):
        assert float(ciede2000(np.array(lab1), np.array(lab2))) == pytest.approx(expected, abs=1e-4)


class TestWhatAPersonWouldSay:
    def test_an_identical_copy_is_one(self):
        a = _scene(1)
        assert _sim(a, a.copy()) == 1.0

    def test_a_resized_copy_shows_the_cap(self):
        a = _scene(1)
        assert percent(_sim(a, a.resize((320, 240), Image.LANCZOS)), identical=False) == SHOWN_CAP

    def test_a_change_below_what_the_eye_sees_costs_nothing(self):
        """A copy 3 % darker (a re-export, a recompression) stays under the
        colour difference a glance can tell, in every cell."""
        a = _scene(1)
        assert _sim(a, ImageEnhance.Brightness(a).enhance(0.97)) == 1.0

    def test_a_different_picture_keeps_little(self):
        assert _sim(_scene(1), _scene(2)) < 0.3

    def test_a_darker_copy_sits_between_identical_and_different(self):
        """The same view re-exposed: every edge is still in place, so it
        keeps most of its credit, and the further the tone moves the less
        it keeps."""
        a = _scene(1)
        slightly = _sim(a, ImageEnhance.Brightness(a).enhance(0.8))
        strongly = _sim(a, ImageEnhance.Brightness(a).enhance(0.6))
        different = _sim(a, _scene(2))
        assert different + 0.3 < strongly < slightly < 0.95

    def test_a_local_change_costs_about_its_area(self):
        """A block painted over 9.8 % of the picture."""
        a = _scene(1)
        b = a.copy()
        ImageDraw.Draw(b).rectangle([200, 150, 400, 300], fill=(250, 250, 250))
        assert _sim(a, b) == pytest.approx(0.90, abs=0.04)


class TestPlacement:
    def test_a_crop_is_worth_the_share_it_covers(self):
        """The middle 60 % x 60 % of a picture has the picture's own
        shape, so the full-frame overlay exists too and scores lower (0.30
        here): the answer has to come from the window search."""
        a = _scene(1)
        crop = a.crop((128, 96, 512, 384))
        assert _sim(a, crop) == pytest.approx(0.36, abs=0.02)

    def test_the_known_pixel_ratio_is_one_more_scale_to_try(self):
        """The crop is 384 of the picture's 640 px wide and was not
        resampled; telling the search so must not move the answer away."""
        a = _scene(1)
        crop = a.crop((128, 96, 512, 384))
        assert _sim(a, crop, pixel_ratio=384 / 640) == pytest.approx(0.36, abs=0.02)

    def test_the_order_of_the_two_pictures_does_not_matter(self):
        a = _scene(1)
        crop = a.crop((128, 96, 512, 384))
        assert _sim(crop, a) == pytest.approx(_sim(a, crop), abs=0.01)

    def test_a_crop_of_another_shape(self):
        """A 16:9 strip cut from a 4:3 picture covers 75 % of it."""
        a = _scene(1)
        assert _sim(a, a.crop((0, 60, 640, 420))) == pytest.approx(0.75, abs=0.02)

    def test_a_shifted_view_beats_a_different_picture(self):
        """Two frames 4 % apart sideways overlap but neither contains the
        other; the search still finds most of the shared view."""
        a = _scene(1)
        left, right = a.crop((0, 0, 614, 480)), a.crop((26, 0, 640, 480))
        assert _sim(left, right) > _sim(a, _scene(2)) + 0.4

    def test_plain_pictures_of_different_shapes(self):
        """Two blank pictures of one colour have nothing to correlate; the
        smaller still lies inside the larger and covers 75 % of it."""
        wide = Image.new("RGB", (400, 300), (40, 90, 160))
        square = Image.new("RGB", (300, 300), (40, 90, 160))
        assert _sim(wide, square) == pytest.approx(0.75, abs=0.01)

    def test_shapes_too_far_apart_to_overlay(self):
        """A wide strip and a tall strip: neither fits inside the other at
        a size that can be measured. That is 0, not an error."""
        assert _sim(_scene(7, (512, 16)), _scene(7, (16, 512))) == 0.0


class TestSignature:
    def test_a_large_picture_is_reduced_and_a_small_one_kept(self):
        assert Signature.from_image(_scene(1)).size == (SIGNATURE_SIDE, 192)
        assert Signature.from_image(_scene(6, (16, 16))).size == (16, 16)

    def test_a_small_picture_compares_with_itself(self):
        icon = _scene(6, (16, 16))
        assert _sim(icon, icon.copy()) == 1.0

    @pytest.mark.parametrize("mode", ["L", "RGBA", "P"])
    def test_other_pixel_modes_are_read_as_colour(self, mode):
        """PNG and GIF files arrive as grey, palette or alpha pictures."""
        a = _scene(1)
        assert _sim(a.convert(mode), a.convert(mode).convert("RGB")) == 1.0


class TestShownPercent:
    def test_only_identical_files_show_100(self):
        assert percent(1.0, identical=True) == 100
        assert percent(1.0, identical=False) == SHOWN_CAP
        assert percent(0.9949, identical=False) == SHOWN_CAP

    def test_everything_else_is_rounded(self):
        assert percent(0.874, identical=False) == 87
        assert percent(0.0, identical=False) == 0
