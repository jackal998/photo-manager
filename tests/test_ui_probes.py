"""UI invariants — static probes for cross-cutting UI consistency.

Per [#243](https://github.com/jackal998/photo-manager/issues/243): the
qa-explore scenario batch is excellent at *replay* of canonical paths
but architecturally can't catch certain bug classes — field-vs-column
drift, label uniqueness violations, dropped callsite parameters. This
file is the complementary *probe* layer that runs in CI on every PR.

Each probe targets a structural invariant that, if violated, has bitten
us at least once. The current state of each invariant when the probe
was added is recorded in a comment next to the test so a failing CI run
points the maintainer at the right open issue.

Implementation note: probe #237 still inspects ``dialog_handler.py``
via ``ast.parse(file.read_text())`` because its invariant is at the
callsite ("the ``ActionDialog(...)`` call passes ``groups=``") — that's
inherently a source-text shape, not a runtime fact. Probe #238 was
formerly AST-only for the same coverage-gate reason; after #293 cleared
``dialog_handler.py`` from the omit list and extracted the canonical
field list to ``dialog_handler_helpers.py``, probe #238 now imports
the helper directly (no Qt cascade) and compares its return value
against the tree columns.

Authoring guide: ``docs/testing.md`` — "Probe layer — authoring a new probe".
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from app.viewmodels.main_vm import PhotoGroup, PhotoRecord
from app.views import constants
from app.views.tree_model_builder import build_model

REPO = Path(__file__).resolve().parents[1]
DIALOG_HANDLER_PATH = REPO / "app" / "views" / "handlers" / "dialog_handler.py"
MENU_CONTROLLER_PATH = REPO / "app" / "views" / "components" / "menu_controller.py"
ACTION_HANDLERS_PATH = REPO / "app" / "views" / "handlers" / "action_handlers.py"
CONTEXT_MENU_PATH = REPO / "app" / "views" / "handlers" / "context_menu.py"
MAIN_WINDOW_PATH = REPO / "app" / "views" / "main_window.py"
EN_YAML = REPO / "translations" / "en.yml"
ZH_TW_YAML = REPO / "translations" / "zh_TW.yml"


# Tree columns that the user expects to filter against in the Select
# dialog. The Group column (index 0) is the row-grouping mechanism
# itself, not a filterable per-row value — exclude it. Similarity (the
# within-group label "Ref" / "100%" / "85%") IS filterable: it's how a
# user picks "all near-duplicates with > 90% similarity".
_FILTERABLE_COLUMNS: dict[int, str] = {
    constants.COL_ACTION:        "Action",
    constants.COL_SCORE:         "Score",
    constants.COL_LOCK:          "Lock",
    constants.COL_NAME:          "File Name",
    constants.COL_FOLDER:        "Folder",
    constants.COL_SIZE_BYTES:    "Size (Bytes)",
    constants.COL_GROUP_COUNT:   "Group Count",
    constants.COL_CREATION_DATE: "Creation Date",
    constants.COL_SHOT_DATE:     "Shot Date",
    constants.COL_RESOLUTION:    "Resolution",
}


def _show_action_dialog_ast() -> ast.FunctionDef:
    """Return the AST node for ``DialogHandler.show_action_dialog``.

    Read as text + ast.parse so we don't import dialog_handler.py — see
    module docstring for the coverage-gate rationale.
    """
    tree = ast.parse(DIALOG_HANDLER_PATH.read_text(encoding="utf-8"))
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "DialogHandler":
            for fn in cls.body:
                if (isinstance(fn, ast.FunctionDef)
                        and fn.name == "show_action_dialog"):
                    return fn
    raise AssertionError(
        "DialogHandler.show_action_dialog not found in "
        f"{DIALOG_HANDLER_PATH} — file may have been moved or renamed."
    )


def test_probe_select_dialog_exposes_every_filterable_tree_column():
    """Every filterable tree column must appear in the Select dialog's
    field dropdown.

    Forward-defensive against #238 recurring: if a new tree column lands
    without being added to the dialog's field list, the probe flags it.

    Post-#293: the field list lives in
    ``dialog_handler_helpers.default_action_dialog_fields()``. The helper
    has no Qt cascade so importing it is cheap.
    """
    from app.views.handlers.dialog_handler_helpers import (
        default_action_dialog_fields,
    )

    declared = set(default_action_dialog_fields())

    missing = set(_FILTERABLE_COLUMNS.values()) - declared
    assert not missing, (
        f"Select dialog's field dropdown is missing tree columns: "
        f"{sorted(missing)}. Every column visible in the tree must be "
        f"filterable from the Select dialog. See #238."
    )


def test_probe_similarity_column_emits_at_most_one_ref_per_group(qapp):
    """Within a single duplicate group, only one row should render as
    the reference / primary ("Ref"); the rest must show 100% (exact
    duplicate), a similarity percentage (near-duplicate), or the
    passenger sentinel ("—").

    Forward-defensive against #241 recurring: Live Photo HEIC primary +
    MOV passenger, multi-source duplicates union-find collapsed into
    one group, etc. all used to render two or three "Ref" labels in the
    same group.
    """
    from infrastructure.i18n import t

    # Synthetic group: two MOVE-action records, the canonical shape that
    # triggers #241 (Live Photo HEIC primary + MOV passenger both
    # classified MOVE by the scanner).
    base_kwargs = dict(
        group_number=1, is_mark=False, is_locked=False,
        capture_date=None, modified_date=None,
    )
    rec_heic = PhotoRecord(
        file_path="/fake/IMG_0001.HEIC", folder_path="/fake",
        action="MOVE", score=0.87, file_size_bytes=1_000_000, **base_kwargs,
    )
    rec_mov = PhotoRecord(
        file_path="/fake/IMG_0001.MOV", folder_path="/fake",
        # Passengers carry no score per scanner/scoring.py
        action="MOVE", score=None, file_size_bytes=2_000_000, **base_kwargs,
    )
    group = PhotoGroup(group_number=1, items=[rec_heic, rec_mov])

    model, _proxy = build_model([group])

    ref_label = t("tree.similarity_ref")
    ref_count = 0
    for parent_row in range(model.rowCount()):
        parent = model.item(parent_row, constants.COL_GROUP)
        for child_row in range(parent.rowCount()):
            sim_cell = parent.child(child_row, constants.COL_GROUP)
            if sim_cell is not None and sim_cell.text() == ref_label:
                ref_count += 1

    assert ref_count <= 1, (
        f"Group rendered {ref_count} '{ref_label}' labels — at most 1 "
        f"per group. Additional Ref-tier rows should fall back to a "
        f"similarity percentage or neutral sentinel. See #241."
    )


def test_probe_action_dialog_receives_groups_from_main_window_callsite():
    """When the main window opens the Select dialog via right-click,
    the ``groups=`` parameter MUST be threaded through — otherwise the
    numeric-condition panel (>, <, =, Top-N) silently stays hidden even
    when the user picks a numeric field.

    Forward-defensive against #237 recurring. We inspect dialog_handler.py's
    AST rather than invoking the function so the probe doesn't pull
    dialog_handler.py into coverage measurement.
    """
    fn = _show_action_dialog_ast()
    # Find the `ActionDialog(...)` call inside show_action_dialog.
    action_dialog_calls: list[ast.Call] = []
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ActionDialog"):
            action_dialog_calls.append(node)

    assert action_dialog_calls, (
        "No ActionDialog(...) call found inside "
        "DialogHandler.show_action_dialog — file may have been "
        "refactored. Update the probe to match."
    )

    for call in action_dialog_calls:
        kwarg_names = {kw.arg for kw in call.keywords if kw.arg}
        assert "groups" in kwarg_names, (
            "DialogHandler.show_action_dialog calls ActionDialog "
            "without `groups=`. The numeric-condition panel is gated "
            "on a non-empty `self._groups`, so without this argument "
            "picking Size / Score / Group Count / Similarity from the "
            "dropdown silently shows the regex panel instead of the "
            ">/</= panel. See #237."
        )


def _ast_class_method_names(path: Path, class_name: str) -> set[str]:
    """Return the set of public method names defined on a class, read
    from source via AST so the probe doesn't import the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == class_name:
            return {
                fn.name for fn in cls.body
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not fn.name.startswith("_")
            }
    raise AssertionError(
        f"class {class_name} not found in {path} — file may have been "
        f"moved or renamed."
    )


