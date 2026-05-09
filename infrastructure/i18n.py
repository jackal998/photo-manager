"""Translation catalog loader and lookup helpers.

YAML-based string catalog. One file per locale under ``translations/``,
each carrying every UI string keyed by dotted path
(e.g. ``menu.file.scan_sources``). Phase 1 ships ``en.yml`` (canonical
reference) and ``zh_TW.yml`` (Traditional Chinese).

The ``t(key, **fmt)`` helper looks the key up in the active locale,
falls back to English, and finally falls back to the key itself so
missing keys never crash the UI — they just stay visibly untranslated,
which is the right failure mode for a translation gap.

Locale switching requires app restart: ``init_translator`` is called
once from ``main.py`` before any widget is constructed, so widget text
captured at import time gets the right language.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _flatten(data: Any, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into dotted-key form.

    Strings, ints, and floats become string values. Lists and other
    non-dict types are kept as-is at their dotted key (used by no
    catalog entries today, but harmless if added).
    """
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for raw_key, value in data.items():
        key = str(raw_key)
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, full))
        elif value is None:
            out[full] = ""
        else:
            out[full] = str(value)
    return out


class Translator:
    """Loads YAML catalogs and resolves dotted-key lookups."""

    def __init__(self, locale: str, translations_dir: Path) -> None:
        self._locale = locale
        self._dir = Path(translations_dir)
        # Always load English as the fallback — even when the locale
        # IS English, we keep the reference around for parity with
        # other locales (no special-case branches downstream).
        self._fallback: dict[str, str] = self._load_locale("en")
        if locale == "en":
            self._strings = self._fallback
        else:
            self._strings = self._load_locale(locale)
            # If the requested locale failed to load (file missing /
            # malformed beyond YAML's tolerance), self._strings will
            # be empty — fallback chain still hands the user English
            # rather than crashing or displaying raw keys everywhere.

    @property
    def locale(self) -> str:
        return self._locale

    def t(self, key: str, **fmt: object) -> str:
        """Resolve ``key`` in the active locale; format-substitute if asked.

        Lookup order: active locale → English → key string itself.
        ``key`` falls through visibly so missing translations are obvious
        at a glance during review.
        """
        value = self._strings.get(key)
        if value is None or value == "":
            value = self._fallback.get(key, key)
        if fmt:
            try:
                return value.format(**fmt)
            except (KeyError, IndexError):
                # Translator wrote {wrong_placeholder} or stripped a
                # placeholder by mistake. Return the un-formatted
                # string rather than crashing the UI.
                return value
        return value

    def available_locales(self) -> list[tuple[str, str]]:
        """Return ``[(code, display_name), …]`` for every ``.yml`` in the dir.

        Display name comes from each file's ``_meta.display_name``;
        falls back to the locale code if missing. Sorted with English
        first, then alphabetically — gives a stable menu order.
        """
        result: list[tuple[str, str]] = []
        for path in sorted(self._dir.glob("*.yml")):
            code = path.stem
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):
                continue
            display = code
            meta = data.get("_meta") if isinstance(data, dict) else None
            if isinstance(meta, dict):
                display = str(meta.get("display_name") or code)
            result.append((code, display))
        result.sort(key=lambda x: (x[0] != "en", x[0]))
        return result

    def _load_locale(self, code: str) -> dict[str, str]:
        path = self._dir / f"{code}.yml"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"Translation file {path} must be a YAML mapping at the root, "
                f"got {type(raw).__name__}"
            )
        # Drop the meta block — it's not user-facing strings, so it
        # shouldn't pollute the lookup namespace.
        raw.pop("_meta", None)
        return _flatten(raw)


_translator: Translator | None = None


def init_translator(locale: str, translations_dir: Path) -> Translator:
    """Initialize the process-global Translator singleton.

    Called exactly once at application startup, before any widget
    construction. Re-calling with the same locale is idempotent;
    re-calling with a different locale replaces the singleton (used by
    tests; production switches locale by restarting).
    """
    global _translator
    _translator = Translator(locale, translations_dir)
    return _translator


def get_translator() -> Translator:
    """Return the active Translator. Auto-initializes English-only if unset.

    The auto-init is a safety net for code paths that touch ``t()``
    before ``main.py`` runs (notably unit tests that import view modules
    in isolation). Production always hits ``init_translator`` first.
    """
    global _translator
    if _translator is None:
        _translator = Translator("en", Path(__file__).parent.parent / "translations")
    return _translator


def t(key: str, **fmt: object) -> str:
    """Module-level lookup helper. See ``Translator.t``."""
    return get_translator().t(key, **fmt)
