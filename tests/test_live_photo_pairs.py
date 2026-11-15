"""Tests for core/services/live_photo.py (PR-4b, rule B6 pairing).

Each test pins a way a Live video could pair with the wrong still, or fail
to pair with its own still. Rows are a stand-in, so the module stays tested
against the shape it reads and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.services.live_photo import content_identifier, live_photo_stills


@dataclass
class _Row:
    source_path: str
    group_id: str | None
    content_identifier: str | None = None


class TestLivePhotoStills:
    def test_video_pairs_with_still_of_same_folder_and_cid(self):
        """Catches: a Live video pairing with nothing, so B6 never fires."""
        rows = [
            _Row("/a/IMG_1.HEIC", "g1", "C1"),
            _Row("/a/IMG_1.MOV", "g1", "C1"),
        ]
        assert live_photo_stills(rows) == {"/a/IMG_1.MOV": ("/a/IMG_1.HEIC",)}

    def test_file_name_does_not_split_a_pair(self):
        """Catches: the file name being part of the pairing key. A Takeout
        copy IMG_1(1).MOV and a clip renamed on export both keep their still
        IMG_1.HEIC: the ContentIdentifier says they are its video."""
        rows = [
            _Row("/a/IMG_1.HEIC", "g1", "C1"),
            _Row("/a/IMG_1(1).MOV", "g1", "C1"),
            _Row("/a/export_2024.MP4", "g1", "C1"),
        ]
        assert live_photo_stills(rows) == {
            "/a/IMG_1(1).MOV": ("/a/IMG_1.HEIC",),
            "/a/export_2024.MP4": ("/a/IMG_1.HEIC",),
        }

    def test_every_qualifying_still_is_returned_sorted(self):
        """Catches: the rule reading one still and ignoring a second one. With
        IMG_1.HEIC and IMG_1.JPG both qualifying, both are returned, sorted."""
        rows = [
            _Row("/a/IMG_1.JPG", "g1", "C1"),
            _Row("/a/IMG_1.HEIC", "g1", "C1"),
            _Row("/a/IMG_1.MOV", "g1", "C1"),
        ]
        assert live_photo_stills(rows) == {
            "/a/IMG_1.MOV": ("/a/IMG_1.HEIC", "/a/IMG_1.JPG"),
        }

    def test_edited_still_with_the_identifier_is_one_of_the_stills(self):
        """Catches: dropping an edited copy that kept the identifier. It is a
        still of the capture, so the video waits for it too: B6 deletes the
        video only when every still returned here is deleted."""
        rows = [
            _Row("/a/IMG_1-edited.JPG", "g1", "C1"),
            _Row("/a/IMG_1.HEIC", "g1", "C1"),
            _Row("/a/IMG_1.MOV", "g1", "C1"),
        ]
        assert live_photo_stills(rows) == {
            "/a/IMG_1.MOV": ("/a/IMG_1-edited.JPG", "/a/IMG_1.HEIC"),
        }

    def test_ungrouped_rows_ignored(self):
        """Catches: pairing rows with no group. B6 acts inside a group, so a
        lone Live Photo (no group) has nothing to follow."""
        rows = [
            _Row("/a/IMG_1.HEIC", None, "C1"),
            _Row("/a/IMG_1.MOV", None, "C1"),
        ]
        assert live_photo_stills(rows) == {}

    def test_empty_string_cid_is_missing(self):
        """Catches: an empty ContentIdentifier pairing as if it were a value.
        Two videos with CID "" must not pair with one still."""
        rows = [
            _Row("/a/IMG_1.HEIC", "g1", ""),
            _Row("/a/IMG_1.MOV", "g1", ""),
        ]
        assert live_photo_stills(rows) == {}
        assert content_identifier(_Row("/x", "g1", "")) is None

    def test_cid_mismatch_not_paired(self):
        """Catches: pairing by name and folder alone, which would mix two
        captures that share a filename."""
        rows = [
            _Row("/a/IMG_1.HEIC", "g1", "C1"),
            _Row("/a/IMG_1.MOV", "g1", "C2"),
        ]
        assert live_photo_stills(rows) == {}

    def test_other_folder_not_paired(self):
        """Catches: pairing across folders. A clip in /c must not take a still
        from /a even when name, group and CID all match: the folder says
        which copy of the capture a clip sits with."""
        rows = [
            _Row("/a/IMG_1.HEIC", "g1", "C1"),
            _Row("/c/IMG_1.MOV", "g1", "C1"),
        ]
        assert live_photo_stills(rows) == {}
