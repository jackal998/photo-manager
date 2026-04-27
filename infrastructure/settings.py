"""Settings access helpers for JSON-based configuration."""

from __future__ import annotations

import json
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
        """Set a dotted `key` to `value` (creates intermediate dicts as needed)."""
        parts = key.split(".")
        node: Any = self._data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def save(self) -> None:
        """Persist current data back to the settings file."""
        with self._path.open("w", encoding="utf-8") as f:
            import json as _json
            _json.dump(self._data, f, indent=2, ensure_ascii=False)
