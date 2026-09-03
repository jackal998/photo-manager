"""Settings access helpers for JSON-based configuration."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

# Serialises the read-merge-write in `save()` across every JsonSettings
# instance in this process. There are at least three concurrent writers
# (#790 review): the two `/api/settings` route handlers, which FastAPI now
# dispatches to its worker threadpool, and the scan worker thread persisting
# calibration via `scanner.autotune.store_read_knee` /
# `core.app_service.scan_runner.store_hash_pool_rates`. A lock in the route
# layer would not have covered that third writer.
_SAVE_LOCK = threading.Lock()


class JsonSettings:
    """Lightweight JSON settings reader with dotted-key access."""

    def __init__(self, settings_path: str | Path) -> None:
        self._path = Path(settings_path)
        # Dotted keys mutated via `set()` since construction. `save()` applies
        # only these on top of the file's CURRENT content, so a concurrent
        # writer's unrelated keys survive instead of being clobbered by this
        # instance's construction-time snapshot.
        self._pending: set[str] = set()
        if not self._path.exists():
            self._data: dict = {}
            return
        with self._path.open("r", encoding="utf-8") as f:
            self._data = json.load(f)

    def get(self, key: str, default: Any | None = None) -> Any:
        """Return value for dotted `key`, or `default` if not present."""
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
        self._pending.add(key)

    def _read_disk(self) -> dict:
        """Current file content, or ``{}`` when absent/unreadable-as-object."""
        if not self._path.exists():
            return {}
        with self._path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else {}

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

        Cross-PROCESS writers (the Qt desktop app and the web server pointed at
        one settings.json) still resolve last-writer-wins for a contended key;
        the unique temp name keeps that from raising, but a lock cannot span
        processes. Unchanged from before and out of scope here.
        """
        with _SAVE_LOCK:
            merged = self._read_disk()
            for key in self._pending:
                parts = key.split(".")
                src: Any = self._data
                for part in parts:
                    src = src[part]
                node = merged
                for part in parts[:-1]:
                    if not isinstance(node.get(part), dict):
                        node[part] = {}
                    node = node[part]
                node[parts[-1]] = src

            tmp = self._path.with_name(
                f"{self._path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
            )
            try:
                with tmp.open("w", encoding="utf-8") as f:
                    json.dump(merged, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                # Never leave a partial temp file behind; the original is
                # untouched because os.replace either ran or did not.
                tmp.unlink(missing_ok=True)
                raise

            self._data = merged
            self._pending.clear()
