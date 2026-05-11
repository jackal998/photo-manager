from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP,
    COL_GROUP_COUNT,
    COL_LOCK,
    COL_NAME,
    COL_RESOLUTION,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
    PATH_ROLE,
    REMOVE_FROM_LIST_DECISION,
    SORT_ROLE,
    headers,
)
from infrastructure.i18n import t


_LOCK_GLYPH = "\U0001F512"  # 🔒


def _action_display(decision: str, is_locked: bool = False) -> str:
    """Map an internal user_decision value to its localized label.

    Currently only the deferred remove-from-list value gets a
    translated label; ``"delete"`` and ``"keep"`` are passed through
    as-is.

    ``is_locked`` is accepted for backward compatibility but no longer
    affects the returned text — the lock indicator moved to its own
    :data:`COL_LOCK` column in #182. See :func:`_lock_display`.
    """
    if decision == REMOVE_FROM_LIST_DECISION:
        return t("decision.remove_from_list")
    return decision


def _lock_display(is_locked: bool) -> str:
    """Cell text for the Lock column — 🔒 glyph if locked, empty otherwise.

    Empty for unlocked rows so the column reads as visually quiet at
    the typical state (very few rows locked at any time). Sortable via
    SORT_ROLE on the same column (0 / 1). Searchable as the string
    ``"Locked"`` (see :func:`_get_record_field` in file_operations).
    """
    return _LOCK_GLYPH if is_locked else ""

# Numeric sort priorities — lower value = sorted first (ascending).
#
# "Ref tier" — every action whose `_file_similarity` renders as "Ref" (KEEP /
# MOVE / UNDATED / unset) shares position 1 so the reference / primary file
# of a group always lands at the top, regardless of which classifier branch
# assigned its action. This is what users mean by "winner first" (#55, #76).
#
# Duplicates follow in descending similarity: EXACT (100%) before
# REVIEW_DUPLICATE (near-match), so a group reads top-down as
# Ref → strongest match → weaker matches.
_ACTION_SORT: dict[str, int] = {
    "KEEP": 1,
    "MOVE": 1,
    "UNDATED": 1,
    "": 1,
    "EXACT": 2,
    "REVIEW_DUPLICATE": 3,
}  # missing key → 1 (treated as Ref tier, matching `_file_similarity`)

_DECISION_SORT: dict[str, int] = {
    "delete": 1,
    "keep": 2,
    REMOVE_FROM_LIST_DECISION: 4,
}  # "" (undecided) → 3 (between keep and remove_from_list)

def _hamming_to_pct(hamming: int | None) -> str:
    """Convert pHash Hamming distance to a similarity percentage string."""
    if hamming is None:
        return t("tree.similarity_near_dup")
    return f"{round((64 - hamming) / 64 * 100)}%"


def _file_similarity(action: str, record: object) -> str:
    """Return similarity label for a file row.

    EXACT → "100%"; REVIEW_DUPLICATE → percentage from hamming_distance.
    Any other action (KEEP, MOVE, UNDATED, "") is the reference/source file → "Ref".
    """
    if action == "EXACT":
        return "100%"
    if action == "REVIEW_DUPLICATE":
        return _hamming_to_pct(getattr(record, "hamming_distance", None))
    return t("tree.similarity_ref")


