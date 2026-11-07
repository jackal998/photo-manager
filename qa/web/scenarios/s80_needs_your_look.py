"""Web scenario s80 — "Needs your look" under the high threshold (PR-3, #1047).

WEB-ONLY. There is no Qt driver: the desktop client was removed in #646 and
never had a confidence threshold. The layer-1 contracts (the level function,
the threshold, the group filter) are pinned by tests/test_auto_select.py and
frontend/src/lib/needsLook.test.ts / rowFilter.test.ts. What only a live run
settles is the whole path: the dialog's threshold select reaches the scan, the
stamps the backend writes, and the status-bar toggle narrows the real list.

Scan (aggressive auto-select, confirm threshold "high", two sources):
  - qa/sandbox/near-duplicates/  — REVIEW_DUPLICATE rows (pixels differ), which
    sit at the medium level. Under "high" that group needs a look.
  - a temp folder holding two BYTE copies of one qa/sandbox/unique JPEG — EXACT
    rows (high). That pair is admitted.

Asserts:
  1. Near-dup group: no row is stamped KEEP, none is marked delete, none is
     locked. The group is decided by nothing, so it is only shown under the
     toggle (#1047: aggressive mode must not delete a near-duplicate as a copy).
  2. Copy pair: exactly one row is KEEP and exactly one is marked delete, and
     no row is locked (PR-3 removed the keeper lock).
  3. The status-bar toggle reads 1 (one group needs a look) and is enabled.
  4. Pressing the toggle hides the copy pair and keeps the near-dup rows; the
     toggle reports aria-pressed="true".

zh_TW is not exercised here; the toggle's zh label is pinned by the i18n parity
test (tests/test_web_i18n.py).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    open_scan_dialog,
    set_output_path,
    start_scan,
    wait_manifest_loaded,
)
from qa.web.testid_constants import (
    MAIN_STATUS_NEEDS_LOOK,
    SCAN_ADVANCED,
    SCAN_AGGRESSIVE_DELETE,
    SCAN_AUTO_SELECT,
    SCAN_CONFIRM_BELOW,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_UNIQUE_JPEG = _REPO / "qa" / "sandbox" / "unique" / "unique_00.jpg"
_NEAR_DUP_NAMES = (
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
)
_COPY_NAMES = ("copy_a.jpg", "copy_b.jpg")


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _rows_by_name(manifest: dict) -> dict[str, dict]:
    """Return {basename: {group, action, user_decision, is_locked}}."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "group": str(group["group_number"]),
                "action": it.get("action", "") or "",
                "user_decision": it.get("user_decision", "") or "",
                "is_locked": bool(it.get("is_locked")),
            }
    return out


def _make_copy_folder(tmpdir: str) -> str:
    """Two byte-identical copies of one unique JPEG (EXACT by pHash and bytes)."""
    folder = os.path.join(tmpdir, "copies")
    os.makedirs(folder)
    for name in _COPY_NAMES:
        shutil.copyfile(_UNIQUE_JPEG, os.path.join(folder, name))
    return folder


def _check_manifest(rows: dict[str, dict]) -> None:
    near = [rows[n] for n in _NEAR_DUP_NAMES if n in rows]
    assert len(near) == 5, f"Expected 5 near-dup rows, got {sorted(rows)}"
    for r in near:
        assert r["action"] != "KEEP", f"near-dup row stamped KEEP under 'high': {r!r}"
        assert r["user_decision"] != "delete", (
            f"near-dup row marked delete under 'high' (#1047): {r!r}"
        )
    copies = [rows[n] for n in _COPY_NAMES if n in rows]
    assert len(copies) == 2, f"Expected 2 copy rows, got {sorted(rows)}"
    keeps = [r for r in copies if r["action"] == "KEEP"]
    deletes = [r for r in copies if r["user_decision"] == "delete"]
    assert len(keeps) == 1, f"copy pair must have exactly one KEEP: {copies!r}"
    assert len(deletes) == 1, f"copy pair must have exactly one delete: {copies!r}"
    locked = [n for n, r in rows.items() if r["is_locked"]]
    assert not locked, f"PR-3: no row may be locked after auto-select: {locked!r}"


def run(*, base_url: str) -> None:
    """Aggressive auto-select at "high": near-dup group needs a look, copies decided."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s80_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)
            page.get_by_test_id(SCAN_ADVANCED).click()
            page.get_by_test_id(SCAN_AUTO_SELECT).click()
            page.get_by_test_id(SCAN_AGGRESSIVE_DELETE).click()
            page.get_by_test_id(SCAN_CONFIRM_BELOW).select_option("high")

            add_scan_source(page, _NEAR_DUPS_DIR, idx=0)
            add_scan_source(page, _make_copy_folder(tmpdir), idx=1)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)

            manifest = _get_manifest(base_url, db_path)
            rows = _rows_by_name(manifest)
            print(f"probe_status: s80 rows={ {k: rows[k] for k in sorted(rows)} }")
            _check_manifest(rows)

            toggle = page.get_by_test_id(MAIN_STATUS_NEEDS_LOOK)
            toggle.wait_for(state="visible", timeout=10_000)
            label = toggle.inner_text().strip()
            assert label.endswith("1"), f"needs-look toggle must read 1; got {label!r}"
            assert toggle.is_enabled(), "needs-look toggle must be enabled at 1"

            near_group = rows[_NEAR_DUP_NAMES[0]]["group"]
            copy_group = rows[_COPY_NAMES[0]]["group"]
            toggle.click()
            assert toggle.get_attribute("aria-pressed") == "true", (
                "needs-look toggle must report aria-pressed=true once pressed"
            )
            page.get_by_test_id(
                row_file_testid(near_group, _NEAR_DUP_NAMES[0])
            ).wait_for(state="visible", timeout=10_000)
            hidden = page.get_by_test_id(row_file_testid(copy_group, _COPY_NAMES[0]))
            assert hidden.count() == 0, (
                "needs-look filter must hide the admitted copy pair"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
