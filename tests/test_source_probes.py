"""Source-shape probes — cross-cutting invariants no scripted test can hold.

Per [#243](https://github.com/jackal998/photo-manager/issues/243): the qa
scenario batch is excellent at *replay* of canonical paths but architecturally
cannot catch certain bug classes — a sweep-shaped invariant ("no file anywhere
under these roots may do X") has no single call site to test. This file is the
complementary *probe* layer that runs in CI on every PR.

Each probe below targets an invariant that has bitten this repo at least once,
and each one's target survives the #646 desktop-client removal. They come from
`tests/test_ui_probes.py`, which #646 deleted because most of its probes read
`app/views/**` source that no longer exists; these six do not, so they moved
here with their scan roots narrowed to the surviving trees:

* `subprocess.Popen` without `creationflags` in `scanner/` + `infrastructure/`
  (#427 — a visible console window on the `--noconsole` build)
* the literal `"keep"` written to `user_decision` (#425)
* `scanner/dedup.py::_make_row`'s per-row stat budget (#474)
* the `-video` testid `VideoTile.tsx` owes web scenario s71
* every `ScanProgressBus` method implemented by `SseScanBus` (T7)
* translation VALUE sweeps — English passthroughs and legacy MOVE wording

What did NOT come back, and why, is recorded in #646 PR A's body: nine of the
dropped probes read a module under `app/views/` that no longer exists. The
other two — `image_service_has_zero_pyside6_references` and
`scan_runner_has_no_qthread_method_calls` — were dropped because live guards
now cover them: `tests/test_scan_runner.py` (no-QThread, at runtime) and
`tests/test_web_qt_free.py`'s `_PHASE2B_FILES`, which names
`infrastructure/image_service.py`.

Authoring guide: `docs/testing.md` — "Probe layer — authoring a new probe".
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SCANNER_DEDUP_PATH = REPO / "scanner" / "dedup.py"
EN_YAML = REPO / "translations" / "en.yml"
ZH_TW_YAML = REPO / "translations" / "zh_TW.yml"
VIDEO_TILE_TSX_PATH = REPO / "frontend" / "src" / "components" / "VideoTile.tsx"

# Scan roots are parameters, not constants baked into the walkers, so the
# red half of each probe's proof can point it at a synthetic tree.
_POPEN_SCAN_ROOTS = [REPO / "scanner", REPO / "infrastructure"]
_KEEP_LITERAL_SCAN_ROOTS = [REPO / "core", REPO / "scanner", REPO / "app"]


# ---------------------------------------------------------------------------
# #427 — Popen creationflags
# ---------------------------------------------------------------------------
# PR #420 introduced a PyInstaller `--noconsole` (windowed) Windows build. When
# a windowed-subsystem parent spawns a console-subsystem child (e.g.
# `exiftool.exe`) WITHOUT a `creationflags` value that suppresses console
# allocation, Windows allocates a fresh visible console for the child. Users see
# it as spam, close it, and inadvertently kill the child mid-batch. Issue #427
# was the concrete bite: `scanner/exif.py` spawned exiftool with no
# `creationflags` and a console popped up during scans.
#
# `tests/test_scanner_exif.py` pins that ONE call site's kwargs. This probe is
# the sweep: every Popen in the two trees the frozen exe bundles must declare
# the kwarg, whatever its value — the point is that somebody decided.

# Relative path -> one-line reason. Empty today: every Popen under these roots
# spawns a child that COULD open a console on a windowed-build parent. A future
# GUI-subsystem child (explorer.exe and friends) belongs here with its reason.
_POPEN_CREATIONFLAGS_ALLOWLIST: dict[str, str] = {}


def _find_popen_calls_missing_creationflags(py_path: Path) -> list[tuple[int, str]]:
    """Return ``(line_no, snippet)`` for every ``Popen(...)`` in *py_path*
    that does not declare a ``creationflags=`` keyword.

    Catches both `subprocess.Popen(...)` (attribute call on the module) and a
    bare `Popen(...)` from `from subprocess import Popen`.
    """
    findings: list[tuple[int, str]] = []
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_popen = (
            (isinstance(func, ast.Attribute) and func.attr == "Popen")
            or (isinstance(func, ast.Name) and func.id == "Popen")
        )
        if not is_popen:
            continue
        if not any(kw.arg == "creationflags" for kw in node.keywords):
            findings.append(
                (node.lineno, "subprocess.Popen(...) without creationflags=")
            )
    return findings


def _sweep_popen(scan_roots: list[Path]) -> list[tuple[str, int, str]]:
    leaks: list[tuple[str, int, str]] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for py_path in root.rglob("*.py"):
            rel = py_path.relative_to(REPO).as_posix()
            if rel in _POPEN_CREATIONFLAGS_ALLOWLIST:
                continue
            for lineno, snippet in _find_popen_calls_missing_creationflags(py_path):
                leaks.append((rel, lineno, snippet))
    return leaks


def test_probe_scanner_and_infrastructure_popen_declare_creationflags():
    """#427 forward-defensive: every ``subprocess.Popen`` call site in
    ``scanner/`` and ``infrastructure/`` must declare ``creationflags=``.

    Why those two trees: the ``--noconsole`` build bundles both. The probe does
    not care WHAT value is passed (0 on POSIX, ``CREATE_NO_WINDOW`` on Windows,
    or a computed expression) — only that the choice was made rather than
    defaulted into #427.
    """
    leaks = _sweep_popen(_POPEN_SCAN_ROOTS)
    assert not leaks, (
        "subprocess.Popen call sites in scanner/ or infrastructure/ are missing "
        "a creationflags= keyword. On a PyInstaller --noconsole (windowed) "
        "build, a console-subsystem child spawned without "
        "creationflags=CREATE_NO_WINDOW allocates a visible console window — "
        "the bug class fixed by #427. Pass creationflags=_CREATE_NO_WINDOW "
        "(the module-level constant pattern from scanner/exif.py) or add the "
        "path to _POPEN_CREATIONFLAGS_ALLOWLIST with a one-line reason if the "
        "child is legitimately a GUI-subsystem process. Leaks: "
        + "\n".join(f"  {f}:{n}: {s}" for f, n, s in leaks)
    )


# ---------------------------------------------------------------------------
# #425 — the literal "keep" written to user_decision
# ---------------------------------------------------------------------------
# The canonical keep state is the empty string. Pre-#425
# `core/services/auto_select.py:73` wrote `{p: "keep" for p in keepers}`, which
# surfaced as raw "keep" text in the Action column — two different stored values
# for one semantic. The allowlist below was five `app/views/` read-side files;
# all five are gone with #646, so it is empty and the sweep is now strict.

_KEEP_LITERAL_WRITE_ALLOWLIST: dict[str, str] = {}


def _ast_finds_keep_literal_writes(py_path: Path) -> list[tuple[int, str]]:
    """Return ``(line_no, snippet)`` for every literal ``"keep"`` WRITE.

    Three shapes, matching the ones that have actually occurred: an Assign to a
    name/attribute called ``user_decision``, a ``user_decision="keep"`` keyword
    argument, and the `{p: "keep" for p in keepers}` dict comprehension that was
    the #425 bite. Comparisons are deliberately not matched — reading the legacy
    value back is legitimate.
    """
    findings: list[tuple[int, str]] = []
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                is_user_decision = (
                    (isinstance(tgt, ast.Name) and tgt.id == "user_decision")
                    or (isinstance(tgt, ast.Attribute) and tgt.attr == "user_decision")
                )
                if is_user_decision and isinstance(node.value, ast.Constant) \
                        and node.value.value == "keep":
                    findings.append((node.lineno, 'user_decision = "keep"'))
        if isinstance(node, ast.keyword) and node.arg == "user_decision":
            if isinstance(node.value, ast.Constant) and node.value.value == "keep":
                findings.append((node.lineno, 'user_decision="keep" keyword'))
        if isinstance(node, ast.DictComp):
            if isinstance(node.value, ast.Constant) and node.value.value == "keep":
                findings.append(
                    (node.lineno, '{...: "keep" for ...} dict comprehension')
                )
    return findings


def _sweep_keep_literal(scan_roots: list[Path]) -> list[tuple[str, int, str]]:
    leaks: list[tuple[str, int, str]] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for py_path in root.rglob("*.py"):
            rel = py_path.relative_to(REPO).as_posix()
            if rel in _KEEP_LITERAL_WRITE_ALLOWLIST:
                continue
            for lineno, snippet in _ast_finds_keep_literal_writes(py_path):
                leaks.append((rel, lineno, snippet))
    return leaks


def test_probe_production_code_does_not_write_literal_keep_to_user_decision():
    """#425 forward-defensive: no production source may write the literal
    ``"keep"`` as a ``user_decision`` value — the canonical keep state is ``""``.

    Scope is production source under ``core/``, ``scanner/`` and ``app/``. Tests
    and qa drivers are exempt: they consciously construct legacy fixture data to
    exercise back-compat reads.
    """
    leaks = _sweep_keep_literal(_KEEP_LITERAL_SCAN_ROOTS)
    assert not leaks, (
        'Production source files write the literal "keep" string to '
        'user_decision. Use the canonical empty string "" instead (matches '
        "review_service.VALID_DECISIONS and the Set Action → keep path). If a "
        "file is legitimately a back-compat READER, add it to "
        "_KEEP_LITERAL_WRITE_ALLOWLIST with a one-line reason. Leaks: "
        + "\n".join(f"  {f}:{n}: {s}" for f, n, s in leaks)
    )


# ---------------------------------------------------------------------------
# #474 — per-row stat budget in scanner/dedup.py::_make_row
# ---------------------------------------------------------------------------
# `_make_row` runs once per classified record in the scan loop. Its `getsize` +
# `getmtime` plus `get_filesystem_creation_datetime` are the documented
# scan-time-vs-load-time trade-off (scanner/dedup.py:113-114). The trade-off is
# principled but has no automatic guardrail: a 4th `os.path.get*` added without
# realising this is a hot loop would silently compound per-row I/O.


def _count_os_path_get_calls(fn: ast.FunctionDef) -> list[tuple[int, str]]:
    """Return ``(line_no, attr)`` for every ``os.path.get*`` call inside *fn*.

    Bare ``getsize(...)`` (from ``from os.path import getsize``) is not counted:
    the probe is tied to the canonical spelling ``_make_row`` uses today.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute)
                and func.attr.startswith("get")
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"):
            continue
        found.append((node.lineno, func.attr))
    return found


