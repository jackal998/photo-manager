"""Tests for scripts/check_rulings_register.py (the design rulings register).

Each case builds a miniature tree in tmp_path so one failure is one cause. The
last two cases run the real register and a CRLF copy of it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import scripts.check_rulings_register as crl

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check_rulings_register.py"


def _block(
    rid: str = "Q1",
    status: str = "implemented",
    implemented_by: str = "`frontend/src/x.tsx::X`",
    date: str = "2026-09-14",
    title: str = "A ruling",
    decision: str = "Something was decided.",
    source: str = "REPLY - review screen open questions.md",
) -> str:
    return (
        f"### {rid} — {title}\n"
        "\n"
        f"- **Decision:** {decision}\n"
        f"- **Date:** {date}\n"
        f"- **Source:** {source}\n"
        f"- **Status:** {status}\n"
        f"- **Implemented by:** {implemented_by}\n"
        "\n"
    )


def _root(tmp_path: Path, *blocks: str) -> Path:
    register = tmp_path / "docs" / "design" / "rulings.md"
    register.parent.mkdir(parents=True)
    register.write_bytes(("# Rulings\n\n" + "".join(blocks)).encode("utf-8"))
    return tmp_path


def _problems(tmp_path: Path, *blocks: str) -> list[str]:
    problems, _rulings, _superseded = crl.check(_root(tmp_path, *blocks))
    return problems


def test_real_register_passes() -> None:
    problems, rulings, _superseded = crl.check(_REPO)
    assert problems == []
    assert rulings > 0


def test_valid_register_passes(tmp_path: Path) -> None:
    assert _problems(tmp_path, _block("Q1"), _block("F1", status="closed (no build)", implemented_by="—")) == []


def test_duplicate_id_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q1"), _block("Q1", title="again"))
    assert any("Q1" in p and "duplicate" in p for p in problems)


def test_missing_field_fails(tmp_path: Path) -> None:
    broken = _block("Q1").replace("- **Source:** REPLY - review screen open questions.md\n", "")
    problems = _problems(tmp_path, broken)
    assert any("Q1" in p and "Source" in p for p in problems)


def test_bad_status_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q1", status="done"))
    assert any("Q1" in p and "Status" in p for p in problems)


def test_superseded_by_dangling_id_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q7", status="superseded by F9", implemented_by="—"))
    assert any("Q7" in p and "F9" in p for p in problems)


def test_superseded_by_existing_id_passes(tmp_path: Path) -> None:
    problems = _problems(
        tmp_path,
        _block("F2", implemented_by="`frontend/src/y.tsx::Y`"),
        _block("Q7", status="superseded by F2", implemented_by="—"),
    )
    assert problems == []


def test_implemented_without_implementer_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q1", implemented_by="—"))
    assert any("Q1" in p and "Implemented by" in p for p in problems)


def test_bad_id_shape_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("X9"))
    assert any("X9" in p and "ID" in p for p in problems)


def test_bad_date_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q1", date="2026/09/14"))
    assert any("Q1" in p and "Date" in p for p in problems)


def test_unknown_date_with_reason_passes(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("OWN-896", date="unknown — ruling date not in the sources"))
    assert problems == []


def test_cli_exit_codes(tmp_path: Path) -> None:
    dup_root = _root(tmp_path / "dup", _block("Q1"), _block("Q1"))
    good_root = _root(tmp_path / "good", _block("Q1"))
    dup = subprocess.run(
        [sys.executable, str(_SCRIPT), str(dup_root)], capture_output=True, text=True, check=False
    )
    good = subprocess.run(
        [sys.executable, str(_SCRIPT), str(good_root)], capture_output=True, text=True, check=False
    )
    assert dup.returncode == 1
    assert good.returncode == 0


@pytest.mark.parametrize("field", ["decision", "source"])
def test_blank_text_field_fails(tmp_path: Path, field: str) -> None:
    problems = _problems(tmp_path, _block("Q1", **{field: ""}))
    assert any("Q1" in p and field.capitalize() in p for p in problems)


def test_blank_date_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q1", date=""))
    assert any("Q1" in p and "Date" in p for p in problems)


@pytest.mark.parametrize("date", ["unknown", "unknown —", "unknown — "])
def test_unknown_date_without_reason_fails(tmp_path: Path, date: str) -> None:
    problems = _problems(tmp_path, _block("OWN-896", date=date))
    assert any("OWN-896" in p and "Date" in p for p in problems)


def test_self_supersession_fails(tmp_path: Path) -> None:
    problems = _problems(tmp_path, _block("Q7", status="superseded by Q7", implemented_by="—"))
    assert any("Q7" in p and "itself" in p for p in problems)


def test_supersession_cycle_fails(tmp_path: Path) -> None:
    problems = _problems(
        tmp_path,
        _block("Q1", status="superseded by Q2", implemented_by="—"),
        _block("Q2", status="superseded by Q1", implemented_by="—"),
    )
    assert any("cycle" in p for p in problems)


def test_zero_rulings_parsed_fails(tmp_path: Path) -> None:
    register = tmp_path / "docs" / "design" / "rulings.md"
    register.parent.mkdir(parents=True)
    register.write_bytes(b"# Rulings\n\n#### Q1 \xe2\x80\x94 wrong heading level\n\n- **Status:** implemented\n")
    problems, rulings, _superseded = crl.check(tmp_path)
    assert rulings == 0
    assert any("no rulings" in p for p in problems)


def test_crlf_copy_of_real_register_passes(tmp_path: Path) -> None:
    source = (_REPO / "docs" / "design" / "rulings.md").read_bytes()
    crlf = source.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    target = tmp_path / "docs" / "design" / "rulings.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(crlf)
    problems, _rulings, _superseded = crl.check(tmp_path)
    assert problems == []
