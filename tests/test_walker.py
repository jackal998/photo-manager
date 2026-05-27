"""Tests for scanner/walker.py — directory walking and Live Photo pairing."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image


def _write_jpeg(path: Path) -> None:
    Image.new("RGB", (16, 16), (128, 128, 128)).save(path, "JPEG")


def _write_mov(path: Path) -> None:
    # Minimal ftyp box that looks like a QuickTime MOV
    path.write_bytes(b"\x00\x00\x00\x08ftyp" + b"qt  ")


class TestScanSources:
    def test_missing_source_raises(self, tmp_path):
        from scanner.walker import scan_sources
        with pytest.raises(FileNotFoundError, match="Source directory not found"):
            scan_sources({"label": tmp_path / "nonexistent"})

    def test_finds_jpeg_files(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "photo.jpg")
        records = scan_sources({"test": tmp_path})
        paths = [r.path for r in records]
        assert tmp_path / "photo.jpg" in paths

    def test_skips_thumbs_db(self, tmp_path):
        from scanner.walker import scan_sources
        (tmp_path / "Thumbs.db").write_bytes(b"db")
        (tmp_path / "thumbs.db").write_bytes(b"db")
        records = scan_sources({"test": tmp_path})
        assert not records

    def test_skips_json_sidecars(self, tmp_path):
        from scanner.walker import scan_sources
        (tmp_path / "photo.jpg.json").write_text("{}", encoding="utf-8")
        records = scan_sources({"test": tmp_path})
        assert not records

    def test_skips_symlinked_file(self, tmp_path):
        """Files reached via a symlink/junction are excluded from the manifest.

        Without this guard, the recycle-bin step would later route files outside
        the configured source root through send2trash.
        """
        from scanner.walker import scan_sources

        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.jpg"
        _write_jpeg(real)

        source = tmp_path / "source"
        source.mkdir()
        _write_jpeg(source / "regular.jpg")

        link = source / "link.jpg"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted in this environment: {exc}")

        records = scan_sources({"test": source})
        names = [r.path.name for r in records]

        assert "regular.jpg" in names
        assert "link.jpg" not in names

    def test_skips_files_under_symlinked_dir(self, tmp_path):
        """Files inside a symlinked/junctioned subdirectory are excluded too."""
        from scanner.walker import scan_sources

        outside = tmp_path / "outside"
        outside.mkdir()
        _write_jpeg(outside / "buried.jpg")

        source = tmp_path / "source"
        source.mkdir()
        _write_jpeg(source / "kept.jpg")

        link_dir = source / "linked_sub"
        try:
            link_dir.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted in this environment: {exc}")

        records = scan_sources({"test": source})
        names = [r.path.name for r in records]

        assert "kept.jpg" in names
        assert "buried.jpg" not in names

    def test_skips_files_when_path_reports_as_symlink(self, tmp_path, monkeypatch):
        """Mock-driven coverage so the skip-symlink guard runs even where
        actually creating a symlink requires admin/developer mode (Windows).
        """
        from pathlib import Path

        from scanner.walker import scan_sources

        source = tmp_path / "source"
        source.mkdir()
        _write_jpeg(source / "kept.jpg")
        fake_link = source / "fakelink.jpg"
        _write_jpeg(fake_link)

        original_is_symlink = Path.is_symlink

        def mocked_is_symlink(self):
            if self == fake_link:
                return True
            return original_is_symlink(self)

        monkeypatch.setattr(Path, "is_symlink", mocked_is_symlink)

        records = scan_sources({"test": source})
        names = [r.path.name for r in records]

        assert "kept.jpg" in names
        assert "fakelink.jpg" not in names

    def test_source_label_assigned(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "a.jpg")
        records = scan_sources({"mylabel": tmp_path})
        assert all(r.source_label == "mylabel" for r in records)

    def test_recursive_walk(self, tmp_path):
        from scanner.walker import scan_sources
        subdir = tmp_path / "2024" / "event"
        subdir.mkdir(parents=True)
        _write_jpeg(subdir / "photo.jpg")
        records = scan_sources({"test": tmp_path})
        assert len(records) == 1

    def test_multiple_sources(self, tmp_path):
        from scanner.walker import scan_sources
        src_a = tmp_path / "a"
        src_b = tmp_path / "b"
        src_a.mkdir()
        src_b.mkdir()
        _write_jpeg(src_a / "x.jpg")
        _write_jpeg(src_b / "y.jpg")
        records = scan_sources({"alpha": src_a, "beta": src_b})
        labels = {r.source_label for r in records}
        assert labels == {"alpha", "beta"}


class TestLivePhotoPairing:
    def test_heic_paired_with_mov(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_1234.HEIC")
        _write_mov(tmp_path / "IMG_1234.MOV")
        records = scan_sources({"test": tmp_path})
        heic = next(r for r in records if r.path.suffix.upper() == ".HEIC")
        assert len(heic.pair_cluster) == 1
        assert heic.pair_cluster[0].name == "IMG_1234.MOV"

    def test_jpg_paired_with_mov(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_5678.JPG")
        _write_mov(tmp_path / "IMG_5678.MOV")
        records = scan_sources({"test": tmp_path})
        jpg = next(r for r in records if r.path.suffix.upper() == ".JPG")
        assert len(jpg.pair_cluster) == 1

    def test_no_pairing_without_partner(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_9999.HEIC")
        records = scan_sources({"test": tmp_path})
        assert records[0].pair_cluster == ()

    def test_edited_not_paired(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_1234-已編輯.HEIC")
        _write_mov(tmp_path / "IMG_1234.MOV")
        records = scan_sources({"test": tmp_path})
        heic = next(r for r in records if "編輯" in r.path.name)
        assert heic.pair_cluster == ()

    def test_takeout_numbered_pair(self, tmp_path):
        """``IMG_9556(1).HEIC + IMG_9556(1).MOV`` pair via exact-stem
        match (clean_stem AND number match)."""
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_9556(1).HEIC")
        _write_mov(tmp_path / "IMG_9556(1).MOV")
        records = scan_sources({"test": tmp_path})
        heic = next(r for r in records if r.path.suffix.upper() == ".HEIC")
        assert len(heic.pair_cluster) == 1

    def test_pair_emits_both_records(self, tmp_path):
        """Both halves of a Live Photo pair must appear in the records.

        Regression for the surface bug behind photo-manager#88: the old
        walker maintained a ``paired`` set and skipped any path already
        named as a partner, dropping the MOV/MP4 half entirely before
        hashing. The video never reached the manifest, so dedup-side
        pair-edge logic had only one record to work with and the pair
        couldn't form a 2-row group on the GUI side.

        Mirrors the production-data shape verified against
        ``D:\\Takeout-0508`` — 4 simple HEIC+MP4 pairs in the
        ``2024 June-July Japan (Nishi-Nihon)`` album, all observed
        missing their MP4 half from the manifest pre-fix.
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_2247.HEIC")
        _write_mov(tmp_path / "IMG_2247.MP4")
        records = scan_sources({"test": tmp_path})
        names = sorted(r.path.name for r in records)
        assert names == ["IMG_2247.HEIC", "IMG_2247.MP4"], (
            f"expected both halves of pair to appear; got {names}"
        )

    def test_pair_cluster_bidirectional(self, tmp_path):
        """HEIC's cluster names the MOV; MOV's cluster names the HEIC.

        Symmetric clusters mean ``_collect_pair_edges`` emits an edge
        in each direction, so union-find groups them regardless of
        iteration order or record-survival differences.
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_2247.HEIC")
        _write_mov(tmp_path / "IMG_2247.MP4")
        records = scan_sources({"test": tmp_path})
        heic = next(r for r in records if r.path.suffix == ".HEIC")
        mp4  = next(r for r in records if r.path.suffix == ".MP4")
        assert tuple(p.name for p in heic.pair_cluster) == ("IMG_2247.MP4",)
        assert tuple(p.name for p in mp4.pair_cluster)  == ("IMG_2247.HEIC",)

    def test_unpaired_video_still_emits_record(self, tmp_path):
        """A video with no same-stem image partner (e.g. a standalone
        recording, like ``IMG_2296.MOV`` in the production data set)
        must still produce its own FileRecord with empty pair_cluster.
        """
        from scanner.walker import scan_sources
        _write_mov(tmp_path / "IMG_2296.MOV")
        records = scan_sources({"test": tmp_path})
        assert len(records) == 1
        assert records[0].path.name == "IMG_2296.MOV"
        assert records[0].pair_cluster == ()

    def test_takeout_numbered_pair_emits_both(self, tmp_path):
        """Strengthening of ``test_takeout_numbered_pair`` — assert BOTH
        halves of a ``(1)``-suffixed pair appear, not just the HEIC
        side. Pre-#88 the MOV was silently dropped here too.
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_9556(1).HEIC")
        _write_mov(tmp_path / "IMG_9556(1).MOV")
        records = scan_sources({"test": tmp_path})
        names = sorted(r.path.name for r in records)
        assert names == ["IMG_9556(1).HEIC", "IMG_9556(1).MOV"]

    # ── Production-data edge cases ─────────────────────────────────────

    def test_dup_marker_clash_pairs_correctly(self, tmp_path):
        """``IMG_1856.HEIC + IMG_1856.MP4 + IMG_1856(1).HEIC + IMG_1856(1).MP4``
        in the same directory must form TWO independent pairs, not one
        confused 4-member cluster.

        Production case (``D:\\Takeout-0508\\Takeout\\Google 相簿\\2022 年的相片``):
        Google extracts the same Live Photo from two albums, adds
        ``(1)`` to disambiguate. Pre-#88 the walker matched on
        ``clean_stem`` only — ``IMG_1856.HEIC`` could pair with
        ``IMG_1856(1).MP4`` non-deterministically. Now pairing
        requires exact ``(clean_stem, number)`` match.
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_1856.HEIC")
        _write_mov(tmp_path / "IMG_1856.MP4")
        _write_jpeg(tmp_path / "IMG_1856(1).HEIC")
        _write_mov(tmp_path / "IMG_1856(1).MP4")
        records = scan_sources({"test": tmp_path})

        by_name = {r.path.name: r for r in records}
        # Unsuffixed pair: HEIC clusters only with the unsuffixed MP4
        unsuf_heic_cluster = sorted(p.name for p in by_name["IMG_1856.HEIC"].pair_cluster)
        assert unsuf_heic_cluster == ["IMG_1856.MP4"]
        # Suffixed pair: clusters only with the (1).MP4
        suf_heic_cluster = sorted(p.name for p in by_name["IMG_1856(1).HEIC"].pair_cluster)
        assert suf_heic_cluster == ["IMG_1856(1).MP4"]
        # And mirror the symmetric direction
        assert sorted(p.name for p in by_name["IMG_1856.MP4"].pair_cluster) == ["IMG_1856.HEIC"]
        assert sorted(p.name for p in by_name["IMG_1856(1).MP4"].pair_cluster) == ["IMG_1856(1).HEIC"]

    def test_image_plus_two_videos_clusters_all_three(self, tmp_path):
        """``IMG_4278.HEIC + IMG_4278.MOV + IMG_4278.MP4`` is a single
        Live Photo where Google Takeout transcoded the video to both
        formats. All three files must cluster together (downstream
        dedup will then group them under one group_id).

        Production case (``D:\\Takeout-0508\\Takeout\\Google 相簿\\2023 年的相片``).
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_4278.HEIC")
        _write_mov(tmp_path / "IMG_4278.MOV")
        _write_mov(tmp_path / "IMG_4278.MP4")
        records = scan_sources({"test": tmp_path})

        by_name = {r.path.name: r for r in records}
        # HEIC clusters with both videos
        assert sorted(p.name for p in by_name["IMG_4278.HEIC"].pair_cluster) == [
            "IMG_4278.MOV", "IMG_4278.MP4",
        ]
        # MOV clusters with HEIC and MP4
        assert sorted(p.name for p in by_name["IMG_4278.MOV"].pair_cluster) == [
            "IMG_4278.HEIC", "IMG_4278.MP4",
        ]
        # MP4 clusters with HEIC and MOV
        assert sorted(p.name for p in by_name["IMG_4278.MP4"].pair_cluster) == [
            "IMG_4278.HEIC", "IMG_4278.MOV",
        ]

    def test_image_plus_image_plus_video_clusters(self, tmp_path):
        """``IMG_5332.HEIC + IMG_5332.jpg + IMG_5332.MP4`` — a Live
        Photo with an extra JPG variant of the image (e.g. extraction
        of a still from the live photo). All three same-exact-stem
        files cluster.

        Production case (``D:\\Takeout-0508\\Takeout\\Google 相簿\\2023 年的相片``).
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_5332.HEIC")
        _write_jpeg(tmp_path / "IMG_5332.jpg")
        _write_mov(tmp_path / "IMG_5332.MP4")
        records = scan_sources({"test": tmp_path})

        by_name = {r.path.name: r for r in records}
        assert sorted(p.name for p in by_name["IMG_5332.HEIC"].pair_cluster) == [
            "IMG_5332.MP4", "IMG_5332.jpg",
        ]
        assert sorted(p.name for p in by_name["IMG_5332.jpg"].pair_cluster) == [
            "IMG_5332.HEIC", "IMG_5332.MP4",
        ]
        assert sorted(p.name for p in by_name["IMG_5332.MP4"].pair_cluster) == [
            "IMG_5332.HEIC", "IMG_5332.jpg",
        ]


class TestProgressCallback:
    """#448 — scan_sources fires progress_callback once per accepted media file.

    Lets a worker render a live "Walking sources — N files…" indicator
    on a long NAS scan where the synchronous rglob would otherwise sit
    silent for minutes.
    """

    def test_callback_fires_once_per_media_file(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "a.jpg")
        _write_jpeg(tmp_path / "b.jpg")
        _write_jpeg(tmp_path / "c.jpg")
        ticks = []
        scan_sources({"test": tmp_path}, progress_callback=lambda: ticks.append(1))
        assert len(ticks) == 3

    def test_callback_not_fired_for_filtered_paths(self, tmp_path):
        """``Thumbs.db`` / json sidecars / non-media extensions must not
        tick — they never reach the result set, so a counter built from
        ticks must match the eventual records length.
        """
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "photo.jpg")
        (tmp_path / "Thumbs.db").write_bytes(b"x")
        (tmp_path / "photo.jpg.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("readme", encoding="utf-8")
        ticks = []
        records = scan_sources({"t": tmp_path}, progress_callback=lambda: ticks.append(1))
        assert len(ticks) == len(records) == 1

    def test_callback_omitted_default_keeps_existing_behaviour(self, tmp_path):
        """Existing callers passing no callback must still work."""
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "a.jpg")
        records = scan_sources({"test": tmp_path})
        assert len(records) == 1


class TestFlatScan:
    def test_flat_scan_finds_top_level_file(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "photo.jpg")
        records = scan_sources({"test": tmp_path}, recursive_map={"test": False})
        assert len(records) == 1

    def test_flat_scan_ignores_nested_file(self, tmp_path):
        from scanner.walker import scan_sources
        subdir = tmp_path / "sub"
        subdir.mkdir()
        _write_jpeg(subdir / "photo.jpg")
        records = scan_sources({"test": tmp_path}, recursive_map={"test": False})
        assert records == []

    def test_recursive_map_none_means_all_recursive(self, tmp_path):
        """Omitting recursive_map preserves existing fully-recursive behaviour."""
        from scanner.walker import scan_sources
        subdir = tmp_path / "sub"
        subdir.mkdir()
        _write_jpeg(subdir / "nested.jpg")
        records = scan_sources({"test": tmp_path})   # no recursive_map
        assert len(records) == 1

    def test_recursive_map_per_source(self, tmp_path):
        from scanner.walker import scan_sources
        flat_dir = tmp_path / "flat"
        rec_dir = tmp_path / "rec"
        flat_dir.mkdir()
        rec_dir.mkdir()
        (flat_dir / "sub").mkdir()
        (rec_dir / "sub").mkdir()
        _write_jpeg(flat_dir / "sub" / "a.jpg")   # nested in flat — excluded
        _write_jpeg(rec_dir / "sub" / "b.jpg")    # nested in recursive — included
        records = scan_sources(
            {"flat": flat_dir, "rec": rec_dir},
            recursive_map={"flat": False, "rec": True},
        )
        labels = {r.source_label for r in records}
        assert "flat" not in labels
        assert "rec" in labels


class TestWalkerExclusionsFixture:
    """Pin the qa/sandbox/walker-exclusions/ fixture's expected walker output.

    The fixture mixes 2 real JPEGs with the three skip patterns documented
    on `scanner/walker.py` and `scanner/media.py`:

      - real_photo_a.jpg.json   (Google Takeout sidecar — non-media extension)
      - Thumbs.db               (Windows thumbnail cache — SKIP_FILENAMES)
      - desktop.ini             (Windows folder config — SKIP_FILENAMES)

    If any of the three skip rules regresses, the count flips from 2 to 3+
    and this test fails immediately rather than silently letting noise
    into hash comparisons. Companion to QA scenario s09_walker_exclusions.
    """

    def test_walker_exclusions_fixture_returns_only_real_photos(self):
        from scanner.walker import scan_sources

        fixture = Path(__file__).resolve().parent.parent / "qa" / "sandbox" / "walker-exclusions"
        if not fixture.is_dir():
            pytest.skip(f"fixture not present: {fixture}")

        records = scan_sources({"wx": fixture}, recursive_map={"wx": True})
        names = sorted(r.path.name for r in records)
        assert names == ["real_photo_a.jpg", "real_photo_b.jpg"], (
            f"walker leaked excluded files into the result set: {names}"
        )


# ---------------------------------------------------------------------------
# Win32-unsafe filename detection (photo-manager#169)
# ---------------------------------------------------------------------------

class TestWin32UnsafeName:
    """``_has_win32_unsafe_name`` flags filenames the Win32 GUI layer hides.

    NTFS preserves trailing ``.`` and whitespace; Win32 GUI strips them. A
    pathlib walk from the parent enumerates the dir name but cannot recurse
    into it, so any contents are silently invisible. We warn the user instead
    of silently coercing — matches the issue's recommendation.
    """

    @pytest.mark.parametrize("name", [
        "E.J.",
        "trailing space ",
        "tabchar\t",
        "dot.",
        "ends with newline\n",
    ])
    def test_unsafe_names_detected(self, name):
        from scanner.walker import _has_win32_unsafe_name
        assert _has_win32_unsafe_name(name) is True

    @pytest.mark.parametrize("name", [
        "normal_folder",
        "E.J",                    # the Win32-stripped form — no trailing dot
        "photo.jpg",              # extension dot is mid-name, not trailing
        "name with internal spaces but ending letter",
        "trailing.tar.gz",
        ".hidden",                # leading dot is fine
    ])
    def test_safe_names_not_flagged(self, name):
        from scanner.walker import _has_win32_unsafe_name
        assert _has_win32_unsafe_name(name) is False

    def test_empty_name_not_flagged(self):
        """Defensive: empty string must not crash the boolean coercion."""
        from scanner.walker import _has_win32_unsafe_name
        assert _has_win32_unsafe_name("") is False


class TestWalkerWin32UnsafeWarning:
    """``scan_sources`` warns once per Win32-unsafe path encountered during
    the walk. The warning text must mention rename guidance — silent
    coercion would be a surprise; the issue body explicitly says don't."""

    def test_warning_emitted_on_trailing_dot_dir(self, tmp_path, caplog):
        """If rglob enumerates a trailing-dot directory, log a warning naming
        the path. Mocked because a real trailing-dot directory needs the
        ``\\\\?\\`` NT raw API to create on Windows — covered separately by
        the platform-gated test below."""
        from unittest.mock import patch
        from scanner.walker import scan_sources
        from loguru import logger
        import logging

        # Real directory exists so scan_sources passes the existence check;
        # we mock rglob to inject the synthetic trailing-dot path.
        fake_unsafe = tmp_path / "E.J."

        # Bridge loguru → caplog so pytest captures the warning.
        handler_id = logger.add(caplog.handler, format="{message}", level="WARNING")
        try:
            with patch("pathlib.Path.rglob", return_value=iter([fake_unsafe])):
                with caplog.at_level(logging.WARNING):
                    scan_sources({"label": tmp_path})
        finally:
            logger.remove(handler_id)

        warnings = [r.message for r in caplog.records
                    if "trailing dot" in r.message.lower()
                    or "win32" in r.message.lower()]
        assert warnings, (
            f"expected warning about trailing-dot path; got: "
            f"{[r.message for r in caplog.records]}"
        )
        # Warning must name the offending path so the user knows what to rename
        assert "E.J." in warnings[0]

    def test_warning_emitted_only_once_per_path(self, tmp_path, caplog):
        """If rglob returns the same unsafe path multiple times across the
        walk (theoretically possible — a unique-by-name set is the guard),
        we warn once and stay quiet thereafter."""
        from unittest.mock import patch
        from scanner.walker import scan_sources
        from loguru import logger
        import logging

        fake_unsafe = tmp_path / "trailing.dir."

        handler_id = logger.add(caplog.handler, format="{message}", level="WARNING")
        try:
            with patch("pathlib.Path.rglob",
                       return_value=iter([fake_unsafe, fake_unsafe, fake_unsafe])):
                with caplog.at_level(logging.WARNING):
                    scan_sources({"label": tmp_path})
        finally:
            logger.remove(handler_id)

        relevant = [r for r in caplog.records
                    if "trailing.dir." in r.message]
        assert len(relevant) == 1, (
            f"expected exactly 1 warning, got {len(relevant)}: "
            f"{[r.message for r in relevant]}"
        )
