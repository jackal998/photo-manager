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
import os
import re
from pathlib import Path

import pytest
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
# unrelated to the legacy MOVE action.
_TRANSLATION_MOVE_ALLOWLIST: dict[str, str] = {
    # The Execute confirm's safety sentence (#917), specified verbatim by the
    # design REPLY Q6: "Files are moved to the Recycle Bin and can be
    # restored." It names the Recycle Bin explicitly, so it is the accurate
    # send2trash wording this probe's own comment permits — the passive form
    # is the design's, not a resurrection of the folder-MOVE action.
    "web.delete_confirm.recycle_note": "send2trash wording fixed by design REPLY Q6 (#917)",
}


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


# ---------------------------------------------------------------------------
# Client transport: default-deny for plain writes (#1000, A1)
# ---------------------------------------------------------------------------
# Every top-level declaration in frontend/src/api/client.ts that reaches the
# plain postJson / patchJson transport, directly or through a local alias, must
# be on _PLAIN_TRANSPORT_ALLOWLIST. A write that answers 409 locked_paths has to
# go through the …OrConflict transport, or the lock-confirm dialog never opens
# and the user sees a raw "HTTP 409" string (#1000). The allow-list holds only
# the seven writers that touch no decision, outcome or lock row, each pinned to
# the one path it may use. Boundaries come from the token stream (brace and
# paren depth on comment- and string-masked source), not from line ranges, so
# one declaration cannot be absorbed into another.

CLIENT_TS_PATH = REPO / "frontend" / "src" / "api" / "client.ts"

_PLAIN_TRANSPORT_ALLOWLIST: dict[str, tuple[str, str]] = {
    "startScan": ("/api/scan", "starts a scan task; writes no decision, outcome or lock"),
    "cancelScan": ("/api/scan/", "cancels a scan task; writes no decision, outcome or lock"),
    "patchLocks": ("/api/lock", "the lock toggle is ungated (REC-EXC, execute-action.md:143)"),
    "patchSettings": ("/api/settings", "writes settings.json; no row is touched"),
    "postPruneCandidates": (
        "/api/prune/candidates",
        "read-only classifier; the POST only carries the manifest path",
    ),
    "postSave": (
        "/api/save",
        "checkpoints the WAL and copies the file; writes no decision, outcome or lock",
    ),
    "postReveal": ("/api/reveal", "opens the file manager; writes nothing"),
}
_TRANSPORT_PRIMITIVES = frozenset({"postJson", "patchJson"})
_TOP_KEYWORD_RE = re.compile(
    r"\b(export|function|const|let|var|class|type|interface|import)\b"
)
_DECL_NAME_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?(?:function\s*\*?\s*|const\s+|let\s+|var\s+"
    r"|class\s+|type\s+|interface\s+)(\w+)"
)
_ALIAS_RHS_RE = re.compile(r"=\s*(\w+)\s*;?\s*$")
_PATH_LITERAL_RE = re.compile(r"""["'`](/api/[^"'`$]*)""")


def _mask_source(src: str) -> tuple[str, str]:
    """Return ``(code, masked)``.

    Both blank comments. ``code`` keeps string contents (path literals are read
    from it); ``masked`` also blanks string contents, so braces or keywords
    inside a string cannot move the depth or the boundaries.
    """
    code = list(src)
    masked = list(src)
    n = len(src)
    i = 0
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = src.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                code[k] = masked[k] = " "
            i = end
        elif ch == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                if src[k] != "\n":
                    code[k] = masked[k] = " "
            i = end
        elif ch in "\"'`":
            end = i + 1
            while end < n and src[end] != ch:
                end += 2 if src[end] == "\\" else 1
            for k in range(i + 1, min(end, n)):
                if src[k] != "\n":
                    masked[k] = " "
            i = end + 1
        else:
            i += 1
    return "".join(code), "".join(masked)


def _top_level_regions(masked: str) -> list[tuple[int, int]]:
    """Split *masked* into top-level declaration spans, by syntax.

    A span starts at a keyword sitting at brace/paren depth 0. An ``export``
    keyword starts its declaration, so the ``function`` / ``const`` after it
    does not start a second one.
    """
    depth_at: list[int] = []
    depth = 0
    for ch in masked:
        depth_at.append(depth)
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
    starts: list[int] = []
    for match in _TOP_KEYWORD_RE.finditer(masked):
        pos = match.start()
        if depth_at[pos] != 0:
            continue
        if match.group(1) != "export" and re.search(
            r"\bexport\s+(?:async\s+)?$", masked[max(0, pos - 24):pos]
        ):
            continue
        starts.append(pos)
    bounds = starts + [len(masked)]
    return [(bounds[i], bounds[i + 1]) for i in range(len(starts))]


def _client_declarations(src: str) -> dict[str, tuple[str, str]]:
    """Map each top-level declaration name to ``(masked, code)`` for its span."""
    code, masked = _mask_source(src)
    declarations: dict[str, tuple[str, str]] = {}
    for start, end in _top_level_regions(masked):
        region_masked = masked[start:end]
        match = _DECL_NAME_RE.match(region_masked)
        if match is None:
            continue  # e.g. `export { a, b };`: no body to check
        declarations[match.group(1)] = (region_masked, code[start:end])
    return declarations


