"""check_rules_register after the #958 split: rule-ID citations in docs/features/<area>.md.

The register checks every R-<AREA>-<n> token in docs/**/*.md. The entries moved
into docs/features/, so a citation there must still be found and resolved. Both
cases use a real miniature tree (tests/test_check_rules_register.py helpers).
"""
from __future__ import annotations

from pathlib import Path

from scripts.check_rules_register import check
from tests.test_check_rules_register import _block, _register, _root


def _area_file(root: Path, text: str) -> None:
    target = root / "docs" / "features" / "scan.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))


def test_area_file_citing_a_registered_rule_passes(tmp_path: Path) -> None:
    root = _root(tmp_path, _register(_block()))
    _area_file(root, "### Entry\n\n- **Rules applied:** R-LOCK-1\n")
    problems, rules, _ = check(root)
    assert problems == [], problems
    assert rules == 1


def test_area_file_citing_an_unregistered_rule_is_named(tmp_path: Path) -> None:
    root = _root(tmp_path, _register(_block()))
    _area_file(root, "### Entry\n\n- **Rules applied:** R-LOCK-9\n")
    problems, _, _ = check(root)
    assert problems == ["R-LOCK-9: cited in docs/features/scan.md but not in the register"]
