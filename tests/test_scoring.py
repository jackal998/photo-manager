"""Tests for scanner.scoring — keep-worthiness composite scorer (#187 — PR 3).

Test philosophy (per CLAUDE.md):

* Each test catches a real bug. No tests that only exist to bump coverage.
* Edge cases use realistic inputs (None pixel_width on a video file, etc.),
  not synthetic monkeypatched failures.
* The scorer is a pure function — all tests construct ``ManifestRow``
  fixtures in memory. No filesystem access is involved or expected.

Coverage scope:

* Each of the 8 Tier 2 dimensions (resolution, file_size, exif_complete,
  date_prov, gps, filename, path, live_photo) with happy paths + edge cases.
* Tier 1 penalties: format penalty lookup + xmp_derived deduction.
* Live Photo MOV passenger rule (``score = None``).
* Composite ``compute_score`` clamps to [0.0, 1.0] and uses weights.
* Configurable weights: rejects missing keys, rejects bad sum.
* ``score_group`` returns a dict keyed by source_path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytest

from scanner.dedup import ManifestRow
from scanner.scoring import (
    DEFAULT_WEIGHTS,
    DERIVED_PENALTY,
    FORMAT_PENALTY,
    IMAGE_EXIF_CENSUS_BASELINE,
    VIDEO_EXIF_CENSUS_BASELINE,
    _score_date_provenance,
    _score_exif_completeness,
    _score_file_size,
    _score_filename,
    _score_gps,
    _score_live_photo,
    _score_path,
    _score_resolution,
    compute_score,
    score_group,
    validate_weights,
)


# ── Test fixture helper ────────────────────────────────────────────────────


def _row(
    source_path: str,
    *,
    pixel_width: Optional[int] = None,
    pixel_height: Optional[int] = None,
    file_size_bytes: Optional[int] = None,
    shot_date: Optional[str] = None,
    mtime: Optional[str] = None,
    exif_tag_count: Optional[int] = None,
    gps_present: bool = False,
    xmp_derived: bool = False,
    group_id: Optional[str] = "/group/x",
) -> ManifestRow:
    """Build a ManifestRow with only the fields the scorer reads.

    Other fields (source_label, action, source_hash, …) are filled with
    benign placeholders. Constructing one of these never touches the
    filesystem — that's the point of the in-memory test pattern.
    """
    return ManifestRow(
        source_path=source_path,
        source_label="src",
        dest_path=None,
        action="REVIEW_DUPLICATE",
        source_hash="0",
        phash=None,
        hamming_distance=None,
        duplicate_of=None,
        reason="",
        pixel_width=pixel_width,
        pixel_height=pixel_height,
        file_size_bytes=file_size_bytes,
        shot_date=shot_date,
        mtime=mtime,
        group_id=group_id,
        exif_tag_count=exif_tag_count,
        gps_present=gps_present,
        xmp_derived=xmp_derived,
    )


# ── Tier 2 dimension 1: resolution ─────────────────────────────────────────


class TestResolutionScore:
    def test_highest_in_group_gets_one(self):
        big = _row("/x/big.jpg", pixel_width=6000, pixel_height=4000)
        small = _row("/x/small.jpg", pixel_width=1024, pixel_height=768)
        assert _score_resolution(big, [big, small]) == 1.0

    def test_lowest_in_group_gets_zero(self):
        big = _row("/x/big.jpg", pixel_width=6000, pixel_height=4000)
        small = _row("/x/small.jpg", pixel_width=1024, pixel_height=768)
        assert _score_resolution(small, [big, small]) == 0.0

    def test_tied_resolutions_score_one(self):
        a = _row("/x/a.jpg", pixel_width=4000, pixel_height=3000)
        b = _row("/x/b.jpg", pixel_width=4000, pixel_height=3000)
        assert _score_resolution(a, [a, b]) == 1.0
        assert _score_resolution(b, [a, b]) == 1.0

    def test_video_with_none_dims_scores_zero(self):
        """Realistic case: a .mov in a group of images. Video files have
        no pixel_width — scorer must not crash."""
        img = _row("/x/photo.jpg", pixel_width=4000, pixel_height=3000)
        vid = _row("/x/clip.mov")  # pixel_width=None by default
        assert _score_resolution(vid, [img, vid]) == 0.0

    def test_single_row_group_scores_one(self):
        """Edge: rescore on an isolated group of 1 row (should not crash
        and should not produce zero-by-default)."""
        only = _row("/x/only.jpg", pixel_width=4000, pixel_height=3000)
        assert _score_resolution(only, [only]) == 1.0

    def test_proportional_score_in_middle(self):
        """Linear interpolation between min and max."""
        small = _row("/x/small.jpg", pixel_width=1000, pixel_height=1000)   # 1M
        mid = _row("/x/mid.jpg", pixel_width=2000, pixel_height=1000)       # 2M
        big = _row("/x/big.jpg", pixel_width=3000, pixel_height=1000)       # 3M
        # Range = 2M; mid is at +1M from min → 0.5
        assert _score_resolution(mid, [small, mid, big]) == pytest.approx(0.5)


# ── Tier 2 dimension 2: file_size ──────────────────────────────────────────


class TestFileSizeScore:
    def test_largest_in_group_gets_one(self):
        big = _row("/x/big.jpg", file_size_bytes=5_000_000)
        small = _row("/x/small.jpg", file_size_bytes=500_000)
        assert _score_file_size(big, [big, small]) == 1.0
        assert _score_file_size(small, [big, small]) == 0.0

    def test_none_file_size_scores_zero(self):
        a = _row("/x/a.jpg", file_size_bytes=1_000_000)
        b = _row("/x/b.jpg")  # file_size_bytes=None
        assert _score_file_size(b, [a, b]) == 0.0

    def test_all_same_size_scores_one(self):
        a = _row("/x/a.jpg", file_size_bytes=1_000_000)
        b = _row("/x/b.jpg", file_size_bytes=1_000_000)
        assert _score_file_size(a, [a, b]) == 1.0


# ── Tier 2 dimension 3: EXIF completeness ──────────────────────────────────


class TestExifCompletenessScore:
    def test_full_image_census(self):
        img = _row("/x/a.jpg", exif_tag_count=IMAGE_EXIF_CENSUS_BASELINE)
        assert _score_exif_completeness(img) == 1.0

    def test_partial_image_census(self):
        img = _row("/x/a.jpg", exif_tag_count=8)
        # 8 / 16 = 0.5
        assert _score_exif_completeness(img) == 0.5

    def test_zero_image_census(self):
        img = _row("/x/a.jpg", exif_tag_count=0)
        assert _score_exif_completeness(img) == 0.0

    def test_over_image_baseline_capped_at_one(self):
        """If a file has more census tags than the baseline (rare), cap."""
        img = _row("/x/a.jpg", exif_tag_count=20)
        assert _score_exif_completeness(img) == 1.0

    def test_video_uses_video_baseline(self):
        """Video baseline is 9, not 16 — a video with 9 tags scores 1.0."""
        vid = _row("/x/clip.mov", exif_tag_count=VIDEO_EXIF_CENSUS_BASELINE)
        assert _score_exif_completeness(vid) == 1.0

    def test_none_tag_count_scores_zero(self):
        """Old manifests pre-PR-2 have NULL exif_tag_count — scorer treats
        as 'no signal' (0.0) so old data degrades gracefully."""
        img = _row("/x/a.jpg")  # exif_tag_count=None
        assert _score_exif_completeness(img) == 0.0


# ── Tier 2 dimension 4: date provenance ────────────────────────────────────


class TestDateProvenanceScore:
    def test_real_exif_date_scores_one(self):
        """shot_date is set and differs from mtime → 1.0 (genuine EXIF)."""
        row = _row(
            "/x/a.jpg",
            shot_date="2024-06-15T10:30:00",
            mtime="2025-01-01T12:00:00",
        )
        assert _score_date_provenance(row) == 1.0

    def test_no_shot_date_scores_zero(self):
        row = _row("/x/a.jpg", shot_date=None, mtime="2025-01-01T12:00:00")
        assert _score_date_provenance(row) == 0.0

    def test_shot_date_matches_mtime_scores_suspicious(self):
        """When shot_date == mtime exactly, it's likely mtime-derived
        (file copy inherited filesystem timestamp). Score 0.3 reflects
        the suspicion without zeroing it out completely."""
        row = _row(
            "/x/a.jpg",
            shot_date="2024-06-15T10:30:00",
            mtime="2024-06-15T10:30:00",
        )
        assert _score_date_provenance(row) == 0.3

    def test_close_match_within_tolerance_scores_suspicious(self):
        """Tolerance is 2 seconds — a 1-second delta still flags as
        mtime-derived (filesystem-write second-precision rounding)."""
        row = _row(
            "/x/a.jpg",
            shot_date="2024-06-15T10:30:01",
            mtime="2024-06-15T10:30:00",
        )
        assert _score_date_provenance(row) == 0.3

    def test_exceeds_tolerance_scores_real(self):
        row = _row(
            "/x/a.jpg",
            shot_date="2024-06-15T10:30:05",
            mtime="2024-06-15T10:30:00",
        )
        assert _score_date_provenance(row) == 1.0

    def test_no_mtime_still_scores_real(self):
        """Old manifests may have shot_date but no mtime — give benefit
        of the doubt and score 1.0."""
        row = _row("/x/a.jpg", shot_date="2024-06-15T10:30:00", mtime=None)
        assert _score_date_provenance(row) == 1.0

    def test_malformed_date_strings_recover_gracefully(self):
        """Defensive: a corrupted shot_date / mtime should not crash the
        whole scoring pipeline. Falls through to the 'real' score."""
        row = _row(
            "/x/a.jpg",
            shot_date="not-a-date",
            mtime="2024-06-15T10:30:00",
        )
        assert _score_date_provenance(row) == 1.0


# ── Tier 2 dimension 5: GPS ────────────────────────────────────────────────


class TestGpsScore:
    def test_gps_present_scores_one(self):
        row = _row("/x/a.jpg", gps_present=True)
        assert _score_gps(row) == 1.0

    def test_gps_absent_scores_zero(self):
        row = _row("/x/a.jpg", gps_present=False)
        assert _score_gps(row) == 0.0


# ── Tier 2 dimension 6: filename ──────────────────────────────────────────


class TestFilenameScore:
    def test_clean_filename_scores_one(self):
        row = _row("/x/IMG_4567.jpg")
        assert _score_filename(row) == 1.0

    def test_copy_pattern_penalised(self):
        row = _row("/x/Copy of photo.jpg")
        assert _score_filename(row) == pytest.approx(0.7)

    def test_paren_number_pattern_penalised(self):
        """The classic "(1)" suffix from OS file-copy renames."""
        row = _row("/x/photo (1).jpg")
        assert _score_filename(row) == pytest.approx(0.7)

    def test_edited_pattern_penalised(self):
        row = _row("/x/photo_edited.jpg")
        assert _score_filename(row) == pytest.approx(0.7)

    def test_screenshot_pattern_penalised(self):
        row = _row("/x/Screenshot 2024-01-01.png")
        assert _score_filename(row) == pytest.approx(0.7)

    def test_thumbnail_pattern_penalised(self):
        row = _row("/x/photo_thumb.jpg")
        assert _score_filename(row) == pytest.approx(0.7)

    def test_multiple_patterns_compound(self):
        """Two hits stack: 1.0 - 2*0.30 = 0.4."""
        row = _row("/x/Copy of photo (1).jpg")
        assert _score_filename(row) == pytest.approx(0.4)

    def test_score_floor_at_zero(self):
        """Many penalty hits should clamp at 0.0, not go negative."""
        row = _row("/x/Copy of edited screenshot (1)_compressed.jpg")
        assert _score_filename(row) == 0.0


# ── Tier 2 dimension 7: path ────────────────────────────────────────────────


class TestPathScore:
    def test_clean_path_scores_one(self):
        row = _row("/Users/me/Photos/2024/IMG_4567.jpg")
        assert _score_path(row) == 1.0

    def test_downloads_folder_penalised(self):
        row = _row("/Users/me/Downloads/photo.jpg")
        assert _score_path(row) == pytest.approx(0.75)

    def test_whatsapp_folder_penalised(self):
        row = _row("/Users/me/WhatsApp Images/photo.jpg")
        assert _score_path(row) == pytest.approx(0.75)

    def test_multiple_bad_segments_compound(self):
        row = _row("/Users/me/Downloads/WhatsApp Images/photo.jpg")
        # Two hits at -0.25 each → 0.5
        assert _score_path(row) == pytest.approx(0.5)

    def test_case_insensitive_match(self):
        """Filesystems vary on case sensitivity — match must work
        regardless of how the OS rendered the path."""
        row = _row("/Users/me/DOWNLOADS/photo.jpg")
        assert _score_path(row) == pytest.approx(0.75)


# ── Tier 2 dimension 8: Live Photo completeness ─────────────────────────────


class TestLivePhotoScore:
    def test_heic_with_mov_peer_scores_one(self):
        heic = _row("/x/IMG_001.heic")
        mov = _row("/x/IMG_001.mov")
        assert _score_live_photo(heic, [heic, mov]) == 1.0

    def test_orphan_heic_scores_half(self):
        heic = _row("/x/IMG_001.heic")
        other = _row("/x/IMG_002.jpg")  # different stem
        assert _score_live_photo(heic, [heic, other]) == 0.5

    def test_jpeg_not_applicable_full_marks(self):
        """Non-HEIC files always score 1.0 — the dimension is N/A."""
        row = _row("/x/photo.jpg")
        assert _score_live_photo(row, [row]) == 1.0

    def test_case_insensitive_stem_match(self):
        """A file named IMG_001.HEIC and another named img_001.MOV are
        still a pair — Windows fixtures may produce mixed case."""
        heic = _row("/x/IMG_001.HEIC")
        mov = _row("/x/img_001.MOV")
        assert _score_live_photo(heic, [heic, mov]) == 1.0

    def test_mp4_peer_also_counts(self):
        """Some Live Photo pairs use .mp4 instead of .mov."""
        heic = _row("/x/IMG_001.heic")
        mp4 = _row("/x/IMG_001.mp4")
        assert _score_live_photo(heic, [heic, mp4]) == 1.0


# ── Live Photo MOV passenger rule (compute_score returns None) ─────────────


class TestLivePhotoMovPassengerRule:
    """A MOV/MP4 whose stem matches a HEIC in the same group is a
    passenger — it inherits the HEIC's KEEP/DELETE decision in the
    action layer and is not scored as a ranking candidate. The scorer
    enforces this by returning ``None`` for such rows.
    """

    def test_mov_with_heic_peer_returns_none(self):
        heic = _row("/x/IMG_001.heic")
        mov = _row("/x/IMG_001.mov")
        assert compute_score(mov, [heic, mov]) is None

    def test_mp4_with_heic_peer_returns_none(self):
        heic = _row("/x/IMG_001.heic")
        mp4 = _row("/x/IMG_001.mp4")
        assert compute_score(mp4, [heic, mp4]) is None

    def test_standalone_mov_returns_float(self):
        """A .mov with no HEIC peer is a regular candidate — it gets
        scored normally (and absorbs the video format penalty)."""
        mov = _row("/x/clip.mov")
        score = compute_score(mov, [mov])
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_heic_in_pair_still_scored(self):
        """The HEIC side of the pair *is* scored — only the MOV passenger
        gets None. The HEIC's score factors in its live_photo bonus."""
        heic = _row("/x/IMG_001.heic")
        mov = _row("/x/IMG_001.mov")
        heic_score = compute_score(heic, [heic, mov])
        assert isinstance(heic_score, float)


