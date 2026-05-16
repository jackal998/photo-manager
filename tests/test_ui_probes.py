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

Implementation note: probes #237 and #238 inspect ``dialog_handler.py``
via ``ast.parse(file.read_text())`` instead of importing it. Importing
the module would force coverage measurement (#185 lists this file as
out-of-scope for layer-1 — it's a thin Qt wrapper) and would trip the
per-file gate. AST inspection sidesteps that entanglement.
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
    """
    fn = _show_action_dialog_ast()
    # The fields list is a literal in show_action_dialog: locate the
    # `fields = [...]` assignment and read its string constants.
    declared: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "fields"
                and isinstance(node.value, ast.List)):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    declared.add(elt.value)
            break

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


@pytest.mark.xfail(
    strict=True,
    reason="#245 — 9 zh_TW values from #209 (numeric compare panel) "
           "plus execute_dialog.execute_button_highlighted still ship "
           "the raw English string. Remove this marker when #245 lands.",
)
def test_probe_zh_tw_translations_are_not_english_passthroughs():
    """Every zh_TW value that differs from the English string visually
    must actually be in Chinese — not a structurally-present key whose
    value was copy-pasted from en.yml during a feature PR.

    Catches: the 9 numeric-panel keys from #209 and
    ``execute_dialog.execute_button_highlighted`` that shipped with
    English text in zh_TW. The existing
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
