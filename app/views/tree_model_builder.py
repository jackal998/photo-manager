from __future__ import annotations

import math
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
    COL_SCORE,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
    IGNORE_DECISION,
    PATH_ROLE,
    SORT_ROLE,
    headers,
)
from infrastructure.i18n import t
from scanner.phash_distance import hamming_distance as _phash_hamming


_LOCK_GLYPH = "\U0001F512"  # 🔒


def _action_display(decision: str, is_locked: bool = False) -> str:
    """Map an internal user_decision value to its localized label.

    Three explicit branches so every locale sees a translated label,
    and the canonical empty-keep state stays empty:

    * ``IGNORE_DECISION`` ('ignore') → ``t("decision.remove_from_list")``
      (user-facing label stays "remove from list"; wire value is internal)
    * ``"delete"``                   → ``t("decision.delete")`` (#425 — was raw passthrough,
      invisible on en but rendered as English "delete" on zh_TW)
    * ``"keep"``                     → ``""`` (back-compat: legacy manifests
      that pre-date #425's canonicalisation of auto-select's keeper write
      still carry the literal "keep" — render as the canonical empty
      cell so the leak doesn't surface)
    * ``""`` and any other value     → returned as-is (the canonical
      keep state is the empty string)

    ``is_locked`` is accepted for backward compatibility but no longer
    affects the returned text — the lock indicator moved to its own
    :data:`COL_LOCK` column in #182. See :func:`_lock_display`.
    """
    if decision == IGNORE_DECISION:
        return t("decision.remove_from_list")
    if decision == "delete":
        return t("decision.delete")
    if decision == "keep":
        # Back-compat for legacy manifests written before #425 — auto-
        # select used to store the literal "keep" string. Display as
        # the canonical empty cell so older manifests don't show the
        # leak. New manifests use "" directly.
        return ""
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
# UNDATED / unset) shares position 1 so the reference / primary file
# of a group always lands at the top, regardless of which classifier branch
# assigned its action. This is what users mean by "winner first" (#55, #76).
# Unique non-duplicate files now carry the empty action "" (the legacy MOVE
# action + dest_path column were dropped in #433); they fall to position 1
# via the explicit "" entry — and any unknown action via the default-1 rule.
#
# Duplicates follow in descending similarity: EXACT (100%) before
# REVIEW_DUPLICATE (near-match), so a group reads top-down as
# Ref → strongest match → weaker matches.
_ACTION_SORT: dict[str, int] = {
    "KEEP": 1,
    "UNDATED": 1,
    "": 1,
    "EXACT": 2,
    "REVIEW_DUPLICATE": 3,
}  # missing key → 1 (treated as Ref tier, matching `_file_similarity`)

_DECISION_SORT: dict[str, int] = {
    "delete": 1,
    "keep": 2,
    IGNORE_DECISION: 4,
}  # "" (undecided) → 3 (between keep and ignore)

def _hamming_to_pct(hamming: int | None) -> str:
    """Convert pHash Hamming distance to a similarity percentage string."""
    if hamming is None:
        return t("tree.similarity_near_dup")
    return f"{round((64 - hamming) / 64 * 100)}%"


def _nearest_member(
    record: object, group_items: "Iterable[object] | None"
) -> "tuple[object, int] | None":
    """The OTHER group member closest to ``record`` by pHash Hamming, returned
    as ``(member, distance)`` — or ``None`` when ``record`` has no pHash, the
    group is unknown/empty, or no other member has a comparable pHash (#536
    Direction A).

    Self is excluded by object identity (not pHash equality), so a passenger
    whose peer is pixel-identical still resolves to that peer at distance 0.
    Used for the passenger tooltip ("N% similar to <nearest>").
    """
    if not group_items:
        return None
    record_phash = getattr(record, "phash", None)
    if not record_phash:
        return None
    best: "tuple[object, int] | None" = None
    for other in group_items:
        if other is record:
            continue
        d = _phash_hamming(record_phash, getattr(other, "phash", None))
        if d is None:
            continue
        if best is None or d < best[1]:
            best = (other, d)
    return best


