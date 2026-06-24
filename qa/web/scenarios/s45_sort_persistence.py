"""Web scenario s45 — result-tree column-header sort + in-session persistence (#685).

Ported from the Qt s45_sort_persistence. The web result tree gained a sticky
column-header row whose File Name / Size cells toggle a client-side sort of the
rows within each group (frontend/src/lib/resultColumns.ts). This scenario drives
those header clicks and asserts:

  1. File Name header → case-folded alphabetical ASC; a second click → DESC.
  2. Size header → NUMERIC ascending, verified against an independent SQL oracle
     (the manifest .db ordered by file_size_bytes) so a lexicographic-vs-numeric
     comparator bug is caught (the same oracle the Qt s45 uses).
  3. The chosen sort survives an in-session manifest reopen (re-loading the same
     .db without a page reload) — the web analog of the Qt
     ``refresh_tree → refresh_model → sortByColumn`` replay. Sort lives in the
     resultView store slice, which loadManifest never resets, so a regression
     that tied sort to the manifest groups (or reset it on reload) goes red here.

Why the near-duplicates fixture: its 5 files are the same image at decreasing
JPEG quality (q95→q65), so name-ASC order (00→04) differs from size-ASC order
(q65 smallest → q95 largest). The two sorts are therefore distinguishable, and
all 5 land in ONE similarity group so the rendered order equals the global
size order (no cross-group interleaving to confuse the oracle).

Desktop source: qa/scenarios/s45_sort_persistence.py
Fixture:        qa/sandbox/near-duplicates
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    count_groups,
    load_manifest,
    open_scan_dialog,
    set_output_path,
    start_scan,
    wait_manifest_loaded,
)
from qa.web.testid_constants import col_header_testid

_REPO = Path(__file__).resolve().parents[3]
_SRC = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_EXPECTED_ROWS = 5


def _rendered_basenames(page) -> list[str]:
    """Return the visible file-row basenames in on-screen (document) order.

    The virtualizer renders rows in vrow order, so document order == the sorted
    order. The 5-file fixture fits on screen, so every row is in the DOM.
    """
    testids = page.eval_on_selector_all(
        '[data-testid^="row-file-"]',
        "els => els.map(e => e.getAttribute('data-testid'))",
    )
    out: list[str] = []
    for tid in testids:
        m = re.match(r"^row-file-\d+-(.+)$", tid or "")
        if m:
            out.append(m.group(1))
    return out


def _sizes_asc_from_sqlite(db_path: str) -> list[str]:
    """Independent oracle: fixture basenames ordered by file_size_bytes ASC.

    Reads the manifest .db directly (a different code path from the React sort),
    so agreement rules out a comparator that ordered by some other field or
    lexicographically. Mirrors the Qt s45 SQL oracle.

    No ``WHERE outcome = ''`` filter (which the API applies) is needed here
    because s45 runs NO execute step — every row's outcome is '' — so the oracle
    row set equals the API's. A future extension that adds an execute step before
    this read MUST add the filter, or the oracle and the rendered tree diverge.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source_path FROM migration_manifest ORDER BY file_size_bytes ASC"
        ).fetchall()
    finally:
        conn.close()
    return [Path(r[0]).name for r in rows]


def _wait_rows(page, n: int = _EXPECTED_ROWS) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('[data-testid^=\"row-file-\"]').length === n",
        arg=n,
        timeout=20_000,
    )


def _click_header(page, col_id: str) -> None:
    page.get_by_test_id(col_header_testid(col_id)).click()
    page.wait_for_timeout(150)  # let the re-sort + re-render settle


