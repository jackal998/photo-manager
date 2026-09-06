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


# ── manifest_repository.py semantics-aware schema gate ────────────────────


class TestManifestRepositorySchemaGate:
    """The manifest_repository.py MODIFIED trigger is semantics-aware: it
    only fires when the diff actually touches schema-defining content
    (``_MIGRATIONS``, ``CREATE TABLE``, ``ADD COLUMN``,
    ``_POST_DROP_COLUMNS``, ``_DDL``). A pure refactor of that file with
    none of those tokens in the +/- lines previously false-positived and
    blocked a PR asking for an unwarranted README schema-table update.

    These tests exercise the real ``_manifest_repository_touches_schema``
    function (via a mocked ``subprocess.check_output``), not a
    reimplementation.
    """

    def _run_manifest(self, monkeypatch, diff_text: str, command: str) -> int:
        mod = _load_hook()
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "infrastructure/manifest_repository.py",
        ])
        monkeypatch.setattr(mod, "_new_files", lambda: set())  # modified, not new

        def fake_check_output(cmd, **kwargs):
            return diff_text

        monkeypatch.setattr(mod.subprocess, "check_output", fake_check_output)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        return mod.main()

    def test_non_schema_refactor_not_flagged(self, monkeypatch):
        """Extracting an existing migration into a shared helper (or any
        other refactor that never touches a schema marker) must NOT
        require a doc update — this is the regression the gate fixes."""
        diff_text = (
            "--- a/infrastructure/manifest_repository.py\n"
            "+++ b/infrastructure/manifest_repository.py\n"
            "@@ -40,3 +40,4 @@\n"
            "-def _old_helper(conn):\n"
            "+def _shared_helper(conn, extra):\n"
            "+    return extra\n"
        )
        rc = self._run_manifest(
            monkeypatch,
            diff_text,
            "gh pr create --title 'refactor: extract shared helper'",
        )
        assert rc == 0

    def test_migrations_marker_still_flagged(self, monkeypatch, capsys):
        """A diff that adds a ``_MIGRATIONS`` entry must still block
        without a doc update."""
        diff_text = (
            "--- a/infrastructure/manifest_repository.py\n"
            "+++ b/infrastructure/manifest_repository.py\n"
            "@@ -84,3 +84,4 @@\n"
            '+    ("new_col", "TEXT"),  # _MIGRATIONS entry\n'
        )
        rc = self._run_manifest(
            monkeypatch, diff_text, "gh pr create --title 'feat: add column'"
        )
        assert rc == 2
        assert "manifest_repository.py" in capsys.readouterr().err

    def test_add_column_marker_still_flagged(self, monkeypatch):
        """``ADD COLUMN`` in the diff is a schema marker on its own,
        independent of ``_MIGRATIONS``."""
        diff_text = (
            "--- a/infrastructure/manifest_repository.py\n"
            "+++ b/infrastructure/manifest_repository.py\n"
            "@@ -250,2 +250,3 @@\n"
            '+        f"ADD COLUMN {col} {ddl}"\n'
        )
        rc = self._run_manifest(
            monkeypatch, diff_text, "gh pr create --title 'feat: add column'"
        )
        assert rc == 2

    def test_diff_unavailable_fails_safe(self, monkeypatch):
        """If the diff can't be computed, treat the file as doc-relevant
        (the prior, coarser behaviour) rather than risk silently passing
        a real schema change."""
        mod = _load_hook()
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "infrastructure/manifest_repository.py",
        ])
        monkeypatch.setattr(mod, "_new_files", lambda: set())

        def raising_check_output(cmd, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(mod.subprocess, "check_output", raising_check_output)
        payload = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title 'feat'"},
        })
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert mod.main() == 2

    def test_dialog_change_still_triggers_unaffected(self, monkeypatch):
        """Sanity: the new schema-marker gate is scoped ONLY to
        manifest_repository.py — other doc-relevant patterns (e.g. a
        new dialog file) are unaffected by this change."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: new dialog'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 2


# ── bypass token: the reason must be non-blank (#857) ─────────────────────


class TestBypassEmptyReason:
    """PR #856 pasted a brief's template text verbatim — ``[docs-not-needed:]``
    with no reason — and the gate reported a bypass and passed. A copy-pasted
    placeholder must not disable the guard, and the developer must be told
    what to fix rather than reading the generic "no docs touched" text.

    Both halves are tested by name: the empty / whitespace-only forms BLOCK,
    and every ordinary real token still BYPASSES (a guard that refuses
    everything is as broken as one that refuses nothing).
    """

    # ── the empty form must no longer bypass ──────────────────────────────

    def test_empty_reason_blocks_and_names_the_problem(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing' --body 'body [docs-not-needed:]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "bypass token seen but its reason is empty" in err
        assert "docs-not-needed: <reason>" in err

    def test_whitespace_only_reason_blocks(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing [docs-not-needed:   ]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 2
        assert "its reason is empty" in capsys.readouterr().err

    def test_empty_reason_blocks_on_behavioural_path_too(self, monkeypatch, capsys):
        """The #262 behavioural-modify path has its own block message; the
        empty-token explanation must reach it too, not just the generic one."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'fix: dialog copy [docs-not-needed:]'",
            changed=["app/views/dialogs/execute_action_dialog.py"],
            added=[],
            behavioural_qualifies=True,
        )
        assert rc == 2
        assert "its reason is empty" in capsys.readouterr().err

    # ── every ordinary real token must still bypass ───────────────────────

    def test_ordinary_reason_still_bypasses(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'refactor [docs-not-needed: internal refactor]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0

    def test_punctuation_heavy_reason_still_bypasses(self, monkeypatch):
        """A real reason carries commas, colons, dashes, slashes, #refs and
        parentheses — only ``]`` ends the token."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x' --body "
            "'[docs-not-needed: guard-only change (see #857): scripts/hooks/*.py "
            "+ tests — no user-visible behaviour, 100% internal]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0

    def test_reason_after_leading_whitespace_still_bypasses(self, monkeypatch):
        """Whitespace BEFORE a real reason is normal formatting, not a blank
        reason — the two must not be confused."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [docs-not-needed:    real reason here]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0

    def test_single_character_reason_still_bypasses(self, monkeypatch):
        """The pre-existing ``[docs-not-needed: x]`` shape stays valid — the
        fix must not raise the bar beyond "non-blank"."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x' --body 'whatever [docs-not-needed: x] more'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0

    # ── the documented placeholder is not a reason (#858) ─────────────────

    def test_literal_placeholder_reason_blocks(self, monkeypatch, capsys):
        """#858: ``<reason>`` is a non-blank string, so #857's pattern
        accepted it — and that is exactly the text README.md, docs/features.md
        and every brief template carry. A PR body that pastes or quotes the
        convention must not thereby disable the gate."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing' --body "
            "'Bypass with `[docs-not-needed: <reason>]` in the body.'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "literal `<reason>`" in err

    def test_real_reason_containing_the_word_still_bypasses(self, monkeypatch):
        """Only a reason that IS exactly ``<reason>`` is rejected — a real
        reason that happens to mention it must still bypass, or the guard
        blocks the very PR that documents the token."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x' --body "
            "'[docs-not-needed: doc-only: renames the <reason> placeholder]'",
            changed=["app/views/dialogs/new_dialog.py"],
        )
        assert rc == 0


# ── CI mode (#273) ────────────────────────────────────────────────────────


class TestCiMode:
    """The ``--ci`` entry point used by .github/workflows/pr-gates.yml.

    Three regressions worth catching:
      1. ``--ci`` flag not detected → falls through to the stdin path,
         which hangs (no stdin) or errors. CI runs become red for the
         wrong reason.
      2. ``PR_BODY`` env var unset → silent crash or, worse, silent
         pass that bypasses the gate.
      3. ``DIFF_BASE`` env var ignored → diff is computed against
         ``origin/master`` for stacked PRs whose base is a feature
         branch, surfacing files from earlier stages as false positives.
    """

    def test_ci_mode_blocks_when_pr_body_empty_and_docs_missing(
        self, monkeypatch, capsys
    ):
        """Doc-relevant change, no bypass token, no docs touched →
        exit 2. PR_BODY explicitly unset (PRs from the web UI can have
        an empty body)."""
        mod = _load_hook()
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "app/views/dialogs/new_dialog.py",
        ])
        monkeypatch.setattr(mod, "_new_files", lambda: {
            "app/views/dialogs/new_dialog.py",
        })
        monkeypatch.setattr(sys, "argv", ["docs_guard.py", "--ci"])
        monkeypatch.setenv("PR_TITLE", "feat: new dialog")
        monkeypatch.delenv("PR_BODY", raising=False)
        rc = mod.main()
        assert rc == 2
        assert "new_dialog.py" in capsys.readouterr().err

    def test_ci_mode_honours_bypass_token_in_pr_body(self, monkeypatch):
        """CI mode must read PR_BODY for the bypass token — otherwise
        PRs opened from the web UI can't use the documented escape
        valve."""
        mod = _load_hook()
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "app/views/dialogs/new_dialog.py",
        ])
        monkeypatch.setattr(mod, "_new_files", lambda: {
            "app/views/dialogs/new_dialog.py",
        })
        monkeypatch.setattr(sys, "argv", ["docs_guard.py", "--ci"])
        monkeypatch.setenv("PR_TITLE", "feat: new dialog")
        monkeypatch.setenv(
            "PR_BODY", "details here\n[docs-not-needed: internal scaffold]\n"
        )
        assert mod.main() == 0

    def test_ci_mode_blocks_on_empty_reason_token(self, monkeypatch, capsys):
        """#857: the hole existed server-side too — pr-gates.yml feeds
        PR_TITLE + PR_BODY into the same check, so a pasted placeholder in
        the PR body passed the CI gate. It must block there as well."""
        mod = _load_hook()
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "app/views/dialogs/new_dialog.py",
        ])
        monkeypatch.setattr(mod, "_new_files", lambda: {
            "app/views/dialogs/new_dialog.py",
        })
        monkeypatch.setattr(sys, "argv", ["docs_guard.py", "--ci"])
        monkeypatch.setenv("PR_TITLE", "feat: new dialog")
        monkeypatch.setenv("PR_BODY", "## What\ndetails\n[docs-not-needed:]\n")
        rc = mod.main()
        assert rc == 2
        assert "bypass token seen but its reason is empty" in capsys.readouterr().err

    def test_diff_base_env_var_overrides_default(self, monkeypatch):
        """DIFF_BASE plumbing: the git diff command must include the
        env-supplied base, not always ``origin/master``."""
        mod = _load_hook()
        captured: list[list[str]] = []

        def fake_check_output(cmd, **kwargs):
            captured.append(cmd)
            return ""

        monkeypatch.setattr(mod.subprocess, "check_output", fake_check_output)
        monkeypatch.setenv("DIFF_BASE", "origin/feat/parent-branch")
        mod._changed_files()
        assert captured, "expected git diff to be invoked"
        assert any(
            "origin/feat/parent-branch...HEAD" in arg for arg in captured[0]
        )
