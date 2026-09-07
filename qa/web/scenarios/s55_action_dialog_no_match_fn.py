"""Web scenario s55 — ActionDialog C1: no-match_fn informational placeholder.

Ported from qa/scenarios/s55_action_dialog_no_match_fn.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/unique (10 truly-unique JPEGs → 0 dedup groups, so the
    dialog_handler resolves ``match_fn=None``:
    ``if groups: match_fn = build_match_fn(groups)``).
  - Open the Set-Action dialog (manifest-gated, NOT groups-gated).
  - Assert the C1 placeholder state: the Simple op-combo + text input are
    DISABLED, the explanatory note is visible, and the Regex line edit stays
    interactive (the only usable path on this entry point).

Web slice (#689 / s55 — RegexPanel ``hasGroups=false`` branch):
  ActionDialog.tsx passes ``hasGroups={totalGroups > 0}`` to RegexPanel. When
  totalGroups===0 the panel disables ACTION_SIMPLE_OP + ACTION_SIMPLE_TEXT and
  renders ACTION_SIMPLE_DISABLED_NOTE; ACTION_REGEX_INPUT stays enabled —
  mirroring the desktop select_dialog.py:854-861 match_fn=None constructor
  branch. The disabled-Simple + enabled-Regex pair is the non-vacuity bracket:
  a broken always-enabled wiring fails the disabled assertions, and a broken
  whole-panel-disabled wiring fails the regex-enabled assertion.

Web divergences:
  D1. The web review view ORPHAN-DROPS single-member groups
      (manifest_repository.py:400, ``len(db_rows) < 2``), so a scan over 10
      unique files yields 0 displayable groups (GET /api/manifest
      ``total_groups == 0`` and 0 items) even though the SQLite manifest holds
      10 rows. The desktop s55 reads the 10 rows from SQLite; the web asserts
      the no-groups precondition via GET /api/manifest ``total_groups == 0``
      instead. The ActionDialog is still reachable because ``manifest.path`` is
      set — a real scan, NOT ``completed_empty`` (10 files ARE walked).
  D2. The note text is asserted via the rendered DOM (inner_text) instead of a
      Qt UIA window_text() probe.

Non-destructive: no decisions are written; no files are deleted.

Desktop source: qa/scenarios/s55_action_dialog_no_match_fn.py
Fixture:        qa/sandbox/unique (10 truly-unique JPEGs)
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    ACTION_MAIN_BUTTON,
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_SIMPLE_OP,
    ACTION_SIMPLE_TEXT,
    ACTION_SIMPLE_DISABLED_NOTE,
    ACTION_REGEX_INPUT,
)

_REPO = Path(__file__).resolve().parents[3]
_UNIQUE_DIR = str(_REPO / "qa" / "sandbox" / "unique")

# The note carries the established desktop wording. Either locale substring is
# accepted so a server left in zh_TW by a prior run still passes (mirrors the
# desktop s55 EXPECTED_NOTE_EN / EXPECTED_NOTE_ZH dual-check).
_NOTE_EN = "Write-through preview unavailable"
_NOTE_ZH = "即時預覽不可用"


def _manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    """Scan unique-only → 0 groups → assert the ActionDialog no-match_fn state."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the unique fixture (10 files, 0 dedup groups) ──
            run_scan(
                page,
                sources=[_UNIQUE_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: precondition — manifest loaded with ZERO groups ─────
            data = _manifest(base_url, db_path)
            total_groups = data.get("total_groups")
            assert total_groups == 0, (
                f"expected 0 dedup groups from the unique fixture (singleton-"
                f"drop), got total_groups={total_groups!r}; the no-match_fn "
                f"branch only fires when groups is empty"
            )

            # ── Step 3: the Action dialog is reachable (manifest-loaded gate,
            #            NOT groups-present) — desktop s55 contract ───────────
            action_btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
            action_btn.wait_for(state="visible", timeout=10_000)
            assert not action_btn.is_disabled(), (
                "main-action-button is disabled at 0 groups; the Action menu "
                "is gated by manifest-loaded, not by groups-present (a manifest "
                "IS loaded here)"
            )
            action_btn.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # Default field is the string field "File Name" → RegexPanel shown.
            field_combo = page.get_by_test_id(ACTION_FIELD_COMBO)
            field_combo.wait_for(state="visible", timeout=5_000)

            # ── Step 4: C1 placeholder — Simple op-combo + text DISABLED ─────
            simple_op = page.get_by_test_id(ACTION_SIMPLE_OP)
            simple_op.wait_for(state="visible", timeout=5_000)
            assert simple_op.is_disabled(), (
                "action-simple-op should be DISABLED at 0 groups (no match_fn "
                "→ write-through preview can't function); the disabled state "
                "may have been dropped from the hasGroups=false branch"
            )
            simple_text = page.get_by_test_id(ACTION_SIMPLE_TEXT)
            assert simple_text.is_disabled(), (
                "action-simple-text should be DISABLED at 0 groups (no match_fn)"
            )

            # ── Step 5: the explanatory note is visible with the wording ─────
            note = page.get_by_test_id(ACTION_SIMPLE_DISABLED_NOTE)
            note.wait_for(state="visible", timeout=5_000)
            note_text = (note.inner_text() or "").strip()
            assert _NOTE_EN in note_text or _NOTE_ZH in note_text, (
                f"action-simple-disabled-note should explain why the Simple "
                f"inputs are read-only; expected a note containing {_NOTE_EN!r} "
                f"(or the zh equivalent), got {note_text!r}"
            )

            # ── Step 6: Regex input stays interactive (the escape hatch) ─────
            #   Brackets the disabled assertions: a broken whole-panel-disabled
            #   wiring would fail HERE, so the test cannot pass vacuously by
            #   disabling everything.
            regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
            regex_input.wait_for(state="visible", timeout=5_000)
            assert not regex_input.is_disabled(), (
                "action-regex-input must stay enabled at 0 groups — it is the "
                "only interactive path on the no-match_fn branch (Simple is an "
                "informational placeholder, Regex is the usable escape hatch)"
            )

            print(
                f"probe_status: s55 total_groups={total_groups} "
                f"simple_disabled=True note={note_text!r} regex_enabled=True"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