def _transport_names(declarations: dict[str, tuple[str, str]]) -> set[str]:
    """The primitives plus every local alias of them (``const send = postJson``)."""
    names = set(_TRANSPORT_PRIMITIVES)
    changed = True
    while changed:
        changed = False
        for name, (masked, _code) in declarations.items():
            rhs = _ALIAS_RHS_RE.search(masked.strip())
            if name not in names and rhs and rhs.group(1) in names:
                names.add(name)
                changed = True
    return names


def _find_plain_transport_findings(src: str) -> list[str]:
    """Return one finding per top-level declaration that is not allowed to reach
    the plain transport, or whose allow-listed path has drifted.
    """
    declarations = _client_declarations(src)
    transport = _transport_names(declarations)
    findings: list[str] = []
    for name, (masked, code) in declarations.items():
        if name in _TRANSPORT_PRIMITIVES:
            continue  # the transport itself, not a caller of it
        reached = sorted(t for t in transport if re.search(rf"\b{t}\b", masked))
        if not reached:
            continue
        paths = _PATH_LITERAL_RE.findall(code)
        if name in _PLAIN_TRANSPORT_ALLOWLIST:
            expected = _PLAIN_TRANSPORT_ALLOWLIST[name][0]
            # Every path literal in the declaration, not only the first one.
            if not paths or any(p != expected for p in paths):
                findings.append(
                    f"{name} is allow-listed for {expected!r} but reaches the plain "
                    f"transport on {paths!r}"
                )
            continue
        findings.append(
            f"{name} reaches the plain postJson/patchJson transport "
            f"(via {', '.join(reached)}) on {paths!r}; use the …OrConflict transport "
            "or add it to _PLAIN_TRANSPORT_ALLOWLIST with a reason"
        )
    return findings


def test_probe_client_plain_transport_is_default_deny():
    """No client declaration reaches postJson / patchJson unless allow-listed.

    A new writer, a new endpoint, a local alias, a second call inside an
    allow-listed declaration, a class method, or a declaration absorbed into an
    allow-listed one all fail here (#1000).

    This is a TRIPWIRE, not a proof. The server's per-path confirmation (Track
    A6/A8) is the backstop. Known limits, one per line:
    - an aliased import (`import { postJson as pj }`) is not followed;
    - a regex literal that contains a quote character can confuse the masker;
    - a raw `fetch` with method POST, outside postJson/patchJson, is not seen.
    """
    src = CLIENT_TS_PATH.read_text(encoding="utf-8")
    findings = _find_plain_transport_findings(src)
    assert not findings, (
        "A client declaration reaches the plain transport. A 409 locked_paths on "
        "a row write would show as a raw 'HTTP 409' error instead of the "
        "lock-confirm dialog. Findings: " + "; ".join(findings)
    )


def test_probe_client_allow_list_names_live_declarations():
    """An allow-list entry must name a declaration that still exists, so a stale
    exemption fails loudly instead of silently covering nothing.
    """
    names = set(_client_declarations(CLIENT_TS_PATH.read_text(encoding="utf-8")))
    stale = sorted(set(_PLAIN_TRANSPORT_ALLOWLIST) - names)
    assert not stale, f"_PLAIN_TRANSPORT_ALLOWLIST names missing declarations: {stale}"


def test_probe_transport_catches_new_endpoint_on_plain_transport():
    """Must-catch: a new function on a new endpoint, on the plain transport."""
    fixture = (
        "export async function undoKeepBest(req: X): Promise<Y> {\n"
        '  return patchJson<Y>("/api/action/undo-keep-best", req);\n'
        "}\n"
    )
    findings = _find_plain_transport_findings(fixture)
    assert len(findings) == 1 and findings[0].startswith("undoKeepBest ")


def test_probe_transport_catches_local_alias_of_plain_transport():
    """Must-catch: a local alias of postJson, reached by an export."""
    fixture = (
        "const send = postJson;\n"
        "export async function sneak(req: X): Promise<Y> {\n"
        '  return send<Y>("/api/execute", req);\n'
        "}\n"
    )
    names = [f.split(" ")[0] for f in _find_plain_transport_findings(fixture)]
    assert names == ["send", "sneak"]


def test_probe_transport_catches_declaration_after_allow_listed_one():
    """Must-catch: an arrow export placed after an allow-listed function is its
    own declaration, so it is not absorbed into the allow-listed region.
    """
    fixture = (
        "export async function patchLocks(req: X): Promise<Y> {\n"
        '  return patchJson<Y>("/api/lock", req);\n'
        "}\n"
        "export const sneak = (req: X): Promise<Y> => postJson<Y>(\"/api/execute\", req);\n"
    )
    findings = _find_plain_transport_findings(fixture)
    assert len(findings) == 1 and findings[0].startswith("sneak ")


