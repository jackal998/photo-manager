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
            raise FileNotFoundError(f"settings.json not found: {self._path}")
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