def build_model(
    groups: Iterable[object],
) -> tuple[QStandardItemModel, QSortFilterProxyModel | None]:
    """Builds the tree model and a proxy for sorting with roles.

    Returns (model, proxy). Proxy can be None on failure.
    """
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(headers())

    for g in groups:
        group_number = int(getattr(g, "group_number", 0) or 0)
        items_list = getattr(g, "items", []) or []

        # Col 0 at group row: "Group N" label
        group_item = QStandardItem(t("tree.group_label", n=group_number))
        group_item.setEditable(False)

        group_count_val = len(items_list)
        group_row = [
            group_item,                              # COL_GROUP      (0)
            QStandardItem(""),                       # COL_ACTION     (1) — decision at file level
            QStandardItem(""),                       # COL_LOCK       (2) — lock at file level
            QStandardItem(""),                       # COL_NAME       (3)
            QStandardItem(""),                       # COL_FOLDER     (4)
            QStandardItem(""),                       # COL_SIZE_BYTES (5)
            QStandardItem(str(group_count_val)),     # COL_GROUP_COUNT (6)
            QStandardItem(""),                       # COL_CREATION_DATE (7)
            QStandardItem(""),                       # COL_SHOT_DATE  (8)
            QStandardItem(""),                       # COL_RESOLUTION (9) — group level empty
        ]
        for it in group_row:
            it.setEditable(False)

        # Group-level SORT_ROLE: aggregate across all files so that sorting a column
        # reorders groups by their "best" file's value (first file after in-group sort).
        # Min-priority wins for ranked fields (delete=1 < keep=2 < ""=3); max wins for size.
        try:
            group_row[COL_GROUP].setData(group_number, SORT_ROLE)
        except Exception:
            pass
        try:
            group_row[COL_ACTION].setData(
                min((_DECISION_SORT.get(getattr(it, "user_decision", ""), 3)
                     for it in items_list),
                    default=3),
                SORT_ROLE,
            )
        except Exception:
            pass
        try:
            # Group-level Lock sort: max wins (any locked row makes the
            # group "locked-tier") so groups containing a locked row
            # sort together when the user clicks the Lock column header.
            group_row[COL_LOCK].setData(
                max(
                    (1 if getattr(it, "is_locked", False) else 0
                     for it in items_list),
                    default=0,
                ),
                SORT_ROLE,
            )
        except Exception:
            pass
        try:
            group_row[COL_NAME].setData(
                min((Path(getattr(it, "file_path", "")).name.lower() for it in items_list),
                    default=""),
                SORT_ROLE,
            )
        except Exception:
            pass
        try:
            group_row[COL_FOLDER].setData(
                min((str(getattr(it, "folder_path", "")).lower() for it in items_list),
                    default=""),
                SORT_ROLE,
            )
        except Exception:
            pass
        try:
            group_row[COL_SIZE_BYTES].setData(
                max((int(getattr(it, "file_size_bytes", 0) or 0) for it in items_list),
                    default=0),
                SORT_ROLE,
            )
        except Exception:
            pass
        try:
            group_row[COL_GROUP_COUNT].setData(int(group_count_val), SORT_ROLE)
        except Exception:
            pass
        try:
            cd_timestamps = [
                int(cd.timestamp())
                for it in items_list
                if (cd := getattr(it, "creation_date", None)) is not None
            ]
            group_row[COL_CREATION_DATE].setData(min(cd_timestamps, default=0), SORT_ROLE)
        except Exception:
            pass
        try:
            sd_timestamps = [
                int(sd.timestamp())
                for it in items_list
                if (sd := getattr(it, "shot_date", None)) is not None
            ]
            group_row[COL_SHOT_DATE].setData(min(sd_timestamps, default=0), SORT_ROLE)
        except Exception:
            pass
        try:
            megapixels = [
                (getattr(it, "pixel_width", None) or 0) * (getattr(it, "pixel_height", None) or 0)
                for it in items_list
            ]
            group_row[COL_RESOLUTION].setData(max(megapixels, default=0), SORT_ROLE)
        except Exception:
            pass

        model.appendRow(group_row)

        for p in items_list:
            name = Path(getattr(p, "file_path", "")).name
            folder = getattr(p, "folder_path", "")
            size_num = int(getattr(p, "file_size_bytes", 0) or 0)
            shot_dt = getattr(p, "shot_date", None)
            creation_dt = getattr(p, "creation_date", None)
            shot_txt = shot_dt.strftime("%Y-%m-%d %H:%M:%S") if shot_dt else ""
            creation_txt = creation_dt.strftime("%Y-%m-%d %H:%M:%S") if creation_dt else ""
            px_w = getattr(p, "pixel_width", None)
            px_h = getattr(p, "pixel_height", None)
            resolution_txt = f"{px_w}×{px_h}" if px_w and px_h else ""
            resolution_mp = (px_w or 0) * (px_h or 0)

            # Col 0 at file row: similarity % for duplicates, "Ref" for the source file
            file_action = getattr(p, "action", "") or ""
            file_match = _file_similarity(file_action, p)

            # Col 1: user's decision (delete / keep / "" / remove_from_list).
            # Lock state moved to its own COL_LOCK column in #182 so the
            # Action column stays sortable / searchable as just the
            # decision label — no 🔒 prefix.
            item_decision = getattr(p, "user_decision", "") or ""
            item_locked = bool(getattr(p, "is_locked", False))

            child_row = [
                QStandardItem(file_match),                       # COL_GROUP      (0) — similarity
                QStandardItem(_action_display(item_decision)),   # COL_ACTION     (1) — localized decision label
                QStandardItem(_lock_display(item_locked)),       # COL_LOCK       (2) — 🔒 glyph or empty
                QStandardItem(name),                             # COL_NAME       (3)
                QStandardItem(folder),                           # COL_FOLDER     (4)
                QStandardItem(str(size_num)),        # COL_SIZE_BYTES (5)
                QStandardItem(""),                   # COL_GROUP_COUNT (6) — group level only
                QStandardItem(creation_txt),         # COL_CREATION_DATE (7)
                QStandardItem(shot_txt),             # COL_SHOT_DATE  (8)
                QStandardItem(resolution_txt),       # COL_RESOLUTION (9)
            ]

            try:
                child_row[COL_GROUP].setData(_ACTION_SORT.get(file_action, 1), SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_ACTION].setData(_DECISION_SORT.get(item_decision, 3), SORT_ROLE)
            except Exception:
                pass
            try:
                # Boolean sort key: 0=unlocked, 1=locked. Ascending puts
                # unlocked first; descending puts locked first.
                child_row[COL_LOCK].setData(1 if item_locked else 0, SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_NAME].setData(str(name).lower(), SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_FOLDER].setData(str(folder).lower(), SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_SIZE_BYTES].setData(int(size_num), SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_CREATION_DATE].setData(
                    int(creation_dt.timestamp()) if creation_dt else 0, SORT_ROLE
                )
            except Exception:
                pass
            try:
                child_row[COL_SHOT_DATE].setData(
                    int(shot_dt.timestamp()) if shot_dt else 0, SORT_ROLE
                )
            except Exception:
                pass
            try:
                child_row[COL_RESOLUTION].setData(resolution_mp, SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_NAME].setData(getattr(p, "file_path", ""), PATH_ROLE)
            except Exception:
                pass

            for it in child_row:
                it.setEditable(False)
            group_item.appendRow(child_row)

    # Install proxy for numeric/text sort with roles
    try:
        proxy = QSortFilterProxyModel()
        proxy.setSortRole(SORT_ROLE)
        proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
        proxy.setSourceModel(model)
    except Exception:
        proxy = None

    return model, proxy
