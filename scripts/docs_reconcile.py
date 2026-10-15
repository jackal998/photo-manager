"""Mechanical half of /docs-reconcile: what changed since the last reconcile.

Usage:
    python scripts/docs_reconcile.py                   # --since = SHA of the last entry in docs/reconcile-log.md
    python scripts/docs_reconcile.py --since <sha>     # explicit lower bound (exclusive)
    python scripts/docs_reconcile.py --since <sha> --until <rev>   # --until defaults to HEAD

Prints a markdown report on stdout, sections (a)-(f):
  (a) merged PRs in the range (squash commits ending in `(#N)`), plus other commits;
  (b) changed files per PR / commit;
  (c) rules at risk: rules in docs/behaviour/rules.md whose owning-code path changed;
  (d) pins at risk: rules whose pin file (test, vitest file or scenario) changed;
  (d2) unpinned rules: every one, changed or not, since nothing else asserts them;
  (e) features entries: touched in the range, and candidates that may be stale;
  (f) the existing mechanical checks, run on the working tree.

The judgment half (re-verify each rule sentence, compare Docs impact claims, file
or fix findings, append the log entry) is the `docs-reconcile` skill.

Exit 0 when every check passes, 1 when one fails, 2 on a usage or git error.
Stdlib only. Git is read-only (log, diff-tree, diff, show, cat-file, rev-parse).
Parsing helpers are imported from check_rules_register, gen_features_index and the
diff mapping of docs_impact_guard, so "entry" and "rule" mean what the CI gates mean.
"""
from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts import check_rules_register as crr  # noqa: E402
from scripts.gen_features_index import EntrySection, entry_sections  # noqa: E402
from scripts.hooks import docs_impact_guard as dig  # noqa: E402

LOG_PATH = "docs/reconcile-log.md"
REGISTER_PATH = "docs/behaviour/rules.md"
PR_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")
LOG_SHA_RE = re.compile(r"^- SHA: ([0-9a-f]{7,40})\s*$", re.M)
# Source paths an entry cites: markdown links (relative to docs/) and backticked repo paths.
_LINK_PATH_RE = re.compile(r"\]\(((?:\.\./)*[\w./-]+\.(?:py|tsx?|ya?ml))(?:#[^)]*)?\)")
_TICK_PATH_RE = re.compile(r"`((?:[\w-]+/)+[\w.-]+\.(?:py|tsx?|ya?ml))`")
_NOT_SOURCE_PREFIXES = ("tests/", "qa/", "docs/", "news/")
_BEHAVIOUR_ROOTS = ("app/", "core/", "infrastructure/", "scanner/", "frontend/src/")
_TAIL_LINES = 12


@dataclass(frozen=True)
class Unit:
    """One non-merge commit in the range: a squash-merged PR when its subject ends in `(#N)`."""

    sha: str
    subject: str
    pr: int | None
    files: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"#{self.pr}" if self.pr is not None else f"{self.sha[:7]} (no PR number)"


@dataclass(frozen=True)
class RuleRef:
    """A register rule, reduced to the paths it depends on."""

    rule_id: str
    title: str
    owners: tuple[str, ...]
    pins: tuple[str, ...]
    unpinned: bool


@dataclass(frozen=True)
class CheckResult:
    name: str
    code: int
    tail: str


# ---- pure helpers (unit-tested) ---------------------------------------------


def parse_pr_number(subject: str) -> int | None:
    """The N of a squash subject ending in `(#N)`; None for any other subject."""
    match = PR_SUFFIX_RE.search(subject)
    return int(match.group(1)) if match else None


def last_logged_sha(log_text: str) -> str | None:
    """The SHA on the last `- SHA: <sha>` line of the reconcile log, or None."""
    found = LOG_SHA_RE.findall(log_text.replace("\r\n", "\n"))
    return found[-1] if found else None


def _pin_path(token: str) -> str:
    return token.split("::", 1)[0]


def _unique(paths) -> tuple[str, ...]:
    """Paths in first-seen order, each once: a rule may cite one file for two symbols or pins."""
    return tuple(dict.fromkeys(paths))


