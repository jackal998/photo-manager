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
from pathlib import Path

import pytest

from app.viewmodels.main_vm import PhotoGroup, PhotoRecord
from app.views import constants
from app.views.tree_model_builder import build_model

REPO = Path(__file__).resolve().parents[1]
DIALOG_HANDLER_PATH = REPO / "app" / "views" / "handlers" / "dialog_handler.py"
MENU_CONTROLLER_PATH = REPO / "app" / "views" / "components" / "menu_controller.py"


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


@pytest.mark.xfail(
    strict=True,
    reason="#238 — Score and Resolution not yet in dialog dropdown. "
           "Remove this marker when #238 lands.",
)
def test_probe_select_dialog_exposes_every_filterable_tree_column():
    """Every filterable tree column must appear in the Select dialog's
    field dropdown.

    Catches: #238 (Score, Resolution missing from dialog despite being
    visible as tree columns).
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


@pytest.mark.xfail(
    strict=True,
    reason="#241 — multiple Ref labels render in groups with multiple "
           "MOVE-action rows (e.g. Live Photo HEIC + MOV passenger). "
           "Remove this marker when #241 lands.",
)
def test_probe_similarity_column_emits_at_most_one_ref_per_group(qapp):
    """Within a single duplicate group, only one row should render as
    the reference / primary ("Ref"); the rest must show 100% (exact
    duplicate), a similarity percentage (near-duplicate), or a neutral
    sentinel.

    Catches: #241 (multiple MOVE rows in a group all render "Ref" —
    Live Photo HEIC + MOV passenger, etc.).
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


@pytest.mark.xfail(
    strict=True,
    reason="#237 — main-window callsite drops `groups=`, so the "
           "numeric compare panel never appears when picking Size etc. "
           "Remove this marker when #237 lands.",
)
def test_probe_action_dialog_receives_groups_from_main_window_callsite():
    """When the main window opens the Select dialog via right-click,
    the ``groups=`` parameter MUST be threaded through — otherwise the
    numeric-condition panel (>, <, =, Top-N) silently stays hidden even
    when the user picks a numeric field.

    Catches: #237. We inspect dialog_handler.py's AST rather than
    invoking the function so the probe doesn't pull dialog_handler.py
    into coverage measurement.
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


@pytest.mark.xfail(
    strict=True,
    reason="#240 — Execute Mode toggle (Option-B prototype for #165) "
           "has not yet been removed. Remove this marker when #240 lands.",
)
def test_probe_no_execute_mode_toggle_in_menu():
    """The Execute Mode toggle (Ctrl+E) was added in d8bd1dc as an
    Option-B prototype for #165. The decision was made to remove it and
    keep the legacy Execute Action dialog as the sole destructive path
    (see #240). This probe enforces the absence so the toggle can't
    silently come back.

    Catches: #240 (Option-B prototype still registered in
    menu_controller.py).
    """
    src = MENU_CONTROLLER_PATH.read_text(encoding="utf-8")
    assert '"execute_mode"' not in src, (
        "menu_controller.py still registers an 'execute_mode' action. "
        "The Execute Mode toggle was decided to be removed in favor of "
        "the legacy Execute Action dialog (see #240). All references "
        "to 'execute_mode' should be deleted along with the supporting "
        "code in main_window.py and execute_mode_helpers.py."
    )
