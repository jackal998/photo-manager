"""Tests for ``scripts/hooks/team_task_created.py`` — the
TaskCreated event hook that allowlists task subjects against the
known /pr-review gate-naming patterns when Agent Teams mode is
active.

Failure mode the hook is preventing: a team-mode LEAD (or a runaway
teammate) creates a task with a free-form subject, expanding the
team's scope beyond the gates the user opted into. The allowlist
keeps team-mode work bounded; the ``[team-task-freeform: …]`` bypass
preserves the legitimate one-off case.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "team_task_created.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "team_task_created", str(HOOK_PATH)
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
    @pytest.mark.parametrize("subject", [
        "Gate 2+3: features.md and qa scenario coverage",
        "Gates 2+3: features.md drift and qa scenario coverage",
        "Gate 7: app-level security",
        "Gate 8+9+10: migration / perf / test review",
        "Gates 8: SQLite migration safety",
        "docs-reviewer: apply gates 2+3 to this diff",
        "app-security-reviewer: scan diff for Gate 7 patterns",
        "quality-reviewer: gates 8+9+10 sweep",
    ])
    def test_allowlisted_subject_passes(self, monkeypatch, subject):
        """Every named gate-pattern in _ALLOWED_PATTERNS must pass."""
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": subject},
        })
        assert rc == 0

    def test_task_shape_payload_also_works(self, monkeypatch):
        """Hook must sniff the alternate ``task.subject`` shape."""
        rc = _run(monkeypatch, {
            "event": "TaskCreated",
            "task": {"subject": "Gate 7: app-level security"},
        })
        assert rc == 0

    def test_bypass_token_skips_check(self, monkeypatch):
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {
                "subject": "freeform [team-task-freeform: LEAD debugging] task"
            },
        })
        assert rc == 0

    def test_malformed_stdin_fails_open(self, monkeypatch):
        """Hook must not block on parse errors — fail open."""
        assert _run(monkeypatch, "not valid json") == 0

    def test_non_dict_payload_fails_open(self, monkeypatch):
        assert _run(monkeypatch, [1, 2, 3]) == 0

    def test_unknown_payload_shape_fails_open(self, monkeypatch):
        """Hook should not block on a payload it doesn't recognise."""
        rc = _run(monkeypatch, {"event": "SomeOtherEvent", "data": {}})
        assert rc == 0

    def test_non_taskcreate_tool_fails_open(self, monkeypatch):
        """A different tool_name with a tool_input.subject should not
        be enforced — only TaskCreate is in scope."""
        rc = _run(monkeypatch, {
            "tool_name": "SomethingElse",
            "tool_input": {"subject": "random off-topic task"},
        })
        assert rc == 0

    def test_non_string_subject_fails_open(self, monkeypatch):
        """If subject is not a string, hook can't apply regex — fail open."""
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": None},
        })
        assert rc == 0


# ── exit 2 paths (block) ──────────────────────────────────────────────────


class TestBlock:
    @pytest.mark.parametrize("subject", [
        "random off-topic task",
        "investigate weird bug",
        "Gate 99: not a real gate",
        "feat: implement something",
        "PR review",  # too generic — not naming a specific gate
    ])
    def test_non_allowlisted_subject_blocks(self, monkeypatch, subject, capsys):
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": subject},
        })
        assert rc == 2
        err = capsys.readouterr().err
        assert "team-mode task-created guard" in err
        assert subject in err
        assert "team-task-freeform" in err  # bypass surfaced in error

    def test_block_message_explains_allowlist_location(
        self, monkeypatch, capsys
    ):
        _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "random thing"},
        })
        err = capsys.readouterr().err
        assert "_ALLOWED_PATTERNS" in err
        assert "team_task_created.py" in err


# ── bypass token edge cases ───────────────────────────────────────────────


class TestBypassTokenShape:
    def test_unclosed_bracket_does_not_count(self, monkeypatch):
        """A malformed bypass like ``[team-task-freeform: forgot`` without
        closing ``]`` must NOT count as bypass — otherwise an accidental
        edit could disable enforcement."""
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "task [team-task-freeform: forgot to close"},
        })
        assert rc == 2

    def test_similar_but_wrong_token_does_not_count(self, monkeypatch):
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "task [team: freeform]"},
        })
        assert rc == 2

    def test_bypass_with_unicode_reason_works(self, monkeypatch):
        rc = _run(monkeypatch, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "task [team-task-freeform: 偵錯用]"},
        })
        assert rc == 0


# ── check() direct entry point ────────────────────────────────────────────


class TestCheckDirect:
    """Direct unit tests on ``check()`` for fast-path coverage of the
    pattern table without going through the JSON-parsing layer."""

    def test_check_returns_zero_for_allowlisted(self):
        mod = _load_hook()
        rc, msg = mod.check("Gate 7: app-level security review")
        assert rc == 0
        assert msg == ""

    def test_check_returns_two_for_non_allowlisted(self):
        mod = _load_hook()
        rc, msg = mod.check("random task")
        assert rc == 2
        assert "blocking TaskCreate" in msg
