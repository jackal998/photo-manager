"""GreySigStore: the scan-local spill file for grey signatures (GROUPING 11).

Each test pins one contract from the store's docstrings. The pickle test is the
real process-pool contract: a HashResult carrying GreyBytes must cross a pipe.
"""

from __future__ import annotations

import pickle
import tempfile
import threading
from pathlib import Path

import pytest

from scanner.dedup import HashResult
from scanner.grey_store import GREY_SIG_BYTES, GreyBytes, GreySigStore
from scanner.walker import FileRecord


def _sig(value: int) -> bytes:
    return bytes([value]) * GREY_SIG_BYTES


def test_round_trip_three_records_in_order():
    with GreySigStore() as store:
        indexes = [store.put(_sig(v)) for v in (1, 2, 3)]
        assert indexes == [0, 1, 2]
        assert [store.get(i) for i in (2, 0, 1)] == [_sig(3), _sig(1), _sig(2)]


def test_put_wrong_size_raises_value_error():
    with GreySigStore() as store:
        with pytest.raises(ValueError):
            store.put(bytes(GREY_SIG_BYTES - 1))
        assert store.path is None  # nothing was written, so no file exists


def test_get_past_the_last_index_raises_index_error():
    with GreySigStore() as store:
        store.put(_sig(7))
        with pytest.raises(IndexError):
            store.get(1)
        with pytest.raises(IndexError):
            store.get(-1)


def test_close_and_delete_removes_the_file_and_is_idempotent():
    store = GreySigStore()
    store.put(_sig(9))
    path = store.path
    assert path is not None and path.exists()
    store.close_and_delete()
    assert not path.exists()
    store.close_and_delete()  # second call is a no-op, not an error


def test_put_and_get_after_close_raise_runtime_error():
    store = GreySigStore()
    store.put(_sig(4))
    store.close_and_delete()
    with pytest.raises(RuntimeError):
        store.put(_sig(5))
    with pytest.raises(RuntimeError):
        store.get(0)


def test_nothing_put_means_no_file_is_created():
    store = GreySigStore()
    assert store.path is None
    store.close_and_delete()
    assert store.path is None


def test_file_lives_in_the_system_temp_dir_with_a_unique_name():
    first, second = GreySigStore(), GreySigStore()
    try:
        first.put(_sig(1))
        second.put(_sig(2))
        assert first.path is not None and second.path is not None
        assert first.path.parent.resolve() == Path(tempfile.gettempdir()).resolve()
        assert first.path.name.startswith("photo-manager-grey128-")
        assert first.path.name.endswith(".tmp")
        assert first.path != second.path
    finally:
        first.close_and_delete()
        second.close_and_delete()


def test_concurrent_puts_keep_each_index_bound_to_its_own_bytes():
    store = GreySigStore()
    try:
        assigned: dict[int, int] = {}
        lock = threading.Lock()

        def worker(value: int) -> None:
            index = store.put(_sig(value))
            with lock:
                assigned[index] = value

        threads = [threading.Thread(target=worker, args=(v,)) for v in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(assigned) == list(range(8))
        for index, value in assigned.items():
            assert store.get(index) == _sig(value)
    finally:
        store.close_and_delete()


def test_hash_result_with_grey_bytes_survives_pickle():
    # The process pool pickles every HashResult back to the parent.
    record = FileRecord(path=Path("/a/x.jpg"), source_label="s", file_type="jpeg")
    result = HashResult(
        record=record, sha256="x", phash="a", exif_date=None,
        grey128=GreyBytes(_sig(42)),
    )
    restored = pickle.loads(pickle.dumps(result))
    assert restored.grey128 is not None
    assert restored.grey128.bytes() == _sig(42)