def test_probe_action_handlers_impl_proxies_every_protocol_method():
    """Every method on the ``ActionHandlers`` Protocol (the contract
    the context menu invokes against) MUST be present on
    ``ActionHandlersImpl`` (the manual proxy bridge to file_operations).

    Catches: future drift of the bridge-pattern hole that caused #175
    (forgot ``set_locked_state``) and #182 (forgot
    ``set_decision_with_lock_check``). Python's Protocol is advisory —
    a missing method survives until the menu item fires at runtime and
    the AttributeError gets swallowed by Qt's signal dispatch. This
    probe is the static enforcement the Protocol can't give us.

    Today: passes (3 manual tests in
    ``tests/test_context_menu.py::TestActionHandlersImplBridge`` cover
    a subset; this probe enforces 100% Protocol parity automatically).
    """
    protocol_methods = _ast_class_method_names(
        CONTEXT_MENU_PATH, "ActionHandlers"
    )
    impl_methods = _ast_class_method_names(
        ACTION_HANDLERS_PATH, "ActionHandlersImpl"
    )

    missing = protocol_methods - impl_methods
    assert not missing, (
        f"ActionHandlersImpl is missing proxies for ActionHandlers "
        f"Protocol methods: {sorted(missing)}. Context-menu items that "
        f"invoke these will silently no-op via Qt's swallowed "
        f"AttributeError. Background: feedback_action_handlers_bridge "
        f"in memory; #175 / #182 are prior instances."
    )

    # Reverse direction is informative but not a hard error: the impl
    # can carry helper methods that aren't part of the Protocol surface.
    # If a stale proxy ever needs to be removed, that's a code-review
    # concern, not a structural invariant.


