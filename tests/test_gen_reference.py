"""Tests for scripts/gen_reference.py (the generated reference tables in docs/reference/).

Each case covers a failure a reader of the docs would otherwise hit: a row the
parser silently drops, a TS shape change that must fail closed rather than emit a
partial table, a hand-edited table that --check must name, a CRLF checkout that
must not look stale, and the committed tables themselves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.gen_reference as gr

_REPO = Path(__file__).resolve().parent.parent

COLUMNS_TS = """\
export type ColumnId =
  | "name"
  | "action";

export const COLUMNS: readonly ColumnDef[] = [
  // a comment line between rows is allowed
  { id: "name", labelKey: "web.column.file_name", labelFallback: "File Name", defaultWidth: 160, sortable: true, align: "left", mono: false, flexible: true },
  { id: "action", labelKey: "web.column.action", labelFallback: "Action", defaultWidth: 168, sortable: true, align: "left", mono: false, flexible: false },
] as const;
"""

TOOLBAR_TS = """\
export const TOOLBAR_MANIFEST_SHED_WIDTH = 1100;

export const TOOLBAR_OVERFLOW_MENU_WIDTH = TOOLBAR_MANIFEST_SHED_WIDTH;
"""

SCAN_PY = """\
class SseScanBus:
    def _set_terminal(self, status: str, name: str, payload: dict) -> None:
        pass

    def log(self, msg: str) -> None:
        self._append_and_fanout("log", {"msg": msg})

    def failed(self, msg: str) -> None:
        self._set_terminal("failed", "failed", {"msg": msg})

    def finished(self, output_path: str) -> None:
        self._set_terminal("finished", "finished", {"output_path": output_path})


def _generate():
    yield {"event": "ping", "data": "{}"}
"""

I18N_YAML = """\
_meta:
  locale: en
web:
  a: x
  b:
    c: y
menu:
  t: z
"""

SCENARIO_YAML = """\
scenarios:
  - scenario: s01_happy_path
    playwright_module: qa.web.scenarios.s01_happy_path
    status: done
    notes: ""
  - scenario: s02_empty_folder
    playwright_module: null
    status: todo
    notes: ""
"""


def test_columns_parser_reads_every_row() -> None:
    cols = gr.parse_result_columns(COLUMNS_TS, "resultColumns.ts")
    assert [(c.id, c.label, c.sortable) for c in cols] == [
        ("name", "File Name", True),
        ("action", "Action", True),
    ]


@pytest.mark.parametrize(
    "mutated",
    [
        COLUMNS_TS.replace("sortable: true, align: \"left\", mono: false, flexible: true",
                           "sortable: yes, align: \"left\", mono: false, flexible: true"),
        COLUMNS_TS.replace("] as const;", "];"),
        COLUMNS_TS.replace('  | "action";', '  | "action"\n  | "similarity";'),
    ],
    ids=["row-shape-changed", "block-terminator-gone", "union-drifted-from-rows"],
)
def test_columns_parser_fails_closed_on_shape_change(mutated: str) -> None:
    with pytest.raises(gr.StructureError, match="resultColumns.ts"):
        gr.parse_result_columns(mutated, "resultColumns.ts")


def test_toolbar_parser_resolves_alias_to_its_literal() -> None:
    widths = gr.parse_toolbar_shed(TOOLBAR_TS, "toolbarShed.ts")
    assert widths == {
        "TOOLBAR_MANIFEST_SHED_WIDTH": 1100,
        "TOOLBAR_OVERFLOW_MENU_WIDTH": 1100,
    }


def test_toolbar_parser_fails_closed_when_a_constant_is_missing() -> None:
    only_one = TOOLBAR_TS.replace("export const TOOLBAR_OVERFLOW_MENU_WIDTH = TOOLBAR_MANIFEST_SHED_WIDTH;", "")
    with pytest.raises(gr.StructureError, match="TOOLBAR_OVERFLOW_MENU_WIDTH"):
        gr.parse_toolbar_shed(only_one, "toolbarShed.ts")


def test_toolbar_parser_fails_closed_on_an_unlisted_width_constant() -> None:
    # A new shed width the table does not describe must stop the table, not vanish from it.
    extra = TOOLBAR_TS + "\nexport const TOOLBAR_COMPACT_SEARCH_WIDTH = 900;\n"
    with pytest.raises(gr.StructureError, match="TOOLBAR_COMPACT_SEARCH_WIDTH"):
        gr.parse_toolbar_shed(extra, "toolbarShed.ts")


def test_check_flags_an_orphaned_table(tmp_path: Path) -> None:
    (tmp_path / "columns.md").write_text("a\n", encoding="utf-8", newline="\n")
    (tmp_path / "old-table.md").write_text("gone\n", encoding="utf-8", newline="\n")
    problems = gr.check_tables({"columns.md": "a\n"}, tmp_path)
    assert len(problems) == 1
    assert "old-table.md" in problems[0]


def test_import_helper_fails_closed_with_a_structure_error() -> None:
    with pytest.raises(gr.StructureError, match="no_such_module"):
        gr._import_attr("app.web.no_such_module", "X", "app/web/no_such_module.py")


def test_scan_events_parser_names_literal_events_and_keepalive() -> None:
    events = {(e.name, e.terminal) for e in gr.parse_scan_events(SCAN_PY, "scan.py")}
    assert events == {
        ("log", False),
        ("failed", True),
        ("finished", True),
        ("ping", False),
    }


def test_scan_events_parser_fails_closed_on_a_dynamic_name() -> None:
    dynamic = SCAN_PY.replace('self._append_and_fanout("log", {"msg": msg})',
                              "self._append_and_fanout(msg, {})")
    with pytest.raises(gr.StructureError, match="scan.py"):
        gr.parse_scan_events(dynamic, "scan.py")


def test_i18n_counts_leaf_keys_and_excludes_meta() -> None:
    assert gr.count_i18n_keys(I18N_YAML, "en.yml") == (3, 2)


def test_scenario_map_parser_reads_rows_and_fails_on_missing_status() -> None:
    rows = gr.parse_scenario_map(SCENARIO_YAML, "scenario_map.yml")
    assert [(r.scenario, r.status) for r in rows] == [
        ("s01_happy_path", "done"),
        ("s02_empty_folder", "todo"),
    ]
    broken = SCENARIO_YAML.replace("    status: todo\n", "")
    with pytest.raises(gr.StructureError, match="scenario_map.yml"):
        gr.parse_scenario_map(broken, "scenario_map.yml")


def test_check_names_a_hand_edited_table(tmp_path: Path) -> None:
    expected = {"columns.md": "a\nb\n", "toolbar-shed.md": "c\n"}
    for name, text in expected.items():
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    (tmp_path / "columns.md").write_text("a\nHAND EDIT\n", encoding="utf-8", newline="\n")
    problems = gr.check_tables(expected, tmp_path)
    assert len(problems) == 1
    assert "columns.md" in problems[0]


def test_check_is_line_ending_agnostic(tmp_path: Path) -> None:
    expected = {"columns.md": "a\nb\n"}
    (tmp_path / "columns.md").write_bytes(b"a\r\nb\r\n")
    assert gr.check_tables(expected, tmp_path) == []


def test_committed_reference_tables_are_up_to_date() -> None:
    expected = gr.build_tables(_REPO)
    assert gr.check_tables(expected, _REPO / "docs" / "reference") == []
