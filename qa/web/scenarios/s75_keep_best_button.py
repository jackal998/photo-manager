"""Web scenario s75 — "Keep best · delete rest" in the group header (#878).

WEB-ONLY. Layout slice G promotes the #744 right-click action to a visible
button on the group header, per open questions Q5: «Promote it to the group
header, and do not add a confirm step — add undo instead … it is the fast path
through the entire screen and a right-click action is invisible to the users
who most need it».

s72 already covers the ACTION's manifest semantics through the context menu.
What this scenario exists for is the three things Q5 attached to the promoted
button, none of which a unit test can settle:

  1. **It never overrides a lock, with no dialog in the way.** The button
     passes ``skip_locked``, so a locked row keeps both its decision and its
     lock — and, crucially, the user is NOT interrupted by the LockConfirmDialog
     the right-click item still raises. Those are two different flows through
     the same store action, and only a live run exercises the button's.
  2. **The toast says what happened, in the user's language.** Q5 made the
     toast the replacement for a confirm step, so its sentence is the entire
     account of a bulk write: the group, the count, and «1 locked file
     unchanged». Asserted in BOTH locales, because a toast that silently falls
     back to English is exactly the R8 defect the group header itself had.
  3. **Undo actually reverses it.** The toast's Undo is the only way back from
     a bulk decision write. A jsdom test can prove the button dispatches; only
     a live run proves the decisions and locks come BACK — including the lock
     apply-best-copy put on the keeper, which is the thing that would otherwise
     refuse the undo's own write.

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs, one group) — the same fixture
s72/s73/s74 use, and the same keeper/non-keeper split s57 pins.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    MAIN_LANG_TOGGLE,
    MAIN_TOAST,
    MAIN_TOAST_UNDO,
    row_group_keep_best_testid,
    row_lock_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_EXPECTED_KEEPER = "neardup_00_q95.jpg"
# Classifier actions the service treats as a positively-identified duplicate
# (mirrors core.services.auto_select._DUPLICATE_ACTIONS). A ref-tier non-keeper
# is the reference and is never a write target — so it is never a row whose
# lock could be "skipped", and picking it as the locked victim would make this
# scenario green for the wrong reason.
_DUPLICATE_ACTIONS = frozenset({"REVIEW_DUPLICATE", "EXACT"})


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _collect(manifest: dict) -> dict[str, dict]:
    """Return {basename: {action, user_decision, is_locked}} across all groups."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "action": it.get("action", "") or "",
                "user_decision": it.get("user_decision", "") or "",
                "is_locked": bool(it.get("is_locked")),
            }
    return out