def test_probe_transport_catches_allow_listed_path_drift():
    """Must-catch: an allow-listed writer that moves to another path is reported."""
    fixture = (
        "export async function patchLocks(req: X): Promise<Y> {\n"
        '  return patchJson<Y>("/api/execute", req);\n'
        "}\n"
    )
    findings = _find_plain_transport_findings(fixture)
    assert len(findings) == 1 and "allow-listed for '/api/lock'" in findings[0]


def test_probe_transport_passes_conflict_transport_and_allow_listed_fixture():
    """Must-pass: the OrConflict transport, a comment or string mentioning
    postJson, and an allow-listed writer on its declared path are all accepted.
    """
    fixture = (
        "// postJson is only mentioned in this comment\n"
        "export async function goodWrite(req: X): Promise<Y> {\n"
        '  const note = "postJson(";\n'
        '  return postJsonOrConflict<Y>("/api/prune", req);\n'
        "}\n"
        "export async function patchLocks(req: X): Promise<Y> {\n"
        '  return patchJson<Y>("/api/lock", req);\n'
        "}\n"
    )
    assert _find_plain_transport_findings(fixture) == []


def test_probe_client_plain_transport_must_pass_on_current_tree():
    """Must-pass on today's tree: the probe is clean on the real client.ts."""
    src = CLIENT_TS_PATH.read_text(encoding="utf-8")
    assert _find_plain_transport_findings(src) == []


def test_probe_transport_catches_second_call_inside_allow_listed_declaration():
    """Must-catch: an allow-listed writer with a second plain call to another path
    is reported, because every path literal is compared, not only the first.
    """
    fixture = (
        "export async function postSave(req: X): Promise<Y> {\n"
        '  await postJson<Y>("/api/remove", req);\n'
        '  return postJson<Y>("/api/save", req);\n'
        "}\n"
    )
    findings = _find_plain_transport_findings(fixture)
    assert len(findings) == 1 and findings[0].startswith("postSave is allow-listed")


def test_probe_transport_catches_class_method_on_plain_transport():
    """Must-catch: a class method on the plain transport is its own declaration
    (``class`` is a split keyword), so it cannot hide inside a neighbour.
    """
    fixture = (
        "export class Api {\n"
        "  static undo(req: X): Promise<Y> {\n"
        '    return postJson<Y>("/api/execute", req);\n'
        "  }\n"
        "}\n"
    )
    findings = _find_plain_transport_findings(fixture)
    assert len(findings) == 1 and findings[0].startswith("Api reaches")
# ---------------------------------------------------------------------------
# A6 — named confirmation: the decision PATCH never sends a boolean force
# ---------------------------------------------------------------------------
# design 3.2 (rev 3): a TRIPWIRE, not a proof. The decision PATCH names the
# locked rows the user confirmed (`confirmed_locked`); the server refuses any
# locked row not named, and that is the backstop. Two checks:
#   1. the wire name (and its camelCase option) lives only in allow-listed
#      frontend files: default-deny;
#   2. the decision request builder names `confirmed_locked` and never sends a
#      boolean force.
# A8 extends the same tripwire to every verb: no `force_locked` / `forceLocked`
# appears in frontend code outside the lock module and api/client.ts.

FRONTEND_SRC = REPO / "frontend" / "src"
_CONFIRMED_ALLOWLIST: dict[str, str] = {
    "api/client.ts": "the decision PATCH builder, the one place the wire key is set",
    "store/types.ts": "the setDecisions contract (opts.confirmedLocked)",
    "store/useAppStore.ts": "setDecisions and the Undo of Keep best name their confirmed rows",
    "components/dialogs/LockConfirmDialog.tsx": "Unlock & Apply names the rows the dialog showed",
    "components/action/ActionDialog.tsx": "bulk decide names the rows Unlock & Apply showed (A8)",
    "components/execute/ExecuteDialog.tsx": "types the execute re-send's confirmed rows (A8)",
    "api/types.ts": "the wire request types declare the confirmed_locked field (A8)",
}
_CONFIRMED_NAME_RE = re.compile(r"\bconfirmed_locked\b|\bconfirmedLocked\b")
_BOOLEAN_FORCE_RE = re.compile(r"\bforce_locked\b|\bforceLocked\b")
_DECISION_BUILDER = "patchDecisions"


def _frontend_source_files(root: Path = FRONTEND_SRC) -> dict[str, str]:
    """Non-test .ts/.tsx sources under frontend/src, keyed by path relative to src."""
    files: dict[str, str] = {}
    for path in sorted([*root.rglob("*.ts"), *root.rglob("*.tsx")]):
        if ".test." in path.name:
            continue
        files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return files


def _find_confirmed_name_findings(files: dict[str, str]) -> list[str]:
    """One finding per file outside the allow-list that names the confirmation.

    Reads the code only: comments are blank, strings are kept, so a quoted wire
    key counts and a mention in a comment does not.
    """
    findings: list[str] = []
    for rel, src in sorted(files.items()):
        if rel in _CONFIRMED_ALLOWLIST:
            continue
        code, _ = _mask_source(src)
        if _CONFIRMED_NAME_RE.search(code):
            findings.append(f"{rel} names the confirmation outside the allow-list")
    return findings


