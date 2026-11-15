"""Tests for scanner/thumb_store.py: the scan-local spill file for thumbnails.

The store keeps about 36 KB per picture out of memory during a scan. Each
test names what a user would lose if it failed: a thumbnail read back as
another picture's bytes, a temp file left behind on the disk, or a scan that
crashes on a closed store.
"""

from __future__ import annotations

import pickle

import pytest

from scanner.thumb_store import ThumbBytes, ThumbStore


class TestThumbStoreRoundTrip:
    def test_values_of_different_lengths_read_back_intact_in_any_order(self):
        """Thumbnails are variable-length JPEGs. Reading them in a different
        order from the write order must return each one's own bytes, or a
        picture would be measured against another picture's look."""
        values = [b"a" * 10, b"b" * 3000, b"c" * 1, b"d" * 512]
        with ThumbStore() as store:
            refs = [store.put(v) for v in values]
            assert [refs[i].bytes() for i in (3, 1, 0, 2)] == [values[i] for i in (3, 1, 0, 2)]

    def test_stored_thumb_reports_its_own_length(self):
        """The reference records how many bytes the thumbnail takes, so the
        next thumbnail starts exactly after it."""
        with ThumbStore() as store:
            first = store.put(b"x" * 7)
            second = store.put(b"y" * 5)
        assert (first.offset, first.length) == (0, 7)
        assert (second.offset, second.length) == (7, 5)


class TestThumbStoreLifecycle:
    def test_no_file_exists_before_the_first_put(self):
        """Most scans of small libraries never store a thumbnail through the
        spill path at all; no temp file may be created until one is needed."""
        store = ThumbStore()
        try:
            assert store.path is None
        finally:
            store.close_and_delete()

    def test_file_is_removed_on_close_and_a_second_close_is_harmless(self):
        """The spill file holds a whole scan's thumbnails. It must not outlive
        the scan, or every scan leaves hundreds of megabytes in the temp folder."""
        store = ThumbStore()
        store.put(b"thumbnail")
        path = store.path
        assert path is not None and path.exists()

        store.close_and_delete()
        assert not path.exists()
        store.close_and_delete()  # idempotent: the scan's finally block may run it twice

    def test_put_after_close_raises_runtime_error(self):
        store = ThumbStore()
        store.put(b"x")
        store.close_and_delete()
        with pytest.raises(RuntimeError):
            store.put(b"y")

    def test_get_after_close_raises_runtime_error(self):
        store = ThumbStore()
        ref = store.put(b"x")
        store.close_and_delete()
        with pytest.raises(RuntimeError):
            store.get(ref.offset, ref.length)

    def test_get_outside_the_stored_range_raises_index_error(self):
        """Reading past what was written must fail loudly rather than return
        padding or another thumbnail's bytes."""
        with ThumbStore() as store:
            ref = store.put(b"abcd")
            with pytest.raises(IndexError):
                store.get(ref.offset + 2, 10)
            with pytest.raises(IndexError):
                store.get(-1, 2)


class TestThumbBytes:
    def test_bytes_round_trip_and_survive_pickling(self):
        """A HashResult crosses the process pool with its ThumbBytes inside, so
        the pickle must return the same bytes on the other side."""
        thumb = ThumbBytes(b"x")
        assert thumb.bytes() == b"x"
        assert pickle.loads(pickle.dumps(thumb)).bytes() == b"x"
