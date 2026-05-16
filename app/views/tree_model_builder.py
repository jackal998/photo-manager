from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP,
    COL_GROUP_COUNT,
    COL_LOCK,
    COL_NAME,
    COL_RESOLUTION,
    COL_SCORE,
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


def _file_similarity(
    action: str, record: object, is_ref_winner: bool = True
) -> str:
    """Return similarity label for a file row.

    EXACT → "100%"; REVIEW_DUPLICATE → percentage from hamming_distance.
    For Ref-tier actions (KEEP / MOVE / UNDATED / ""): when
    ``is_ref_winner`` is True the row carries the "Ref" label; when
    False it falls back to the neutral passenger sentinel "—". Within
    a single duplicate group only ONE row should be the Ref winner
    (#241) — Live Photo HEIC primary + MOV passenger, multi-source
    duplicates union-find collapsed into one group, etc. all used to
    render two or three "Ref" labels in the same group.

    Default ``is_ref_winner=True`` keeps the legacy behaviour for
    callers that don't track within-group winners (notably the unit
    tests in ``tests/test_tree_model_builder.py::TestFileSimilarity``,
    which exercise the helper in isolation).
    """
    if action == "EXACT":
        return "100%"
    if action == "REVIEW_DUPLICATE":
        return _hamming_to_pct(getattr(record, "hamming_distance", None))
    if is_ref_winner:
        return t("tree.similarity_ref")
    return t("tree.similarity_passenger")


def _pick_ref_winner_id(items_list: Iterable[object]) -> int | None:
    """Return ``id()`` of the items_list element that should carry the
    "Ref" label within its group, or ``None`` if no item is Ref-tier.

    Tie-break (ascending — smallest tuple wins):
      1. ``_ACTION_SORT`` priority (Ref-tier == 1 always beats
         EXACT == 2 / REVIEW_DUPLICATE == 3)
      2. Negated score (HEIC primary with a real float score beats an
         unscored MOV passenger; both within the MOVE tier of #241's
         canonical case)
      3. ``file_path`` lexicographic (deterministic when score ties)

    Only returns an id when the candidate is genuinely Ref-tier
    (priority 1). A group of only EXACT/REVIEW_DUPLICATE rows has no
    Ref winner — those rows render their own similarity labels and
    no "Ref" should appear.
    """
    best_item: object | None = None
    best_key: tuple | None = None
    for it in items_list:
        action = getattr(it, "action", "") or ""
        priority = _ACTION_SORT.get(action, 1)
        if priority != 1:
            continue  # not Ref-tier — skip
        score = getattr(it, "score", None)
        # Negate so the *highest* real score becomes the smallest key.
        # Unscored rows (score is None) collapse to -inf → -(-inf) = inf,
        # i.e. they rank LAST among Ref-tier candidates. That puts the
        # HEIC primary (scored) ahead of the MOV passenger (unscored)
        # — the #241 canonical case.
        score_key = -(score if score is not None else -math.inf)
        path_key = str(getattr(it, "file_path", ""))
        key = (priority, score_key, path_key)
        if best_key is None or key < best_key:
            best_key = key
            best_item = it
    return id(best_item) if best_item is not None else None


# Sentinel SORT_ROLE value for unscored rows (Live Photo MOV passengers,
# isolated rows, old manifests pre-#187). Set lower than any real score
# (which is in [0.0, 1.0]) so unscored rows sort to the bottom under
# descending order. -1.0 is the chosen sentinel — any negative would
# work; -1 makes the convention explicit when reading sort code.
_UNSCORED_SORT_VALUE: float = -1.0


def _score_display(score: float | None) -> str:
    """Format a score for the COL_SCORE cell.

    Two-decimal float for real scores (e.g. ``"0.87"``); em-dash for
    unscored rows. Em-dash distinguishes a deliberate None (passenger,
    isolated, unscored manifest) from a missing render — the user reads
    ``"—"`` as 'no score' rather than 'broken display'.
    """
    if score is None:
        return "—"
    return f"{score:.2f}"


# #165 prototype — foreground brush for undecided rows in Execute mode.
# Mid-grey so the row stays readable (text + path visible) but visibly
# distinct from decided rows. Stamped on every cell when
# ``grey_undecided=True`` and the record's ``user_decision`` is empty.
_UNDECIDED_FG = QBrush(QColor(150, 150, 150))