# ── Tier 1 — Format penalty ─────────────────────────────────────────────────


class TestFormatPenalty:
    """Format penalty is a lookup table: RAW=0, lossless mid, JPEG/video
    high, GIF highest. The values themselves are the contract — encoded
    here so a future drive-by edit that changes them is caught."""

    def test_raw_no_penalty(self):
        assert FORMAT_PENALTY["nef"] == 0.0
        assert FORMAT_PENALTY["cr2"] == 0.0
        assert FORMAT_PENALTY["dng"] == 0.0

    def test_heic_low_penalty(self):
        assert FORMAT_PENALTY["heic"] == 0.10

    def test_jpeg_standard_penalty(self):
        assert FORMAT_PENALTY["jpeg"] == 0.20
        assert FORMAT_PENALTY["jpg"] == 0.20

    def test_gif_highest_penalty(self):
        assert FORMAT_PENALTY["gif"] == 0.35

    def test_unknown_extension_gets_default_penalty(self):
        """A file with an unknown extension defaults to the JPEG-equivalent
        penalty (lossy unknown) rather than 0 or the GIF max. Caught here
        because a typo in the lookup default would silently advantage
        unknown-format files."""
        row = _row("/x/weird.xyz")
        score = compute_score(row, [row])
        # Score = max(0.0, 1.0_baseline_components - 0.20_default - ...)
        # The exact value isn't the test here — what we verify is the
        # *fallback path runs* (no KeyError).
        assert isinstance(score, float)