def test_probe_manifest_dependent_menu_actions_are_gated():
    """Actions that operate on the loaded manifest MUST be in
    ``MANIFEST_ACTIONS`` so they're disabled before a manifest is open
    and re-enabled on load.

    Catches: ``action_by_regex`` not gated (#244, fixed). We hardcode
    the design rule here rather than trying to derive it statically —
    that's the probe's job. Add new gated actions to
    ``_MANIFEST_DEPENDENT`` when the menu grows.
    """
    # Read MANIFEST_ACTIONS via AST — same coverage-isolation rationale
    # as the other static probes. The constant is declared with a type
    # annotation (``MANIFEST_ACTIONS: tuple[str, ...] = (…)``) which
    # parses as ast.AnnAssign, not ast.Assign — both forms must be
    # walked or the probe silently sees an empty set and "passes" for
    # the wrong reason.
    src = MENU_CONTROLLER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    manifest_actions: set[str] = set()
    for node in ast.walk(tree):
        target_id: str | None = None
        value_node: ast.expr | None = None
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            target_id = node.targets[0].id
            value_node = node.value
        elif (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)):
            target_id = node.target.id
            value_node = node.value
        if target_id != "MANIFEST_ACTIONS" or not isinstance(value_node, ast.Tuple):
            continue
        for elt in value_node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                manifest_actions.add(elt.value)
        break

    assert manifest_actions, (
        "Probe could not locate the MANIFEST_ACTIONS constant in "
        f"{MENU_CONTROLLER_PATH}. Did the declaration form change "
        "(Assign / AnnAssign / something else)? Update the AST walker."
    )

    # Design-rule: actions that require a loaded manifest to make sense.
    # Add new entries here when the menu grows. Keep this list aligned
    # with the user expectation that "no manifest open" = "every
    # manifest-mutating menu item is greyed out".
    _MANIFEST_DEPENDENT = {
        "save_manifest",
        "execute_action",
        "remove_from_list",
        "action_by_regex",
    }

    missing = _MANIFEST_DEPENDENT - manifest_actions
    assert not missing, (
        f"Menu actions need manifest gating but are NOT in "
        f"MANIFEST_ACTIONS: {sorted(missing)}. These will stay enabled "
        f"before any manifest loads, so the user can click them and "
        f"get an empty / undefined-behaviour dispatch. Add the missing "
        f"entries to MANIFEST_ACTIONS in "
        f"app/views/components/menu_controller.py."
    )


def _walk_yaml_leaf_strings(
    d_en, d_zh, prefix: str = ""
) -> list[tuple[str, str, str]]:
    """Yield ``(dotted_key, en_value, zh_value)`` for every leaf string
    where both locales define the same key. Skips missing-from-zh keys
    (those are caught by the existing ``test_zh_tw_has_every_key_present_in_english``)."""
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


# Keys that are intentionally identical in both locales — product /
# proper-noun strings that are not localized. Keep this list tiny;
# anything new added here should have an explicit reason in the PR
# description (e.g. "brand name", "version string format").
_TRANSLATION_EXEMPT_KEYS: frozenset[str] = frozenset({
    "main_window.title",  # "Photo Manager" — product name, untranslated by design
})