def run(*, base_url: str) -> None:
    """Scan → sort by File Name (asc/desc) and Size (vs SQL oracle) → reopen."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s45_")
    db_path = os.path.join(tmpdir, "manifest.db")
    failures: list[str] = []
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)
            add_scan_source(page, _SRC, idx=0)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)
            _wait_rows(page)

            # Fixture invariants — fail loud if the sandbox changed.
            n_groups = count_groups(page)
            if n_groups != 1:
                raise AssertionError(
                    f"expected the near-duplicates fixture to form 1 group, "
                    f"got {n_groups} — the per-group sort oracle assumes a "
                    f"single group; fixture or grouping changed."
                )
            baseline = _rendered_basenames(page)
            print(f"probe_status: s45 baseline={baseline}")
            if len(baseline) != _EXPECTED_ROWS:
                raise AssertionError(
                    f"expected {_EXPECTED_ROWS} file rows, got {len(baseline)}: "
                    f"{baseline!r}"
                )

            # NON-VACUITY GUARD (the load-bearing structure of this driver):
            # the near-duplicates fixture's sizes decrease monotonically with the
            # filename index (q95→q65), so size-ASC == name-DESC AND the server
            # order already == name-ASC. If we clicked name→ASC→DESC→size in that
            # order, the DOM would already be in size order before the size click,
            # so a DEAD size handler would pass vacuously. To make EVERY sort click
            # observable, each click below is driven from a state whose order
            # DIFFERS from the expected post-click order, and we assert that the
            # click actually reordered (`!= prior order`) in addition to matching
            # the expected order. A no-op handler then fails the "did not reorder"
            # guard. (Field isolation — size sorts numerically, not by name — is
            # additionally pinned at the unit layer in ResultTree.columns.test.tsx
            # on a fixture where size/name/server orders are all distinct.)

            # ── Size → numeric ASC vs SQL oracle (observable from server order) ──
            _click_header(page, "size")
            by_size = _rendered_basenames(page)
            sql_order = _sizes_asc_from_sqlite(db_path)
            print(f"probe_status: s45 baseline={baseline} by_size={by_size} sql={sql_order}")
            if by_size == baseline:
                failures.append(
                    f"Size click did not reorder the rows (still server order "
                    f"{baseline!r}) — col-header-size is a no-op / dead handler."
                )
            if by_size != sql_order:
                failures.append(
                    f"Size ASC: observed={by_size!r} != sql_oracle={sql_order!r} "
                    f"— the size comparator is not producing numeric order "
                    f"(lexicographic?) or clicked the wrong header."
                )

            # ── File Name → ascending (observable transition from size order) ──
            _click_header(page, "name")
            ascending = _rendered_basenames(page)
            expected_asc = sorted(baseline, key=str.lower)
            if ascending == by_size:
                failures.append(
                    f"File Name click did not reorder away from the size order "
                    f"{by_size!r} — col-header-name is a no-op / dead handler."
                )
            if ascending != expected_asc:
                failures.append(
                    f"File Name ASC: observed={ascending!r} != "
                    f"expected={expected_asc!r} — the name comparator is wrong."
                )

            # ── File Name again → descending (toggle observable) ───────────
            _click_header(page, "name")
            descending = _rendered_basenames(page)
            expected_desc = list(reversed(expected_asc))
            if descending == ascending:
                failures.append(
                    f"Second File Name click did not flip direction (still "
                    f"{ascending!r}) — the asc↔desc toggle is broken."
                )
            if descending != expected_desc:
                failures.append(
                    f"File Name DESC: observed={descending!r} != "
                    f"expected={expected_desc!r} — repeat click did not flip order."
                )

            # ── In-session persistence: reopen the same manifest ───────────
            # The persisted order (name-DESC) differs from the server order, so a
            # sort-state reset on reload would revert to server order and be caught
            # (asserted explicitly below so the reopen step can't pass vacuously).
            pre_reopen = descending
            if pre_reopen == baseline:
                failures.append(
                    f"pre-reopen order == server order {baseline!r}: the reopen "
                    f"assertion would be vacuous — expected a sorted order that "
                    f"differs from the server order."
                )
            load_manifest(page, db_path)
            _wait_rows(page)
            page.wait_for_timeout(150)
            post_reopen = _rendered_basenames(page)
            print(f"probe_status: s45 pre_reopen={pre_reopen} post_reopen={post_reopen}")
            if post_reopen != pre_reopen:
                failures.append(
                    f"sort not preserved across in-session manifest reopen: "
                    f"pre={pre_reopen!r} != post={post_reopen!r} — loadManifest "
                    f"reset the resultView sort state."
                )

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s45_sort_persistence DONE")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