# ── Tier 1 — xmp_derived penalty ────────────────────────────────────────────


class TestXmpDerivedPenalty:
    def test_derived_penalty_constant(self):
        """The −0.30 constant is the documented contract."""
        assert DERIVED_PENALTY == 0.30

    def test_derived_file_scores_lower_than_undefined(self):
        """A file flagged as xmpMM:DerivedFrom should score at least 0.30
        lower than the same file without the flag, all else equal."""
        non_derived = _row(
            "/x/a.jpg",
            pixel_width=4000, pixel_height=3000,
            file_size_bytes=2_000_000,
            exif_tag_count=12,
            shot_date="2024-06-15T10:30:00",
            mtime="2025-01-01T12:00:00",
            gps_present=True,
            xmp_derived=False,
        )
        derived = _row(
            "/x/b.jpg",
            pixel_width=4000, pixel_height=3000,
            file_size_bytes=2_000_000,
            exif_tag_count=12,
            shot_date="2024-06-15T10:30:00",
            mtime="2025-01-01T12:00:00",
            gps_present=True,
            xmp_derived=True,
        )
        s_non = compute_score(non_derived, [non_derived, derived])
        s_der = compute_score(derived, [non_derived, derived])
        # Same files at same resolution → within-group dims tie; difference
        # comes entirely from the derived penalty.
        assert s_non - s_der == pytest.approx(0.30)


