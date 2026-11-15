"""How alike two pictures look to a person, as a number from 0 to 1.

The measure works on a small copy of each picture (a :class:`Signature`,
256 px on the long side), so it never needs the original file:

1. **Glance.** Both pictures are reduced to 64 px. A glance cell whose
   colour difference (CIEDE2000) is at most :data:`SAME_COLOUR` reads as
   the same and gets full credit.
2. **A visibly different cell** gets partial credit = *structure* x *tone*.
   Structure is the SSIM structure term (Wang et al. 2004) of the lightness
   inside the cell, 4x4 px of the 256 px picture: are the edges and textures
   still in the same place? Tone falls in proportion to the colour
   difference and reaches zero at :data:`OPPOSITE_COLOUR`. So the same view
   re-toned or re-exposed keeps most of its credit, and a different thing in
   the same place keeps little.
3. **Placement.** One picture may be a crop of the other or slightly
   shifted. The measure is taken with the pictures laid over each other
   full-frame (when their shapes agree) and at the best cross-correlation
   window in each direction; the credit is scaled by the share of the wider
   picture the overlap covers, and the highest result is the answer.

:func:`percent` turns the number into the label a row shows: 100 only for
byte-identical files, at most :data:`SHOWN_CAP` for everything else.

Constants are the published ones (CIEDE2000 weights 1:1:1, SSIM K = 0.03 on
a 0-100 lightness scale); none is fitted to a photo set.
"""
from __future__ import annotations

from functools import cached_property

import numpy as np
from PIL import Image

SIGNATURE_SIDE = 256
GLANCE_SIDE = 64
CELL = SIGNATURE_SIDE // GLANCE_SIDE
SAME_COLOUR = 10.0
OPPOSITE_COLOUR = 50.0
SHOWN_CAP = 99

_STRUCTURE_C3 = (0.03 * 100) ** 2 / 2
_FULL_FRAME = (0.0, 0.0, 1.0, 1.0)
_SEARCH_SIDE, _REFINE_SIDE = 128, 256
_SCALE_STEPS, _REFINE_STEPS = 31, 9
# The score of a one-colour picture in the window search: below every real
# normalised cross-correlation, which lies in (-1, 1].
_FEATURELESS = -1.0
# A full-frame result at or above this already shows SHOWN_CAP; no other
# placement can show more, so the window search is skipped.
_CAP_VALUE = (SHOWN_CAP - 0.5) / 100

_CHANNEL = np.arange(256) / 255.0
_LINEAR = np.where(_CHANNEL <= 0.04045, _CHANNEL / 12.92, ((_CHANNEL + 0.055) / 1.055) ** 2.4)
_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_WHITE = np.array([0.95047, 1.0, 1.08883])

Box = tuple[float, float, float, float]


