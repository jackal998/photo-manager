"""Unit tests for the YAML-based translation catalog.

Each test targets a specific failure mode the Translator must avoid:
  * crashing on missing locale files (a typo in settings.json)
  * crashing on missing keys (a translator hasn't caught up to a new
    English string)
  * silently corrupting format placeholders
  * losing the locale's display name from the language picker
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infrastructure import i18n
from infrastructure.i18n import Translator, get_translator, init_translator, t


@pytest.fixture
def cat_dir(tmp_path: Path) -> Path:
    """Create a tmp translations dir with en + zh_TW + a partial fr_FR.

    The fr_FR file deliberately omits some keys so we can exercise the
    locale → en fallback chain.
    """
    en = {
        "_meta": {"display_name": "English", "locale": "en"},
        "greet": "Hello, {name}",
        "menu": {"file": "File", "exit": "Exit"},
        "count_files": "{n} files",
    }
    zh = {
        "_meta": {"display_name": "繁體中文", "locale": "zh_TW"},
        "greet": "你好,{name}",
        "menu": {"file": "檔案", "exit": "結束"},
        "count_files": "{n} 個檔案",
    }
    fr = {
        "_meta": {"display_name": "Français"},
        "greet": "Bonjour, {name}",
        # menu / count_files intentionally missing
    }
    (tmp_path / "en.yml").write_text(yaml.safe_dump(en, allow_unicode=True), encoding="utf-8")
    (tmp_path / "zh_TW.yml").write_text(yaml.safe_dump(zh, allow_unicode=True), encoding="utf-8")
    (tmp_path / "fr_FR.yml").write_text(yaml.safe_dump(fr, allow_unicode=True), encoding="utf-8")
    return tmp_path


def test_translator_loads_active_locale_strings(cat_dir: Path) -> None:
    """Real bug guarded: the active-locale lookup must return its own
    translation, not silently use the fallback."""
    tr = Translator("zh_TW", cat_dir)
    assert tr.t("menu.file") == "檔案"
    assert tr.t("menu.exit") == "結束"


def test_translator_falls_back_to_english_for_missing_keys(cat_dir: Path) -> None:
    """If a translator hasn't caught up to a new English string, the UI
    must still render — falling back to English, not a crash or empty cell."""
    tr = Translator("fr_FR", cat_dir)
    # fr_FR has greet but not menu — must fall back to English.
    assert tr.t("greet", name="Marie") == "Bonjour, Marie"
    assert tr.t("menu.file") == "File"
    assert tr.t("menu.exit") == "Exit"


def test_translator_returns_key_when_neither_locale_has_it(cat_dir: Path) -> None:
    """A missing key in BOTH the locale and the English fallback should
    surface visibly — the key string itself — so a developer notices the
    gap on first review, instead of seeing an empty label and assuming
    everything is fine."""
    tr = Translator("zh_TW", cat_dir)
    assert tr.t("nonexistent.key") == "nonexistent.key"


def test_translator_loads_english_when_locale_file_is_missing(cat_dir: Path) -> None:
    """A bad ui.locale value (typo, locale not yet shipped) must not
    crash startup; the user just gets English."""
    tr = Translator("klingon", cat_dir)
    # Strings dict is empty for the missing locale, so every lookup
    # falls through to English.
    assert tr.t("menu.file") == "File"
    assert tr.t("greet", name="Worf") == "Hello, Worf"


def test_translator_format_placeholder_substitution(cat_dir: Path) -> None:
    """Format args must substitute correctly — caller convenience aside,
    this is what makes pluralized status-bar messages work."""
    tr = Translator("en", cat_dir)
    assert tr.t("greet", name="World") == "Hello, World"
    assert tr.t("count_files", n=5) == "5 files"


def test_translator_format_with_unknown_placeholder_returns_unformatted(cat_dir: Path) -> None:
    """If a translator wrote ``{wrong}`` in their value, ``str.format``
    raises KeyError. The Translator must catch that and return the raw
    string so the UI shows something instead of crashing — a translator
    error becomes a visible glitch (great for review), not an outage."""
    bad_dir = cat_dir
    (bad_dir / "es_ES.yml").write_text(
        "_meta:\n  display_name: Español\nbad_fmt: 'Hola {nombre}'\n",
        encoding="utf-8",
    )
    tr = Translator("es_ES", bad_dir)
    # We pass `name` — the value expects `{nombre}`. Don't crash.
    assert tr.t("bad_fmt", name="Marie") == "Hola {nombre}"


def test_available_locales_uses_meta_display_name(cat_dir: Path) -> None:
    """The View → Language submenu reads display_name from each YAML.
    If the meta block isn't honored, users see codes like 'zh_TW'
    instead of '繁體中文' — confusing for non-developers."""
    tr = Translator("en", cat_dir)
    locales = dict(tr.available_locales())
    assert locales["en"] == "English"
    assert locales["zh_TW"] == "繁體中文"
    assert locales["fr_FR"] == "Français"


def test_available_locales_sorts_english_first(cat_dir: Path) -> None:
    """English is the canonical reference and the most likely default
    choice — putting it first in the menu reduces hunting."""
    tr = Translator("en", cat_dir)
    codes = [code for code, _ in tr.available_locales()]
    assert codes[0] == "en"
    # Remainder alphabetical.
    assert codes[1:] == sorted(codes[1:])


def test_init_translator_replaces_singleton(cat_dir: Path) -> None:
    """Tests rely on being able to swap the active locale between cases
    without process restart. Production calls init exactly once."""
    init_translator("en", cat_dir)
    assert get_translator().t("menu.file") == "File"
    init_translator("zh_TW", cat_dir)
    assert get_translator().t("menu.file") == "檔案"


def test_module_level_t_uses_active_translator(cat_dir: Path) -> None:
    """The module-level ``t()`` helper is what view code calls; it must
    delegate to whichever Translator was last installed."""
    init_translator("zh_TW", cat_dir)
    assert t("menu.exit") == "結束"


def test_malformed_yaml_root_raises(tmp_path: Path) -> None:
    """A YAML file whose root isn't a mapping (e.g. a list) is a
    structural bug; we want it to fail at startup with a clear error,
    not silently produce empty translations that look fine until a
    user clicks the menu."""
    (tmp_path / "en.yml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        Translator("en", tmp_path)


def test_missing_translations_dir_falls_back_silently(tmp_path: Path) -> None:
    """If the entire translations dir is missing (broken install / wrong
    cwd), the translator should still construct — every lookup falls
    through to the key itself, which is loud enough to debug."""
    nonexistent = tmp_path / "no_such_dir"
    tr = Translator("en", nonexistent)
    # No catalog loaded; both maps empty.
    assert tr.t("menu.file") == "menu.file"
    assert tr.available_locales() == []


def test_real_en_catalog_loads_in_repo() -> None:
    """Smoke test: the actual translations/en.yml shipped with the repo
    must load. Catches malformed YAML in the canonical reference at
    test-time rather than at app startup."""
    repo_root = Path(__file__).resolve().parent.parent
    tr = Translator("en", repo_root / "translations")
    # Spot-check a key from each major namespace.
    assert tr.t("menu.file.scan_sources")
    assert tr.t("scan_dialog.title")
    assert tr.t("execute_dialog.title")
    assert tr.t("main_window.title") == "Photo Manager"


def test_real_zh_tw_catalog_loads_in_repo() -> None:
    """Same as above for Traditional Chinese — guards against missing
    keys when a developer adds a new English string but forgets to
    update zh_TW.yml."""
    repo_root = Path(__file__).resolve().parent.parent
    tr = Translator("zh_TW", repo_root / "translations")
    # Spot-check that zh_TW is loaded (not falling through to English).
    assert tr.t("menu.file.scan_sources") == "掃描來源…"
    assert tr.t("scan_dialog.title") == "掃描來源"


def test_zh_tw_has_every_key_present_in_english() -> None:
    """Phase 1 commits to a fully-translated zh_TW. If a key exists in
    en.yml but not in zh_TW.yml, this catches it — preventing the
    'silently English' label drift that's hard to spot during review."""
    repo_root = Path(__file__).resolve().parent.parent
    en = Translator("en", repo_root / "translations")
    zh = Translator("zh_TW", repo_root / "translations")
    # Read internal _strings dicts — that's what's been flattened.
    en_keys = set(en._strings.keys())
    zh_keys = set(zh._strings.keys())
    missing = sorted(en_keys - zh_keys)
    assert not missing, f"zh_TW missing translations for: {missing}"


@pytest.fixture(autouse=True)
def _reset_translator():
    """Ensure each test starts with a clean module-level Translator.

    Without this, tests that call init_translator leak state into
    later tests, and the test_real_*_catalog tests get whatever the
    previous case set up."""
    yield
    i18n._translator = None