def _file_similarity(
    action: str,
    record: object,
    is_ref_winner: bool = True,
    ref_phash: str | None = None,
) -> str:
    """Return similarity label for a file row.

    EXACT → "100%"; REVIEW_DUPLICATE → percentage from pHash Hamming
    distance against the *displayed* Ref winner (#253). The render-time
    recomputation preserves #241's score-aware Ref pick: when the
    scanner anchored on a different Ref-tier file than ``_pick_ref_winner``
    selects, the stored ``hamming_distance`` would read "X% similar to
    *what?*" — so we re-measure against the row the user actually sees
    as the group's Ref. Falls back to the scanner's stored
    ``hamming_distance`` when either pHash is missing (old manifests
    pre-phash, video rows, or imagehash unavailable).

    For Ref-tier actions (KEEP / UNDATED / ""): when ``is_ref_winner``
    is True the row carries the "Ref" label. When False the row is a
    "passenger" and shows its similarity to the displayed Ref with a
    trailing star (e.g. ``75*%``, #536 Direction A) — the star marks it
    as an indirect/transitive member, and ``build_model`` adds a tooltip
    naming the nearest member. It falls back to the bare "—" sentinel
    only when no comparable pHash exists (a Live Photo MOV). Within a
    single duplicate group only ONE row is the Ref winner (#241).

    Default ``is_ref_winner=True`` and ``ref_phash=None`` keep the
    legacy behaviour for callers that don't track within-group winners
    (notably the unit tests in
    ``tests/test_tree_model_builder.py::TestFileSimilarity``, which
    exercise the helper in isolation).
    """
    if action == "EXACT":
        return "100%"
    if action == "REVIEW_DUPLICATE":
        record_phash = getattr(record, "phash", None)
        recomputed = _phash_hamming(ref_phash, record_phash)
        if recomputed is not None:
            return _hamming_to_pct(recomputed)
        return _hamming_to_pct(getattr(record, "hamming_distance", None))
    if is_ref_winner:
        return t("tree.similarity_ref")
    # #536 Direction A (option D) — a Ref-tier passenger (a member pulled into
    # the group but not the chosen Ref: a same-stem RAW+JPG companion or a
    # #538-reconnected near-dup) shows its similarity to the DISPLAYED Ref,
    # exactly like a REVIEW_DUPLICATE row, so every grouped image row is measured
    # against the same reference (a consistent column). A trailing star marks it
    # as an indirect/transitive member rather than a directly-classified
    # duplicate; build_model sets a tooltip naming the nearest member (the
    # strongest actual link). Falls back to the bare "—" sentinel only when no
    # comparable pHash exists (a Live Photo MOV), keeping "—" reserved for that.
    record_phash = getattr(record, "phash", None)
    vs_ref = _phash_hamming(ref_phash, record_phash)
    if vs_ref is not None:
        return _hamming_to_pct(vs_ref).replace("%", "*%")
    return t("tree.similarity_passenger")


def _pick_ref_winner(items_list: Iterable[object]) -> object | None:
    """Return the items_list element that should carry the "Ref" label
    within its group, or ``None`` if no item is Ref-tier.

    Tie-break (ascending — smallest tuple wins):
      1. ``_ACTION_SORT`` priority (Ref-tier == 1 always beats
         EXACT == 2 / REVIEW_DUPLICATE == 3)
      2. Negated score (HEIC primary with a real float score beats an
         unscored MOV passenger; both within the Ref tier of #241's
         canonical case)
      3. ``file_path`` lexicographic (deterministic when score ties)

    Only returns an item when the candidate is genuinely Ref-tier
    (priority 1). A group of only EXACT/REVIEW_DUPLICATE rows has no
    Ref winner — those rows render their own similarity labels and
    no "Ref" should appear.

    Returning the item itself (not just its ``id()``) lets the caller
    read the winner's pHash to drive #253's render-time Similarity %
    recomputation.
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
    return best_item


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


def build_model(
    groups: Iterable[object],
) -> tuple[QStandardItemModel, QSortFilterProxyModel | None]:
    """Builds the tree model and a proxy for sorting with roles.

    Returns (model, proxy). Proxy can be None on failure.

    Args:
        groups: Iterable of group objects with ``items`` and
            ``group_number``.
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
        #
        # #253 — also grab the winner's pHash so REVIEW_DUPLICATE rows
        # can render % against the *displayed* Ref rather than the
        # scanner's anchor (which may differ when #241's score-aware
        # tie-break picks a different Ref-tier row).
        ref_winner = _pick_ref_winner(items_list)
        ref_winner_phash = (
            getattr(ref_winner, "phash", None) if ref_winner is not None else None
        )

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
                file_action,
                p,
                is_ref_winner=(p is ref_winner),
                ref_phash=ref_winner_phash,
            )

            # Col 1: user's decision (delete / keep / "" / ignore).
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

            # #536 Direction A — for a passenger row (the starred "N*%" cell,
            # i.e. a Ref-tier non-winner), set a tooltip naming the nearest
            # group member so the strongest actual link behind the star is
            # explicit on hover. No tooltip when there is no comparable pHash
            # (a Live Photo MOV, which renders the bare "—").
            if p is not ref_winner and file_action not in ("EXACT", "REVIEW_DUPLICATE"):
                nearest = _nearest_member(p, items_list)
                if nearest is not None:
                    near_item, near_h = nearest
                    child_row[COL_GROUP].setToolTip(
                        t(
                            "tree.similarity_passenger_tooltip",
                            pct=round((64 - near_h) / 64 * 100),
                            name=Path(getattr(near_item, "file_path", "")).name,
                        )
                    )

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