def _lab_f(t: np.ndarray) -> np.ndarray:
    return np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 (..., 3) -> CIELAB (D65)."""
    f = _lab_f(_LINEAR[rgb] @ _XYZ.T / _WHITE)
    return np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )


def _lightness(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 (..., 3) -> CIE L* (0-100)."""
    return 116 * _lab_f(_LINEAR[rgb] @ _XYZ[1]) - 16


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 colour difference (Sharma, Wu and Dalal 2005), kL = kC = kH = 1."""
    l1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    l2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    c_mean = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c_mean ** 7 / (c_mean ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1, c2 = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1 = np.degrees(np.arctan2(b1, a1p)) % 360
    h2 = np.degrees(np.arctan2(b2, a2p)) % 360
    grey = c1 * c2 == 0
    dh = h2 - h1
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(grey, 0.0, dh)
    d_hue = 2 * np.sqrt(c1 * c2) * np.sin(np.radians(dh) / 2)
    l_mean, c_meanp = (l1 + l2) / 2, (c1 + c2) / 2
    h_sum = h1 + h2
    h_mean = np.where(np.abs(h1 - h2) > 180, np.where(h_sum < 360, h_sum + 360, h_sum - 360), h_sum) / 2
    h_mean = np.where(grey, h_sum, h_mean)
    t = (
        1
        - 0.17 * np.cos(np.radians(h_mean - 30))
        + 0.24 * np.cos(np.radians(2 * h_mean))
        + 0.32 * np.cos(np.radians(3 * h_mean + 6))
        - 0.20 * np.cos(np.radians(4 * h_mean - 63))
    )
    s_l = 1 + 0.015 * (l_mean - 50) ** 2 / np.sqrt(20 + (l_mean - 50) ** 2)
    s_c = 1 + 0.045 * c_meanp
    s_h = 1 + 0.015 * c_meanp * t
    r_t = -2 * np.sqrt(c_meanp ** 7 / (c_meanp ** 7 + 25.0 ** 7)) * np.sin(
        np.radians(60 * np.exp(-(((h_mean - 275) / 25) ** 2)))
    )
    t_l, t_c, t_h = (l2 - l1) / s_l, (c2 - c1) / s_c, d_hue / s_h
    return np.sqrt(np.maximum(0.0, t_l ** 2 + t_c ** 2 + t_h ** 2 + r_t * t_c * t_h))


def _fit(size: tuple[int, int], long_side: int) -> tuple[int, int]:
    scale = long_side / max(size)
    return max(8, round(size[0] * scale)), max(8, round(size[1] * scale))


class Signature:
    """The small copy of one picture that the measure reads.

    Build it with :meth:`from_image` from any upright RGB picture at least
    :data:`SIGNATURE_SIDE` px on its long side (a thumbnail is enough);
    smaller pictures are kept at their own size.
    """

    def __init__(self, colour: Image.Image) -> None:
        self.size = colour.size
        self._colour = colour
        self._planes: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}

    @cached_property
    def _light(self) -> Image.Image:
        """Lightness (CIE L* x 2.55). Built on first use: a pair that reads
        the same in every glance cell never asks for it."""
        lightness = _lightness(np.asarray(self._colour))
        return Image.fromarray(np.clip(np.rint(lightness * 2.55), 0, 255).astype(np.uint8))

    @cached_property
    def _grey(self) -> Image.Image:
        """Grey picture for the placement search. Built on first use."""
        return self._colour.convert("L")

    @classmethod
    def from_image(cls, image: Image.Image) -> Signature:
        rgb = image.convert("RGB")
        if max(rgb.size) > SIGNATURE_SIDE:
            rgb = rgb.resize(_fit(rgb.size, SIGNATURE_SIDE), Image.BOX)
        return cls(rgb)

    def _plane(self, long_side: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Grey picture at ``long_side`` with its FFT and the integral
        images of its values and squares, computed once per size."""
        if long_side not in self._planes:
            img = np.asarray(self._grey.resize(_fit(self.size, long_side), Image.BOX), dtype=np.float64)
            sums = np.pad(img.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
            squares = np.pad((img * img).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
            self._planes[long_side] = (img, np.fft.rfft2(img), sums, squares)
        return self._planes[long_side]


def _window_sum(integral: np.ndarray, h: int, w: int) -> np.ndarray:
    return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]


def _search(cont: Signature, inner: Signature, long_side: int, scales: list[float]) -> tuple[float, float, Box] | None:
    """Best normalised cross-correlation of ``inner``, at each width
    ``scale`` x the width of ``cont``, over every offset inside ``cont``.
    Returns (score, scale, box as fractions of cont).

    A featureless ``inner`` (one flat colour) correlates with nothing, so
    its first, widest fit is returned at the origin with the score
    :data:`_FEATURELESS` and the other scales are not tried.
    """
    img, img_fft, sums, squares = cont._plane(long_side)
    h, w = img.shape
    aspect = inner.size[1] / inner.size[0]
    best: tuple[float, float, Box] | None = None
    for scale in scales:
        tw = round(scale * w)
        th = round(tw * aspect)
        if th == h + 1:
            th = h
        if tw < 8 or th < 8 or tw > w or th > h:
            continue
        tpl = np.asarray(inner._grey.resize((tw, th), Image.BOX), dtype=np.float64)
        centred = tpl - tpl.mean()
        norm = np.sqrt((centred * centred).sum())
        if norm < 1e-6:
            return best or (_FEATURELESS, scale, (0.0, 0.0, tw / w, th / h))
        corr = np.fft.irfft2(img_fft * np.conj(np.fft.rfft2(centred, s=img.shape)), s=img.shape)
        corr = corr[: h - th + 1, : w - tw + 1]
        s1, s2 = _window_sum(sums, th, tw), _window_sum(squares, th, tw)
        ncc = corr / (np.sqrt(np.maximum(s2 - s1 * s1 / tpl.size, float(tpl.size))) * norm)
        y, x = (int(v) for v in np.unravel_index(np.argmax(ncc), ncc.shape))
        score = float(ncc[y, x])
        if best is None or score > best[0]:
            best = (score, scale, (x / w, y / h, tw / w, th / h))
    return best


def _placements(cont: Signature, inner: Signature) -> list[Box]:
    """Where ``inner`` may sit inside ``cont``: the widest fit (full frame
    when the shapes agree) and the best cross-correlation window."""
    cw, ch = cont.size
    aspect = inner.size[1] / inner.size[0]
    widest = min(1.0, (ch / cw) / aspect)
    out: list[Box] = []
    if abs(aspect - ch / cw) / aspect < 0.005:
        out.append(_FULL_FRAME)
    else:
        fit = _search(cont, inner, _REFINE_SIDE, [widest])
        if fit is not None:
            out.append(fit[2])
    scales = [float(s) for s in widest * np.linspace(1.0, 0.5, _SCALE_STEPS)]
    coarse = _search(cont, inner, _SEARCH_SIDE, scales)
    if coarse is None or coarse[0] <= _FEATURELESS:
        return out  # nothing fits, or the picture has no feature to place
    step = widest * 0.5 / (_SCALE_STEPS - 1)
    fine = _search(
        cont, inner, _REFINE_SIDE,
        [min(widest, coarse[1] + float(d)) for d in np.linspace(-step, step, _REFINE_STEPS)],
    )
    window = (fine or coarse)[2]
    if all(max(abs(p - q) for p, q in zip(window, seen, strict=True)) > 0.01 for seen in out):
        out.append(window)
    return out


def _cells(x: np.ndarray) -> np.ndarray:
    h, w = x.shape[0] // CELL, x.shape[1] // CELL
    return x.reshape(h, CELL, w, CELL).transpose(0, 2, 1, 3).reshape(h, w, CELL * CELL)


def _credit(colour: np.ndarray, light_a: np.ndarray, light_b: np.ndarray) -> float:
    """Mean credit over the glance cells (steps 1 and 2 of the module doc),
    given each cell's colour difference and the two lightness pictures."""
    ca, cb = _cells(light_a), _cells(light_b)
    da, db = ca - ca.mean(-1, keepdims=True), cb - cb.mean(-1, keepdims=True)
    structure = ((da * db).mean(-1) + _STRUCTURE_C3) / (
        np.sqrt((da ** 2).mean(-1) * (db ** 2).mean(-1)) + _STRUCTURE_C3
    )
    tone = np.clip(1 - colour / OPPOSITE_COLOUR, 0.0, 1.0)
    return float(np.where(colour <= SAME_COLOUR, 1.0, np.clip(structure, 0.0, 1.0) * tone).mean())


def _measure(cont: Signature, inner: Signature, box: Box) -> float:
    """Credit of ``inner`` laid over ``box`` of ``cont``, times coverage.

    Most pairs of a duplicate group read the same in every cell. Those are
    answered from the glance alone: the lightness pictures are only built
    when some cell differs visibly, and two identical glances skip the
    colour difference too. A picture smaller than the glance is measured at
    its own size; enlarging it would repeat the same cells.
    """
    x0, y0, w, h = box
    coverage = min(1.0, w * h)
    glance = _fit(inner.size, min(GLANCE_SIDE, max(inner.size)))
    fine = (glance[0] * CELL, glance[1] * CELL)
    cw, ch = cont.size
    region = None if box == _FULL_FRAME else (x0 * cw, y0 * ch, (x0 + w) * cw, (y0 + h) * ch)

    glance_a = np.asarray(cont._colour.resize(glance, Image.BOX, box=region))
    glance_b = np.asarray(inner._colour.resize(glance, Image.BOX))
    if np.array_equal(glance_a, glance_b):
        return coverage
    colour = ciede2000(_lab(glance_a), _lab(glance_b))
    if not (colour > SAME_COLOUR).any():
        return coverage

    def light(sig: Signature, crop: tuple[float, float, float, float] | None) -> np.ndarray:
        picture = sig._light
        if crop is not None or picture.size != fine:
            picture = picture.resize(fine, Image.BILINEAR, box=crop)
        return np.asarray(picture, dtype=np.float64) / 2.55

    return _credit(colour, light(cont, region), light(inner, None)) * coverage


def _same_shape(a: Signature, b: Signature) -> bool:
    return abs(a.size[1] / a.size[0] - b.size[1] / b.size[0]) / (b.size[1] / b.size[0]) < 0.005


def similarity(a: Signature, b: Signature) -> float:
    """How alike ``a`` and ``b`` look, 0 (nothing in common) to 1."""
    results: list[float] = []
    full_frame_done = _same_shape(a, b)
    if full_frame_done:
        results.append(_measure(a, b, _FULL_FRAME))
        if results[0] >= _CAP_VALUE:
            return results[0]
    for cont, inner in ((a, b), (b, a)):
        for box in _placements(cont, inner):
            if box == _FULL_FRAME:
                if full_frame_done:
                    continue  # the full-frame placement is the same both ways
                full_frame_done = True
            results.append(_measure(cont, inner, box))
    # No placement at all: the shapes are so far apart that neither picture
    # fits inside the other at a measurable size.
    return max(results, default=0.0)


def percent(value: float, identical: bool) -> int:
    """The label for a similarity ``value``: 100 only for byte-identical
    files, otherwise at most :data:`SHOWN_CAP`."""
    return 100 if identical else min(SHOWN_CAP, round(100 * value))
