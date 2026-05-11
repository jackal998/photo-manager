"""Tests for ``scripts/hooks/docs_guard.py`` — the PreToolUse hook that
blocks ``gh pr create`` when doc-relevant code changed without any
doc file being touched.

Mirror of test_qa_scenario_guard.py. The hook itself mirrors the QA
guard's shape — same stdin protocol, same bypass-token convention,
same exit-2-on-block contract.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "docs_guard.py"


def _load_hook():
    import importlib.util

    spec = importlib.util.spec_from_file_location("docs_guard", str(HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(
    monkeypatch,
    command: str,
    changed: list[str],
    added: list[str] | None = None,
) -> int:
    """Invoke ``main()`` with synthetic stdin + mocked diff helpers.

    ``added`` defaults to the same list as ``changed`` so tests can
    pass new files without restating them. To test the "modified
    existing file" branch, pass ``added=[]`` explicitly.
    """
    mod = _load_hook()
    monkeypatch.setattr(mod, "_changed_files", lambda: list(changed))
    monkeypatch.setattr(
        mod, "_new_files", lambda: set(changed if added is None else added)
    )
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    return mod.main()


# ── exit 0 paths (allowed) ────────────────────────────────────────────────


class TestAllow:
    def test_non_pr_command_passes(self, monkeypatch):
        rc = _run(monkeypatch, "git status", changed=[])
        assert rc == 0

    def test_pr_create_with_no_diff_passes(self, monkeypatch):
        rc = _run(monkeypatch, "gh pr create --title 'fix: typo'", changed=[])
        assert rc == 0

    def test_pr_create_with_doc_change_alongside_code_passes(self, monkeypatch):
        """The happy path — new module + README.md updated."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new dialog'",
            changed=[
                "app/views/dialogs/new_dialog.py",
                "README.md",
            ],
        )
        assert rc == 0

    def test_pr_create_with_only_docs_changed_passes(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'docs: clarify'",
            changed=["docs/testing.md"],
        )
        assert rc == 0

    def test_pr_create_with_only_translations_passes(self, monkeypatch):
        """Translation string updates don't structurally change anything;
        no doc update required."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'i18n: zh_TW polish'",
            changed=["translations/zh_TW.yml"],
        )
        assert rc == 0

    def test_modified_existing_module_passes_without_docs(self, monkeypatch):
        """Modifying an existing module (not adding a new one) is the
        common case — not every bug fix needs a doc update."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'fix: dialog button label'",
            changed=["app/views/dialogs/execute_action_dialog.py"],
            added=[],  # the file already exists; this is a modification
        )
        assert rc == 0

    def test_bypass_token_in_command_skips_check(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing [docs-not-needed: trivial]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0

    def test_bypass_token_in_body_skips_check(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat' --body 'whatever [docs-not-needed: x] more'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0

    def test_new_test_with_readme_passes(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'tests: cover new dialog'",
            changed=["tests/test_new_dialog.py", "README.md"],
        )
        assert rc == 0

    def test_scenario_change_with_testing_doc_passes(self, monkeypatch):
        """qa/scenarios/sNN change is doc-relevant on both add and modify."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'qa: extend s32'",
            changed=[
                "qa/scenarios/s32_lock_confirm_bulk_regex.py",
                "docs/testing.md",
            ],
            added=[],  # modified, not new
        )
        assert rc == 0


# ── exit 2 paths (blocked) ────────────────────────────────────────────────


class TestBlock:
    def test_new_dialog_without_docs_blocks(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new dialog'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "docs guard" in err.lower()
        assert "new_dialog.py" in err

    def test_new_handler_without_docs_blocks(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat'",
            changed=["app/views/handlers/new_handler.py"],
        )
        assert rc == 2

    def test_new_infrastructure_without_docs_blocks(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: cache service'",
            changed=["infrastructure/cache_service.py"],
        )
        assert rc == 2

    def test_new_scanner_module_without_docs_blocks(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new scanner stage'",
            changed=["scanner/new_stage.py"],
        )
        assert rc == 2

    def test_new_core_service_without_docs_blocks(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new service'",
            changed=["core/services/new_service.py"],
        )
        assert rc == 2

    def test_new_test_file_without_docs_blocks(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'tests: new module'",
            changed=["tests/test_new_module.py"],
        )
        assert rc == 2

    def test_scenario_rename_without_testing_doc_blocks(self, monkeypatch):
        """Scenario renames are doc-relevant even though both names
        appear under qa/scenarios/sNN_*.py."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'qa: rename s32'",
            changed=["qa/scenarios/s32_renamed.py"],
            added=[],  # treat as modification of existing scenario
        )
        assert rc == 2

    def test_block_message_mentions_suggested_doc(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "README.md" in err  # suggested doc file


# ── stdin / malformed-payload robustness ──────────────────────────────────


class TestStdinRobustness:
    def test_malformed_json_passes(self, monkeypatch):
        mod = _load_hook()
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        assert mod.main() == 0

    def test_missing_tool_name_passes(self, monkeypatch):
        mod = _load_hook()
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
        assert mod.main() == 0

    def test_non_bash_tool_passes(self, monkeypatch):
        mod = _load_hook()
        payload = json.dumps({"tool_name": "Read", "tool_input": {}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert mod.main() == 0