def _find_decision_builder_findings(src: str) -> list[str]:
    """Findings for the decision builder: it names confirmed_locked, never a force."""
    declarations = _client_declarations(src)
    if _DECISION_BUILDER not in declarations:
        return [f"{_DECISION_BUILDER} is not declared, so the decision builder is unchecked"]
    _masked, code = declarations[_DECISION_BUILDER]
    findings: list[str] = []
    if _BOOLEAN_FORCE_RE.search(code):
        findings.append(f"{_DECISION_BUILDER} sends a boolean force")
    if not re.search(r"\bconfirmed_locked\b", code):
        findings.append(f"{_DECISION_BUILDER} does not send confirmed_locked")
    return findings


_FORCE_ALLOWLIST: dict[str, str] = {
    "api/client.ts": "the HTTP client, where wire keys are built",
    "components/dialogs/LockConfirmDialog.tsx": "the lock-confirm module (Unlock & Apply)",
}


def _find_boolean_force_findings(files: dict[str, str]) -> list[str]:
    """A8: one finding per file outside the allow-list that names a boolean force.

    Code only: comments are blanked by _mask_source, so a comment that records
    the removal does not count, and a quoted or split wire key does.
    """
    findings: list[str] = []
    for rel, src in sorted(files.items()):
        if rel in _FORCE_ALLOWLIST:
            continue
        code, _ = _mask_source(src)
        if _BOOLEAN_FORCE_RE.search(code):
            findings.append(f"{rel} names a boolean force outside the lock module")
    return findings


def test_probe_no_boolean_force_on_current_tree():
    """A8 must-pass on today's tree: no boolean force in frontend code."""
    assert _find_boolean_force_findings(_frontend_source_files()) == []


def test_probe_force_allow_list_names_live_files():
    """A stale force allow-list entry fails loudly instead of covering nothing."""
    stale = sorted(set(_FORCE_ALLOWLIST) - set(_frontend_source_files()))
    assert not stale, f"_FORCE_ALLOWLIST names missing files: {stale}"


_MUST_FAIL_FORCE_FIXTURES: dict[str, tuple[str, str]] = {
    "boolean_force_key_outside": (
        "components/action/ActionDialog.tsx",
        "const body = { force_locked: true };\n",
    ),
    "camel_force_read_outside": (
        "store/useAppStore.ts",
        "const f = opts.forceLocked ?? false;\n",
    ),
    "quoted_spaced_force_key_outside": (
        "store/useAppStore.ts",
        'const body = { "force_locked"  :  true };\n',
    ),
    "camel_force_split_across_lines_outside": (
        "components/action/ActionDialog.tsx",
        "const body = {\n  forceLocked:\n    true,\n};\n",
    ),
}

_MUST_PASS_FORCE_FIXTURES: dict[str, tuple[str, str]] = {
    "force_only_in_comment_outside": (
        "components/action/ActionDialog.tsx",
        "// force_locked is gone: named confirmation replaced it\nconst x = 1;\n",
    ),
    "force_in_allow_listed_client": (
        "api/client.ts",
        "const body = { force_locked: true };\n",
    ),
}


@pytest.mark.parametrize("name", sorted(_MUST_FAIL_FORCE_FIXTURES))
def test_probe_boolean_force_fixture_must_fail(name):
    rel, src = _MUST_FAIL_FORCE_FIXTURES[name]
    assert _find_boolean_force_findings({rel: src}), (
        f"probe missed the must-fail force fixture {name!r}"
    )


@pytest.mark.parametrize("name", sorted(_MUST_PASS_FORCE_FIXTURES))
def test_probe_boolean_force_fixture_must_pass(name):
    rel, src = _MUST_PASS_FORCE_FIXTURES[name]
    assert not _find_boolean_force_findings({rel: src}), (
        f"probe flagged the must-pass force fixture {name!r}"
    )


def test_probe_confirmed_name_stays_on_allow_listed_files_on_current_tree():
    """Must-pass on today's tree: no file outside the allow-list names the confirmation."""
    assert _find_confirmed_name_findings(_frontend_source_files()) == []


def test_probe_confirmed_allow_list_names_live_files():
    """A stale allow-list entry fails loudly instead of covering nothing."""
    stale = sorted(set(_CONFIRMED_ALLOWLIST) - set(_frontend_source_files()))
    assert not stale, f"_CONFIRMED_ALLOWLIST names missing files: {stale}"


def test_probe_decision_builder_must_pass_on_current_tree():
    """Must-pass on today's tree: patchDecisions names confirmed_locked, no force."""
    src = CLIENT_TS_PATH.read_text(encoding="utf-8")
    assert _find_decision_builder_findings(src) == []


_MUST_FAIL_NAME_FIXTURES: dict[str, tuple[str, str]] = {
    "name_outside_allow_list": (
        "components/ContextMenu.tsx",
        "const body = { confirmed_locked: paths };\n",
    ),
    "quoted_spaced_wire_key_outside": (
        "components/ContextMenu.tsx",
        'const body = { "confirmed_locked"  :  paths };\n',
    ),
    "camel_name_split_across_lines_outside": (
        "components/ContextMenu.tsx",
        "const body = {\n  confirmedLocked:\n    paths,\n};\n",
    ),
}

