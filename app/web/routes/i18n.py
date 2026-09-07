"""GET /api/i18n/{locale} — web.* translation catalog for the web UI."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from infrastructure.i18n import Translator

router = APIRouter()

# translations/ sits at the repo root — four directories above this file:
#   app/web/routes/i18n.py -> app/web/routes/ -> app/web/ -> app/ -> repo root
_TRANSLATIONS_DIR = Path(__file__).resolve().parents[3] / "translations"

# Only the ``web.*`` namespace is served to the browser. The desktop catalog
# carries ~300 Qt-only strings (menu mnemonics, tooltip HTML) the web UI never
# uses; serving them would be ~22 KB of dead payload per request and would leak
# internal desktop copy over HTTP. Every browser string in the web port lives
# under ``web.*``, so this prefix filter is the whole contract.
_WEB_PREFIX = "web."


@lru_cache(maxsize=8)
def _web_catalog(locale: str) -> dict[str, str]:
    """Flattened ``web.*`` strings for *locale*, cached (catalogs are static).

    ``Translator()`` re-reads en.yml plus the locale file on every
    construction; the cache makes a repeated locale-switch request O(1) after
    the first hit. The YAML files never change at runtime, so the cache never
    goes stale within a process.
    """
    tr = Translator(locale, _TRANSLATIONS_DIR)
    return {k: v for k, v in tr.strings.items() if k.startswith(_WEB_PREFIX)}


@lru_cache(maxsize=1)
def _available() -> tuple[tuple[str, str], ...]:
    """``(code, display_name)`` for every shipped locale, cached."""
    return tuple(Translator("en", _TRANSLATIONS_DIR).available_locales())


@router.get("/api/i18n/{locale}")
def get_i18n(locale: str) -> dict:
    """Return the ``web.*`` string catalog for *locale*.

    Args:
        locale: BCP-47-ish locale code, e.g. ``en`` or ``zh_TW``.

    Returns:
        200 ``{"locale": str, "strings": {web.dotted_key: value}, "available": [[code, name], ...]}``
        404 when the requested locale has no ``.yml`` file in ``translations/``.
        500 when the ``translations/`` directory is missing (broken install).
    """
    if not _TRANSLATIONS_DIR.is_dir():
        raise HTTPException(status_code=500, detail="translations directory not found")

    available = _available()
    if locale not in {code for code, _ in available}:
        raise HTTPException(status_code=404, detail=f"locale not available: {locale!r}")

    return {
        "locale": locale,
        # Copy so a caller (or FastAPI's encoder) can never mutate the cached dict.
        "strings": dict(_web_catalog(locale)),
        "available": [[code, name] for code, name in available],
    }
