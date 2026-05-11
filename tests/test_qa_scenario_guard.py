"""Tests for ``scripts/hooks/qa_scenario_guard.py`` — the PreToolUse hook
that blocks ``gh pr create`` when user-facing files changed without a
qa/scenarios/sNN_*.py driver.

Failure mode the hook is preventing: shipping a feature PR (e.g.
photo-manager#175 in its first iteration) that touches dialogs /
handlers / components but lacks layer-3 coverage. CLAUDE.md is the
spec; this hook + these tests are the enforcement.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "qa_scenario_guard.py"


def _load_hook(monkeypatch):
    """Import the hook module without executing main(). Patches
    sys.argv so ``if __name__ == '__main__'`` is suppressed via the
    ``runpy``-free direct-import path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "qa_scenario_guard", str(HOOK_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, command: str, changed: list[str]) -> int:
    """Invoke ``main()`` with a synthetic stdin payload + mocked diff."""
    mod = _load_hook(monkeypatch)
    monkeypatch.setattr(mod, "_changed_files", lambda: list(changed))
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    return mod.main()


# ── exit 0 paths (allowed) ────────────────────────────────────────────────


class TestAllow:
    def test_non_pr_command_passes(self, monkeypatch):
        rc = _run(monkeypatch, "git status", changed=[])
        assert rc == 0

    def test_pr_create_with_no_diff_passes(self, monkeypatch):
        """No files changed vs origin/master — nothing to enforce."""
        rc = _run(monkeypatch, "gh pr create --title 'fix: typo'", changed=[])
        assert rc == 0

    def test_pr_create_with_only_qa_changes_passes(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'qa: extend s14'",
            changed=["qa/scenarios/s14_action_by_regex.py"],
        )
        assert rc == 0

    def test_pr_create_with_only_test_changes_passes(self, monkeypatch):
        """Pure test refactors / docs / scanner-only / infra-only PRs do
        not touch user-facing UI surfaces — no QA scenario required."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'tests: refactor manifest fixtures'",
            changed=[
                "tests/test_manifest_repository.py",
                "infrastructure/manifest_repository.py",
            ],
        )
        assert rc == 0

    def test_pr_create_with_user_facing_AND_qa_passes(self, monkeypatch):
        """Happy path for a feature PR — user-facing change accompanied
        by a qa/scenarios/sNN_*.py driver."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: lock state'",
            changed=[
                "app/views/handlers/file_operations.py",
                "app/views/dialogs/execute_action_dialog.py",
                "qa/scenarios/s32_lock_confirm_bulk_regex.py",
            ],
        )
        assert rc == 0

    def test_bypass_token_in_title_skips_check(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'refactor: rename internal fn [qa-not-needed: pure rename, no behaviour change]'",
            changed=["app/views/handlers/file_operations.py"],
        )
        assert rc == 0

    def test_bypass_token_in_body_skips_check(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'fix' --body 'whatever [qa-not-needed: sweep] more'",
            changed=["app/views/dialogs/select_dialog.py"],
        )
        assert rc == 0

    def test_malformed_stdin_fails_open(self, monkeypatch):
        """Hook must not block on parse errors — fail open is safer than
        blocking every PR if stdin shape ever changes."""
        mod = _load_hook(monkeypatch)
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        assert mod.main() == 0

    def test_non_bash_tool_passes(self, monkeypatch):
        mod = _load_hook(monkeypatch)
        payload = json.dumps(
            {"tool_name": "Read", "tool_input": {"file_path": "x"}}
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        monkeypatch.setattr(mod, "_changed_files", lambda: [])
        assert mod.main() == 0


# ── exit 2 paths (block) ──────────────────────────────────────────────────


class TestBlock:
    @pytest.mark.parametrize("user_facing_file", [
        "app/views/handlers/file_operations.py",
        "app/views/handlers/context_menu.py",
        "app/views/dialogs/execute_action_dialog.py",
        "app/views/dialogs/select_dialog.py",
        "app/views/dialogs/scan_dialog.py",
        "app/views/components/menu_controller.py",
        "app/views/components/status_messages.py",
        "app/views/workers/scan_worker.py",
    ])
    def test_user_facing_change_without_qa_blocks(
        self, monkeypatch, user_facing_file, capsys
    ):
        """Every directory under our user-facing list must trip the guard."""
        rc = _run(
            monkeypatch,
            f"gh pr create --title 'feat: thing'",
            changed=[user_facing_file, "tests/test_thing.py"],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "QA-scenario guard" in err
        assert user_facing_file in err
        assert "qa-not-needed" in err  # bypass instructions surfaced

    def test_block_message_lists_all_offenders(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: lock state'",
            changed=[
                "app/views/handlers/file_operations.py",
                "app/views/dialogs/execute_action_dialog.py",
                "app/views/handlers/context_menu.py",
                "tests/test_file_operations.py",  # not user-facing but unrelated
            ],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "app/views/handlers/file_operations.py" in err
        assert "app/views/dialogs/execute_action_dialog.py" in err
        assert "app/views/handlers/context_menu.py" in err

    def test_test_only_qa_change_does_not_satisfy(self, monkeypatch):
        """A test file that happens to live under tests/ named test_qa_*.py
        is NOT a layer-3 driver — only files matching
        ``qa/scenarios/sNN_*.py`` count."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing'",
            changed=[
                "app/views/dialogs/select_dialog.py",
                "tests/test_qa_helpers.py",  # NOT a qa/scenarios/ file
            ],
        )
        assert rc == 2

    def test_qa_uia_helper_does_not_satisfy(self, monkeypatch):
        """``qa/scenarios/_uia.py`` and other ``_*.py`` helpers don't
        satisfy the rule — must be a numbered ``sNN_*.py`` driver."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing'",
            changed=[
                "app/views/handlers/context_menu.py",
                "qa/scenarios/_uia.py",
            ],
        )
        assert rc == 2


# ── bypass token edge cases ───────────────────────────────────────────────


class TestBypassTokenShape:
    def test_bypass_with_complex_reason_works(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed: covered by existing s14, no new flow]'",
            changed=["app/views/handlers/file_operations.py"],
        )
        assert rc == 0

    def test_bypass_with_unicode_reason_works(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed: 翻譯字串更新]'",
            changed=["app/views/handlers/file_operations.py"],
        )
        assert rc == 0

    def test_unclosed_bracket_does_not_count(self, monkeypatch):
        """Defensive: a malformed token like ``[qa-not-needed: forgot``
        without closing ``]`` must NOT count as bypass — otherwise an
        accidental edit could disable enforcement."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed: forgot to close'",
            changed=["app/views/handlers/file_operations.py"],
        )
        assert rc == 2

    def test_similar_but_wrong_token_does_not_count(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa: not needed]'",
            changed=["app/views/handlers/file_operations.py"],
        )
        assert rc == 2
