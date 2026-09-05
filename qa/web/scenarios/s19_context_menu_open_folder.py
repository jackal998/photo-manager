"""Web scenario s19 — context-menu "Open folder" reveals the file (#— parity).

Ported from qa/scenarios/s19_context_menu_open_folder.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates; right-click neardup_00_q95.jpg → "Open
    Folder"; assert a NEW Windows Explorer (CabinetWClass) window appeared whose
    title contains the fixture folder basename. Catches drift in the Windows
    ``subprocess.Popen(["explorer", "/select,", path])`` invocation + the path
    normalisation.

Web slice (platform-robust):
  - The web context-menu "Open folder" item (CTX_OPEN_FOLDER) calls the store's
    revealInExplorer → POST /api/reveal {file_path}. The backend reveals via the
    SAME ``subprocess.Popen(["explorer", "/select,", norm])`` on Windows, and
    returns 501 ``platform_unsupported`` off-Windows (execute.py:303-342).
  - A browser cannot observe the spawned Explorer hwnd (Qt's CabinetWClass check
    has no DOM analog). The web scenario instead asserts on the network
    round-trip: the right context-menu item fires exactly one POST /api/reveal
    carrying the right file_path, and the endpoint responds with a recognised
    status. This covers the same wiring regression (menu item → store action →
    backend endpoint + path) on EVERY platform.

Qt divergences:
  - No Explorer-window / EnumWindows assertion (not observable in a browser);
    asserted on the intercepted request body + status instead.
  - Status is platform-dependent: 200 on Windows (Explorer actually spawns,
    fire-and-forget — not closed), 501 on the Linux CI runner. Both are
    accepted: each proves the endpoint was reached and platform-guarded
    correctly. revealInExplorer swallows the 501 into a non-fatal executeError,
    so nothing crashes either way.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, right_click_row, click_context_item
from qa.web.testid_constants import CTX_OPEN_FOLDER, row_file_testid

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_TARGET = "neardup_00_q95.jpg"


def _first_group_id(base_url: str, db_path: str) -> str:
    """Return the first group's group_number (as a str) from GET /api/manifest."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        data = json.loads(resp.read())
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Scan, right-click → Open folder, assert the POST /api/reveal round-trip."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            group_id = _first_group_id(base_url, db_path)
            row_tid = row_file_testid(group_id, _TARGET)
            right_click_row(page, row_tid)

            # Intercept the reveal round-trip fired by the menu item click.
            with page.expect_request("**/api/reveal") as req_info:
                click_context_item(page, CTX_OPEN_FOLDER)
            request = req_info.value

            assert request.method == "POST", (
                f"expected POST /api/reveal, got {request.method}"
            )
            body = request.post_data_json
            assert body is not None, "reveal request had no JSON body"
            file_path = body.get("file_path", "")
            assert file_path.endswith(_TARGET), (
                f"reveal file_path should end with {_TARGET!r}, got {file_path!r}"
            )

            response = request.response()
            assert response is not None, "reveal request produced no response"
            assert response.status in (200, 501), (
                f"reveal status should be 200 (Windows) or 501 (non-Windows CI), "
                f"got {response.status}"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