def _patch_locale(base_url: str, locale: str) -> None:
    """PATCH /api/settings to set ui.locale — the restore step.

    `ui.locale` is persisted SERVER-side, so a scenario that toggles the
    language leaves every scenario after it in the batch running against a
    zh_TW UI. That is not hypothetical: the first live run of this file left
    the server in zh_TW and s74 then timed out for two minutes waiting on a
    status bar whose English regex could never match 「1 個群組 · 5 個檔案」.
    Same restore contract as s22_language_switch.
    """
    url = f"{base_url.rstrip('/')}/api/settings"
    body = json.dumps({"updates": {"ui.locale": locale}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"PATCH /api/settings failed ({exc.code}): {exc.read().decode()}"
        ) from exc


def _click_keep_best(page, group_id: str) -> None:
    """Click the header button and block until ITS write has responded."""
    button = page.get_by_test_id(row_group_keep_best_testid(group_id))
    button.wait_for(state="visible", timeout=10_000)
    with page.expect_response(
        lambda r: "/api/action/apply-best-copy" in r.url
        and r.request.method == "POST",
        timeout=15_000,
    ):
        button.click()
    page.wait_for_timeout(400)


def run(*, base_url: str) -> None:
    """Lock one duplicate, press the header button, read the toast, undo."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s75_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto("/")

            # Plain scan — auto-select OFF, every row starts undecided/unlocked.
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            manifest_data = _get_manifest(base_url, db_path)
            assert manifest_data["total_groups"] >= 1, "Expected at least one group"
            group_number = manifest_data["groups"][0]["group_number"]
            group_id = str(group_number)

            pre = _collect(manifest_data)
            assert len(pre) == 5, f"Expected 5 near-dup rows, got {sorted(pre)}"
            for name, row in pre.items():
                assert row["user_decision"] == "" and row["is_locked"] is False, (
                    f"Pre-condition violated: {name} already decided/locked: {row}"
                )

            # The locked victim must be a row the write WOULD otherwise touch —
            # a positively-classified duplicate that is not the keeper. Chosen
            # from the manifest rather than hardcoded: which variant classifies
            # as the ref tier is platform-dependent (s57 D3 / s72).
            duplicates = sorted(
                name
                for name, row in pre.items()
                if name != _EXPECTED_KEEPER and row["action"] in _DUPLICATE_ACTIONS
            )
            print(f"probe_status: s75 duplicate non-keepers = {duplicates}")
            assert len(duplicates) >= 2, (
                "s75 needs at least two duplicate non-keepers — one to lock and "
                f"one to be marked for deletion. Got {duplicates}."
            )
            locked_victim = duplicates[0]
            expect_deleted = duplicates[1:]

            # ── 1. Lock one duplicate through the UI ────────────────────────
            lock = page.get_by_test_id(row_lock_testid(group_id, locked_victim))
            lock.wait_for(state="visible", timeout=10_000)
            with page.expect_response(
                lambda r: "/api/lock" in r.url and r.request.method == "PATCH",
                timeout=15_000,
            ):
                lock.click()
            page.wait_for_timeout(300)
            after_lock = _collect(_get_manifest(base_url, db_path))
            assert after_lock[locked_victim]["is_locked"] is True, (
                f"s75 setup: {locked_victim} did not lock; the rest of this "
                "scenario would pass while testing nothing about locks."
            )

            # ── 2. Press the header button (EN) ─────────────────────────────
            _click_keep_best(page, group_id)

            post = _collect(_get_manifest(base_url, db_path))
            print(f"probe_status: s75 post keep-best state = {post}")

            # Q5: «it must never override a lock».
            assert post[locked_victim]["is_locked"] is True, (
                f"Q5 — the keep-best button UNLOCKED {locked_victim}. The button "
                "must pass skip_locked; a locked row keeps its lock."
            )
            assert post[locked_victim]["user_decision"] == "", (
                f"Q5 — the keep-best button wrote "
                f"{post[locked_victim]['user_decision']!r} onto the locked row "
                f"{locked_victim}. A lock means the decision is not the "
                "button's to change."
            )
            for name in expect_deleted:
                assert post[name]["user_decision"] == "delete", (
                    f"The unlocked duplicate {name} is "
                    f"{post[name]['user_decision']!r}, expected 'delete' — the "
                    "button skipped more than the locked row."
                )
            assert post[_EXPECTED_KEEPER]["user_decision"] == "", (
                f"The keeper {_EXPECTED_KEEPER} is "
                f"{post[_EXPECTED_KEEPER]['user_decision']!r}, expected ''."
            )
            assert post[_EXPECTED_KEEPER]["is_locked"] is True, (
                f"The keeper {_EXPECTED_KEEPER} was not locked by the write."
            )

            # ── 3. The toast, in English ────────────────────────────────────
            toast = page.get_by_test_id(MAIN_TOAST)
            toast.wait_for(state="visible", timeout=5_000)
            toast_en = toast.inner_text()
            print(f"probe_status: s75 toast (en) = {toast_en!r}")
            assert f"Group {group_number}" in toast_en, (
                f"The toast does not name the group: {toast_en!r}"
            )
            assert "marked for deletion" in toast_en, (
                f"The toast does not say what it did: {toast_en!r}"
            )
            assert str(len(expect_deleted)) in toast_en, (
                f"The toast does not carry the count "
                f"{len(expect_deleted)}: {toast_en!r}"
            )
            # Q5: «it must say so when it skips one».
            assert "1 locked file unchanged" in toast_en, (
                "The toast does not report the locked row it skipped — the "
                f"promise Q5 attached to this button: {toast_en!r}"
            )
            assert "Undo" in toast_en, f"The toast has no Undo: {toast_en!r}"

            # ── 4. Undo restores the decisions AND the locks ────────────────
            with page.expect_response(
                lambda r: "/api/lock" in r.url and r.request.method == "PATCH",
                timeout=15_000,
            ):
                page.get_by_test_id(MAIN_TOAST_UNDO).click()
            page.wait_for_timeout(500)

            undone = _collect(_get_manifest(base_url, db_path))
            print(f"probe_status: s75 post-undo state = {undone}")
            assert undone == after_lock, (
                "Undo did not restore the group to its pre-button state.\n"
                f"  expected: {after_lock}\n  got:      {undone}"
            )
            # In particular the keeper's lock — the one apply-best-copy CREATED,
            # and the one that refuses the undo's own decision write unless it
            # is forced.
            assert undone[_EXPECTED_KEEPER]["is_locked"] is False, (
                f"Undo left {_EXPECTED_KEEPER} locked; the lock was created by "
                "the write it was reversing."
            )
            page.get_by_test_id(MAIN_TOAST).wait_for(state="detached", timeout=5_000)

            # ── 5. The same toast in zh-TW ──────────────────────────────────
            page.get_by_test_id(MAIN_LANG_TOGGLE).click()
            page.wait_for_timeout(800)
            _click_keep_best(page, group_id)

            toast_zh = page.get_by_test_id(MAIN_TOAST)
            toast_zh.wait_for(state="visible", timeout=5_000)
            zh_text = toast_zh.inner_text()
            print(f"probe_status: s75 toast (zh_TW) = {zh_text!r}")
            assert f"群組 {group_number}" in zh_text, (
                f"zh_TW toast does not name the group: {zh_text!r}"
            )
            assert "待刪除" in zh_text, (
                f"zh_TW toast does not say what it did: {zh_text!r}"
            )
            assert "1 個鎖定的檔案未變更" in zh_text, (
                f"zh_TW toast does not report the skipped lock: {zh_text!r}"
            )
            assert "復原" in zh_text, f"zh_TW toast has no Undo: {zh_text!r}"
            # The R8 defect, one surface over: English leaking into a zh session.
            for leaked in ("Group", "marked for deletion", "Undo"):
                assert leaked not in zh_text, (
                    f"English {leaked!r} leaked into the zh_TW toast: {zh_text!r}"
                )
    finally:
        # ALWAYS put the server back to English, whether or not step 5 ran —
        # `ui.locale` lives in the server's settings file, not in the browser
        # context PWContext throws away.
        _patch_locale(base_url, "en")
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
