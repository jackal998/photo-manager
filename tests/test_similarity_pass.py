"""Tests for core/app_service/similarity_pass.apply_similarity_to_rows.

The pass stores, per grouped row, how alike it looks to its group's Ref. Each
test names what a user would lose if it failed: a wrong number on the review
screen, a number on the Ref itself, or a number claimed for a picture that
was never measured.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from core.app_service.similarity_pass import apply_similarity_to_rows
from scanner.dedup import HashResult, ManifestRow
from scanner.thumb_store import ThumbBytes
from scanner.thumbnail import thumbnail_from_image
from scanner.walker import FileRecord


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


def _darker(picture: Image.Image) -> Image.Image:
    return ImageEnhance.Brightness(picture).enhance(0.5)


def _thumb(picture: Image.Image) -> ThumbBytes:
    return ThumbBytes(thumbnail_from_image(picture.copy()))


def _hashed(path: str, sha: str, thumb: ThumbBytes | None) -> HashResult:
    return HashResult(
        record=FileRecord(path=Path(path), source_label="src", file_type="jpeg"),
        sha256=sha,
        phash=None,
        exif_date=None,
        thumb=thumb,
    )


def _row(
    path: str,
    action: str,
    sha: str,
    group_id: str | None = "/g/1",
    score: float | None = None,
) -> ManifestRow:
    return ManifestRow(
        # The scan writes str(record.path); the pass keys hash results the same way.
        source_path=str(Path(path)),
        source_label="src",
        action=action,
        source_hash=sha,
        phash=None,
        hamming_distance=None,
        duplicate_of=None,
        reason="",
        group_id=group_id,
        score=score,
    )


class TestMeasuredGroup:
    def test_ref_is_none_same_bytes_is_100_darker_copy_is_measured(self):
        """The group's Ref carries no number, a byte-identical copy reads 100,
        a visibly different copy reads a real percentage strictly between 30
        and 99, a video and an ungrouped picture stay blank, and the count of
        measured rows is returned."""
        ref_pic = _scene(1)
        ref = _row("/p/ref.jpg", "", "ref", score=0.9)
        dark = _row("/p/dark.jpg", "REVIEW_DUPLICATE", "dark", score=0.5)
        same = _row("/p/same.jpg", "EXACT", "ref", score=0.3)
        video = _row("/p/clip.mov", "REVIEW_DUPLICATE", "vid", score=0.2)
        lone = _row("/p/lone.jpg", "", "lone", group_id=None, score=0.8)
        hashes = [
            _hashed("/p/ref.jpg", "ref", _thumb(ref_pic)),
            _hashed("/p/dark.jpg", "dark", _thumb(_darker(ref_pic))),
            _hashed("/p/same.jpg", "ref", _thumb(ref_pic)),
            _hashed("/p/clip.mov", "vid", None),
            _hashed("/p/lone.jpg", "lone", _thumb(ref_pic)),
        ]
        rows = [ref, dark, same, video, lone]

        measured = apply_similarity_to_rows(rows, hashes)

        assert ref.similarity == 100
        assert same.similarity == 100
        assert isinstance(dark.similarity, int)
        assert 30 < dark.similarity < 99
        assert video.similarity is None
        assert lone.similarity is None
        assert measured == 2

    def test_pixel_identical_copy_with_other_bytes_is_99_never_100(self):
        """Only the same bytes earn 100. A re-saved copy whose pixels match but
        whose file hash differs must show 99: 100 would claim byte identity
        the user has not got."""
        pic = _scene(2)
        ref = _row("/p/ref.jpg", "", "ref", score=0.9)
        copy = _row("/p/copy.jpg", "REVIEW_DUPLICATE", "copy", score=0.5)
        hashes = [
            _hashed("/p/ref.jpg", "ref", _thumb(pic)),
            _hashed("/p/copy.jpg", "copy", _thumb(pic)),
        ]

        apply_similarity_to_rows([ref, copy], hashes)

        assert copy.similarity == 99

    def test_the_ref_is_the_row_pick_ref_winner_picks(self):
        """With two Ref-tier rows (undecided and KEEP) the one with the higher
        score is the Ref the review screen shows. It must carry no number; the
        other must carry one measured against it."""
        pic = _scene(3)
        other = _darker(pic)
        low = _row("/p/low.jpg", "", "low", score=0.4)
        high = _row("/p/high.jpg", "KEEP", "high", score=0.9)
        hashes = [
            _hashed("/p/low.jpg", "low", _thumb(other)),
            _hashed("/p/high.jpg", "high", _thumb(pic)),
        ]

        apply_similarity_to_rows([low, high], hashes)

        assert high.similarity == 100
        assert isinstance(low.similarity, int)
        assert 0 <= low.similarity < 100

    def test_group_whose_ref_has_no_thumbnail_measures_nobody(self):
        """A Ref with no picture to compare against gives no number to anyone
        in its group. A number then would compare against nothing."""
        pic = _scene(4)
        ref = _row("/p/ref.jpg", "", "ref", score=0.9)
        dark = _row("/p/dark.jpg", "REVIEW_DUPLICATE", "dark", score=0.5)
        hashes = [
            _hashed("/p/ref.jpg", "ref", None),
            _hashed("/p/dark.jpg", "dark", _thumb(_darker(pic))),
        ]

        measured = apply_similarity_to_rows([ref, dark], hashes)

        assert ref.similarity is None
        assert dark.similarity is None
        assert measured == 0

    def test_cancelled_scan_measures_nothing(self):
        """A cancel during the scan must stop the measuring step: nothing gets
        a number and the count is zero."""
        pic = _scene(5)
        ref = _row("/p/ref.jpg", "", "ref", score=0.9)
        dark = _row("/p/dark.jpg", "REVIEW_DUPLICATE", "dark", score=0.5)
        hashes = [
            _hashed("/p/ref.jpg", "ref", _thumb(pic)),
            _hashed("/p/dark.jpg", "dark", _thumb(_darker(pic))),
        ]

        measured = apply_similarity_to_rows([ref, dark], hashes, cancelled=lambda: True)

        assert dark.similarity is None
        assert measured == 0
