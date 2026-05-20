"""Tests for ``scripts/hooks/team_task_completed.py`` — the
TaskCompleted event hook that rejects empty-findings completions on
tasks whose subject names a triggered /pr-review gate.

Failure mode the hook is preventing: a teammate marks its gate task
completed without sending findings back to LEAD (either silent skip
or findings delivered via a channel LEAD can't see). The hook
distinguishes "honest CLEAN" (explicit SUMMARY line) from "silent
nothing" and blocks only the latter.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "team_task_completed.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "team_task_completed", str(HOOK_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, payload: dict | str) -> int:
    mod = _load_hook()
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    return mod.main()


# ── exit 0 paths (allowed) ────────────────────────────────────────────────


class TestAllow:
    def test_explicit_clean_summary_passes(self, monkeypatch):
        """An explicit ``SUMMARY: 0 findings — CLEAN`` is the honest
        way to say "I looked, found nothing"."""
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "SUMMARY: 0 findings — CLEAN"}],
            },
        })
        assert rc == 0

    def test_findings_with_severity_icons_pass(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "SUMMARY: 1 finding\n⚠ foo.py:10 — f-string SQL"}],
            },
        })
        assert rc == 0

    def test_findings_with_section_header_pass(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "## App-level security (Gate 7)\nfoo.py:10 — pattern"}],
            },
        })
        assert rc == 0

    def test_non_gate_subject_fails_open(self, monkeypatch):
        """A task that doesn't name a known gate is not enforced —
        the hook only watches the gate lanes."""
        rc = _run(monkeypatch, {
            "task": {
                "subject": "random task with no findings",
                "comments": [{"body": ""}],
            },
        })
        assert rc == 0

    def test_bypass_token_passes(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [
                    {"body": "[team-empty-ok: gate triggered on file list but rubric noops]"}
                ],
            },
        })
        assert rc == 0

    def test_taskupdate_tool_shape_works(self, monkeypatch):
        """Hook must sniff the alternate ``tool_input`` shape."""
        rc = _run(monkeypatch, {
            "tool_name": "TaskUpdate",
            "tool_input": {
                "status": "completed",
                "subject": "Gate 7: app-level security",
                "comment": "SUMMARY: 0 findings — CLEAN",
            },
        })
        assert rc == 0

    def test_taskupdate_non_completed_status_fails_open(self, monkeypatch):
        """Status updates other than ``completed`` are out of scope."""
        rc = _run(monkeypatch, {
            "tool_name": "TaskUpdate",
            "tool_input": {
                "status": "in_progress",
                "subject": "Gate 7: app-level security",
            },
        })
        assert rc == 0

    def test_malformed_stdin_fails_open(self, monkeypatch):
        assert _run(monkeypatch, "not json") == 0

    def test_non_dict_payload_fails_open(self, monkeypatch):
        assert _run(monkeypatch, [1, 2, 3]) == 0

    def test_unknown_payload_shape_fails_open(self, monkeypatch):
        rc = _run(monkeypatch, {"event": "SomethingElse"})
        assert rc == 0


# ── exit 2 paths (block) ──────────────────────────────────────────────────


class TestBlock:
    @pytest.mark.parametrize("subject", [
        "Gate 2+3: features.md and qa scenario coverage",
        "Gate 7: app-level security",
        "Gate 8+9+10: migration / perf / test review",
        "docs-reviewer: gates 2+3",
        "app-security-reviewer: scan diff",
        "quality-reviewer: full sweep",
    ])
    def test_empty_findings_on_triggered_gate_blocks(
        self, monkeypatch, subject, capsys
    ):
        rc = _run(monkeypatch, {
            "task": {"subject": subject, "comments": [{"body": ""}]},
        })
        assert rc == 2
        err = capsys.readouterr().err
        assert "team-mode task-completed guard" in err
        assert subject in err
        assert "team-empty-ok" in err  # bypass surfaced

    def test_block_message_describes_expected_shape(self, monkeypatch, capsys):
        _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": ""}],
            },
        })
        err = capsys.readouterr().err
        assert "severity icons" in err
        assert "SUMMARY:" in err

    def test_whitespace_only_comment_blocks(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "   \n\t  "}],
            },
        })
        assert rc == 2

    def test_no_comments_at_all_blocks(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [],
            },
        })
        assert rc == 2

    def test_pretend_summary_without_clean_blocks(self, monkeypatch):
        """A comment that mentions SUMMARY but doesn't actually emit
        ``0 findings — CLEAN`` or any structured findings must block —
        otherwise an empty comment with the word "SUMMARY:" anywhere
        in it would slip through."""
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "SUMMARY: pending"}],
            },
        })
        assert rc == 2


# ── bypass token edge cases ───────────────────────────────────────────────


class TestBypassTokenShape:
    def test_unclosed_bracket_does_not_count(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "[team-empty-ok: forgot to close"}],
            },
        })
        assert rc == 2

    def test_similar_but_wrong_token_does_not_count(self, monkeypatch):
        rc = _run(monkeypatch, {
            "task": {
                "subject": "Gate 7: app-level security",
                "comments": [{"body": "[empty-ok: just trust me]"}],
            },
        })
        assert rc == 2


# ── check() direct entry point ────────────────────────────────────────────


class TestCheckDirect:
    def test_check_passes_non_gate_subject(self):
        mod = _load_hook()
        rc, msg = mod.check("not a gate", "")
        assert rc == 0
        assert msg == ""

    def test_check_blocks_empty_gate(self):
        mod = _load_hook()
        rc, msg = mod.check("Gate 7: app-level security", "")
        assert rc == 2
        assert "blocking TaskUpdate" in msg

    def test_check_passes_explicit_clean(self):
        mod = _load_hook()
        rc, _ = mod.check(
            "Gate 7: app-level security", "SUMMARY: 0 findings — CLEAN"
        )
        assert rc == 0
