"""Tests for infrastructure.settings.JsonSettings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.settings import JsonSettings


@pytest.fixture
def settings_file(tmp_path):
    """Write a minimal settings.json and return its path."""
    data = {
        "thumbnail_size": 512,
        "delete": {"confirm_group_full_delete": True},
        "sources": {
            "iphone": "/nas/iphone",
            "takeout": "/downloads/takeout",
        },
    }
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_get_top_level(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("thumbnail_size") == 512


def test_get_dotted_key(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("delete.confirm_group_full_delete") is True


def test_get_nested_source(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("sources.iphone") == "/nas/iphone"


def test_get_missing_key_returns_default(settings_file):
    s = JsonSettings(settings_file)
    assert s.get("does.not.exist") is None
    assert s.get("missing", "fallback") == "fallback"


def test_get_partial_path_returns_dict(settings_file):
    s = JsonSettings(settings_file)
    result = s.get("sources")
    assert isinstance(result, dict)
    assert result["iphone"] == "/nas/iphone"


def test_set_existing_key(settings_file):
    s = JsonSettings(settings_file)
    s.set("thumbnail_size", 256)
    assert s.get("thumbnail_size") == 256


def test_set_dotted_existing(settings_file):
    s = JsonSettings(settings_file)
    s.set("sources.iphone", "/new/path")
    assert s.get("sources.iphone") == "/new/path"


def test_set_creates_intermediate_dicts(settings_file):
    s = JsonSettings(settings_file)
    s.set("ui.locale", "en-US")
    assert s.get("ui.locale") == "en-US"


def test_set_deep_new_key(settings_file):
    s = JsonSettings(settings_file)
    s.set("a.b.c.d", 42)
    assert s.get("a.b.c.d") == 42


def test_save_persists_to_disk(settings_file):
    s = JsonSettings(settings_file)
    s.set("sources.jdrive", "/j/photos")
    s.save()

    reloaded = json.loads(settings_file.read_text(encoding="utf-8"))
    assert reloaded["sources"]["jdrive"] == "/j/photos"


def test_save_preserves_existing_keys(settings_file):
    s = JsonSettings(settings_file)
    s.set("sources.output", "manifest.sqlite")
    s.save()

    reloaded = json.loads(settings_file.read_text(encoding="utf-8"))
    assert reloaded["thumbnail_size"] == 512
    assert reloaded["sources"]["iphone"] == "/nas/iphone"


def test_missing_file_returns_empty_settings():
    s = JsonSettings(Path("/does/not/exist/settings.json"))
    assert s.get("thumbnail_size") is None
    assert s.get("thumbnail_size", 512) == 512


def test_malformed_json_raises(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        JsonSettings(bad)


# --- #658: non-dict intermediate must raise, never clobber ------------------


def test_set_raises_on_non_dict_intermediate_and_preserves_value(settings_file):
    s = JsonSettings(settings_file)
    # "thumbnail_size" holds a scalar; descending through it must not
    # silently replace 512 with {} (the old behaviour destroyed data and
    # still reported success).
    with pytest.raises(TypeError, match="thumbnail_size"):
        s.set("thumbnail_size.sub", 1)
    assert s.get("thumbnail_size") == 512  # untouched


def test_set_raises_names_the_conflicting_depth(settings_file):
    s = JsonSettings(settings_file)
    with pytest.raises(TypeError, match=r"'sources\.iphone'"):
        s.set("sources.iphone.deep.key", 1)
    assert s.get("sources.iphone") == "/nas/iphone"


# --- #653: atomic save -------------------------------------------------------


def test_save_leaves_no_tmp_file_and_persists(settings_file):
    s = JsonSettings(settings_file)
    s.set("ui.locale", "zh-TW")
    s.save()
    # Glob rather than the old fixed `settings.json.tmp` name: since the #790
    # review the temp name is unique per call, so asserting that one literal
    # path is absent would pass without checking anything.
    leftovers = list(settings_file.parent.glob("*.tmp"))
    assert leftovers == [], f"temp files left behind: {leftovers}"
    assert JsonSettings(settings_file).get("ui.locale") == "zh-TW"


def test_concurrent_saves_keep_both_updates(settings_file):
    """Two threads saving different keys must not crash or lose an update.

    Real failure mode this catches (#790 review): ``patch_settings`` used to be
    ``async def`` with no awaits, so its load-modify-save ran atomically on the
    single event-loop thread. Dispatched to FastAPI's threadpool it no longer
    does, and the UI genuinely fires two PATCHes at once — ScanDialog's
    fire-and-forget ``void patchSettings(...)`` on close, plus the always-visible
    locale toggle. The scan worker thread is a third concurrent writer
    (``scanner/autotune.py`` ``store_read_knee``).

    Before the fix this failed two ways: a fixed sibling temp name made the
    second ``os.replace`` raise ``PermissionError: [WinError 32]`` on Windows,
    and even without the crash each saver wrote its own whole-file snapshot, so
    the slower writer silently erased the faster one's key.
    """
    import threading

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _writer(key: str, value: str) -> None:
        try:
            s = JsonSettings(settings_file)
            s.set(key, value)
            barrier.wait(timeout=10)  # maximise overlap on the write itself
            s.save()
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [
        threading.Thread(target=_writer, args=("ui.locale", "zh-TW")),
        threading.Thread(target=_writer, args=("ui.prune_singletons", "yes")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent save raised: {errors!r}"

    reread = JsonSettings(settings_file)
    assert reread.get("ui.locale") == "zh-TW", "first writer's key was lost"
    assert reread.get("ui.prune_singletons") == "yes", "second writer's key was lost"
    # Pre-existing content must survive both merges.
    assert reread.get("thumbnail_size") == 512


def test_save_succeeds_while_a_reader_holds_the_file_open(settings_file):
    """A reader's open handle must not fail the writer (#790 review r2, HIGH).

    Python opens files without FILE_SHARE_DELETE, so on Windows an open read
    handle makes ``os.replace`` fail with PermissionError/WinError 5. Four
    readers share FastAPI's threadpool with the writer here
    (``app/web/routes/settings.py``, ``review_service``, ``routes/scan.py``,
    ``action_service``), and ``patch_settings`` maps the failure to HTTP 422
    while ScanDialog's fire-and-forget PATCH discards it.
    """
    import threading

    import time

    holding = threading.Event()
    errors: list[BaseException] = []
    # Held long enough that the writer's first os.replace attempt lands during
    # the window, short enough that the bounded retry absorbs it — this models
    # the transient out-of-process handle (Qt app, AV scanner) the retry exists
    # for, not a permanent lock, which is correctly still an error.
    hold_s = 0.05

    def _hold_open() -> None:
        with settings_file.open("r", encoding="utf-8"):
            holding.set()
            time.sleep(hold_s)

    reader = threading.Thread(target=_hold_open)
    reader.start()
    try:
        assert holding.wait(timeout=10), "reader never opened the file"

        s = JsonSettings(settings_file)
        s.set("ui.locale", "zh-TW")
        try:
            s.save()
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)
    finally:
        reader.join(timeout=10)

    assert not errors, f"save failed while a reader held the file: {errors!r}"
    assert JsonSettings(settings_file).get("ui.locale") == "zh-TW"


def test_saves_survive_readers_hammering_the_same_file(settings_file):
    """Four spinning readers + one writer: zero exceptions, last value wins."""
    import threading

    stop = threading.Event()
    errors: list[BaseException] = []

    def _reader() -> None:
        try:
            while not stop.is_set():
                JsonSettings(settings_file).get("thumbnail_size")
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    readers = [threading.Thread(target=_reader) for _ in range(4)]
    for t in readers:
        t.start()
    try:
        for i in range(50):
            try:
                s = JsonSettings(settings_file)
                s.set("ui.locale", f"loc-{i}")
                s.save()
            except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
                errors.append(exc)
                break
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=10)

    assert not errors, f"reader/writer contention raised: {errors!r}"
    assert JsonSettings(settings_file).get("ui.locale") == "loc-49"


def test_save_refuses_to_clobber_a_scalar_intermediate_on_disk(settings_file):
    """The merge must honour #658, not just `set()` (#790 review r2).

    `set()` raises rather than destroy a scalar sitting where a dict is
    needed. The save-time merge writes against the file's CURRENT content, so
    it has to make the same promise — otherwise the scalar dies there instead,
    with the route reporting 200.
    """
    settings_file.write_text(
        json.dumps({"n": "scalar", "other": 7}), encoding="utf-8"
    )
    s = JsonSettings(settings_file)
    # Fresh instance whose own snapshot has `n` as a dict, so the conflict
    # arises only at merge time against what is on disk.
    s._data = {}
    s.set("n.b", 2)
    settings_file.write_text(
        json.dumps({"n": "scalar", "other": 7}), encoding="utf-8"
    )

    with pytest.raises(TypeError, match="non-dict value"):
        s.save()

    on_disk = json.loads(settings_file.read_text(encoding="utf-8"))
    assert on_disk == {"n": "scalar", "other": 7}, "the file must be untouched"


def test_get_result_mutation_is_not_persisted_without_set(settings_file):
    """Pins the documented contract: only `set()` marks a change dirty.

    `get()` returns the live nested object, and since #790 `save()` writes only
    keys passed to `set()`. Mutating a `get()` result therefore does NOT
    persist — the pre-#790 whole-snapshot write did. No caller relies on the
    old behaviour (all nine `save()` sites call `set()` first), but the change
    is invisible at the call site, so it is pinned here rather than left for
    someone to discover through lost data.
    """
    s = JsonSettings(settings_file)
    sources = s.get("sources")
    sources["iphone"] = "/mutated/in/place"  # no set() call
    s.set("thumbnail_size", 256)  # an unrelated, properly-marked change
    s.save()

    reread = JsonSettings(settings_file)
    assert reread.get("thumbnail_size") == 256
    assert reread.get("sources.iphone") == "/nas/iphone", (
        "mutating a get() result must NOT persist — route changes through set()"
    )


def test_set_parent_after_child_persists_both(settings_file):
    """`set('a.b', 1)` then `set('a', {...})` must not raise (#790 review r2).

    The parent's subtree supersedes the pending child path; replaying the
    stale child afterwards used to raise KeyError, where the pre-#790
    whole-snapshot write persisted the parent fine.
    """
    s = JsonSettings(settings_file)
    s.set("a.b", 1)
    s.set("a", {"c": 2})
    s.save()

    reread = JsonSettings(settings_file)
    assert reread.get("a") == {"c": 2}
    assert reread.get("thumbnail_size") == 512  # untouched keys survive


def test_set_child_after_parent_persists_both(settings_file):
    """The other order: the later child must land inside the new subtree."""
    s = JsonSettings(settings_file)
    s.set("a", {"c": 2})
    s.set("a.b", 1)
    s.save()

    reread = JsonSettings(settings_file)
    assert reread.get("a") == {"c": 2, "b": 1}


def test_save_heals_a_file_truncated_after_construction(settings_file):
    """Valid at construction, corrupt at save time → save still writes (#790 r2).

    Pre-#790 `save()` wrote `_data` wholesale and so healed the file. The
    read-merge-write must not regress that into a raise, nor merge onto `{}`
    and drop every other key.
    """
    s = JsonSettings(settings_file)
    settings_file.write_text("{ truncated", encoding="utf-8")

    s.set("ui.locale", "zh-TW")
    s.save()

    reread = JsonSettings(settings_file)  # would raise if still corrupt
    assert reread.get("ui.locale") == "zh-TW"
    assert reread.get("thumbnail_size") == 512, "pre-corruption keys must survive"


def test_save_crash_mid_write_keeps_original_intact(settings_file, monkeypatch):
    # A kill mid-write used to truncate settings.json itself (plain
    # open("w") + dump). With tmp + os.replace, a failure during the dump
    # damages only the temp file; the original stays parseable.
    import infrastructure.settings as settings_mod

    s = JsonSettings(settings_file)
    s.set("thumbnail_size", 1024)

    def _boom(*a, **k):
        raise OSError("disk full / killed mid-write")

    monkeypatch.setattr(settings_mod.json, "dump", _boom)
    with pytest.raises(OSError):
        s.save()

    reread = JsonSettings(settings_file)  # would raise if truncated
    assert reread.get("thumbnail_size") == 512  # pre-crash content intact
