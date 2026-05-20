"""Tests for ``scripts/hooks/team_teammate_idle.py`` — the
TeammateIdle event hook that logs >180s idle teammates to
``.claude/team_idle.log``.

This hook never blocks — it always returns 0. The tests verify the
logging behaviour: nothing written below the threshold, one line
written at or above the threshold, and filesystem errors swallowed
silently so a misconfigured ``.claude/`` directory can't break team
operation.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "team_teammate_idle.py"


def _load_hook(monkeypatch, tmp_path: Path):
    """Load the hook and redirect its LOG_PATH into a tmp directory so
    tests can read what it wrote without touching the real
    ``.claude/team_idle.log``."""
    spec = importlib.util.spec_from_file_location(
        "team_teammate_idle", str(HOOK_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LOG_PATH", tmp_path / "team_idle.log")
    return mod


def _run(monkeypatch, tmp_path: Path, payload: dict | str) -> tuple[int, Path]:
    mod = _load_hook(monkeypatch, tmp_path)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    rc = mod.main()
    return rc, mod.LOG_PATH


# ── exit 0 paths (always) ─────────────────────────────────────────────────


class TestAlwaysReturnsZero:
    """This hook is purely a logger — it never blocks. Verify every
    code path returns 0 even when nothing useful happens."""

    def test_malformed_stdin_returns_zero(self, monkeypatch, tmp_path):
        rc, log = _run(monkeypatch, tmp_path, "not json")
        assert rc == 0
        assert not log.exists()

    def test_non_dict_payload_returns_zero(self, monkeypatch, tmp_path):
        rc, log = _run(monkeypatch, tmp_path, [1, 2, 3])
        assert rc == 0
        assert not log.exists()

    def test_unknown_payload_shape_returns_zero(self, monkeypatch, tmp_path):
        """No idleSeconds key anywhere — silently no-op."""
        rc, log = _run(monkeypatch, tmp_path, {"event": "SomethingElse"})
        assert rc == 0
        assert not log.exists()

    def test_below_threshold_returns_zero_no_log(self, monkeypatch, tmp_path):
        rc, log = _run(monkeypatch, tmp_path, {
            "teammate": {"name": "reader", "idleSeconds": 30},
        })
        assert rc == 0
        assert not log.exists()

    @pytest.mark.parametrize("seconds", [180, 200, 1000, 9999])
    def test_at_or_above_threshold_writes_log_line(
        self, monkeypatch, tmp_path, seconds
    ):
        rc, log = _run(monkeypatch, tmp_path, {
            "teammate": {"name": "reader", "idleSeconds": seconds},
        })
        assert rc == 0
        assert log.exists()
        content = log.read_text(encoding="utf-8")
        assert "idle_warn" in content
        assert "teammate=reader" in content
        assert f"idleSeconds={seconds}" in content

    def test_top_level_seconds_fallback_also_works(self, monkeypatch, tmp_path):
        """Hook should sniff ``idleSeconds`` at the top level too."""
        rc, log = _run(monkeypatch, tmp_path, {
            "name": "reader", "idleSeconds": 500,
        })
        assert rc == 0
        assert log.exists()
        assert "teammate=reader" in log.read_text(encoding="utf-8")

    def test_unknown_teammate_name_logs_as_unknown(self, monkeypatch, tmp_path):
        """Missing name should not skip the log — operator still wants
        to know SOMETHING went idle."""
        rc, log = _run(monkeypatch, tmp_path, {
            "teammate": {"idleSeconds": 500},  # no name
        })
        assert rc == 0
        assert log.exists()
        assert "teammate=<unknown>" in log.read_text(encoding="utf-8")

    def test_log_appends_on_repeated_calls(self, monkeypatch, tmp_path):
        """Multiple idle warnings should append, not overwrite."""
        for name, secs in [("reader", 200), ("writer", 500)]:
            _run(monkeypatch, tmp_path, {
                "teammate": {"name": name, "idleSeconds": secs},
            })
        content = (tmp_path / "team_idle.log").read_text(encoding="utf-8")
        assert "teammate=reader" in content
        assert "teammate=writer" in content
        # Should be exactly two log lines.
        assert content.count("idle_warn") == 2

    def test_filesystem_error_swallowed(self, monkeypatch, tmp_path):
        """If LOG_PATH's parent can't be written (e.g. read-only fs),
        the hook must NOT raise — logging is best-effort."""
        mod = _load_hook(monkeypatch, tmp_path)

        def boom(self, *args, **kwargs):
            raise OSError("read-only fs")

        monkeypatch.setattr(Path, "mkdir", boom)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
            "teammate": {"name": "reader", "idleSeconds": 500},
        })))
        # Must not raise; must return 0.
        assert mod.main() == 0


# ── log line shape ────────────────────────────────────────────────────────


class TestLogLineShape:
    def test_log_line_starts_with_utc_timestamp(self, monkeypatch, tmp_path):
        rc, log = _run(monkeypatch, tmp_path, {
            "teammate": {"name": "reader", "idleSeconds": 500},
        })
        assert rc == 0
        first_line = log.read_text(encoding="utf-8").splitlines()[0]
        # ISO-8601 UTC: starts with YYYY-MM-DDTHH:MM:SS+00:00 (or Z).
        assert first_line[:4].isdigit()
        assert first_line[4] == "-"
        assert "+00:00" in first_line or first_line.endswith("Z")