def _make_row_ast(source_path: Path) -> ast.FunctionDef:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_make_row":
            return node
    raise AssertionError(
        f"_make_row function not found in {source_path} — the file may have "
        "been refactored. Update the probe to match."
    )


def test_probe_make_row_per_row_stat_budget():
    """#474 forward-defensive: ``scanner/dedup.py::_make_row`` must not issue
    more than 3 ``os.path.get*`` calls.

    Two slots match today's calls; the third leaves headroom for one deliberate
    addition. The fourth trips the probe and forces the review conversation: is
    the new stat necessary, or can the data come from an already-issued call?
    """
    calls = _count_os_path_get_calls(_make_row_ast(SCANNER_DEDUP_PATH))
    assert len(calls) <= 3, (
        f"scanner/dedup.py::_make_row issues {len(calls)} os.path.get* calls: "
        f"{calls!r}. The function runs once per classified record in the scan "
        "loop; each extra stat compounds per-row I/O cost. See "
        "scanner/dedup.py:113-114 for the documented trade-off and #474 for "
        "the guardrail rationale. If the new stat is truly necessary, raise "
        "the threshold here in the same PR with a one-line reason."
    )


# ---------------------------------------------------------------------------
# T7 — every ScanProgressBus method is implemented by the live bus
# ---------------------------------------------------------------------------
# ScanProgressBus is a runtime_checkable Protocol; run_pipeline dispatches every
# event through it. `SseScanBus` (app/web/routes/scan.py) is the ONE production
# implementation. Add a method to the Protocol and forget the bus, and the
# pipeline still passes every test — the fakes in tests/test_scan_pipeline.py
# implement the whole Protocol by construction — then raises AttributeError in a
# real scan. pyproject.toml's `core/app_service/events.py` omit entry names this
# probe's subject as where the Protocol IS covered, so the omit depends on it.


