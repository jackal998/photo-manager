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
    behavioural_qualifies: bool = False,
) -> int:
    """Invoke ``main()`` with synthetic stdin + mocked diff helpers.

    ``added`` defaults to the same list as ``changed`` so tests can
    pass new files without restating them. To test the "modified
    existing file" branch, pass ``added=[]`` explicitly.

    ``behavioural_qualifies`` controls the return value of the
    ``_behavioural_modify_qualifies`` helper for every path queried
    in this run. Defaults to ``False`` so legacy tests that don't
    care about the #262 behavioural-modify gate stay below the
    threshold by default.
    """
    mod = _load_hook()
    monkeypatch.setattr(mod, "_changed_files", lambda: list(changed))
    monkeypatch.setattr(
        mod, "_new_files", lambda: set(changed if added is None else added)
    )
    monkeypatch.setattr(
        mod, "_behavioural_modify_qualifies", lambda path: behavioural_qualifies
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


# ── behavioural-modify trigger + strict-accept (#262) ─────────────────────


class TestBehaviouralModifyTrigger:
    """The #262 hardening: MODIFIED files under
    app/views/{dialogs,handlers}/ trigger the docs gate when the diff
    is non-trivial, AND they require docs/features.md specifically
    rather than just any doc touch. New files keep the legacy
    any-doc-touch semantic. Bypass token still works."""

    def test_below_threshold_modify_does_not_trigger(self, monkeypatch):
        """A trivial edit (< 10 lines, no signature change) doesn't
        fire the behavioural gate even on a dialog/handler file. This
        is what keeps the gate from blocking typo fixes."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'fix: copy tweak'",
            changed=["app/views/dialogs/execute_action_dialog.py"],
            added=[],
            behavioural_qualifies=False,
        )
        assert rc == 0

    def test_above_threshold_modify_without_features_blocks(self, monkeypatch, capsys):
        """The core enforcement — a non-trivial dialog edit must touch
        docs/features.md or the PR is blocked."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: scope execute to highlighted'",
            changed=["app/views/dialogs/execute_action_dialog.py"],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "docs/features.md" in err
        assert "execute_action_dialog.py" in err

    def test_above_threshold_modify_with_features_passes(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: scope execute to highlighted'",
            changed=[
                "app/views/dialogs/execute_action_dialog.py",
                "docs/features.md",
            ],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 0

    def test_handler_above_threshold_blocks_same_as_dialog(self, monkeypatch):
        """Handlers (file_operations.py, context_menu.py, etc.) ride
        the same gate as dialogs — both are user-visible behaviour."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new context menu entry'",
            changed=["app/views/handlers/context_menu.py"],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 2

    def test_above_threshold_with_only_testing_doc_blocks(self, monkeypatch):
        """docs/testing.md is not enough for a behavioural change —
        features.md is the canonical user-visible-behaviour doc."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat'",
            changed=[
                "app/views/dialogs/execute_action_dialog.py",
                "docs/testing.md",
            ],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 2

    def test_above_threshold_with_only_readme_blocks(self, monkeypatch):
        """README.md alone is not enough for a behavioural change."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat'",
            changed=[
                "app/views/dialogs/execute_action_dialog.py",
                "README.md",
            ],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 2

    def test_new_dialog_with_testing_doc_passes(self, monkeypatch):
        """NEW files keep the legacy 'any doc touch is enough'
        semantic — they're typically introducing new structure that
        the docs map rows already cover."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new dialog'",
            changed=[
                "app/views/dialogs/new_dialog.py",
                "docs/testing.md",
            ],
            added=["app/views/dialogs/new_dialog.py"],
            behavioural_qualifies=False,
        )
        assert rc == 0

    def test_bypass_token_works_for_behavioural(self, monkeypatch):
        """The bypass escape valve still works for behavioural-modify
        triggers — needed for genuine internal refactors that
        preserve behaviour byte-for-byte."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'refactor [docs-not-needed: pure refactor, no UX change]'",
            changed=["app/views/dialogs/execute_action_dialog.py"],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 0

    def test_above_threshold_workers_dir_not_in_behavioural_scope(self, monkeypatch):
        """Only dialogs/ and handlers/ are in the behavioural scope —
        workers/, components/, widgets/, layout/, viewmodels/ stay
        on the legacy any-doc-touch rule because they're internal
        plumbing (background QThreads, layout helpers, viewmodels)
        that don't independently shift user-facing UX."""
        # A modified worker file with no docs at all — should NOT
        # trigger because workers are not in the behavioural pattern
        # and not in the existing MODIFIED-trigger set.
        rc = _run(
            monkeypatch,
            "gh pr create --title 'fix: worker thread cleanup'",
            changed=["app/views/workers/scan_worker.py"],
            added=[],
            behavioural_qualifies=True,  # would have qualified if scope included workers
        )
        assert rc == 0
