"""Lint docs/behaviour/rules.md, the register of cross-cutting behaviour rules.

Usage:
    python scripts/check_rules_register.py            # check the repo this file lives in
    python scripts/check_rules_register.py ROOT       # check another tree (tests use this)

Checks, per rule block (### R-<AREA>-<n> — title):
  - the ID matches R-[A-Z]+-\\d+ and is unique;
  - Rule, Owning code, Pins and Status are present;
  - each owning code is path::symbol, the file exists and the symbol occurs in it;
  - each pin is a test (path::[Class::]test_fn: file exists, def and class occur,
    the def sits inside the class) or a scenario file (path exists), or `none`;
  - Status is `unpinned` if and only if Pins is `none`.
Across the docs: every R-<AREA>-<n> token in docs/features.md and docs/**/*.md
resolves to a register ID.

Exit 0 on success (prints "rules register OK: N rules, U unpinned"), 1 on any
problem, each naming the rule it concerns. Stdlib only. Files are read as bytes
and CRLF is normalised, so a Windows checkout lints the same as the blob.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
REGISTER = Path("docs") / "behaviour" / "rules.md"
ID_RE = re.compile(r"R-[A-Z]+-\d+")
TOKEN_RE = re.compile(r"\bR-[A-Z]+-\d+\b")
HEADING_RE = re.compile(r"^### (.+?) — (.+)$")
FIELD_RE = re.compile(r"^- \*\*(Rule|Owning code|Pins|Status):\*\* ?(.*)$")
CODE_RE = re.compile(r"`([^`]+)`")
STATUSES = ("pinned", "unpinned")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _blocks(text: str) -> list[tuple[int, str, str, list[str]]]:
    """Return (line_no, id_token, title, body_lines) for each ### block."""
    out: list[tuple[int, str, str, list[str]]] = []
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
        if match is None:
            out.append((idx + 1, line[4:].split(" ")[0], "", body))
        else:
            out.append((idx + 1, match.group(1), match.group(2), body))
    return out


def _fields(body: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in body:
        match = FIELD_RE.match(line)
        if match and match.group(1) not in found:
            found[match.group(1)] = match.group(2).strip()
    return found


def _word_in(symbol: str, text: str) -> bool:
    return all(re.search(rf"\b{re.escape(part)}\b", text) for part in symbol.split("."))


def _class_block(text: str, class_name: str) -> str | None:
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            body: list[str] = []
            for follow in lines[idx + 1:]:
                if follow and not follow[0].isspace():
                    break
                body.append(follow)
            return "\n".join(body)
    return None


def _check_owner(rid: str, token: str, root: Path, problems: list[str]) -> None:
    if "::" not in token:
        problems.append(f"{rid}: owning code `{token}` must be path::symbol")
        return
    rel, symbol = token.split("::", 1)
    path = root / rel
    if not path.is_file():
        problems.append(f"{rid}: owning file does not exist: {rel}")
        return
    if not _word_in(symbol, _read(path)):
        problems.append(f"{rid}: symbol {symbol} not found in {rel}")


def _check_test_pin(rid: str, token: str, root: Path, problems: list[str]) -> None:
    parts = token.split("::")
    if len(parts) not in (2, 3):
        problems.append(f"{rid}: pin `{token}` must be path::test_fn or path::Class::test_fn")
        return
    rel = parts[0]
    class_name = parts[1] if len(parts) == 3 else None
    fn = parts[-1]
    path = root / rel
    if not path.is_file():
        problems.append(f"{rid}: pin file does not exist: {rel}")
        return
    text = _read(path)
    scope = text
    if class_name is not None:
        if not re.search(rf"^class {re.escape(class_name)}\b", text, re.M):
            problems.append(f"{rid}: class {class_name} not found in {rel}")
            return
        scope = _class_block(text, class_name) or ""
    if not re.search(rf"^\s*(?:async\s+)?def {re.escape(fn)}\(", scope, re.M):
        where = f" in class {class_name}" if class_name else ""
        problems.append(f"{rid}: test function {fn} not found{where} in {rel}")


def _check_scenario_pin(rid: str, token: str, root: Path, problems: list[str]) -> None:
    if not (root / token).is_file():
        problems.append(f"{rid}: scenario pin file does not exist: {token}")


def _check_block(rid: str, body: list[str], root: Path, problems: list[str]) -> bool:
    """Check one block; return True when its Status is `unpinned`."""
    fields = _fields(body)
    missing = [name for name in ("Rule", "Owning code", "Pins", "Status") if name not in fields]
    if missing:
        problems.append(f"{rid}: missing field(s): {', '.join(missing)}")
        return False
    owners = CODE_RE.findall(fields["Owning code"])
    if not owners:
        problems.append(f"{rid}: Owning code has no `path::symbol` token")
    for token in owners:
        _check_owner(rid, token, root, problems)
    status = fields["Status"]
    if status not in STATUSES:
        problems.append(f"{rid}: Status must be pinned or unpinned, got {status!r}")
    pins_text = fields["Pins"]
    if pins_text == "none":
        if status == "pinned":
            problems.append(f"{rid}: Status is pinned but Pins is none")
        return status == "unpinned"
    pins = CODE_RE.findall(pins_text)
    if not pins:
        problems.append(f"{rid}: Pins must be `none` or backticked pin tokens")
    if status == "unpinned":
        problems.append(f"{rid}: Status is unpinned but Pins lists tests or scenarios")
    for token in pins:
        if "::" in token:
            _check_test_pin(rid, token, root, problems)
        else:
            _check_scenario_pin(rid, token, root, problems)
    return status == "unpinned"


def _citation_files(root: Path) -> list[Path]:
    files: list[Path] = []
    features = root / "docs" / "features.md"
    if features.is_file():
        files.append(features)
    docs = root / "docs"
    if docs.is_dir():
        files.extend(sorted(docs.rglob("*.md")))
    return files


def check(root: Path) -> tuple[list[str], int, int]:
    """Return (problems, rule_count, unpinned_count) for the tree at ``root``."""
    problems: list[str] = []
    register = root / REGISTER
    if not register.is_file():
        return [f"register missing: {REGISTER.as_posix()}"], 0, 0
    seen: set[str] = set()
    ids: set[str] = set()
    rules = 0
    unpinned = 0
    for line_no, rid, _title, body in _blocks(_read(register)):
        rules += 1
        if not re.fullmatch(r"R-[A-Z]+-\d+", rid):
            problems.append(f"line {line_no}: bad ID shape {rid!r} (want R-<AREA>-<n>)")
            continue
        if rid in seen:
            problems.append(f"{rid}: duplicate ID (line {line_no})")
        seen.add(rid)
        ids.add(rid)
        if _check_block(rid, body, root, problems):
            unpinned += 1
    for path in _citation_files(root):
        for token in sorted(set(TOKEN_RE.findall(_read(path)))):
            if token not in ids:
                rel = path.relative_to(root).as_posix()
                problems.append(f"{token}: cited in {rel} but not in the register")
    return problems, rules, unpinned


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else _REPO
    problems, rules, unpinned = check(root)
    if problems:
        for problem in problems:
            print(f"rules register: {problem}", file=sys.stderr)
        print(f"rules register FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"rules register OK: {rules} rules, {unpinned} unpinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
