"""Probe: at most one "Ref" similarity label per group; no Ref + delete combo.

Would have caught #241 (multiple Ref labels in a single group — Live Photo
HEIC primary + MOV passenger both rendered as "Ref"). Complements the
static layer-1 probe
``test_probe_similarity_column_emits_at_most_one_ref_per_group``
(in ``tests/test_ui_probes.py``), which exercises ``build_model``
against a synthetic group. This live probe walks the actual rendered
tree — catches drift between the model builder's invariant and
whatever the proxy / delegate stack actually displays.

Behaviour (per #243 design comment):
  1. Launch the app, load a near-duplicates fixture (≥1 multi-row group).
  2. Walk every TreeItem in the result tree via UIA. Bucket cells by
     screen Y to reconstruct rows; group rows carry "Group N", file
     rows carry similarity label ("Ref" / "100%" / "%" / "—") as the
     leftmost cell.
  3. Track current group; per group, count rows whose leftmost cell
     equals "Ref".
  4. Flag stale label combinations: a row whose cells contain "delete"
     (user_decision label) AND whose leftmost cell is "Ref" — a row
     marked for deletion shouldn't be tagged as the group's keeper.
  5. Emit ``probe_status: PASS|FAIL`` with per-group counts.

Exit code: 0 on PASS, 1 on FAIL or runtime error.

Run:
    .venv/Scripts/python.exe -m qa.probes.group_label_audit
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from qa.probes._runtime import app_with_manifest
from qa.scenarios import _uia

FIXTURE_SOURCES = ["qa/sandbox/near-duplicates"]

# Translation values from ``translations/en.yml`` — probes run under
# the default English locale (qa/settings.json default; never switched
# by ``qa.probes._runtime``). Comparing translated strings against
# literals here is intentional: the probe asserts what the user sees,
# and the user sees the English label by default in QA.
REF_LABEL = "Ref"
DELETE_DECISION_LABEL = "delete"
# "Group N" — t("tree.group_label", n=N). Must match across locales
# only on this prefix (zh_TW also starts with "Group" since the YAML
# template uses {n}); kept loose so a future translator can localise
# the prefix without breaking the probe heuristic.
GROUP_HEADER_RE = re.compile(r"^Group\s+\d+")


def main() -> int:
    print("probe: group_label_audit")
    with app_with_manifest(FIXTURE_SOURCES) as win:
        print("step: walk_tree_cells")
        cells: list[tuple[int, int, str]] = []
        for item in win.descendants(control_type="TreeItem"):
            try:
                text = (item.window_text() or "").strip()
                if not text:
                    continue
                rect = item.rectangle()
                cells.append((rect.top, rect.left, text))
            except Exception:
                continue
        print(f"  total_cells_with_text={len(cells)}")
        if not cells:
            print("FAIL: tree exposed zero TreeItem cells — manifest not loaded?")
            print("probe_status: FAIL")
            return 1

        # Bucket cells into rows by exact ``top``. Cells of the same row
        # share a top in QTreeView's render; clustering with a bucket
        # width (the ``read_result_rows`` 30-px approach) would over-
        # merge on small CI renders. Exact top is robust because all
        # cells of a row are laid out at identical y inside one frame.
        rows_by_top: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for top, left, text in cells:
            rows_by_top[top].append((left, text))

        print("step: walk_rows_track_groups")
        ref_count_by_group: dict[str, int] = defaultdict(int)
        stale_ref_delete_rows: list[str] = []
        current_group: str | None = None
        file_row_count = 0

        for top in sorted(rows_by_top):
            row_cells = sorted(rows_by_top[top], key=lambda c: c[0])
            leftmost_text = row_cells[0][1]
            row_texts = [c[1] for c in row_cells]

            if GROUP_HEADER_RE.match(leftmost_text):
                current_group = leftmost_text
                # Group headers themselves don't carry Ref labels — the
                # leftmost cell renders "Group N", not a similarity.
                # Initialise the count slot so groups with zero Ref-tier
                # children still surface in the diagnostic output.
                ref_count_by_group.setdefault(current_group, 0)
                continue

            # File row. Anchor it to the most-recent group header; a
            # file row before any header would indicate a structural
            # bug worth flagging (kept as None → "<no group>" tag).
            group_key = current_group if current_group is not None else "<no group>"
            file_row_count += 1

            if leftmost_text == REF_LABEL:
                ref_count_by_group[group_key] += 1
                if DELETE_DECISION_LABEL in row_texts:
                    # Stale combo: row is the group's keeper-tier AND
                    # marked for deletion. Capture the basename if
                    # present so the failure log points at a concrete
                    # row, not just a coordinate.
                    basename = next(
                        (
                            t
                            for _, t in row_cells
                            if _uia._BASENAME_RE.match(t)
                        ),
                        f"<row at top={top}>",
                    )
                    stale_ref_delete_rows.append(
                        f"{group_key} :: {basename} (cells={row_texts!r})"
                    )

        print(f"  file_rows={file_row_count}")
        for group_key, count in sorted(ref_count_by_group.items()):
            print(f"  group={group_key!r} ref_count={count}")
        for entry in stale_ref_delete_rows:
            print(f"  stale_ref_delete: {entry}")

        failures: list[str] = []
        for group_key, count in ref_count_by_group.items():
            if count > 1:
                failures.append(
                    f"{group_key} has {count} Ref labels (expected at most 1)"
                )
        if stale_ref_delete_rows:
            failures.append(
                f"{len(stale_ref_delete_rows)} row(s) carry both Ref and delete"
            )

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            print("probe_status: FAIL")
            return 1

        if file_row_count == 0:
            print("FAIL: no file rows seen — group_label_audit cannot validate")
            print("probe_status: FAIL")
            return 1

        print("probe_status: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
