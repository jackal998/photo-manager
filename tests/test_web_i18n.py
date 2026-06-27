"""Layer-1 unit tests for GET /api/i18n/{locale} and the ui.locale settings key.

Each test guards a specific failure mode:
  * wrong or missing key in a locale's string map
  * 404 for an unknown locale code
  * available-locales list omits a shipped locale
  * settings allowlist silently rejects ui.locale
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.web.main import create_app


def _client() -> TestClient:
    """Build a TestClient with no frontend dist (pure API mode)."""
    from pathlib import Path

    return TestClient(create_app(frontend_dist=Path("/nonexistent")), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# /api/i18n/en
# ---------------------------------------------------------------------------


def test_en_locale_returns_200() -> None:
    """The English locale must always be available and return 200."""
    client = _client()
    resp = client.get("/api/i18n/en")
    assert resp.status_code == 200


def test_en_locale_has_web_toolbar_scan() -> None:
    """web.toolbar.scan must resolve to 'Scan' in English.

    Real failure mode: the web.* YAML block is malformed or the _flatten()
    call skips nested keys, leaving the web UI with a raw dotted key."""
    client = _client()
    body = client.get("/api/i18n/en").json()
    assert body["strings"]["web.toolbar.scan"] == "Scan"


def test_en_locale_shape() -> None:
    """Response must include locale, strings, and available fields."""
    client = _client()
    body = client.get("/api/i18n/en").json()
    assert body["locale"] == "en"
    assert isinstance(body["strings"], dict)
    assert isinstance(body["available"], list)


def test_en_available_contains_both_locales() -> None:
    """Both 'en' and 'zh_TW' must appear in the available list.

    Real failure mode: available_locales() skips a file because of a glob
    or sort bug, hiding the locale from the language picker."""
    client = _client()
    body = client.get("/api/i18n/en").json()
    codes = [item[0] for item in body["available"]]
    assert "en" in codes
    assert "zh_TW" in codes


# ---------------------------------------------------------------------------
# /api/i18n/zh_TW
# ---------------------------------------------------------------------------


def test_zh_tw_locale_returns_200() -> None:
    """Traditional Chinese must be available."""
    client = _client()
    resp = client.get("/api/i18n/zh_TW")
    assert resp.status_code == 200


def test_zh_tw_toolbar_scan_is_chinese() -> None:
    """web.toolbar.scan must resolve to the Chinese string in zh_TW.

    Real failure mode: Translator falls back to English because the zh_TW
    web.* block is missing or the key path differs from the en.yml path."""
    client = _client()
    body = client.get("/api/i18n/zh_TW").json()
    assert body["strings"]["web.toolbar.scan"] == "掃描"


def test_zh_tw_all_web_keys_present() -> None:
    """Every web.* key the UI renders must exist in zh_TW strings (en↔zh parity).

    Real failure mode: one key was added to en.yml but omitted from zh_TW.yml,
    causing the web UI to silently render English in an otherwise Chinese session."""
    expected_keys = [
        "web.toolbar.scan",
        "web.toolbar.execute",
        "web.toolbar.settings",
        "web.toolbar.open",
        "web.status.summary",
        "web.status.loading_manifest",
        "web.status.scanning",
        "web.status.scan_failed",
        "web.status.manifest_failed",  # #712 — manifest.error render line
        "web.status.ready",
        "web.empty_state.no_manifest",
        "web.manifest.input_placeholder",
        "web.manifest.input_aria",
    ]
    client = _client()
    body = client.get("/api/i18n/zh_TW").json()
    strings = body["strings"]
    missing = [k for k in expected_keys if k not in strings]
    assert not missing, f"zh_TW missing web keys: {missing}"


def test_strings_are_web_namespace_only() -> None:
    """The route serves ONLY web.* keys — not the ~300 Qt-only desktop strings.

    Real failure mode: a regression drops the web.* filter and the route ships
    the full catalog, leaking internal desktop copy (menu mnemonics, tooltip
    HTML) over HTTP and bloating every locale-switch response ~22x. It surfaces
    as a Qt-only key (e.g. menu.file.scan_sources) appearing in the payload."""
    client = _client()
    strings = client.get("/api/i18n/en").json()["strings"]
    assert strings, "web catalog is empty"
    non_web = [k for k in strings if not k.startswith("web.")]
    assert not non_web, f"non-web keys leaked into the i18n payload: {non_web[:5]}"
    # Spot-check a known Qt-only key is absent from the web payload.
    assert "menu.file.scan_sources" not in strings


# ---------------------------------------------------------------------------
# Unknown locale → 404
# ---------------------------------------------------------------------------


def test_bogus_locale_returns_404() -> None:
    """A locale code with no .yml file must return 404, not 200 with empty strings.

    Real failure mode: Translator silently loads English as fallback and the
    route returns 200, masking the bad locale code from the caller."""
    client = _client()
    resp = client.get("/api/i18n/bogus")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# settings allowlist — ui.locale
# ---------------------------------------------------------------------------


def test_ui_locale_is_in_settings_allowlist() -> None:
    """ui.locale must be in _WEB_SETTINGS_KEYS so the web app can persist locale choice.

    Real failure mode: the key is absent from the tuple, so PATCH /api/settings
    returns 400 and the locale preference is silently discarded on every reload."""
    from app.web.routes.settings import _WEB_SETTINGS_KEYS

    assert "ui.locale" in _WEB_SETTINGS_KEYS


def test_patch_settings_accepts_ui_locale(tmp_path) -> None:
    """PATCH /api/settings must accept ui.locale without a 400 error.

    Real failure mode: allowlist check fires because ui.locale is missing,
    returning 400 and leaving the user unable to persist language preference."""
    import os

    # Point PHOTO_MANAGER_HOME at a tmp dir so the route uses a fresh settings.json.
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")

    env_patch = {"PHOTO_MANAGER_HOME": str(tmp_path)}
    from pathlib import Path

    client = TestClient(create_app(frontend_dist=Path("/nonexistent")), raise_server_exceptions=True)

    original_env = os.environ.copy()
    os.environ.update(env_patch)
    try:
        resp = client.patch("/api/settings", json={"updates": {"ui.locale": "zh_TW"}})
    finally:
        os.environ.clear()
        os.environ.update(original_env)

    # 200 or 422 (disk write may fail in tmp) — but NOT 400 (allowlist rejection).
    assert resp.status_code != 400, f"ui.locale was rejected by the allowlist: {resp.json()}"