def parse_rules(register_text: str) -> list[RuleRef]:
    """Every rule block of the register, with its owning-code and pin paths."""
    rules: list[RuleRef] = []
    for _line, rule_id, title, body in crr._blocks(register_text.replace("\r\n", "\n")):
        fields = crr._fields(body)
        owners = _unique(
            _pin_path(tok) for tok in crr.CODE_RE.findall(fields.get("Owning code", "")) if "::" in tok
        )
        pins = _unique(_pin_path(tok) for tok in crr.CODE_RE.findall(fields.get("Pins", "")))
        rules.append(RuleRef(rule_id, title, owners, pins, fields.get("Status") == "unpinned"))
    return rules


def unpinned_rules(rules: list[RuleRef]) -> list[RuleRef]:
    """Rules no test or scenario asserts: nothing else would notice them going stale."""
    return [rule for rule in rules if rule.unpinned]


def is_behaviour_bearing(files) -> bool:
    """True when a change touches a source root whose behaviour the register and features describe."""
    return any(path.startswith(_BEHAVIOUR_ROOTS) for path in files)


def rules_at_risk(rules: list[RuleRef], changed: set[str]) -> dict[str, list[str]]:
    """Rule ID -> its owning-code paths that are among the changed files."""
    out: dict[str, list[str]] = {}
    for rule in rules:
        hit = [path for path in rule.owners if path in changed]
        if hit:
            out[rule.rule_id] = hit
    return out


def pins_at_risk(rules: list[RuleRef], changed: set[str]) -> dict[str, list[str]]:
    """Rule ID -> its pin files (tests, vitest files, scenarios) that changed."""
    out: dict[str, list[str]] = {}
    for rule in rules:
        hit = [path for path in rule.pins if path in changed]
        if hit:
            out[rule.rule_id] = hit
    return out


def surface_paths(entry_lines: list[str]) -> set[str]:
    """Source files an entry cites as its implementation surface (not tests, scenarios or docs)."""
    found: set[str] = set()
    for line in entry_lines:
        for raw in _LINK_PATH_RE.findall(line) + _TICK_PATH_RE.findall(line):
            path = re.sub(r"^(?:\.\./)+", "", raw)
            if path.startswith(_NOT_SOURCE_PREFIXES) or ".test." in path:
                continue
            found.add(path)
    return found


def stale_candidates(
    entries: list[tuple[str, str, set[str]]], touched: set[str], changed: set[str]
) -> list[tuple[str, list[str]]]:
    """(label, cited source paths that changed) for entries whose own text was not touched."""
    out: list[tuple[str, list[str]]] = []
    for label, slug, surface in entries:
        hits = sorted(path for path in surface if path in changed)
        if hits and slug not in touched:
            out.append((label, hits))
    return out


# ---- git and process helpers (not unit-tested: they need a repo) -------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    ).stdout


def _show(rev: str, path: str) -> str:
    """The file at a revision; empty when it does not exist there."""
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{rev}:{path}"],
        cwd=_REPO,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return "" if probe.returncode != 0 else _git("show", f"{rev}:{path}")