_CJK_RE = re.compile(r"[一-鿿]")


def test_probe_zh_tw_translations_are_not_english_passthroughs():
    """Every zh_TW value that differs from the English string visually
    must actually be in Chinese — not a structurally-present key whose
    value was copy-pasted from en.yml during a feature PR.

    Forward-defensive against #245 recurring: catches keys whose values
    were pasted-as-English during a future feature PR. The existing
    ``test_zh_tw_has_every_key_present_in_english`` only checks
    structural key parity; it cannot catch a key whose value was
    pasted-as-English.

    Heuristic: zh value equals en value AND contains no CJK characters
    AND has at least one Latin letter AND is at least 3 chars long.
    The exempt list (``_TRANSLATION_EXEMPT_KEYS``) carries product /
    proper-noun strings that are legitimately the same in both
    locales.
    """
    en = yaml.safe_load(EN_YAML.read_text(encoding="utf-8"))
    zh = yaml.safe_load(ZH_TW_YAML.read_text(encoding="utf-8"))

    untranslated: list[tuple[str, str]] = []
    for key, v_en, v_zh in _walk_yaml_leaf_strings(en, zh):
        if key in _TRANSLATION_EXEMPT_KEYS:
            continue
        if v_zh != v_en:
            continue  # different value — translated (even if poorly)
        if _CJK_RE.search(v_zh):
            continue  # contains Chinese — translated (just happens to match en in some part)
        if not re.search(r"[A-Za-z]", v_zh):
            continue  # no alphabetic content (numbers, punctuation only)
        if len(v_zh.strip()) < 3:
            continue  # too short to be a meaningful phrase
        untranslated.append((key, v_zh))

    assert not untranslated, (
        f"zh_TW values appear to be untranslated English passthroughs "
        f"({len(untranslated)} keys):\n  " +
        "\n  ".join(f"{k!r}: {v!r}" for k, v in untranslated) +
        "\n\nEither translate the values in translations/zh_TW.yml, or "
        "if the string is legitimately identical (product name, etc.), "
        "add the key to _TRANSLATION_EXEMPT_KEYS in this file with a "
        "one-line reason."
    )


def test_probe_no_execute_mode_toggle_in_menu():
    """The Execute Mode toggle (Ctrl+E) was an Option-B prototype for
    #165 (added in d8bd1dc, removed in #240). This probe enforces the
    absence so the toggle can't silently come back via a future
    refactor or a partial revert.
    """
    src = MENU_CONTROLLER_PATH.read_text(encoding="utf-8")
    assert '"execute_mode"' not in src, (
        "menu_controller.py registers an 'execute_mode' action — the "
        "Option-B prototype was deliberately removed in #240. If a new "
        "use-case for a similar surface lands, give it a different name "
        "and update this probe's guard string so the regression-vs-new-"
        "feature distinction stays explicit."
    )


# ------------------------------------------------------------------------
# Surface inventory — destructive entry-point enumeration (#302).
#
# Forward-defensive version of `test_probe_no_execute_mode_toggle_in_menu`.
# That probe pins the specific absence of "execute_mode" from
# menu_controller.py; this probe asks the structural question — how many
# distinct user-facing surfaces reach each destructive handler — without
# anyone having to know in advance which menu key to grep for.
#
# Architecture: pure static AST scan (per #302 design discussion). The
# menu-bar surfaces live in `MainWindow._connect_signals`'s handlers
# dict; the context-menu surfaces live in `ContextMenuHandler._create_*`
# lambdas. Two files, one AST walk each, then bucket by handler name.
#
# Same-handler definition: the short name of the bound callable.
#   - Menu bar: RHS of each entry in the handlers dict (e.g. `self.on_execute_action`
#     → "on_execute_action").
#   - Context menu: the `.handlers.X(...)` method name inside each
#     lambda body (e.g. `self.handlers.set_decision_with_lock_check(...)`
#     → "set_decision_with_lock_check"). Plain QMenu.addAction
#     connections (Open Folder, language pick) are not destructive and
#     are filtered out by the destructive-handler set below.