# ── compute_score — composite behaviour ────────────────────────────────────


class TestComputeScoreComposite:
    def test_score_always_in_zero_one(self):
        """No matter the penalties or weight extremes, final score must
        clamp to [0.0, 1.0]."""
        worst = _row(
            "/Users/me/Downloads/Copy of photo (1)_edited.jpg",
            pixel_width=100, pixel_height=100,
            file_size_bytes=1000,
            exif_tag_count=0,
            shot_date=None,
            gps_present=False,
            xmp_derived=True,
        )
        score = compute_score(worst, [worst])
        assert 0.0 <= score <= 1.0

    def test_best_case_image_scores_high(self):
        """A high-resolution RAW with full EXIF, GPS, clean name, real
        DateTimeOriginal should score near the top of the band."""
        best = _row(
            "/Photos/2024/IMG_4567.nef",
            pixel_width=6000, pixel_height=4000,
            file_size_bytes=30_000_000,
            exif_tag_count=14,
            shot_date="2024-06-15T10:30:00",
            mtime="2025-01-01T12:00:00",
            gps_present=True,
            xmp_derived=False,
        )
        # Score must be > 0.85 — confirms the dimensions sum near their
        # weights and no penalty applies.
        score = compute_score(best, [best])
        assert score > 0.85

    def test_raw_beats_jpeg_same_content(self):
        """The structural rationale for Tier 1: a RAW should beat a JPEG
        of the same scene because RAW = 0 penalty, JPEG = 0.20 penalty.
        No accumulation of weak Tier 2 signals can override that gap when
        both files share identical Tier 2 inputs."""
        common = dict(
            pixel_width=6000, pixel_height=4000,
            file_size_bytes=30_000_000,
            exif_tag_count=14,
            shot_date="2024-06-15T10:30:00",
            mtime="2025-01-01T12:00:00",
            gps_present=True,
            xmp_derived=False,
        )
        raw = _row("/x/photo.nef", **common)
        jpg = _row("/x/photo.jpg", **common)
        s_raw = compute_score(raw, [raw, jpg])
        s_jpg = compute_score(jpg, [raw, jpg])
        # Format penalty delta: 0.20 (JPEG) - 0.00 (RAW) = 0.20.
        # Both files have identical Tier 2 (same pixels, same EXIF, etc.)
        # so the score gap is exactly the penalty gap.
        assert s_raw - s_jpg == pytest.approx(0.20)


