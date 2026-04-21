"""Tests for core.services.selection_service.RegexSelectionService."""

from __future__ import annotations

import re

import pytest

from core.services.selection_service import RegexSelectionService


# ── fake accessor ──────────────────────────────────────────────────────────────

_GROUP_LEVEL = frozenset({"Match", "Group Count"})


class _Group:
    def __init__(self, group_text: str, count_text: str, children: list[dict]) -> None:
        self.group_text = group_text
        self.count_text = count_text
        self.children = children  # list of {field: value}


class FakeAccessor:
    def __init__(self, groups: list[_Group]) -> None:
        self._groups = groups
        self.checked: set[tuple[int, int]] = set()

    def iter_groups(self) -> list[_Group]:
        return self._groups

    def iter_children(self, group: _Group) -> list[int]:
        return list(range(len(group.children)))

    def get_field_text(self, group: _Group, child: int | None, field_name: str) -> str | None:
        if child is None:
            if field_name not in _GROUP_LEVEL:
                return None
            if field_name == "Match":
                return group.group_text
            if field_name == "Group Count":
                return group.count_text
            return None
        return group.children[child].get(field_name, "")

    def set_checked(self, group: _Group, child: int, checked: bool) -> None:
        gidx = self._groups.index(group)
        key = (gidx, child)
        if checked:
            self.checked.add(key)
        else:
            self.checked.discard(key)


def _svc(*groups: _Group) -> tuple[RegexSelectionService, FakeAccessor]:
    acc = FakeAccessor(list(groups))
    return RegexSelectionService(acc), acc


# ── tests ──────────────────────────────────────────────────────────────────────

class TestGroupLevelField:
    def test_match_selects_all_children(self):
        g = _Group("EXACT", "3", [{"Action": ""}, {"Action": ""}, {"Action": ""}])
        svc, acc = _svc(g)
        svc.apply("Match", "EXACT", True)
        assert acc.checked == {(0, 0), (0, 1), (0, 2)}

    def test_no_match_skips_group(self):
        g = _Group("MOVE", "1", [{"Action": ""}])
        svc, acc = _svc(g)
        svc.apply("Match", "EXACT", True)
        assert acc.checked == set()

    def test_uncheck_clears_children(self):
        g = _Group("EXACT", "2", [{"Action": ""}, {"Action": ""}])
        svc, acc = _svc(g)
        acc.checked = {(0, 0), (0, 1)}
        svc.apply("Match", "EXACT", False)
        assert acc.checked == set()


class TestFileLevelField:
    def test_selects_matching_child_only(self):
        g = _Group("EXACT", "2", [
            {"Action": "delete"},
            {"Action": "keep"},
        ])
        svc, acc = _svc(g)
        svc.apply("Action", "delete", True)
        assert acc.checked == {(0, 0)}

    def test_uncheck_leaves_non_matching(self):
        g = _Group("EXACT", "2", [
            {"Action": "delete"},
            {"Action": "keep"},
        ])
        svc, acc = _svc(g)
        acc.checked = {(0, 0), (0, 1)}
        svc.apply("Action", "delete", False)
        assert acc.checked == {(0, 1)}  # keep child untouched

    def test_empty_text_skipped(self):
        """Children with empty text are never matched regardless of pattern."""
        g = _Group("MOVE", "1", [{"Action": ""}])
        svc, acc = _svc(g)
        svc.apply("Action", ".*", True)
        assert acc.checked == set()

    def test_multiple_groups_file_level(self):
        g1 = _Group("EXACT", "1", [{"File Name": "photo.jpg"}])
        g2 = _Group("MOVE", "1", [{"File Name": "video.mp4"}])
        svc, acc = _svc(g1, g2)
        svc.apply("File Name", r"\.jpg$", True)
        assert acc.checked == {(0, 0)}
        assert (1, 0) not in acc.checked


class TestEdgeCases:
    def test_invalid_regex_raises(self):
        g = _Group("EXACT", "1", [{"Action": "delete"}])
        svc, _ = _svc(g)
        with pytest.raises(re.error):
            svc.apply("Action", "[invalid", True)

    def test_empty_groups_noop(self):
        svc, acc = _svc()
        svc.apply("Match", ".*", True)
        assert acc.checked == set()
