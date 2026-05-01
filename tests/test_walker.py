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
        assert heic.pair_partner is not None
        assert heic.pair_partner.name == "IMG_1234.MOV"

    def test_jpg_paired_with_mov(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_5678.JPG")
        _write_mov(tmp_path / "IMG_5678.MOV")
        records = scan_sources({"test": tmp_path})
        jpg = next(r for r in records if r.path.suffix.upper() == ".JPG")
        assert jpg.pair_partner is not None

    def test_no_pairing_without_partner(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_9999.HEIC")
        records = scan_sources({"test": tmp_path})
        assert records[0].pair_partner is None

    def test_edited_not_paired(self, tmp_path):
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_1234-已編輯.HEIC")
        _write_mov(tmp_path / "IMG_1234.MOV")
        records = scan_sources({"test": tmp_path})
        heic = next(r for r in records if "編輯" in r.path.name)
        assert heic.pair_partner is None

    def test_takeout_numbered_pair(self, tmp_path):
        """IMG_9556(1).HEIC + IMG_9556(1).MOV should pair via clean_stem."""
        from scanner.walker import scan_sources
        _write_jpeg(tmp_path / "IMG_9556(1).HEIC")
        _write_mov(tmp_path / "IMG_9556(1).MOV")
        records = scan_sources({"test": tmp_path})
        heic = next(r for r in records if r.path.suffix.upper() == ".HEIC")
        assert heic.pair_partner is not None


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