_MUST_PASS_NAME_FIXTURES: dict[str, tuple[str, str]] = {
    "name_in_allow_listed_client": (
        "api/client.ts",
        "const body = {\n  confirmed_locked:\n    paths,\n};\n",
    ),
    "name_only_in_comment_outside": (
        "components/action/ActionDialog.tsx",
        "// confirmed_locked is sent by the decision PATCH only\nconst x = 1;\n",
    ),
    "force_locked_on_other_verb_outside": (
        "components/action/ActionDialog.tsx",
        "const body = { force_locked: true };\n",
    ),
}


def _builder(*entries: str) -> str:
    """A patchDecisions declaration whose request object holds *entries*."""
    body = "\n".join(f"    {entry}" for entry in entries)
    return (
        "export async function patchDecisions(\n"
        "  manifestPath: string,\n"
        "  decisions: DecisionEntry[],\n"
        "  opts?: { confirmedLocked?: string[] }\n"
        "): Promise<PatchDecisionsResult> {\n"
        '  return patchJsonOrConflict<PatchDecisionsResult>("/api/decision", {\n'
        f"{body}\n"
        "  });\n"
        "}\n"
    )


_BASE = ("manifest_path: manifestPath,", "decisions,")

_MUST_FAIL_BUILDER_FIXTURES: dict[str, str] = {
    "boolean_force_key": _builder(
        *_BASE, "confirmed_locked: opts?.confirmedLocked ?? [],",
        "force_locked: opts?.forceLocked ?? false,",
    ),
    "quoted_spaced_force_key": _builder(
        *_BASE, "confirmed_locked: [],", '"force_locked"   :   false,',
    ),
    "split_force_key": _builder(
        *_BASE, "confirmed_locked: [],", "force_locked", ": false,",
    ),
    "single_quoted_force_key": _builder(
        *_BASE, "confirmed_locked: [],", "'force_locked': false,",
    ),
    "spread_force_key": _builder(
        *_BASE, "confirmed_locked: [],",
        "...(opts?.forceLocked ? { force_locked: true } : {}),",
    ),
    "confirmed_key_missing": _builder(*_BASE),
}

_MUST_PASS_BUILDER_FIXTURES: dict[str, str] = {
    "split_confirmed_key": _builder(
        *_BASE, "confirmed_locked", ":", "opts?.confirmedLocked ?? [],",
    ),
    "quoted_confirmed_key_extra_spaces": _builder(
        *_BASE, '"confirmed_locked"   :   opts?.confirmedLocked ?? [],',
    ),
    "force_locked_only_in_comment": _builder(
        *_BASE, "// force_locked is gone: the decision PATCH never sends it",
        "confirmed_locked: opts?.confirmedLocked ?? [],",
    ),
}


@pytest.mark.parametrize("name", sorted(_MUST_FAIL_NAME_FIXTURES))
def test_probe_confirmed_name_fixture_must_fail(name):
    rel, src = _MUST_FAIL_NAME_FIXTURES[name]
    assert _find_confirmed_name_findings({rel: src}), (
        f"probe missed the must-fail fixture {name!r}"
    )


@pytest.mark.parametrize("name", sorted(_MUST_PASS_NAME_FIXTURES))
def test_probe_confirmed_name_fixture_must_pass(name):
    rel, src = _MUST_PASS_NAME_FIXTURES[name]
    assert not _find_confirmed_name_findings({rel: src}), (
        f"probe flagged the must-pass fixture {name!r}"
    )


@pytest.mark.parametrize("name", sorted(_MUST_FAIL_BUILDER_FIXTURES))
def test_probe_decision_builder_fixture_must_fail(name):
    findings = _find_decision_builder_findings(_MUST_FAIL_BUILDER_FIXTURES[name])
    assert findings, f"probe missed the must-fail builder fixture {name!r}"


@pytest.mark.parametrize("name", sorted(_MUST_PASS_BUILDER_FIXTURES))
def test_probe_decision_builder_fixture_must_pass(name):
    findings = _find_decision_builder_findings(_MUST_PASS_BUILDER_FIXTURES[name])
    assert findings == [], f"probe flagged the must-pass builder fixture {name!r}: {findings}"


# ---------------------------------------------------------------------------
# A5 — write-path probe: no unguarded UPDATE of user_decision / outcome / is_locked
# ---------------------------------------------------------------------------
# design 2.3 (rev 3). Scope: UPDATE only. INSERT OR REPLACE and REPLACE INTO are
# out of scope; the only INSERT ... SELECT is the schema rebuild in
# migrate_manifest_schema, which copies rows and sets no decision.
#
# The probe reads the AST, never raw text. Seen: a string literal, implicit
# concatenation, a `+` chain of literals, an f-string and a `%` template, each
# as its literal parts; a non-literal operand is shown as one space. So
# `"... SET outcome = " + x` is caught by its literal column name. Not seen:
# a protected column name chosen at runtime (`"SET " + col + " = ?"`), a column
# name passed into `.format(...)`, `str.join` or `getattr`, and any file the scan
# does not read.

_PROBE_EXCLUDED_DIRS = frozenset(
    {"tests", ".venv", "venv", "node_modules", ".claude", ".git", "__pycache__"}
)