# Destructive handler names (per #302 architecture decision). A handler
# is "destructive" if user-visible file state can change as a result —
# either immediately (Execute) or upon a subsequent flush (set_decision,
# remove_from_list). The Select-by-Field/Regex dialog (show_action_dialog
# / on_open_action_dialog) IS destructive because it is the bulk
# gateway: the dialog's own action combo sets decisions on the model.
#
# Lock state changes (set_locked_state) are deliberately EXCLUDED —
# recoverable by toggling back, no FS mutation, just a flag in the
# manifest. Adding it would force every Lock/Unlock surface into the
# allowlist for no real signal.
_DESTRUCTIVE_HANDLERS: frozenset[str] = frozenset({
    "on_execute_action",
    "set_decision",
    "set_decision_with_lock_check",
    "remove_items_from_list",
    "_remove_from_list_toolbar",
    "show_action_dialog",
    "on_open_action_dialog",
})

# Handlers that intentionally have ≥2 reach surfaces. The probe FAILS
# only if a destructive handler reaches from 2+ surfaces AND is not in
# this map. Each entry needs a one-line justification — adding a new
# entry is a deliberate design choice, not an automatic exemption.
#
# The pairs counted as "context-menu single-select", "context-menu
# multi-select", and "menu bar" are three distinct surfaces from the
# user's POV even when they share lambda code — clicking a right-click
# entry feels separate from clicking a menu-bar entry.
_INTENTIONAL_DUPLICATE_SURFACES: dict[str, str] = {
    # Action menu + right-click single + right-click multi. The dialog
    # is the bulk power tool; per context_menu.py's comment, "right-click
    # parity with the single-selection branch — the regex dialog is the
    # bulk power tool, so it has to be reachable from multi-select
    # right-click too".
    "show_action_dialog": (
        "Action menu (action_by_regex) + right-click single + right-click "
        "multi — intentional bulk-power-tool reach. See context_menu.py."
    ),
    # List menu + right-click single + right-click multi. Mirrors the
    # set-action-by-regex reach for the same reason — remove-from-list
    # is a bulk operation users invoke equally from menu and right-click.
    "remove_items_from_list": (
        "List menu route + right-click single + right-click multi — "
        "intentional bulk reach mirroring set-action-by-regex."
    ),
    # Set Action submenu (single-select) routes set_decision_with_lock_check
    # per settable_decision label; multi-select submenu routes the same.
    # That's two surfaces for one handler by design (#182 unified path).
    "set_decision_with_lock_check": (
        "Right-click single-select Set Action submenu + right-click "
        "multi-select Set Action submenu — intentional #182 unified path."
    ),
}


def _menu_bar_handler_bindings() -> dict[str, str]:
    """Return ``{menu_action_name: handler_short_name}`` parsed from
    ``MainWindow._connect_signals``'s ``handlers`` dict.

    Reads `main_window.py` as text + ``ast.parse`` so the probe doesn't
    pull main_window.py into coverage (same rationale as the other
    static probes in this file)."""
    tree = ast.parse(MAIN_WINDOW_PATH.read_text(encoding="utf-8"))
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "MainWindow"):
            continue
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef)
                    and fn.name == "_connect_signals"):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Assign)
                        and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == "handlers"
                        and isinstance(node.value, ast.Dict)):
                    continue
                bindings: dict[str, str] = {}
                for key_node, val_node in zip(node.value.keys, node.value.values):
                    if not (isinstance(key_node, ast.Constant)
                            and isinstance(key_node.value, str)):
                        continue
                    handler_name: str | None = None
                    # `self.on_execute_action` → Attribute(value=Name('self'),
                    # attr='on_execute_action').
                    if (isinstance(val_node, ast.Attribute)
                            and isinstance(val_node.value, ast.Name)
                            and val_node.value.id == "self"):
                        handler_name = val_node.attr
                    if handler_name is not None:
                        bindings[key_node.value] = handler_name
                return bindings
    raise AssertionError(
        "Could not locate the `handlers = {...}` dict in "
        f"MainWindow._connect_signals ({MAIN_WINDOW_PATH}). The probe "
        "walks this dict to enumerate menu-bar reach — update the AST "
        "walker if the wiring layout changed."
    )


def _context_menu_handler_calls() -> dict[str, list[str]]:
    """Return ``{handler_short_name: [surface_label, ...]}`` for every
    ``lambda: self.handlers.X(...)`` connection in ``context_menu.py``.

    Surface label is the enclosing builder method's name —
    ``_create_single_selection_menu`` vs ``_create_multi_selection_menu``
    — so duplicate detection can tell apart the two right-click modes
    (which ARE distinct from the user's POV)."""
    tree = ast.parse(CONTEXT_MENU_PATH.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "ContextMenuHandler"):
            continue
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef)
                    and fn.name.startswith("_create_")):
                continue
            surface_label = f"context_menu.{fn.name}"
            for node in ast.walk(fn):
                if not isinstance(node, ast.Lambda):
                    continue
                for inner in ast.walk(node.body):
                    # Match `self.handlers.X(...)` Calls.
                    if not (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Attribute)
                            and isinstance(inner.func.value, ast.Attribute)
                            and isinstance(inner.func.value.value, ast.Name)
                            and inner.func.value.value.id == "self"
                            and inner.func.value.attr == "handlers"):
                        continue
                    handler_name = inner.func.attr
                    out.setdefault(handler_name, []).append(surface_label)
    return out


