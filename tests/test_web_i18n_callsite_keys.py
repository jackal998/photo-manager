"""Static probe: every t('web.…') call-site key in the frontend exists in en.yml.

A typo like ``t('web.toolbar.scann', 'Scan')`` silently renders the English
fallback forever — and renders English even in a zh_TW session, because the
typo'd key is in neither catalog. No runtime test catches it (the UI still
"works" in English), so the i18n is silently broken for that string with zero
signal (peer review A4).

This regex probe collects every ``web.*`` key referenced via ``t(...)`` /
``translate(...)`` in ``frontend/src`` and asserts each resolves in the English
catalog, so a typo fails CI instead of shipping a silently-untranslated string.
It runs in the layer-1 pytest suite (no Node/Playwright needed).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO = Path(__file__).resolve().parents[1]
_FRONTEND_SRC = _REPO / "frontend" / "src"
_EN_YML = _REPO / "translations" / "en.yml"

# Matches t('web.x.y', ...) / translate('web.x.y', ...) — and the double-quote
# and multi-line-call variants — capturing the first string argument. \s* spans
# the newline + indentation in wrapped calls (re.DOTALL not needed: \s matches \n).
_CALLSITE_RE = re.compile(r"""\b(?:t|translate)\(\s*['"](web\.[A-Za-z0-9_.]+)['"]""")


def _flatten(data: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for raw_key, value in data.items():
        full = f"{prefix}.{raw_key}" if prefix else str(raw_key)
        if isinstance(value, dict):
            out.update(_flatten(value, full))
        else:
            out[full] = "" if value is None else str(value)
    return out


def _en_web_keys() -> set[str]:
    raw = yaml.safe_load(_EN_YML.read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict):
        raw.pop("_meta", None)
    return {k for k in _flatten(raw) if k.startswith("web.")}


def _callsite_keys() -> dict[str, list[str]]:
    """Map each referenced web.* key -> the source files that reference it."""
    found: dict[str, list[str]] = {}
    for path in _FRONTEND_SRC.rglob("*.ts*"):
        name = path.name
        if name.endswith(".test.ts") or name.endswith(".test.tsx"):
            continue
        text = path.read_text(encoding="utf-8")
        for key in _CALLSITE_RE.findall(text):
            found.setdefault(key, []).append(str(path.relative_to(_REPO)))
    return found


def test_every_web_callsite_key_exists_in_en_catalog() -> None:
    catalog = _en_web_keys()
    callsites = _callsite_keys()
    # If this fires, the regex or the frontend path drifted — the probe would
    # otherwise pass vacuously and stop guarding anything.
    assert callsites, "probe found no t('web.…') call-sites — regex or path drift"
    missing = {k: v for k, v in callsites.items() if k not in catalog}
    assert not missing, (
        "t('web.…') keys referenced in the frontend but absent from "
        f"translations/en.yml (typo or missing catalog entry): {missing}"
    )
