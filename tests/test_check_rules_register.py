"""Tests for scripts/check_rules_register.py (the cross-cutting rules register).

Each case builds a miniature repo in tmp_path so one failure is one cause. The
last two cases run the real register and a CRLF copy of it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import scripts.check_rules_register as crr

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check_rules_register.py"

_OWNER_SRC = "def decide_thing():\n    return 1\n"
_PIN_SRC = (
    "class TestPin:\n"
    "    def test_thing(self):\n"
    "        assert True\n"
    "\n"
    "\n"
    "def test_free():\n"
    "    assert True\n"
)
_SCENARIO_SRC = '"""Scenario fixture."""\n'
_VITEST_SRC = (
    'import { it } from "vitest";\n'
    'it("does a thing", () => {});\n'
)


def _block(
    rid: str = "R-LOCK-1",
    owner: str = "`code/owner.py::decide_thing`",
    pins: str = "`tests/test_pin.py::TestPin::test_thing`",
    status: str = "pinned",
) -> str:
    return (
        f"### {rid} — a rule title\n"
        "\n"
        "- **Rule:** A write to a locked row is refused.\n"
        f"- **Owning code:** {owner}\n"
        f"- **Pins:** {pins}\n"
        f"- **Status:** {status}\n"
        "\n"
    )


def _register(*blocks: str) -> str:
    return "# Rules\n\n## Lock\n\n" + "".join(blocks)


def _root(tmp_path: Path, register: str, features: str | None = None) -> Path:
    files = {
        "code/owner.py": _OWNER_SRC,
        "tests/test_pin.py": _PIN_SRC,
        "qa/web/scenarios/s01_thing.py": _SCENARIO_SRC,
        "frontend/src/x.test.ts": _VITEST_SRC,
        "docs/behaviour/rules.md": register,
    }
    if features is not None:
        files["docs/features.md"] = features
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return tmp_path


def test_valid_register_passes(tmp_path):
    root = _root(tmp_path, _register(_block()))
    problems, rules, unpinned = crr.check(root)
    assert problems == []
    assert (rules, unpinned) == (1, 0)


def test_duplicate_id_fails(tmp_path):
    root = _root(tmp_path, _register(_block(), _block()))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "duplicate" in p for p in problems), problems
    assert crr.main([str(root)]) == 1


def test_bad_id_shape_fails(tmp_path):
    root = _root(tmp_path, _register(_block(rid="R-lock-1")))
    problems, _, _ = crr.check(root)
    assert any("R-lock-1" in p and "shape" in p for p in problems), problems


def test_missing_owning_file_fails(tmp_path):
    root = _root(tmp_path, _register(_block(owner="`code/missing.py::decide_thing`")))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "missing.py" in p for p in problems), problems


def test_symbol_not_in_owning_file_fails(tmp_path):
    root = _root(tmp_path, _register(_block(owner="`code/owner.py::no_such_symbol`")))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "no_such_symbol" in p for p in problems), problems


def test_missing_test_function_fails(tmp_path):
    root = _root(
        tmp_path,
        _register(_block(pins="`tests/test_pin.py::TestPin::test_absent`")),
    )
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "test_absent" in p for p in problems), problems


def test_missing_scenario_file_fails(tmp_path):
    root = _root(
        tmp_path,
        _register(_block(pins="`qa/web/scenarios/s99_nope.py`")),
    )
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "s99_nope" in p for p in problems), problems


def test_unpinned_with_pins_fails(tmp_path):
    root = _root(tmp_path, _register(_block(status="unpinned")))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "unpinned" in p for p in problems), problems


def test_pinned_without_pins_fails(tmp_path):
    root = _root(tmp_path, _register(_block(pins="none", status="pinned")))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "pinned" in p for p in problems), problems


def test_missing_test_file_fails(tmp_path):
    root = _root(tmp_path, _register(_block(pins="`tests/test_nope.py::test_thing`")))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "tests/test_nope.py" in p for p in problems), problems


def test_missing_class_fails(tmp_path):
    root = _root(tmp_path, _register(_block(pins="`tests/test_pin.py::NoSuchClass::test_free`")))
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "NoSuchClass" in p for p in problems), problems


def test_module_level_test_function_passes(tmp_path):
    root = _root(tmp_path, _register(_block(pins="`tests/test_pin.py::test_free`")))
    problems, _, _ = crr.check(root)
    assert problems == []


def test_vitest_pin_passes_when_title_exists(tmp_path):
    root = _root(
        tmp_path,
        _register(_block(pins='`frontend/src/x.test.ts::"does a thing"`')),
    )
    problems, rules, unpinned = crr.check(root)
    assert problems == []
    assert (rules, unpinned) == (1, 0)


def test_vitest_title_missing_fails(tmp_path):
    root = _root(
        tmp_path,
        _register(_block(pins='`frontend/src/x.test.ts::"absent title"`')),
    )
    problems, _, _ = crr.check(root)
    assert any("R-LOCK-1" in p and "absent title" in p for p in problems), problems


def test_unresolved_citation_in_features_reported_once(tmp_path):
    root = _root(
        tmp_path,
        _register(_block()),
        features="# Features\n\nA row cites R-LOCK-9 which is not registered.\n",
    )
    problems, _, _ = crr.check(root)
    cited = [p for p in problems if "R-LOCK-9" in p]
    assert len(cited) == 1, problems


def test_real_register_passes():
    problems, rules, _ = crr.check(_REPO)
    assert problems == []
    assert rules >= 1
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=_REPO,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("rules register OK:")


def test_crlf_copy_of_valid_register_passes(tmp_path):
    register = _register(_block()).replace("\n", "\r\n")
    root = _root(tmp_path, register)
    problems, rules, unpinned = crr.check(root)
    assert problems == []
    assert (rules, unpinned) == (1, 0)