def _protocol_method_names(protocol) -> set[str]:
    """Non-dunder member names declared by a typing.Protocol.

    ``typing.get_protocol_members`` arrived in 3.13; on 3.11/3.12 fall back to
    the semi-public ``__protocol_attrs__``.
    """
    import typing

    if hasattr(typing, "get_protocol_members"):
        return set(typing.get_protocol_members(protocol))
    return set(getattr(protocol, "__protocol_attrs__", set()))


def _public_methods(cls) -> set[str]:
    import inspect

    return {
        name
        for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def test_probe_sse_bus_implements_every_scanprogressbus_method():
    """T7 — ``SseScanBus`` must implement every method on ``ScanProgressBus``.

    The failure this catches is invisible to every other test in the suite: a
    new bus event reaches production through `run_pipeline`, and the only
    implementation that a real scan uses is the one no test constructs.
    """
    from app.web.routes.scan import SseScanBus
    from core.app_service.events import ScanProgressBus

    missing = _protocol_method_names(ScanProgressBus) - _public_methods(SseScanBus)
    assert not missing, (
        f"SseScanBus is missing methods declared on ScanProgressBus: "
        f"{sorted(missing)}. Adding a bus event to the Protocol without "
        "implementing it on SseScanBus means run_pipeline raises AttributeError "
        "during a real web scan, while every unit test stays green (the test "
        "buses implement the whole Protocol). Implement the method on "
        "SseScanBus (app/web/routes/scan.py) and fan it out as an SSE event."
    )


# ---------------------------------------------------------------------------
# VideoTile's -video testid — the contract web scenario s71 addresses
# ---------------------------------------------------------------------------


def _video_testid_line(src: str) -> str:
    """Return the ``data-testid`` line of the JSX ``<video>`` element in *src*.

    Anchoring on ``<video`` + newline distinguishes the real element from the
    literal ``<video>`` in the file's header prose. Returns ``""`` when the
    element carries no data-testid; raises when there is no element at all.
    """
    match = re.search(r"<video\s*\n(.*?)/>", src, re.DOTALL)
    assert match is not None, (
        "No self-closing <video ... /> JSX element found in VideoTile.tsx. The "
        "grid video tile must render a native <video> on click — check the "
        "component was not refactored away from an inline self-closing element."
    )
    video_tag = match.group(1)
    return next(
        (ln for ln in video_tag.splitlines() if "data-testid=" in ln), ""
    )


def test_probe_video_tile_video_element_carries_video_testid():
    """The ``<video>`` inside ``VideoTile.tsx`` must carry a ``{testId}-video``
    data-testid.

    ``qa/web/scenarios/s71_grid_video_tiles.py`` targets each tile's media
    element via ``page.get_by_test_id(f"{tile_testid}-video")`` — the outer tile
    div keeps the plain ``{testId}`` and the inner ``<video>`` appends
    ``-video``. Drop or rename that suffix and the Playwright scenario times out
    with a stale "element not found" that reads like a mount bug rather than
    testid drift.

    The invariant is inherently a JSX-text shape, so the probe inspects source
    text (there is no TS toolchain in the pytest run).
    """
    testid_line = _video_testid_line(
        VIDEO_TILE_TSX_PATH.read_text(encoding="utf-8")
    )
    assert "testId" in testid_line and "-video" in testid_line, (
        "VideoTile.tsx's <video> element does not carry a "
        '`data-testid={testId ? `${testId}-video` : undefined}` attribute. '
        "s71 addresses the tile media element as '{tile-testid}-video'; without "
        "this attribute the scenario cannot find the <video> and will time out. "
        f"Current data-testid line: {testid_line.strip()!r}"
    )


# ---------------------------------------------------------------------------
# Translation VALUE sweeps
# ---------------------------------------------------------------------------
# tests/test_i18n.py::test_zh_tw_has_every_key_present_in_english pins KEY
# parity, and tests/test_web_i18n.py pins the web namespace's keys. Neither
# looks at what the values say — which is exactly how #245 (a zh_TW value
# copy-pasted from en.yml) and #425 (legacy MOVE wording left in two values
# after two cleanup passes) both got through.


def _walk_yaml_leaf_strings(d_en, d_zh, prefix: str = "") -> list[tuple[str, str, str]]:
    """Yield ``(dotted_key, en_value, zh_value)`` for every leaf string both
    locales define. Keys missing from zh are skipped — key parity is
    ``test_zh_tw_has_every_key_present_in_english``'s job.
    """
    out: list[tuple[str, str, str]] = []
    if not isinstance(d_en, dict) or not isinstance(d_zh, dict):
        return out
    for k, v_en in d_en.items():
        v_zh = d_zh.get(k)
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v_en, dict):
            out.extend(_walk_yaml_leaf_strings(v_en, v_zh, key))
        elif isinstance(v_en, str) and isinstance(v_zh, str):
            out.append((key, v_en, v_zh))
    return out


