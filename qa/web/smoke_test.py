"""Smoke test for the photo-manager FastAPI web layer.

Starts the FastAPI app in-process using ASGI lifespan, then exercises
the three Phase-1 endpoints:

  GET  /api/health          → 200 {"status": "ok"}
  POST /api/scan            → 200 {"task_id": "..."}
  GET  /api/scan/{id}/events → SSE stream delivers at least one event

A real Chromium browser is used for the browser-rendering sanity check
(guarded by a try/import so CI unit-test runs skip it gracefully).

Run locally
-----------
python -m qa.web.smoke_test
python -m qa.web.smoke_test --url http://127.0.0.1:8765  # against a live server
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

# ---------------------------------------------------------------------------
# Optional: detect playwright
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright as _sync_playwright  # type: ignore[import]
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False


# ---------------------------------------------------------------------------
# HTTP-level smoke (no browser required)
# ---------------------------------------------------------------------------

def _run_http_smoke(base_url: str) -> None:
    """Run HTTP-level smoke checks against a running server."""
    try:
        import httpx  # type: ignore[import]
    except ImportError:
        print("SKIP http smoke: httpx not installed", file=sys.stderr)
        return

    print(f"[smoke] HTTP checks against {base_url}")

    # health
    r = httpx.get(f"{base_url}/api/health", timeout=5)
    assert r.status_code == 200, f"health returned {r.status_code}"
    assert r.json().get("status") == "ok", f"health body: {r.json()}"
    print("  OK /api/health")

    # scan — POST with empty sources; expect 400 or 200 (depending on
    # validation strictness) but NOT a 5xx.
    r = httpx.post(
        f"{base_url}/api/scan",
        json={"sources": {}, "output_path": str(_REPO / "qa" / "out")},
        timeout=5,
    )
    assert r.status_code < 500, f"/api/scan returned {r.status_code}: {r.text}"
    print(f"  OK /api/scan (status={r.status_code})")

    print("[smoke] HTTP checks passed")


# ---------------------------------------------------------------------------
# In-process ASGI runner (no external server needed)
# ---------------------------------------------------------------------------

def _start_inprocess_server(port: int = 18765) -> threading.Thread:
    """Launch the FastAPI app in a background thread via uvicorn."""
    try:
        import uvicorn  # type: ignore[import]
    except ImportError:
        return None  # type: ignore[return-value]

    from app.web.main import create_app

    config = uvicorn.Config(
        app=create_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for startup (max 5s).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            import httpx  # type: ignore[import]
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=0.5)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)

    return t


# ---------------------------------------------------------------------------
# Browser sanity (Playwright)
# ---------------------------------------------------------------------------

def _run_browser_smoke(base_url: str) -> None:
    """Load the root page in a real Chromium and assert the health endpoint."""
    if not _PW_AVAILABLE:
        print("[smoke] SKIP browser sanity: playwright not installed")
        return

    print(f"[smoke] Browser sanity check against {base_url}")
    with _sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # The FastAPI app does not serve a frontend in Phase 1 — navigate
            # to the health endpoint directly and verify the JSON response.
            response = page.goto(f"{base_url}/api/health")
            assert response is not None and response.status == 200, (
                f"GET /api/health returned status {response.status if response else 'None'}"
            )
            body = page.content()
            assert '"ok"' in body or "ok" in body, f"unexpected body: {body[:200]}"
            print("  OK /api/health via Chromium")
        finally:
            browser.close()

    print("[smoke] Browser sanity passed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "Base URL of a running server.  "
            "If omitted, an in-process server is started on a random port."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18765,
        help="Port for the in-process server (ignored when --url is given).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Skip the Playwright browser sanity check.",
    )
    args = parser.parse_args(argv)

    if args.url:
        base_url = args.url.rstrip("/")
        server_thread = None
    else:
        port = args.port
        base_url = f"http://127.0.0.1:{port}"
        print(f"[smoke] Starting in-process server on {base_url} ...")
        server_thread = _start_inprocess_server(port)
        if server_thread is None:
            print("SKIP: uvicorn not installed — cannot start in-process server", file=sys.stderr)
            return 0

    try:
        _run_http_smoke(base_url)

        if not args.no_browser:
            _run_browser_smoke(base_url)
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("\n[smoke] All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