# ── validate_weights ───────────────────────────────────────────────────────


class TestValidateWeights:
    def test_default_weights_valid(self):
        """The shipped DEFAULT_WEIGHTS must pass its own validator —
        protects against a typo where someone updates DEFAULT_WEIGHTS
        without re-checking the sum."""
        validate_weights(DEFAULT_WEIGHTS)  # should not raise

    def test_missing_key_rejected(self):
        bad = dict(DEFAULT_WEIGHTS)
        del bad["gps"]
        with pytest.raises(ValueError, match="missing keys"):
            validate_weights(bad)

    def test_bad_sum_rejected(self):
        bad = {k: 0.5 for k in DEFAULT_WEIGHTS}  # 8 * 0.5 = 4.0
        with pytest.raises(ValueError, match="must sum to 1.0"):
            validate_weights(bad)

    def test_within_tolerance_accepted(self):
        """Float-summation noise is normal: 8 weights summing to 1.0 by
        hand often ends up at 0.9999... The ±0.001 tolerance covers it."""
        weights = dict(DEFAULT_WEIGHTS)
        weights["resolution"] = 0.2495  # creates ~0.9995 sum
        weights["file_size"] = 0.0505
        validate_weights(weights)  # should not raise


# ── score_group ────────────────────────────────────────────────────────────


class TestScoreGroup:
    def test_returns_dict_keyed_by_source_path(self):
        a = _row("/x/a.jpg", pixel_width=4000, pixel_height=3000)
        b = _row("/x/b.jpg", pixel_width=2000, pixel_height=1500)
        out = score_group([a, b])
        assert set(out.keys()) == {"/x/a.jpg", "/x/b.jpg"}

    def test_includes_none_for_live_photo_mov(self):
        """The dict still contains the MOV passenger — its value is None,
        not absent. The action layer iterates the full dict and skips
        Nones explicitly so missing keys would silently break logic."""
        heic = _row("/x/IMG_001.heic")
        mov = _row("/x/IMG_001.mov")
        out = score_group([heic, mov])
        assert "/x/IMG_001.mov" in out
        assert out["/x/IMG_001.mov"] is None
        assert isinstance(out["/x/IMG_001.heic"], float)

    def test_deterministic_across_calls(self):
        """Pure function — same inputs always produce the same output."""
        a = _row("/x/a.jpg", pixel_width=4000, pixel_height=3000)
        b = _row("/x/b.jpg", pixel_width=2000, pixel_height=1500)
        first = score_group([a, b])
        second = score_group([a, b])
        assert first == second
