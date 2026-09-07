"""Origin/Host CSRF guard on mutating web routes (#662 item 1).

Two layers: the pure policy function (``rejection_reason``) and the wired
middleware observed through real requests (a PATCH /api/settings probe for
the mutating path, GET /api/health for the safe path).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app
from app.web.security import rejection_reason


# ---------------------------------------------------------------------------
# Pure policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8765", "localhost:8765", "127.0.0.1", "[::1]:8765", "testserver"],
)
def test_loopback_hosts_without_origin_are_allowed(host):
    assert rejection_reason(host, None, None, dev_mode=False) is None


@pytest.mark.parametrize("host", [None, "", "evil.example:8765", "192.168.1.7:8765"])
def test_non_loopback_or_missing_host_is_refused(host):
    reason = rejection_reason(host, None, None, dev_mode=False)
    assert reason is not None and "Host" in reason


def test_dns_rebinding_shape_is_refused():
    # Rebound page: browser believes it is same-origin, so Origin MATCHES
    # the (attacker-hostname) Host — only the loopback Host check stops it.
    reason = rejection_reason(
        "evil.example:8765", "http://evil.example:8765", None, dev_mode=False
    )
    assert reason is not None and "Host" in reason


def test_same_origin_matches_host_port_exactly():
    assert (
        rejection_reason(
            "127.0.0.1:8765", "http://127.0.0.1:8765", None, dev_mode=False
        )
        is None
    )
    # localhost-vs-127.0.0.1 mismatch is refused: a browser tab always sends
    # an Origin equal to the URL it loaded, so a legitimate client never mixes.
    assert (
        rejection_reason(
            "127.0.0.1:8765", "http://localhost:8765", None, dev_mode=False
        )
        is not None
    )


def test_cross_origin_and_null_origin_are_refused():
    assert (
        rejection_reason(
            "127.0.0.1:8765", "http://evil.example", None, dev_mode=False
        )
        is not None
    )
    reason = rejection_reason("127.0.0.1:8765", "null", None, dev_mode=False)
    assert reason is not None and "null" in reason


def test_dev_origins_only_in_dev_mode():
    args = ("127.0.0.1:8765", "http://localhost:5173", None)
    assert rejection_reason(*args, dev_mode=True) is None
    assert rejection_reason(*args, dev_mode=False) is not None


def test_referer_fallback_applies_same_rule():
    # Referer carries a PATH; urlsplit compares only scheme + netloc, so a
    # same-origin page URL passes while a foreign one is refused.
    assert (
        rejection_reason(
            "127.0.0.1:8765", None, "http://127.0.0.1:8765/some/page", dev_mode=False
        )
        is None
    )
    assert (
        rejection_reason(
            "127.0.0.1:8765", None, "http://evil.example/attack", dev_mode=False
        )
        is not None
    )
    # Origin, when present, WINS over Referer (a cross-origin attacker's
    # browser sends its own Origin even if Referer is spoofable-looking).
    assert (
        rejection_reason(
            "127.0.0.1:8765",
            "http://evil.example",
            "http://127.0.0.1:8765",
            dev_mode=False,
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Wired middleware
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"ui": {"locale": "en"}}), encoding="utf-8")
    monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_path))
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


_PATCH_BODY = {"updates": {"ui.locale": "en"}}


def test_mutating_request_without_origin_is_allowed(client):
    resp = client.patch("/api/settings", json=_PATCH_BODY)
    assert resp.status_code == 200


def test_mutating_request_with_evil_origin_is_403(client):
    resp = client.patch(
        "/api/settings", json=_PATCH_BODY, headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
    assert "#662" in resp.json()["detail"]


def test_mutating_request_with_evil_host_is_403(client):
    resp = client.patch(
        "/api/settings", json=_PATCH_BODY, headers={"Host": "evil.example:8765"}
    )
    assert resp.status_code == 403


def test_mutating_request_same_origin_is_allowed(client):
    resp = client.patch(
        "/api/settings", json=_PATCH_BODY, headers={"Origin": "http://testserver"}
    )
    assert resp.status_code == 200


def test_safe_method_ignores_origin_and_host(client):
    resp = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 200
