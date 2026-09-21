"""Tests for ``scripts/hooks/qa_scenario_guard.py`` — the PreToolUse hook
that blocks ``gh pr create`` when user-facing files changed without a
qa/web/scenarios/sNN_*.py driver.

Failure mode the hook is preventing: shipping a feature PR (e.g.
photo-manager#175 in its first iteration) that touches the React client
or a FastAPI route but lacks layer-3 coverage. CLAUDE.md is the spec;
this hook + these tests are the enforcement.

Second failure mode, added by #646: the triggers used to name
``app/views/`` — the Qt client deleted in the Phase-4 cutover — so the
gate would have matched nothing and passed every PR in silence.
``TestBlock`` pins the new roots; ``TestAllow`` pins the half that must
NOT fire (vitest specs, the vitest bootstrap, tests/).
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
            changed=["qa/web/scenarios/s14_action_by_regex.py"],
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

    def test_colocated_vitest_spec_alone_passes(self, monkeypatch):
        """A ``*.test.tsx`` next to its component is a test, not a user
        surface — demanding a layer-3 driver for it would make every
        frontend test-only PR need a scenario touch."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'test: cover the decision hook'",
            changed=[
                "frontend/src/components/ResultTree.keyboard.test.tsx",
                "frontend/src/hooks/useDecisionShortcuts.test.ts",
            ],
        )
        assert rc == 0

    def test_stylesheet_only_change_passes(self, monkeypatch):
        """A `.css` edit is not a layer-3 surface.

        A Playwright driver can read a computed style, but "does this
        spacing look right" is a human judgement — so a one-line
        `index.css` edit has no scriptable assertion to demand, and
        demanding one only teaches authors to reach for the bypass token.
        """
        rc = _run(
            monkeypatch,
            "gh pr create --title 'style: tighten the row gutter'",
            changed=["frontend/src/index.css"],
        )
        assert rc == 0

    def test_vitest_bootstrap_alone_passes(self, monkeypatch):
        """``frontend/src/test/`` holds the vitest setup, not UI."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'test: widen the jsdom shim'",
            changed=["frontend/src/test/setup.ts"],
        )
        assert rc == 0

    def test_pr_create_with_user_facing_AND_qa_passes(self, monkeypatch):
        """Happy path for a feature PR — user-facing change accompanied
        by a qa/web/scenarios/sNN_*.py driver."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: lock state'",
            changed=[
                "frontend/src/components/execute/ExecuteDialog.tsx",
                "frontend/src/components/execute/ExecuteTree.tsx",
                "qa/web/scenarios/s32_lock_confirm_bulk_regex.py",
            ],
        )
        assert rc == 0

    def test_bypass_token_in_title_skips_check(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'refactor: rename internal fn [qa-not-needed: pure rename, no behaviour change]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    def test_bypass_token_in_body_skips_check(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'fix' --body 'whatever [qa-not-needed: sweep] more'",
            changed=["frontend/src/components/action/ActionDialog.tsx"],
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
        "frontend/src/components/execute/ExecuteDialog.tsx",
        "frontend/src/components/ContextMenu.tsx",
        "frontend/src/components/execute/ExecuteTree.tsx",
        "frontend/src/components/action/ActionDialog.tsx",
        "frontend/src/components/ScanDialog.tsx",
        "frontend/src/components/MenuBar.tsx",
        "frontend/src/store/useAppStore.ts",
        "app/web/routes/scan.py",
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
                "frontend/src/components/execute/ExecuteDialog.tsx",
                "frontend/src/components/execute/ExecuteTree.tsx",
                "frontend/src/components/ContextMenu.tsx",
                "tests/test_file_operations.py",  # not user-facing but unrelated
            ],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "frontend/src/components/execute/ExecuteDialog.tsx" in err
        assert "frontend/src/components/execute/ExecuteTree.tsx" in err
        assert "frontend/src/components/ContextMenu.tsx" in err

    def test_test_only_qa_change_does_not_satisfy(self, monkeypatch):
        """A test file that happens to live under tests/ named test_qa_*.py
        is NOT a layer-3 driver — only files matching
        ``qa/web/scenarios/sNN_*.py`` count."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing'",
            changed=[
                "frontend/src/components/action/ActionDialog.tsx",
                "tests/test_qa_helpers.py",  # NOT a qa/web/scenarios/ file
            ],
        )
        assert rc == 2

    def test_qa_helper_module_does_not_satisfy(self, monkeypatch):
        """``qa/web/_pw.py`` and other ``_*.py`` helpers don't
        satisfy the rule — must be a numbered ``sNN_*.py`` driver."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: thing'",
            changed=[
                "frontend/src/components/ContextMenu.tsx",
                "qa/web/_pw.py",
            ],
        )
        assert rc == 2


