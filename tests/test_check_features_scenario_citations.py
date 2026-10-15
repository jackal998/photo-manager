"""Tests for scripts/check_features_scenario_citations.py (#958 split).

The citations live in the landing and in every docs/features/<area>.md, so the
check must read both. Each case points the module at a temporary tree and asserts
the verdict and the file:line it names.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.check_features_scenario_citations as cfc


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    features = tmp_path / "docs" / "features"
    features.mkdir(parents=True)
    scenarios = tmp_path / "qa" / "web" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "s01_real.py").write_text("# driver\n", encoding="utf-8")
    monkeypatch.setattr(cfc, "FEATURES", tmp_path / "docs" / "features.md")
    monkeypatch.setattr(cfc, "FEATURES_DIR", features)
    monkeypatch.setattr(cfc, "WEB_SCENARIOS", scenarios)
    (tmp_path / "docs" / "features.md").write_text("# landing\n", encoding="utf-8")
    return tmp_path


def test_existing_driver_cited_from_area_file_passes(tree: Path, capsys) -> None:
    (tree / "docs" / "features" / "scan.md").write_text(
        "### Entry\n\n- **Related:** `qa/web/scenarios/s01_real.py`\n", encoding="utf-8"
    )
    assert cfc.main() == 0
    assert "1 distinct qa/web/scenarios citations, all resolve" in capsys.readouterr().out


def test_missing_driver_cited_from_area_file_fails_with_its_file_and_line(tree: Path, capsys) -> None:
    (tree / "docs" / "features" / "scan.md").write_text(
        "### Entry\n\n- **Related:** `qa/web/scenarios/s99_gone.py`\n", encoding="utf-8"
    )
    assert cfc.main() == 1
    out = capsys.readouterr().out
    assert "missing driver" in out
    assert "scan.md:3  qa/web/scenarios/s99_gone.py" in out


def test_dead_tree_citation_in_landing_fails(tree: Path, capsys) -> None:
    (tree / "docs" / "features.md").write_text(
        "# landing\n\nSee qa/scenarios/s01_old.py for the desktop run.\n", encoding="utf-8"
    )
    assert cfc.main() == 1
    out = capsys.readouterr().out
    assert "removed tree" in out
    assert "features.md:3  qa/scenarios/s01_old" in out


def test_real_inventory_passes(capsys) -> None:
    assert cfc.main() == 0
    assert "all resolve" in capsys.readouterr().out