def test_probe_destructive_surface_inventory():
    """No destructive handler reaches from 2+ surfaces unless allowlisted.

    Forward-defensive version of `test_probe_no_execute_mode_toggle_in_menu`:
    rather than pinning the absence of ONE removed menu key, this probe
    enumerates EVERY destructive handler's reach surface and flags the
    next #240-class duplication automatically.

    A failure here means a destructive code path became reachable from
    a new surface. Decide:
      (a) the new surface is intentional → add the handler to
          `_INTENTIONAL_DUPLICATE_SURFACES` above with a one-line reason.
      (b) the new surface is a #240-class accidental duplication
          (e.g. a menu toggle that re-opens an already-reachable
          destructive dialog) → revert / unwire the new surface.
    """
    menu_bindings = _menu_bar_handler_bindings()
    context_calls = _context_menu_handler_calls()

    # Bucket: handler short name → list of human-readable surface labels.
    reach: dict[str, list[str]] = {}
    for menu_key, handler_name in menu_bindings.items():
        reach.setdefault(handler_name, []).append(f"menu_bar.{menu_key}")
    for handler_name, surfaces in context_calls.items():
        reach.setdefault(handler_name, []).extend(surfaces)

    duplicates: dict[str, list[str]] = {
        h: surfaces for h, surfaces in reach.items()
        if h in _DESTRUCTIVE_HANDLERS and len(surfaces) >= 2
    }

    unauthorized = {
        h: surfaces for h, surfaces in duplicates.items()
        if h not in _INTENTIONAL_DUPLICATE_SURFACES
    }

    assert not unauthorized, (
        "Destructive handlers reachable from 2+ surfaces without an "
        f"_INTENTIONAL_DUPLICATE_SURFACES entry:\n  " +
        "\n  ".join(
            f"{h!r} reachable from {len(surfaces)}: {surfaces!r}"
            for h, surfaces in sorted(unauthorized.items())
        ) +
        "\n\nThis is the #240-class pattern: two distinct user-facing "
        "surfaces invoking the same destructive code path. Either revert "
        "the new surface, or add the handler to "
        "_INTENTIONAL_DUPLICATE_SURFACES in this file with a one-line "
        "reason. See #302."
    )


def test_probe_destructive_surface_inventory_finds_known_handlers():
    """The AST scan actually finds the destructive handlers we expect.

    Defends against a silent-pass regression: if the AST walker breaks
    (e.g. _connect_signals gets refactored into a different shape), the
    main probe would see an empty `reach` map and pass for the wrong
    reason. Pinning a positive expectation on the known handlers
    surfaces that failure mode immediately.
    """
    menu_bindings = _menu_bar_handler_bindings()
    context_calls = _context_menu_handler_calls()

    # Menu bar must wire at least these destructive handlers today.
    # If any of these stop being wired, the test fails — at which point
    # the maintainer either updates the expectation (the menu item was
    # removed by design — like execute_mode in #240) or fixes the bug
    # that dropped the binding.
    assert "on_execute_action" in menu_bindings.values(), (
        "Menu bar no longer wires on_execute_action — "
        f"current bindings: {menu_bindings!r}"
    )
    assert "on_open_action_dialog" in menu_bindings.values(), (
        "Menu bar no longer wires on_open_action_dialog — "
        f"current bindings: {menu_bindings!r}"
    )
    assert "_remove_from_list_toolbar" in menu_bindings.values(), (
        "Menu bar no longer wires _remove_from_list_toolbar — "
        f"current bindings: {menu_bindings!r}"
    )

    # Context menu must reach at least these destructive handlers.
    for expected in (
        "set_decision_with_lock_check",
        "remove_items_from_list",
        "show_action_dialog",
    ):
        assert expected in context_calls, (
            f"Context menu no longer reaches {expected!r} — "
            f"current calls: {sorted(context_calls)!r}"
        )