# ── bypass token edge cases ───────────────────────────────────────────────


class TestBypassTokenShape:
    def test_bypass_with_complex_reason_works(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed: covered by existing s14, no new flow]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    def test_bypass_with_unicode_reason_works(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed: 翻譯字串更新]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    def test_unclosed_bracket_does_not_count(self, monkeypatch):
        """Defensive: a malformed token like ``[qa-not-needed: forgot``
        without closing ``]`` must NOT count as bypass — otherwise an
        accidental edit could disable enforcement."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed: forgot to close'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 2

    def test_similar_but_wrong_token_does_not_count(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa: not needed]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 2


# ── bypass token: the reason must be non-blank (#857) ─────────────────────


class TestBypassEmptyReason:
    """Twin of ``tests/test_docs_guard.py::TestBypassEmptyReason``. PR #856
    pasted ``[qa-not-needed:]`` from a brief's template and the gate reported
    a bypass and passed. A placeholder with no reason must block — and every
    ordinary real token must still bypass.
    """

    # ── the empty form must no longer bypass ──────────────────────────────

    def test_empty_reason_blocks_and_names_the_problem(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: lock' --body 'body [qa-not-needed:]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "bypass token seen but its reason is empty" in err
        assert "qa-not-needed: <reason>" in err

    def test_whitespace_only_reason_blocks(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: lock [qa-not-needed:   ]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 2
        assert "its reason is empty" in capsys.readouterr().err

    # ── every ordinary real token must still bypass ───────────────────────

    def test_ordinary_reason_still_bypasses(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'refactor [qa-not-needed: pure rename]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    def test_punctuation_heavy_reason_still_bypasses(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x' --body "
            "'[qa-not-needed: covered by s14/s32 (see #857): no new flow — "
            "handler signature only, 1:1 rename]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    def test_reason_after_leading_whitespace_still_bypasses(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x [qa-not-needed:    real reason here]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    def test_single_character_reason_still_bypasses(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "gh pr create --title 'x' --body 'whatever [qa-not-needed: x] more'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0

    # ── the documented placeholder is not a reason (#858) ─────────────────

    def test_literal_placeholder_reason_blocks(self, monkeypatch, capsys):
        """#858: ``<reason>`` is a non-blank string, so #857's pattern
        accepted it — and that is exactly the text README.md and every brief
        template carry. A PR body that pastes or quotes the convention must
        not thereby disable the gate."""
        rc = _run(
            monkeypatch,
            "gh pr create --title 'feat: lock' --body "
            "'Bypass with `[qa-not-needed: <reason>]` in the body.'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
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
            "'[qa-not-needed: doc-only: renames the <reason> placeholder]'",
            changed=["frontend/src/components/execute/ExecuteDialog.tsx"],
        )
        assert rc == 0


# ── CI mode (#273) ────────────────────────────────────────────────────────


class TestCiMode:
    """The ``--ci`` entry point used by .github/workflows/pr-gates.yml.

    Mirrors the regressions tested in test_docs_guard.py::TestCiMode —
    same shape, same failure modes, same fix.
    """

    def test_ci_mode_blocks_when_user_facing_change_lacks_qa(
        self, monkeypatch, capsys
    ):
        mod = _load_hook(monkeypatch)
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "frontend/src/components/execute/ExecuteDialog.tsx",
        ])
        monkeypatch.setattr(sys, "argv", ["qa_scenario_guard.py", "--ci"])
        monkeypatch.setenv("PR_TITLE", "feat: lock state")
        monkeypatch.delenv("PR_BODY", raising=False)
        rc = mod.main()
        assert rc == 2
        assert "ExecuteDialog.tsx" in capsys.readouterr().err

    def test_ci_mode_honours_bypass_token_in_pr_body(self, monkeypatch):
        mod = _load_hook(monkeypatch)
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "frontend/src/components/execute/ExecuteDialog.tsx",
        ])
        monkeypatch.setattr(sys, "argv", ["qa_scenario_guard.py", "--ci"])
        monkeypatch.setenv("PR_TITLE", "refactor: rename internal fn")
        monkeypatch.setenv(
            "PR_BODY", "details\n[qa-not-needed: pure rename, no UX change]\n"
        )
        assert mod.main() == 0

    def test_ci_mode_blocks_on_empty_reason_token(self, monkeypatch, capsys):
        """#857: pr-gates.yml feeds PR_TITLE + PR_BODY into the same check,
        so a pasted placeholder in the PR body passed the CI gate too."""
        mod = _load_hook(monkeypatch)
        monkeypatch.setattr(mod, "_changed_files", lambda: [
            "frontend/src/components/execute/ExecuteDialog.tsx",
        ])
        monkeypatch.setattr(sys, "argv", ["qa_scenario_guard.py", "--ci"])
        monkeypatch.setenv("PR_TITLE", "feat: lock state")
        monkeypatch.setenv("PR_BODY", "## What\ndetails\n[qa-not-needed:]\n")
        rc = mod.main()
        assert rc == 2
        assert "bypass token seen but its reason is empty" in capsys.readouterr().err

    def test_diff_base_env_var_overrides_default(self, monkeypatch):
        mod = _load_hook(monkeypatch)
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


# ── bypass token: the reason may not span lines (#872) ────────────────────


def _run_ci(
    monkeypatch,
    *,
    title: str = "fix: something",
    body: str = "",
    changed: list[str] | None = None,
) -> int:
    """Invoke ``main()`` the way ``.github/workflows/pr-gates.yml`` does:
    ``--ci`` plus PR_TITLE / PR_BODY from the environment. The PR body is
    where a multi-line trap actually arrives — GitHub hands it over
    verbatim, CRLF and all — so the newline tests use this entry point
    rather than the ``gh pr create`` command line."""
    mod = _load_hook(monkeypatch)
    files = (
        ["frontend/src/components/execute/ExecuteDialog.tsx"] if changed is None else changed
    )
    monkeypatch.setattr(mod, "_changed_files", lambda: list(files))
    monkeypatch.setattr(sys, "argv", ["qa_scenario_guard.py", "--ci"])
    monkeypatch.setenv("PR_TITLE", title)
    monkeypatch.setenv("PR_BODY", body)
    return mod.main()


# The two shapes a real PR body carries a stray `]` in, below an opener its
# author never closed: a markdown link and a task-list checkbox. Both used
# to close the token.
_LATER_BRACKET_BODIES = {
    "markdown-link": (
        "## What\n"
        "[qa-not-needed: internal rename\n"
        "\n"
        "See [the issue](https://example.invalid/872) for the trap.\n"
    ),
    "checklist-box": (
        "[qa-not-needed: no user-visible flow\n"
        "\n"
        "## Checklist\n"
        "- [ ] tests\n"
    ),
}


class TestBypassMustStayOnOneLine:
    """#872: the reason class was ``[^\\]]*``, which matches newlines. An
    opener left unclosed on its line was therefore closed by the next ``]``
    anywhere below, and every paragraph in between became "the reason" — a
    bypass nobody wrote, whose reviewer-visible reason is garbage.

    Both halves, as in :class:`TestBypassEmptyReason`: the swallowing
    shapes must block, and an honest one-line token in a CRLF body — the
    normal case, since that is how GitHub delivers ``PR_BODY`` — must still
    bypass.
    """

    @pytest.mark.parametrize("shape", sorted(_LATER_BRACKET_BODIES))
    def test_a_later_bracket_does_not_close_the_token(
        self, monkeypatch, capsys, shape
    ):
        rc = _run_ci(monkeypatch, body=_LATER_BRACKET_BODIES[shape])
        assert rc == 2
        err = capsys.readouterr().err
        assert "closing `]` does not arrive on the same" in err

    def test_reason_split_across_a_crlf_does_not_bypass(
        self, monkeypatch, capsys
    ):
        """GitHub delivers PR bodies CRLF-terminated, so the class has to
        exclude ``\\r`` as well as ``\\n`` — excluding only ``\\n`` would
        leave the hole open on every real PR."""
        rc = _run_ci(
            monkeypatch,
            body="[qa-not-needed: pure rename\r\nof a private helper]\r\n",
        )
        assert rc == 2
        assert (
            "closing `]` does not arrive on the same"
            in capsys.readouterr().err
        )

    def test_one_line_token_in_a_crlf_body_still_bypasses(self, monkeypatch):
        """The false-positive half: a CRLF body is the ordinary case, not
        the attack. An honest token inside one must keep working."""
        rc = _run_ci(
            monkeypatch,
            body=(
                "## What\r\nPrivate helper rename.\r\n"
                "[qa-not-needed: pure rename, no user-visible flow]\r\n"
            ),
        )
        assert rc == 0

    def test_empty_token_with_its_bracket_on_the_next_line_reports_unclosed(
        self, monkeypatch, capsys
    ):
        """The #857 empty-reason twin carried the same ``\\s*`` hole: it
        read ``[qa-not-needed:\\n]`` as an empty reason on one line. The
        token is unclosed, and the message has to say that instead."""
        rc = _run_ci(monkeypatch, body="[qa-not-needed:\n]\n")
        assert rc == 2
        err = capsys.readouterr().err
        assert "closing `]` does not arrive on the same" in err
        assert "its reason is empty" not in err
