"""Settings access helpers for JSON-based configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonSettings:
    """Lightweight JSON settings reader with dotted-key access."""

    def __init__(self, settings_path: str | Path) -> None:
        self._path = Path(settings_path)
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

    def save(self) -> None:
        """Persist current data back to the settings file (atomic).

        Sibling temp file + flush + fsync + ``os.replace`` so an ill-timed
        kill truncates the temp file, never ``settings.json`` itself (#653).
        Same pattern as ``scanner/manifest.py``'s atomic manifest write (#464).
        """
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)
