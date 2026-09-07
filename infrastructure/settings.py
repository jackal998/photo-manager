"""Settings access helpers for JSON-based configuration."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from loguru import logger

# Serialises settings file access across every JsonSettings instance in this
# process — READS included, which is not optional on Windows.
#
# Writers (#790 review): the two `/api/settings` route handlers, which FastAPI
# now dispatches to its worker threadpool, and the scan worker thread
# persisting calibration via `scanner.autotune.store_read_knee` /
# `core.app_service.scan_runner.store_hash_pool_rates`. A lock in the route
# layer would not have covered that third writer.
#
# Readers (#790 review round 2): `app/web/routes/settings.py`,
# `core/app_service/review_service.py` (through the plain-def `review.py`
# handler), `app/web/routes/scan.py` and `core/app_service/action_service.py`
# all construct a JsonSettings on the same threadpool. Python opens files
# without FILE_SHARE_DELETE, so on Windows an open read handle makes the
# writer's `os.replace` fail with PermissionError/WinError 5. Reads therefore
# take this lock too, which is why it is an RLock: `save()` holds it and then
# calls `_read_disk()`.
_SAVE_LOCK = threading.RLock()

# Belt-and-braces for handles this process cannot lock away — a second copy
# of the app pointed at the same settings.json, an AV scanner, a backup
# agent. The lock above cannot span processes, so a contended `os.replace`
# gets a few short retries before it is allowed to fail.
_REPLACE_ATTEMPTS = 10
_REPLACE_RETRY_S = 0.02


class JsonSettings:
    """Lightweight JSON settings reader with dotted-key access."""

    def __init__(self, settings_path: str | Path) -> None:
        self._path = Path(settings_path)
        # Dotted keys mutated via `set()` since construction. `save()` applies
        # only these on top of the file's CURRENT content, so a concurrent
        # writer's unrelated keys survive instead of being clobbered by this
        # instance's construction-time snapshot.
        self._pending: set[str] = set()
        # Under the lock: an open read handle here would make a concurrent
        # save()'s os.replace fail on Windows (see _SAVE_LOCK).
        with _SAVE_LOCK:
            if not self._path.exists():
                self._data: dict = {}
                return
            with self._path.open("r", encoding="utf-8") as f:
                # Deliberately NOT caught: a file that is already corrupt when
                # you construct the reader is a real error the caller must see
                # (test_malformed_json_raises). Only the re-read inside save()
                # tolerates corruption — see _read_disk.
                self._data = json.load(f)

    def get(self, key: str, default: Any | None = None) -> Any:
        """Return value for dotted `key`, or `default` if not present.

        Returns the LIVE nested object, not a copy. Mutating it changes this
        instance's in-memory view but does **not** mark the key dirty, so
        ``save()`` will not persist that mutation: since #790 ``save()`` writes
        only the keys passed to :meth:`set`. Always route a change through
        ``set()``::

            cache = settings.get("scan.hash_pool_cache", {}) or {}
            cache[fp] = rates
            settings.set("scan.hash_pool_cache", cache)  # <- required
            settings.save()

        (Pinned by ``test_get_result_mutation_is_not_persisted_without_set``.)
        """
        parts = key.split(".")
        node: Any = self._data
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, key: str, value: Any) -> None:
        """Set a dotted `key` to `value` (creates missing intermediate dicts).

        An intermediate component that EXISTS but is not a dict raises
        ``TypeError`` naming the conflicting path instead of silently
        replacing the stored value with ``{}`` (#658 — the old behaviour
        destroyed data and still reported success). The caller decides how
        to resolve the conflict; nothing is mutated before the raise.

        This is the ONLY way to mark a change for persistence — see
        :meth:`get` for the mutation trap it replaces.
        """
        parts = key.split(".")
        node: Any = self._data
        for i, part in enumerate(parts[:-1]):
            if part not in node:
                node[part] = {}
            elif not isinstance(node[part], dict):
                conflict = ".".join(parts[: i + 1])
                raise TypeError(
                    f"settings key {conflict!r} holds a non-dict value "
                    f"({type(node[part]).__name__}); refusing to overwrite "
                    f"it to set {key!r}"
                )
            node = node[part]
        node[parts[-1]] = value
        # Setting a parent supersedes anything pending below it: the parent's
        # subtree now carries those values, and replaying a stale child path
        # afterwards would KeyError if the new subtree lacks it.
        prefix = f"{key}."
        self._pending = {k for k in self._pending if not k.startswith(prefix)}
        self._pending.add(key)

    def _read_disk(self) -> dict:
        """File content to merge onto, tolerating corruption mid-session.

        Falls back to this instance's own snapshot — never ``{}`` — when the
        file has become unreadable since construction. That reproduces the
        pre-#790 behaviour, which wrote ``_data`` wholesale and so healed a
        file truncated between construction and ``save()``; merging onto ``{}``
        instead would have quietly dropped every other key.
        """
        with _SAVE_LOCK:
            if not self._path.exists():
                return {}
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "settings file {} unreadable at save time ({}); merging onto "
                    "this session's snapshot instead of the file",
                    self._path,
                    exc,
                )
                return deepcopy(self._data)
            if not isinstance(loaded, dict):
                logger.warning(
                    "settings file {} holds a {}, not an object; merging onto "
                    "this session's snapshot instead",
                    self._path,
                    type(loaded).__name__,
                )
                return deepcopy(self._data)
            return loaded

    def save(self) -> None:
        """Persist this instance's ``set()`` changes to the settings file.

        Atomic against a crash: unique sibling temp file + flush + fsync +
        ``os.replace``, so an ill-timed kill truncates the temp file, never
        ``settings.json`` itself (#653). Same pattern as
        ``scanner/manifest.py``'s atomic manifest write (#464).

        Safe against a concurrent writer (#790 review). Two things make it so:

        * The temp file name is unique per call. It used to be a FIXED sibling
          (``settings.json.tmp``), so two savers wrote the same path and the
          second ``os.replace`` died with ``PermissionError``/WinError 32 —
          which the ``PATCH /api/settings`` handler then reported as HTTP 422
          with the update silently dropped.
        * The write is a read-merge-write under :data:`_SAVE_LOCK`, applying
          only the keys THIS instance changed on top of the file's current
          content. Writing the whole construction-time snapshot meant the
          slower of two concurrent savers erased the faster one's key.

        Only keys passed to :meth:`set` are written — see :meth:`get` for the
        in-place-mutation trap that implies.

        Cross-PROCESS writers (the Qt desktop app and the web server pointed at
        one settings.json) still resolve last-writer-wins for a contended key;
        the unique temp name plus a short retry keep that from raising, but a
        lock cannot span processes. Out of scope here.

        Raises:
            TypeError: a pending key needs a dict intermediate where the file
                on disk holds a scalar. Mirrors :meth:`set`'s #658 guarantee —
                the conflicting value is reported, never silently destroyed —
                and the file is left untouched.
        """
        with _SAVE_LOCK:
            merged = self._read_disk()
            # Parent-first: `set("a", {...})` followed by `set("a.b", 1)` must
            # apply the subtree before the leaf, or the leaf gets overwritten.
            for key in sorted(self._pending, key=lambda k: k.count(".")):
                parts = key.split(".")
                src: Any = self._data
                for part in parts:
                    src = src[part]
                node = merged
                for i, part in enumerate(parts[:-1]):
                    if part not in node:
                        node[part] = {}
                    elif not isinstance(node[part], dict):
                        conflict = ".".join(parts[: i + 1])
                        raise TypeError(
                            f"settings key {conflict!r} holds a non-dict value "
                            f"({type(node[part]).__name__}); refusing to overwrite "
                            f"it to set {key!r}"
                        )
                    node = node[part]
                # deepcopy so `merged` never aliases this instance's live
                # structures, which another thread may still be mutating.
                node[parts[-1]] = deepcopy(src)

            tmp = self._path.with_name(
                f"{self._path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
            )
            try:
                with tmp.open("w", encoding="utf-8") as f:
                    json.dump(merged, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                self._replace_with_retry(tmp)
            except BaseException:
                # Never leave a partial temp file behind; the original is
                # untouched because os.replace either ran or did not.
                tmp.unlink(missing_ok=True)
                raise

            self._data = merged
            self._pending.clear()

    def _replace_with_retry(self, tmp: Path) -> None:
        """``os.replace(tmp, path)``, retried briefly on PermissionError.

        In-process readers are already excluded by :data:`_SAVE_LOCK`; this
        covers the handles this process cannot see (the Qt app, an AV scanner),
        which hold the file open only momentarily.
        """
        last: PermissionError | None = None
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, self._path)
                return
            except PermissionError as exc:
                last = exc
                if attempt < _REPLACE_ATTEMPTS - 1:
                    time.sleep(_REPLACE_RETRY_S)
        assert last is not None  # loop only exits here after a PermissionError
        logger.warning(
            "settings file {} stayed locked by another process after {} attempts",
            self._path,
            _REPLACE_ATTEMPTS,
        )
        raise last


def _app_root() -> Path:
    """Return the directory this installation keeps its writable state in.

    Frozen (PyInstaller ``--onedir``): the directory holding the executable.
    NOT ``sys._MEIPASS`` — that is ``<exe>/_internal``, which an upgrade
    replaces wholesale, so a settings.json written there is lost on every
    new release (#882). ``_MEIPASS`` stays the home of the bundle's
    READ-ONLY assets (translations/, frontend/dist).

    Source checkout: the repository root (this file lives in
    ``<repo>/infrastructure/``).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def resolve_settings_path() -> Path:
    """Return the settings.json path for this process.

    The single resolver (#882). Before it there were four private copies —
    two in ``app/web/routes/``, two in ``core/app_service/`` — none of which
    knew about frozen builds, so the packaged app read and wrote
    ``<exe>/_internal/settings.json``.

    Resolution order:

    1. ``PHOTO_MANAGER_HOME``, if set. A relative value is resolved against
       the app root (so the variable is robust to cwd); an absolute value is
       used as-is. This is how the QA harness and the tests point a run at a
       throwaway config root.
    2. The app root itself — next to the executable when frozen, the repo
       root when running from source.
    """
    root = _app_root()
    home_env = os.environ.get("PHOTO_MANAGER_HOME")
    if home_env:
        root = (root / home_env).resolve()
    return root / "settings.json"


def load_settings() -> JsonSettings:
    """Return a :class:`JsonSettings` bound to :func:`resolve_settings_path`.

    The one construction site outside tests: every caller that wants "the
    app's settings" goes through here, so the resolution order above cannot
    drift back apart.

    Blocking (``Path.exists()`` + ``open()`` + ``json.load()``) — async
    handlers must hand it to a threadpool, never call it on the event loop.
    """
    return JsonSettings(resolve_settings_path())
