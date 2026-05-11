"""Tests for ``scripts/hooks/zombie_check.py`` — the PreToolUse hook
that warns about stale Photo Manager / pytest processes before a
QA-related ``git commit``.

The hook is intentionally non-blocking (always exits 0). Tests pin:
  - it fires only on ``git commit`` (not other git verbs)
  - it fires only when staged files are QA-relevant
  - it lists the zombies it found (so the developer sees them)
  - it doesn't emit anything when no zombies are around or the
    staged diff is unrelated
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "zombie_check.py"


def _load_hook():
    import importlib.util

    spec = importlib.util.spec_from_file_location("zombie_check", str(HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(
    monkeypatch,
    command: str,
    staged: list[str],
    zombies: list[tuple[int, str]] | None = None,
) -> int:
    mod = _load_hook()
    monkeypatch.setattr(mod, "_staged_files", lambda: list(staged))
    monkeypatch.setattr(
        mod, "_list_photo_manager_processes", lambda: list(zombies or [])
    )
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    return mod.main()


# ── always exits 0 — but stderr varies ────────────────────────────────────


class TestNonBlocking:
    def test_returns_zero_when_no_staged_files(self, monkeypatch):
        rc = _run(monkeypatch, "git commit -m 'fix'", staged=[])
        assert rc == 0

    def test_returns_zero_when_staged_unrelated(self, monkeypatch):
        rc = _run(
            monkeypatch,
            "git commit -m 'fix'",
            staged=["scanner/dedup.py", "README.md"],
        )
        assert rc == 0

    def test_returns_zero_when_qa_relevant_but_no_zombies(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "git commit -m 'qa: extend s32'",
            staged=["qa/scenarios/s32_lock_confirm_bulk_regex.py"],
            zombies=[],
        )
        assert rc == 0
        assert capsys.readouterr().err == ""

    def test_returns_zero_with_warning_when_zombies_found(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "git commit -m 'qa: extend s32'",
            staged=["qa/scenarios/s32_lock_confirm_bulk_regex.py"],
            zombies=[
                (1234, "python.exe -m qa.scenarios.s32_lock_confirm_bulk_regex"),
                (5678, "python.exe main.py"),
            ],
        )
        assert rc == 0  # NON-BLOCKING
        err = capsys.readouterr().err
        assert "1234" in err
        assert "5678" in err
        assert "taskkill" in err.lower()


# ── trigger filter: which commands and files? ────────────────────────────


class TestTriggerFilter:
    def test_does_not_fire_on_git_log(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "git log --oneline",
            staged=["qa/scenarios/s32_lock_confirm_bulk_regex.py"],
            zombies=[(1234, "python.exe -m qa.scenarios.s32")],
        )
        assert rc == 0
        assert capsys.readouterr().err == ""

    def test_does_not_fire_on_git_commit_tree(self, monkeypatch, capsys):
        """git commit-tree is a plumbing command we don't care about."""
        rc = _run(
            monkeypatch,
            "git commit-tree HEAD^{tree}",
            staged=["qa/scenarios/s32_lock_confirm_bulk_regex.py"],
            zombies=[(1234, "python.exe -m qa.scenarios.s32")],
        )
        assert rc == 0
        assert capsys.readouterr().err == ""

    def test_fires_on_git_commit_with_flags(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "git commit -m 'thing' --no-verify",
            staged=["qa/scenarios/s32_lock_confirm_bulk_regex.py"],
            zombies=[(1234, "python.exe main.py")],
        )
        assert rc == 0
        assert "1234" in capsys.readouterr().err

    def test_does_not_fire_when_staged_files_are_scanner_only(
        self, monkeypatch, capsys
    ):
        rc = _run(
            monkeypatch,
            "git commit -m 'fix dedup edge case'",
            staged=["scanner/dedup.py", "tests/test_dedup.py"],
            zombies=[(1234, "python.exe -m qa.scenarios.s32")],
        )
        assert rc == 0
        # Zombies present, but staged files aren't QA-relevant → silent.
        assert capsys.readouterr().err == ""

    def test_fires_on_dialog_test_change(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "git commit -m 'tests: dialog'",
            staged=["tests/test_execute_action_dialog.py"],
            zombies=[(1234, "python.exe main.py")],
        )
        assert rc == 0
        assert "1234" in capsys.readouterr().err

    def test_fires_on_main_py_change(self, monkeypatch, capsys):
        rc = _run(
            monkeypatch,
            "git commit -m 'main: fix'",
            staged=["main.py"],
            zombies=[(1234, "python.exe main.py")],
        )
        assert rc == 0
        assert "1234" in capsys.readouterr().err


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