_GUARDED_CONSTANT_NAMES = frozenset({"_UPDATE_DECISION_SQL", "_UPDATE_LOCK_SQL"})

# (module, qualified name) of every constant whose SQL may set a protected
# column. The three ManifestTxn statements are guarded in their own WHERE; the
# reconcile dismiss is guarded in its own statement (R-LOCK-6); the lock verb is
# the lock toggle itself; the scanner's fresh-DB write is the one _UPDATE_DECISION_SQL
# user with nothing to guard.
_ALLOWED_SQL_CONSTANTS = frozenset({
    ("infrastructure.manifest_repository", "_WRITE_DECISION_SQL"),   # ManifestTxn.write_decisions
    ("infrastructure.manifest_repository", "_TRANSITION_SQL"),       # ManifestTxn.transition
    ("infrastructure.manifest_repository", "_RECORD_DELETED_SQL"),   # ManifestTxn.record_deleted
    ("infrastructure.manifest_repository", "_RECONCILE_DISMISS_SQL"),  # R-LOCK-6 system write
    ("infrastructure.manifest_repository", "_UPDATE_LOCK_SQL"),      # lock verb
    ("scanner.manifest", "_UPDATE_DECISION_SQL"),                    # fresh temp DB (moved by A5)
})

# constant name -> (module, qualified function) allowed to reference it by name.
_ALLOWED_CONSTANT_USERS: dict[str, frozenset[tuple[str, str]]] = {
    "_UPDATE_DECISION_SQL": frozenset({("scanner.manifest", "write_manifest")}),
    "_UPDATE_LOCK_SQL": frozenset({
        ("infrastructure.manifest_repository", "ManifestRepository.batch_update_lock_state"),
        ("scanner.manifest", "write_manifest"),
    }),
}

# The table may be written as `migration_manifest`, `"migration_manifest"` or
# `[migration_manifest]`, with a `main.` prefix and an `OR <action>` conflict
# clause. A protected column may be quoted in SET.
_UPDATE_HEAD = re.compile(
    r"\bupdate\s+(?:or\s+(?:ignore|replace|abort|fail|rollback)\s+)?"
    r"(?:main\.)?[\"\[`]?migration_manifest[\"\]`]?"
    r"(?:\s+as)?(?:\s+(?!set\b)\w+)?\s+set\b"
)
_PROTECTED_ASSIGN = re.compile(
    r"(?<![\w.])(?:\w+\.)?[\"\[`]?(user_decision|outcome|is_locked)[\"\]`]?\s*=(?!=)"
)


def _protected_update_columns(sql: str) -> set[str]:
    """Protected columns assigned by any UPDATE migration_manifest in ``sql``.

    Whitespace and case are normalised first. The SET clause runs up to the
    first WHERE, so a WHERE that mentions ``outcome`` does not count.
    """
    norm = re.sub(r"\s+", " ", sql).lower()
    hits: set[str] = set()
    for head in _UPDATE_HEAD.finditer(norm):
        rest = norm[head.end():]
        where = rest.find(" where ")
        clause = rest if where < 0 else rest[:where]
        hits.update(_PROTECTED_ASSIGN.findall(clause))
    return hits


def _joined_text(node: ast.JoinedStr) -> str:
    """An f-string's literal parts, each placeholder shown as one space."""
    return "".join(
        part.value if isinstance(part, ast.Constant) else " " for part in node.values
    )