def _yaml_values_with_path(node, path: tuple[str, ...] = ()):
    """Recursively yield ``(dotted_path, value)`` for every leaf string.

    Lists are unrolled with their index in the path; non-string leaves are
    skipped — the probe only inspects user-visible strings.
    """
    if isinstance(node, dict):
        for key, child in node.items():
            yield from _yaml_values_with_path(child, path + (str(key),))
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from _yaml_values_with_path(child, path + (str(i),))
    elif isinstance(node, str):
        yield (".".join(path), node)


# Keys intentionally identical in both locales — product / proper-noun strings
# that are not localized. Keep this tiny; a new entry needs its reason in the
# PR description.
_TRANSLATION_EXEMPT_KEYS: frozenset[str] = frozenset({
    "main_window.title",  # "Photo Manager" — product name, untranslated by design
    # `{field} {op} {value}` is placeholders plus a mathematical operator, with
    # nothing language-specific to translate. The other three pattern_summary
    # keys ARE translated (Simple / Regex / TopN have natural-language structure
    # that differs by locale); this is the format-template degenerate case.
    "action_dialog.pattern_summary_numeric_threshold",
    "web.action_dialog.pattern_summary_numeric_threshold",  # web analog (#741)
    # Language autonyms in the View → Language switcher: each language is shown
    # in its own name in every locale (standard i18n practice), so "English"
    # stays "English" in zh_TW. The zh entry "中文 (繁體)" passes via the CJK check.
    "web.menu.lang_en",
})