def _commit(rev: str) -> str:
    return _git("rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def _units(since: str, until: str) -> list[Unit]:
    log = _git("log", "--no-merges", "--reverse", "--format=%H%x1f%s", f"{since}..{until}")
    units: list[Unit] = []
    for line in log.splitlines():
        sha, subject = line.split("\x1f", 1)
        files = tuple(_git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines())
        units.append(Unit(sha, subject, parse_pr_number(subject), files))
    return units


_LINK_DEST_RE = re.compile(r"\]\(([^)\s]+)\)")
_RULE_LINE = "---"


def _entry_body(lines: list[str]) -> list[str]:
    """An entry's text for comparison: link destinations masked (a move rewrites them for the
    new depth, which is not an edit), trailing blank and separator lines dropped (a block's
    trailing separator depends on what follows it in its file, not on the entry)."""
    masked = [_LINK_DEST_RE.sub("](LINK)", line) for line in lines]
    while masked and (not masked[-1].strip() or masked[-1].strip() == _RULE_LINE):
        masked.pop()
    return masked


def _overlaps(section: EntrySection, ranges: list[tuple[int, int]]) -> bool:
    return any(section.start <= hi and lo <= section.end for lo, hi in ranges)


def touched_entries(
    base_texts: dict[str, str], head_texts: dict[str, str], diff: str
) -> tuple[set[str], dict[str, str], list[tuple[str, str, set[str]]]]:
    """Entry slugs touched by the feature-inventory range, their labels, and every head-side
    entry with the source paths its text cites. ``base_texts``/``head_texts`` map each features
    file to its text; ``diff`` is the range's diff over those files. Pure: texts in, data out.

    An entry's identity is its slug (unique across the inventory), so an entry moved between
    files pairs with itself. It is touched when it is added or deleted, when its text changed
    (link destinations masked), or when a hunk overlaps it in the same file on both sides."""
    hunks = dig._hunks_by_path(diff)
    base = _entry_index(base_texts)
    head = _entry_index(head_texts)
    touched: set[str] = set()
    labels: dict[str, str] = {}
    for slug in set(base) | set(head):
        b, h = base.get(slug), head.get(slug)
        labels[slug] = (h or b)[0]
        if b is None or h is None:
            touched.add(slug)
            continue
        if _entry_body(b[2]) != _entry_body(h[2]):
            touched.add(slug)
        elif b[1] == h[1]:
            removed, added = hunks.get(h[1], ([], []))
            if _overlaps(h[3], added) or _overlaps(b[3], removed):
                touched.add(slug)
    entries = [
        (label, slug, surface_paths(lines))
        for slug, (label, _path, lines, _sec) in head.items()
    ]
    return touched, labels, entries


def _entry_index(texts: dict[str, str]) -> dict[str, tuple[str, str, list[str], EntrySection]]:
    """slug -> (label, file, the entry's lines, its section span) across every features file."""
    index: dict[str, tuple[str, str, list[str], EntrySection]] = {}
    for path in sorted(texts):
        text = texts[path]
        lines = text.replace("\r\n", "\n").split("\n")
        for sec in entry_sections(text):
            index[sec.slug] = (sec.label, path, lines[sec.start - 1 : sec.end], sec)
    return index


def _features_paths(rev: str) -> list[str]:
    """The feature-inventory files at a revision: the landing and every docs/features/<area>.md."""
    listing = _git("ls-tree", "-r", "--name-only", rev, "--", dig.DOC_PATH, dig.DOC_DIR)
    return [path for path in listing.splitlines() if dig.is_features_path(path)]


def _features_touched(since: str, until: str) -> tuple[set[str], dict[str, str], list[tuple[str, str, set[str]]]]:
    return touched_entries(
        {p: _show(since, p) for p in _features_paths(since)},
        {p: _show(until, p) for p in _features_paths(until)},
        _git("diff", "--unified=0", since, until, "--", dig.DOC_PATH, dig.DOC_DIR),
    )


def _run_check(name: str, script: str, args: list[str]) -> CheckResult:
    proc = subprocess.run(
        [sys.executable, script, *args],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    )
    output = (proc.stdout + proc.stderr).strip().splitlines()
    return CheckResult(name, proc.returncode, "\n".join(output[-_TAIL_LINES:]))


def _optional_check_scripts() -> list[tuple[str, str, list[str]]]:
    """Checks that other work may add later: detected by file, skipped when absent."""
    found: list[tuple[str, str, list[str]]] = []
    if (_REPO / "scripts" / "gen_reference.py").is_file():
        found.append(("generated reference", "scripts/gen_reference.py", ["--check"]))
    for path in sorted(glob.glob(str(_REPO / "scripts" / "*ruling*.py"))):
        found.append(("rulings lint", Path(path).relative_to(_REPO).as_posix(), []))
    return found


def run_checks() -> list[CheckResult]:
    checks = [
        _run_check("features index", "scripts/gen_features_index.py", ["--check"]),
        _run_check("rules register", "scripts/check_rules_register.py", []),
    ]
    for name, script, args in _optional_check_scripts():
        checks.append(_run_check(name, script, args))
    return checks


# ---- report -----------------------------------------------------------------


def _list_or_none(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- none"]


def _render_rules(rules: list[RuleRef], changed: set[str], units: list[Unit]) -> list[str]:
    hits = rules_at_risk(rules, changed)
    lines = ["## (c) Rules at risk", ""]
    if not hits:
        return [*lines, "- none", ""]
    for rule_id, paths in hits.items():
        touched_by = [u.label for u in units if any(p in u.files for p in paths)]
        lines.append(f"- {rule_id} (owning code changed: {', '.join(paths)}; PR/commit: {', '.join(touched_by)})")
    return [*lines, ""]


def _render_pins(rules: list[RuleRef], changed: set[str], units: list[Unit]) -> list[str]:
    hits = pins_at_risk(rules, changed)
    lines = ["## (d) Pins at risk", ""]
    if not hits:
        return [*lines, "- none", ""]
    for rule_id, paths in hits.items():
        touched_by = [u.label for u in units if any(p in u.files for p in paths)]
        lines.append(f"- {rule_id} (pin changed: {', '.join(paths)}; PR/commit: {', '.join(touched_by)})")
    return [*lines, ""]


def _render_unpinned(rules: list[RuleRef]) -> list[str]:
    """Unpinned rules have no test net: the skill re-verifies every one, changed or not."""
    lines = [
        "## (d2) Unpinned rules (always re-verified)",
        "",
        "No test or scenario asserts these; the skill sends every one to a fresh reviewer.",
        "",
    ]
    return [*lines, *_list_or_none([f"{rule.rule_id} — {rule.title}" for rule in unpinned_rules(rules)]), ""]


def _render_features(since: str, until: str, changed: set[str]) -> list[str]:
    touched, labels, entries = _features_touched(since, until)
    lines = [
        "## (e) Features entries",
        "",
        "Touched in range:",
        *_list_or_none(sorted({labels[slug] for slug in touched})),
        "",
        "Possibly stale (best effort): an entry whose text did not change, but a non-test",
        "source file it cites (markdown link or backticked path) changed in range. Entries",
        "that cite no source path cannot be checked this way; the Docs impact gate and the",
        "skill's PR-by-PR comparison cover them.",
        "",
    ]
    stale = stale_candidates(entries, touched, changed)
    return [*lines, *_list_or_none([f"{label}: {', '.join(paths)}" for label, paths in stale]), ""]


def _render_checks(checks: list[CheckResult]) -> list[str]:
    lines = ["## (f) Mechanical checks (working tree)", ""]
    for check in checks:
        status = "PASS" if check.code == 0 else f"FAIL (exit {check.code})"
        lines += [f"- {check.name}: {status}", "", "  ```", *(f"  {ln}" for ln in check.tail.splitlines()), "  ```", ""]
    return lines


def build_report(since: str, until: str) -> tuple[str, bool]:
    """The full markdown report, and whether every mechanical check passed."""
    units = _units(since, until)
    changed = {path for unit in units for path in unit.files}
    rules = parse_rules(_show(until, REGISTER_PATH))
    checks = run_checks()
    prs = [u for u in units if u.pr is not None]
    others = [u for u in units if u.pr is None]
    lines = [
        "# docs-reconcile report",
        "",
        f"Range: `{since[:7]}..{until[:7]}` ({len(units)} non-merge commit(s), {len(changed)} changed file(s)).",
        "",
        "## (a) Merged PRs in range",
        "",
        *_list_or_none([f"#{u.pr} — {u.subject} (`{u.sha[:7]}`)" for u in prs]),
        "",
        "Other commits (no `(#N)` suffix):",
        *_list_or_none([f"`{u.sha[:7]}` {u.subject}" for u in others]),
        "",
        "## (b) Changed files per PR",
        "",
    ]
    for unit in units:
        bearing = " [behaviour-bearing]" if is_behaviour_bearing(unit.files) else ""
        lines.append(f"### {unit.label} — {unit.subject}{bearing}")
        lines.extend(_list_or_none(list(unit.files)))
        lines.append("")
    lines.extend(_render_rules(rules, changed, units))
    lines.extend(_render_pins(rules, changed, units))
    lines.extend(_render_unpinned(rules))
    lines.extend(_render_features(since, until, changed))
    lines.extend(_render_checks(checks))
    return "\n".join(lines) + "\n", all(check.code == 0 for check in checks)


def _resolve_since(arg: str | None, until: str) -> str:
    if arg:
        return _commit(arg)
    log_text = _show(until, LOG_PATH)
    sha = last_logged_sha(log_text)
    if sha is None:
        raise ValueError(f"no `- SHA:` entry in {LOG_PATH}; pass --since <sha>")
    return _commit(sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--since", help="lower bound, exclusive (default: last SHA in the reconcile log)")
    parser.add_argument("--until", default="HEAD", help="upper bound, inclusive (default: HEAD)")
    args = parser.parse_args(argv)
    try:
        until = _commit(args.until)
        since = _resolve_since(args.since, until)
        report, passed = build_report(since, until)
    except (subprocess.CalledProcessError, ValueError) as exc:
        sys.stderr.write(f"docs_reconcile: {exc}\n")
        return 2
    sys.stdout.write(report)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
