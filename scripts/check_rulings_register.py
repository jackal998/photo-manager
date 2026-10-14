"""Lint docs/design/rulings.md, the register of design rulings.

Usage:
    python scripts/check_rulings_register.py            # check the repo this file lives in
    python scripts/check_rulings_register.py ROOT       # check another tree (tests use this)

Checks, per ruling block (### <ID> — title):
  - the ID is Q<n>, L<n>, F<n>, DEV-<n> or OWN-<n>, and is unique;
  - Decision, Date, Source, Status and Implemented by are present and non-blank;
  - Date is YYYY-MM-DD, or `unknown — <reason>`;
  - Status is `implemented`, `closed (no build)`, `retracted`, or
    `superseded by <ID>` where <ID> is a block in this register, not the
    ruling itself, and not part of a supersession cycle;
  - Implemented by is not `—` when Status is `implemented`;
  - at least one ruling is parsed (a file of `####` headings fails).

Exit 0 on success (prints "rulings register OK: N rulings, S superseded"), 1 on
any problem, each naming the ruling it concerns. Stdlib only. Files are read as
bytes and CRLF is normalised, so a Windows checkout lints the same as the blob.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
REGISTER = Path("docs") / "design" / "rulings.md"
HEADING_RE = re.compile(r"^### (\S+) — (.+)$")
FIELD_RE = re.compile(r"^- \*\*(Decision|Date|Source|Status|Implemented by):\*\* ?(.*)$")
ID_RE = re.compile(r"^(?:[QLF]\d+|DEV-\d+|OWN-\d+)$")
DATE_RE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|unknown — \S.*)$")
SUPERSEDED_RE = re.compile(r"^superseded by (\S+)$")
REQUIRED = ("Decision", "Date", "Source", "Status", "Implemented by")
FIXED_STATUSES = ("implemented", "closed (no build)", "retracted")
NO_IMPLEMENTER = ("", "—", "-")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _blocks(text: str) -> list[tuple[int, str, list[str]]]:
    """Return (line_no, heading_id, body_lines) for each ### block."""
    out: list[tuple[int, str, list[str]]] = []
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        body: list[str] = []
        for follow in lines[idx + 1:]:
            if follow.startswith("### ") or follow.startswith("## "):
                break
            body.append(follow)
        match = HEADING_RE.match(line)
        rid = match.group(1) if match else line[4:].split(" ")[0]
        out.append((idx + 1, rid, body))
    return out


def _fields(body: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in body:
        match = FIELD_RE.match(line)
        if match and match.group(1) not in found:
            found[match.group(1)] = match.group(2).strip()
    return found


def _check_block(rid: str, body: list[str], ids: set[str], problems: list[str]) -> str | None:
    """Check one block; return the supersession target when Status is `superseded by`."""
    fields = _fields(body)
    missing = [name for name in REQUIRED if not fields.get(name)]
    if missing:
        problems.append(f"{rid}: missing or blank field(s): {', '.join(missing)}")
    date = fields.get("Date", "")
    if date and not DATE_RE.match(date):
        problems.append(f"{rid}: Date must be YYYY-MM-DD or `unknown — <reason>`, got {date!r}")
    status = fields.get("Status", "")
    superseded = SUPERSEDED_RE.match(status)
    if superseded:
        target = superseded.group(1)
        if target == rid:
            problems.append(f"{rid}: superseded by itself")
        elif target not in ids:
            problems.append(f"{rid}: superseded by {target}, which is not in the register")
        return target
    if status not in FIXED_STATUSES:
        problems.append(f"{rid}: Status must be implemented, closed (no build), retracted "
                        f"or superseded by <ID>, got {status!r}")
    if status == "implemented" and fields.get("Implemented by", "") in NO_IMPLEMENTER:
        problems.append(f"{rid}: Status is implemented but Implemented by is empty or —")
    return None


def _cycle_from(rid: str, supersedes: dict[str, str]) -> list[str] | None:
    """Return the loop reached by following supersession from ``rid``, if any."""
    path: list[str] = []
    cur = rid
    while cur in supersedes and cur not in path:
        path.append(cur)
        cur = supersedes[cur]
    if cur in path:
        return path[path.index(cur):]
    return None


def check(root: Path) -> tuple[list[str], int, int]:
    """Return (problems, ruling_count, superseded_count) for the tree at ``root``."""
    register = root / REGISTER
    if not register.is_file():
        return [f"register missing: {REGISTER.as_posix()}"], 0, 0
    problems: list[str] = []
    blocks = _blocks(_read(register))
    if not blocks:
        problems.append(f"no rulings parsed from {REGISTER.as_posix()}: headings must be `### <ID> — <title>`")
    ids = {rid for _line, rid, _body in blocks if ID_RE.match(rid)}
    seen: set[str] = set()
    supersedes: dict[str, str] = {}
    for line_no, rid, body in blocks:
        if not ID_RE.match(rid):
            problems.append(f"line {line_no}: bad ID shape {rid!r} (want Q<n>, L<n>, F<n>, DEV-<n> or OWN-<n>)")
            continue
        if rid in seen:
            problems.append(f"{rid}: duplicate ID (line {line_no})")
        seen.add(rid)
        target = _check_block(rid, body, ids, problems)
        if target is not None:
            supersedes[rid] = target
    for rid in sorted(supersedes):
        if supersedes[rid] == rid:
            continue
        loop = _cycle_from(rid, supersedes)
        if loop is not None:
            problems.append(f"{rid}: supersession cycle through {' → '.join(loop)}")
    return problems, len(blocks), len(supersedes)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else _REPO
    problems, rulings, superseded = check(root)
    if problems:
        for problem in problems:
            print(f"rulings register: {problem}", file=sys.stderr)
        print(f"rulings register FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"rulings register OK: {rulings} rulings, {superseded} superseded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