def _add_leaves(node: ast.expr) -> list[ast.expr]:
    """The operands of a left-or-right nested ``+`` chain, in source order."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_leaves(node.left) + _add_leaves(node.right)
    return [node]


class _ScopedStrings(ast.NodeVisitor):
    """Collect (scope, SQL-text) and (scope, guarded-name) pairs from one module.

    A module-level ``NAME = ...`` gives its own scope the name, so a constant's
    SQL is keyed by the constant. Implicit concatenation is one ``Constant`` in
    the AST; a ``+`` chain and an f-string are joined by ``visit_BinOp`` and
    ``visit_JoinedStr``. A ``%`` template is its literal string.
    """

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.sql: list[tuple[str, str]] = []
        self.refs: list[tuple[str, str]] = []

    def _qual(self) -> str:
        return ".".join(self.scope) or "<module>"

    def _in_scope(self, name: str, node: ast.AST) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._in_scope(node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._in_scope(node.name, node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if (not self.scope and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            self._in_scope(node.targets[0].id, node)
        else:
            self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.sql.append((self._qual(), node.value))

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self.sql.append((self._qual(), _joined_text(node)))
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                self.visit(part.value)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # ast does not fold `+`, so each literal of `"A " + "B"` would be matched
        # alone and a split UPDATE would slip through. Join the whole chain once.
        if not isinstance(node.op, ast.Add):
            self.generic_visit(node)
            return
        parts: list[str] = []
        for leaf in _add_leaves(node):
            if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                parts.append(leaf.value)
            elif isinstance(leaf, ast.JoinedStr):
                parts.append(_joined_text(leaf))
                self.visit_JoinedStr_refs(leaf)
            else:
                parts.append(" ")
                self.visit(leaf)
        self.sql.append((self._qual(), "".join(parts)))

    def visit_JoinedStr_refs(self, node: ast.JoinedStr) -> None:
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                self.visit(part.value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in _GUARDED_CONSTANT_NAMES:
            self.refs.append((self._qual(), node.id))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _GUARDED_CONSTANT_NAMES:
            self.refs.append((self._qual(), node.attr))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in _GUARDED_CONSTANT_NAMES:
                self.refs.append((self._qual(), alias.name))


def _definitions(tree: ast.Module, module: str) -> set[tuple[str, str]]:
    """Module-level constants and (methods of) top-level functions and classes."""
    defined: set[tuple[str, str]] = set()

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add((module, prefix + node.name))
            elif isinstance(node, ast.ClassDef):
                defined.add((module, prefix + node.name))
                walk(node.body, prefix + node.name + ".")
            elif not prefix and isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add((module, target.id))

    walk(tree.body, "")
    return defined


def _probe_violations(modules: dict[str, str]) -> tuple[list[str], int]:
    """Unallowed protected UPDATE sites and unallowed guarded-name references.

    Returns (violations, UPDATE sites seen). Fixtures call this directly.
    """
    violations: list[str] = []
    update_sites = 0
    for module, src in sorted(modules.items()):
        collector = _ScopedStrings()
        collector.visit(ast.parse(src))
        for scope, text in collector.sql:
            columns = _protected_update_columns(text)
            if not columns:
                continue
            update_sites += 1
            if (module, scope) not in _ALLOWED_SQL_CONSTANTS:
                violations.append(
                    f"{module}:{scope} sets {sorted(columns)} in an UPDATE "
                    "migration_manifest that is not on the allow-list"
                )
        for scope, name in collector.refs:
            allowed = _ALLOWED_CONSTANT_USERS.get(name, frozenset())
            if (module, scope) not in allowed:
                violations.append(
                    f"{module}:{scope} references {name}, which is not allow-listed"
                )
    return violations, update_sites


def _probe_allow_list_gaps(modules: dict[str, str]) -> list[str]:
    """Fail closed: every allow-list entry must be found by name in the scan,
    and every allowed SQL constant must match the probe.
    """
    defined: set[tuple[str, str]] = set()
    sql_seen: set[tuple[str, str]] = set()
    refs_seen: set[tuple[str, str, str]] = set()
    for module, src in modules.items():
        tree = ast.parse(src)
        defined |= _definitions(tree, module)
        collector = _ScopedStrings()
        collector.visit(tree)
        for scope, text in collector.sql:
            if _protected_update_columns(text):
                sql_seen.add((module, scope))
        for scope, name in collector.refs:
            refs_seen.add((name, module, scope))
    gaps: list[str] = []
    for module, name in sorted(_ALLOWED_SQL_CONSTANTS):
        if (module, name) not in defined:
            gaps.append(f"allow-list entry {module}.{name} is not defined")
        elif (module, name) not in sql_seen:
            gaps.append(f"allow-list entry {module}.{name} matches no UPDATE the probe can see")
    for name, users in sorted(_ALLOWED_CONSTANT_USERS.items()):
        for module, scope in sorted(users):
            if (module, scope) not in defined:
                gaps.append(f"user entry {module}.{scope} is not defined")
            elif (name, module, scope) not in refs_seen:
                gaps.append(f"user entry {module}.{scope} no longer references {name}")
    return gaps


def _production_modules() -> dict[str, str]:
    """Every production *.py under the repo: no tests/, venv, node_modules or worktrees."""
    modules: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in _PROBE_EXCLUDED_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            rel = path.relative_to(REPO).with_suffix("")
            modules[".".join(rel.parts)] = path.read_text(encoding="utf-8")
    return modules


def test_probe_write_paths_are_guarded_or_allow_listed():
    """UPDATE migration_manifest of user_decision / outcome / is_locked, and any
    reference to _UPDATE_DECISION_SQL or _UPDATE_LOCK_SQL, is allowed only at an
    allow-listed site. UPDATE only: INSERT OR REPLACE / REPLACE INTO are out of
    scope (design 2.3, rev 3).
    """
    modules = _production_modules()
    violations, update_sites = _probe_violations(modules)
    gaps = _probe_allow_list_gaps(modules)
    assert update_sites > 0, "the probe sees zero UPDATE sites; it cannot guard anything"
    assert not gaps, "allow-list drifted (fail closed): " + "; ".join(gaps)
    assert not violations, (
        "unguarded write path to a decision / outcome / lock column. Route it "
        "through ManifestTxn (write_decisions / transition / record_deleted) or "
        "allow-list it with a reason. " + "; ".join(violations)
    )


# Must-FAIL fixtures: each is a whole module source, scanned as "fixture.bad".
_MUST_FAIL_WRITE_FIXTURES = {
    "plain_new_update": (
        "def f(conn):\n"
        "    conn.execute('UPDATE migration_manifest SET outcome = ? WHERE source_path = ?')\n"
    ),
    "lowercase_update": (
        "def f(conn):\n"
        "    conn.execute('update migration_manifest set user_decision = ?')\n"
    ),
    "split_across_lines": (
        "SQL = '''\n"
        "UPDATE migration_manifest\n"
        "   SET is_locked = ?\n"
        " WHERE source_path = ?\n"
        "'''\n"
    ),
    "table_alias": (
        "SQL = 'UPDATE migration_manifest AS m SET m.outcome = ? WHERE m.source_path = ?'\n"
    ),
    "table_alias_without_as": (
        "SQL = 'UPDATE migration_manifest m SET is_locked = 0 WHERE m.source_path = ?'\n"
    ),
    "extra_whitespace": (
        "SQL = 'UPDATE   migration_manifest\\t SET   outcome   =  ?'\n"
    ),
    "implicit_concat_splits_table": (
        "SQL = ('UPDATE migration_' 'manifest SET is_locked = ? '\n"
        "       'WHERE source_path = ?')\n"
    ),
    "fstring_literal_parts": (
        "def f(x):\n"
        "    return f'UPDATE migration_manifest SET outcome = {x} WHERE source_path = ?'\n"
    ),
    "percent_template": (
        "def f(x):\n"
        "    return 'UPDATE migration_manifest SET user_decision = %s' % x\n"
    ),
    "new_reference_decision_sql": (
        "from infrastructure.manifest_repository import _UPDATE_DECISION_SQL\n"
        "def f(conn):\n"
        "    conn.execute(_UPDATE_DECISION_SQL, ('delete', '/a.jpg'))\n"
    ),
    "new_reference_lock_sql_attribute": (
        "import infrastructure.manifest_repository as repo\n"
        "def f(conn):\n"
        "    conn.execute(repo._UPDATE_LOCK_SQL, (1, '/a.jpg'))\n"
    ),
    "plus_concat_split_across_lines": (
        'S = ("UPDATE migration_manifest SET executed = 1, "\n'
        '     + "is_locked = 0 WHERE source_path = ?")\n'
    ),
    "plus_concat_table_then_set": (
        'S = "UPDATE migration_manifest " + "SET outcome = ? WHERE source_path = ?"\n'
    ),
    "or_ignore_conflict_clause": (
        "SQL = 'UPDATE OR IGNORE migration_manifest SET outcome = ?'\n"
    ),
    "quoted_table_name": (
        "SQL = 'UPDATE \"migration_manifest\" SET outcome = ?'\n"
    ),
    "bracketed_table_name": (
        "SQL = 'UPDATE [migration_manifest] SET outcome = ?'\n"
    ),
    "main_schema_prefix": (
        "SQL = 'UPDATE main.migration_manifest SET outcome = ?'\n"
    ),
    "quoted_column_in_set": (
        "SQL = 'UPDATE migration_manifest SET \"outcome\" = ?'\n"
    ),
}

# Must-PASS fixtures: the same probe must stay silent on each.
_MUST_PASS_WRITE_FIXTURES = {
    "update_sets_only_score": (
        "def f(conn):\n"
        "    conn.execute('UPDATE migration_manifest SET score = ? WHERE source_path = ?')\n"
    ),
    "update_where_mentions_outcome_only": (
        "SQL = \"UPDATE migration_manifest SET score = ? WHERE outcome = ''\"\n"
    ),
    "select_mentioning_is_locked": (
        "SQL = \"SELECT source_path, is_locked FROM migration_manifest WHERE outcome = ''\"\n"
    ),
    "score_only_lowercase": (
        "SQL = 'update migration_manifest set score = ? where source_path = ?'\n"
    ),
    "score_only_split_across_lines": (
        "SQL = '''\nUPDATE migration_manifest\n   SET score = ?\n WHERE source_path = ?\n'''\n"
    ),
    "score_only_with_alias": (
        "SQL = 'UPDATE migration_manifest AS m SET m.score = ? WHERE m.source_path = ?'\n"
    ),
}


@pytest.mark.parametrize("name", sorted(_MUST_FAIL_WRITE_FIXTURES))
def test_probe_write_fixture_must_fail(name):
    violations, _ = _probe_violations({"fixture.bad": _MUST_FAIL_WRITE_FIXTURES[name]})
    assert violations, f"probe missed the must-fail fixture {name!r}"


@pytest.mark.parametrize("name", sorted(_MUST_PASS_WRITE_FIXTURES))
def test_probe_write_fixture_must_pass(name):
    violations, _ = _probe_violations({"fixture.ok": _MUST_PASS_WRITE_FIXTURES[name]})
    assert not violations, f"probe flagged the must-pass fixture {name!r}: {violations}"


def test_probe_fails_closed_when_allow_list_entries_are_gone():
    """A tree that defines none of the allow-listed names reports every entry as a gap."""
    gaps = _probe_allow_list_gaps({"fixture.empty": "x = 1\n"})
    assert len(gaps) == len(_ALLOWED_SQL_CONSTANTS) + sum(
        len(users) for users in _ALLOWED_CONSTANT_USERS.values()
    )


def test_probe_fails_closed_when_it_sees_no_update_sites():
    _, update_sites = _probe_violations({"fixture.empty": "x = 1\n"})
    assert update_sites == 0
