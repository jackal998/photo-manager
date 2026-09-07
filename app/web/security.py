"""Origin/Host CSRF guard for mutating web routes (#662 item 1).

Threat model: the server binds loopback only, but "localhost app" alone is
not a guard — a hostile web page in the user's browser can fire
cross-origin ``fetch``/form POSTs at ``http://127.0.0.1:<port>`` (CSRF), and
DNS rebinding can point an attacker-controlled hostname at 127.0.0.1 so the
request *reaches* us with a foreign ``Host``. This middleware closes both
for every state-changing method:

- **Host allowlist** — the ``Host`` header's hostname must be loopback
  (``127.0.0.1`` / ``localhost`` / ``::1``). A DNS-rebound request arrives
  with the attacker's hostname and is refused. Side effect (deliberate,
  fail-safe): a deliberately network-bound instance serves reads but
  refuses mutations, until this policy is consciously revisited.
- **Origin same-origin check** — when the browser supplies an ``Origin``
  it must match the request's own ``host:port`` exactly (the WebView2
  shell and any loopback browser tab satisfy this trivially), or be one of
  the Vite dev origins when ``PHOTO_MANAGER_DEV_MODE=1`` (mirrors the CORS
  config in ``main.py``). A literal ``Origin: null`` (sandboxed-iframe
  CSRF vector) is refused, NOT treated as "absent".
- **Referer fallback** — only consulted when ``Origin`` is absent; same
  same-origin rule applied to its host:port.
- **No headers at all** → allowed: non-browser clients (tests, curl, the
  qa harness) send neither, and a browser cannot strip its own Origin on
  a cross-origin mutating request.

Safe methods (GET/HEAD/OPTIONS) pass through untouched — CORS preflights
keep working and the SSE stream is never wrapped (raw ASGI middleware, no
response buffering).
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# "testserver" is starlette TestClient's default Host (same precedent as
# Django's ALLOWED_HOSTS testserver carve-out). A bare single-label name is
# not publicly resolvable, so it cannot be used for DNS rebinding from the
# internet; an attacker who controls local DNS for it owns the box already.
_LOOPBACK_HOSTNAMES = frozenset(
    {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}
)
_DEV_ORIGINS = frozenset({"http://localhost:5173", "http://127.0.0.1:5173"})


def _hostname_of(host_header: str) -> str:
    """Extract the hostname from a ``Host`` header value (strip the port)."""
    host = host_header.strip().lower()
    if host.startswith("["):  # bracketed IPv6, e.g. [::1]:8765
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def rejection_reason(
    host: Optional[str],
    origin: Optional[str],
    referer: Optional[str],
    *,
    dev_mode: bool,
) -> Optional[str]:
    """Return why this mutating request must be refused, or None to allow.

    Pure function so the policy is unit-testable without an ASGI stack.
    """
    if not host or _hostname_of(host) not in _LOOPBACK_HOSTNAMES:
        return (
            f"mutating request refused: Host {host!r} is not a loopback host "
            "(DNS-rebinding guard, #662)"
        )

    source = origin
    source_name = "Origin"
    if source is None and referer:
        source, source_name = referer, "Referer"
    if source is None:
        return None  # non-browser client; browsers always send Origin here

    value = source.strip().lower()
    if value == "null":
        return (
            "mutating request refused: Origin 'null' (sandboxed-iframe CSRF "
            "vector) is not accepted (#662)"
        )
    if dev_mode and value in _DEV_ORIGINS:
        return None
    parts = urlsplit(value)
    if parts.scheme == "http" and parts.netloc == host.strip().lower():
        return None  # same-origin: scheme + exact host:port match
    return (
        f"mutating request refused: {source_name} {source!r} does not match "
        f"this server's origin http://{host} (CSRF guard, #662)"
    )


class OriginHostGuard:
    """Raw ASGI middleware applying :func:`rejection_reason` to mutations."""

    def __init__(self, app, *, dev_mode: Optional[bool] = None) -> None:
        self.app = app
        self._dev_mode = (
            dev_mode
            if dev_mode is not None
            else os.environ.get("PHOTO_MANAGER_DEV_MODE") == "1"
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        reason = rejection_reason(
            headers.get("host"),
            headers.get("origin"),
            headers.get("referer"),
            dev_mode=self._dev_mode,
        )
        if reason is not None:
            response = JSONResponse({"detail": reason}, status_code=403)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