def build_model(
    groups: Iterable[object],
    grey_undecided: bool = False,
) -> tuple[QStandardItemModel, QSortFilterProxyModel | None]:
    """Builds the tree model and a proxy for sorting with roles.

    Returns (model, proxy). Proxy can be None on failure.

    Args:
        groups: Iterable of group objects with ``items`` and
            ``group_number``.
        grey_undecided: When True, file rows whose ``user_decision``
            is empty get a mid-grey ``Qt.ForegroundRole`` brush on
            every cell. Used by the #165 Execute-mode prototype so
            the user sees at a glance which rows still need a
            decision. Default False keeps the existing Review-mode
            rendering untouched.
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
            QStandardItem(""),                       # COL_SCORE      (2) — group level empty; sort role = max
            QStandardItem(""),                       # COL_LOCK       (3) — lock at file level
            QStandardItem(""),                       # COL_NAME       (4)
            QStandardItem(""),                       # COL_FOLDER     (5)
            QStandardItem(""),                       # COL_SIZE_BYTES (6)
            QStandardItem(str(group_count_val)),     # COL_GROUP_COUNT (7)
            QStandardItem(""),                       # COL_CREATION_DATE (8)
            QStandardItem(""),                       # COL_SHOT_DATE  (9)
            QStandardItem(""),                       # COL_RESOLUTION (10) — group level empty
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
        try:
            # Group-level Score sort: max score across files in the group.
            # Unscored rows (score is None) contribute the sentinel so a
            # group containing only unscored rows sorts to the bottom under
            # descending order, like its individual rows do.
            file_scores = [
                getattr(it, "score", None) for it in items_list
            ]
            real_scores = [s for s in file_scores if s is not None]
            group_row[COL_SCORE].setData(
                max(real_scores) if real_scores else _UNSCORED_SORT_VALUE,
                SORT_ROLE,
            )
        except Exception:
            pass

        model.appendRow(group_row)

        # #241 — exactly one row per group earns the "Ref" label; the
        # rest of any Ref-tier rows render as "—". Compute the winner
        # once per group instead of tracking ref_seen in the loop so
        # the source-model iteration order stays untouched (sort happens
        # downstream through QSortFilterProxyModel).
        ref_winner_id = _pick_ref_winner_id(items_list)

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
            score_val = getattr(p, "score", None)
            score_txt = _score_display(score_val)

            # Col 0 at file row: similarity % for duplicates, "Ref" for
            # the per-group winner, "—" for sibling Ref-tier rows (#241).
            file_action = getattr(p, "action", "") or ""
            file_match = _file_similarity(
                file_action, p, is_ref_winner=(id(p) == ref_winner_id),
            )

            # Col 1: user's decision (delete / keep / "" / remove_from_list).
            # Lock state moved to its own COL_LOCK column in #182 so the
            # Action column stays sortable / searchable as just the
            # decision label — no 🔒 prefix.
            item_decision = getattr(p, "user_decision", "") or ""
            item_locked = bool(getattr(p, "is_locked", False))

            child_row = [
                QStandardItem(file_match),                       # COL_GROUP      (0) — similarity
                QStandardItem(_action_display(item_decision)),   # COL_ACTION     (1) — localized decision label
                QStandardItem(score_txt),                        # COL_SCORE      (2)
                QStandardItem(_lock_display(item_locked)),       # COL_LOCK       (3) — 🔒 glyph or empty
                QStandardItem(name),                             # COL_NAME       (4)
                QStandardItem(folder),                           # COL_FOLDER     (5)
                QStandardItem(str(size_num)),        # COL_SIZE_BYTES (6)
                QStandardItem(""),                   # COL_GROUP_COUNT (7) — group level only
                QStandardItem(creation_txt),         # COL_CREATION_DATE (8)
                QStandardItem(shot_txt),             # COL_SHOT_DATE  (9)
                QStandardItem(resolution_txt),       # COL_RESOLUTION (10)
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
                # Score sort: float when present, sentinel _UNSCORED_SORT_VALUE
                # (-1.0) when None so unscored rows sort below real-score rows
                # under descending order.
                child_row[COL_SCORE].setData(
                    float(score_val) if score_val is not None else _UNSCORED_SORT_VALUE,
                    SORT_ROLE,
                )
            except Exception:
                pass
            try:
                child_row[COL_NAME].setData(getattr(p, "file_path", ""), PATH_ROLE)
            except Exception:
                pass

            for it in child_row:
                it.setEditable(False)
            # #165 prototype — grey undecided file rows in Execute mode.
            # We stamp a foreground brush on every cell of the child row
            # rather than only COL_NAME because Qt renders each cell with
            # its own foreground; selectively colouring one column would
            # produce a half-grey row.
            if grey_undecided and not item_decision:
                for it in child_row:
                    it.setForeground(_UNDECIDED_FG)
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