_CJK_RE = re.compile(r"[一-鿿]")

# Values are user-visible; the keys (e.g. `manifest_summary.move`) are internal
# identifiers and exempt. The plain verb "move" is allowed — "move to recycle
# bin" is accurate send2trash language; the banned shape is the past/passive
# form implying the removed MOVE action class.
_BANNED_MOVE_PATTERNS = (
    re.compile(r"\bmoved\b", re.IGNORECASE),
    re.compile(r"to be moved", re.IGNORECASE),
    re.compile(r"搬[移到走]"),  # zh_TW: 搬移 / 搬到 / 搬走
)

# Keys whose value legitimately contains the banned wording for a reason
# unrelated to the legacy MOVE action. Empty today.
_TRANSLATION_MOVE_ALLOWLIST: dict[str, str] = {}


def _find_english_passthroughs(en_doc, zh_doc) -> list[tuple[str, str]]:
    """Return ``(key, value)`` for zh_TW values that look like untranslated en.

    Heuristic: the zh value equals the en value AND contains no CJK AND has at
    least one Latin letter AND is at least 3 characters long.
    """
    untranslated: list[tuple[str, str]] = []
    for key, v_en, v_zh in _walk_yaml_leaf_strings(en_doc, zh_doc):
        if key in _TRANSLATION_EXEMPT_KEYS:
            continue
        if v_zh != v_en:
            continue  # different value — translated, even if poorly
        if _CJK_RE.search(v_zh):
            continue  # contains Chinese
        if not re.search(r"[A-Za-z]", v_zh):
            continue  # numbers / punctuation only
        if len(v_zh.strip()) < 3:
            continue  # too short to be a meaningful phrase
        untranslated.append((key, v_zh))
    return untranslated


def test_probe_zh_tw_translations_are_not_english_passthroughs():
    """Every zh_TW value must actually be translated, not a key whose value was
    copy-pasted from en.yml during a feature PR (#245).

    ``test_zh_tw_has_every_key_present_in_english`` checks structural key parity
    only; it cannot see a key whose value is English.
    """
    en = yaml.safe_load(EN_YAML.read_text(encoding="utf-8"))
    zh = yaml.safe_load(ZH_TW_YAML.read_text(encoding="utf-8"))
    untranslated = _find_english_passthroughs(en, zh)
    assert not untranslated, (
        f"zh_TW values appear to be untranslated English passthroughs "
        f"({len(untranslated)} keys):\n  "
        + "\n  ".join(f"{k!r}: {v!r}" for k, v in untranslated)
        + "\n\nEither translate the values in translations/zh_TW.yml, or if the "
        "string is legitimately identical (product name, autonym, pure format "
        "template), add the key to _TRANSLATION_EXEMPT_KEYS with a one-line "
        "reason."
    )


def _find_legacy_move_wording(docs: list[tuple[str, object]]) -> list[tuple[str, str, str]]:
    """Return ``(file_label, key, value)`` for every value matching a banned
    MOVE-wording pattern.
    """
    leaks: list[tuple[str, str, str]] = []
    for label, doc in docs:
        for dotted_key, value in _yaml_values_with_path(doc):
            if dotted_key in _TRANSLATION_MOVE_ALLOWLIST:
                continue
            if any(rx.search(value) for rx in _BANNED_MOVE_PATTERNS):
                leaks.append((label, dotted_key, value))
    return leaks


def test_probe_no_legacy_move_wording_in_user_facing_translations():
    """#425 forward-defensive: translation VALUES must not carry "moved" /
    "搬移" wording implying the legacy MOVE action — this app never physically
    moves a file.

    Catches the recurrence pattern that motivated #425: two earlier cleanups
    (#242, PR #310) each updated some values and missed others.
    """
    docs = [
        (EN_YAML.name, yaml.safe_load(EN_YAML.read_text(encoding="utf-8"))),
        (ZH_TW_YAML.name, yaml.safe_load(ZH_TW_YAML.read_text(encoding="utf-8"))),
    ]
    leaks = _find_legacy_move_wording(docs)
    assert not leaks, (
        "Translation values still contain legacy MOVE-action wording. "
        "photo-manager no longer moves files; the verb implies a feature that "
        "does not exist. Reword, or add the key to _TRANSLATION_MOVE_ALLOWLIST "
        "with a one-line reason. Leaks: "
        + "\n".join(f"  {f}: {k} = {v!r}" for f, k, v in leaks)
    )
