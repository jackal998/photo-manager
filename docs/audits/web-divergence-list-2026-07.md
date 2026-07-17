# Web divergence list — single artifact for #744

## #744's literal deliverable

Issue [#744](https://github.com/jackal998/photo-manager/issues/744) ("reconcile
web UI with the DesignSync Daylight/Aperture prototype — divergence list +
sign-off") asks for a reconciliation pass over the web UI producing "a
divergence list; owner marks each item fix-to-prototype vs sign-off-as-is."
The owner's 2026-07-17 comment on the issue re-scoped the literal ask: the
substance already exists scattered across
[`docs/audits/web-parity-matrix-2026-07.md`](web-parity-matrix-2026-07.md)
(the 70-row features.md sweep + 51-issue census + G1 owner sign-off table)
and [`docs/audits/web-port-goal-audit-2026-07-16.md`](web-port-goal-audit-2026-07-16.md)
(the 2026-07-16 full-tree recheck), but "the issue's literally-named artifact
— a single structured divergence list with per-item keep/fix owner sign-off —
has not been produced as its own document." This file is that document: a
distillation of every known Qt↔web behavioral/UX divergence from those two
audits plus the open issues each audit left tracked, one row per atomic
decision.

## Sign-off protocol

- **SIGNED (source)** — an explicit owner decision already exists on record
  (a commit message recording a declined feature, the G1 owner-endorsement
  table, or a "not a gap" scope decision documented in `docs/features.md`).
  No further action needed unless the owner wants to revisit it.
- **OPEN** — no owner decision exists yet. The owner marks each OPEN row
  **keep** (accept the divergence as permanent/by-design) or **fix** (points
  at the issue that should do the work, or asks for one to be filed).
- **FIX** — the owner chose fix-to-Qt-behaviour (or a web-native retarget);
  the cited issue tracks the implementation work.
- **N/A** — the row documents an audit finding that turned out **not** to be
  a real divergence (an audit self-correction), so there is nothing to sign
  off on.

No sign-off is invented below — see individual **Sign-off** cells for the
literal source of every SIGNED entry.

## Divergence table

| ID | Surface | Qt behaviour | Web behaviour | Class | Evidence | Sign-off |
|---|---|---|---|---|---|---|
| D1 | Scan dialog — inline source-path validation | `_on_add_typed` gate validates a typed path before Add, inline | No pre-submit path check; an invalid path surfaces only via the post-submit SSE `failed` event | endorsed-omission | Declined in commit `eac87ca` ("avoids a filesystem-disclosure endpoint for a timing-only nicety"); `qa/web/scenarios/s38_scan_dialog_invalid_path.py:34-40`; `docs/features.md:792`; parity matrix G1 #1 | SIGNED (commit `eac87ca`, G1 table #1) |
| D2 | Menu bar — List → Remove from List entry | Top-level List menu with a Remove-from-List item | Was omitted at menu-bar level (context menu + multi-select covered the action); **now shipped** | endorsed-omission (resolved) | `frontend/src/components/MenuBar.tsx:157-170` (`MENU_LIST` dropdown, live); shipping commit `506735f` (#678-D / #776); parity matrix G1 #2 originally read "scheduled … instead of endorsed," closed by the 2026-07-16 re-audit delta | N/A — resolved, shipped #776; no longer a divergence |
| D3 | Menu bar — Log menu | File-menu-adjacent Log menu (live log viewer / diagnostics) | No Log menu; live scan log is covered by the in-dialog scan-progress log pane, no `/api/log*` endpoint exists | endorsed-omission | `frontend/src/components/MenuBar.tsx:13`; `qa/web/scenario_map.yml` s18_log_menu ("SKIP — no web equivalent surface"); parity matrix G1 #3 | SIGNED (G1 table #3) |
| D4 | Menu bar — Save Manifest Decisions entry | Explicit File → Save Manifest Decisions… menu action with a dirty-flag prompt | No menu entry; decisions persist synchronously per `PATCH /api/decision` (backend `POST /api/save` exists but has no UI trigger) | endorsed-omission | `frontend/src/components/MenuBar.tsx:10-12`; `qa/web/scenario_map.yml` s12_save_manifest (status done, reframed); parity matrix G1 #4 | SIGNED (G1 table #4) |
| D5 | Menu bar — File → Exit | File → Exit menu action with a dirty-flag close prompt | No Exit menu item — a browser tab has no application-level quit action | n-a-by-design | `frontend/src/components/MenuBar.tsx:14` ("File → Exit: N/A for a browser tab") | SIGNED (owner 2026-07-17: keep — a web page cannot close a user-opened tab) |
| D6 | Execute Action dialog — window geometry persistence | `QDialog.saveGeometry()`/`QSettings` round-trip restores dialog size/position across launches | Radix `<Dialog>` in a fixed-layout browser viewport; no OS-level resizable window, nothing to persist | open-polish | `qa/web/scenario_map.yml` s48 note: "Web dialogs are Radix/DOM overlays with no user-resizable geometry and no persistence contract"; parity matrix T1B row "Execute Action — dialog geometry persistence" | FIX (owner 2026-07-17): ride the #739 drag/resize/persist mechanism (see D12) |
| D7 | Set Action dialog — window geometry persistence | Same `QSettings` geometry round-trip as other Qt dialogs | Same Radix-overlay reasoning as D6 — no browser-native window-geometry API to hook | open-polish | Grep of `frontend/src/components/action/` for `geometry\|resizable\|localStorage` — zero hits; parity matrix T1D row "Set Action dialog — geometry persistence" | FIX (owner 2026-07-17): ride the #739 drag/resize/persist mechanism (see D12) |
| D8 | Main window — window geometry + splitter persistence (chrome half) | `MainWindow.closeEvent` saves window position/size/maximize-state + splitter ratio, restores on next launch | No browser API to move/query the browser tab's own chrome; the **splitter** half was re-flipped into its own web-native feature (preview-panel resizable width, parity — see `docs/features.md:386`) | n-a-by-design | `qa/web/scenario_map.yml` s39_window_geometry_persist note: "window chrome belongs to the browser, not the app"; parity matrix T1B row "Main window — geometry+splitter persistence" | SIGNED (owner 2026-07-17: keep — window chrome belongs to the browser; the splitter half already shipped web-native, features.md:386) |
| D9 | Main window — dirty-flag exit prompt | Closing with unsaved decisions raises a Save/Discard/Cancel prompt | No dirty state exists to protect (decisions persist synchronously per-`PATCH`) and no interceptable custom close dialog in a browser tab | n-a-by-design | `qa/web/scenario_map.yml` s28_exit_dirty_prompt: "SKIP — desktop-only (#688) … no web analog for the Save/Back branches"; parity matrix T1B row "Exit dirty-flag prompt" | SIGNED (owner 2026-07-17: keep — synchronous per-PATCH persistence removes the dirty state the prompt existed to protect) |
| D10 | Main window — row-selection horizontal auto-scroll suppression | A historical Qt `QTreeView.autoScroll` bug scrolled the tree horizontally on selection change; suppressed as a fix | No virtualizer/programmatic scroll-on-select exists in the web tree at all — the bug class the Qt fix targets doesn't exist on this stack | n-a-by-design | Grep of `frontend/src/components/result/*` for `autoScroll\|scrollIntoView\|scrollToItem\|react-window` — zero hits; parity matrix T1B row "Main window — row selection, no horizontal auto-scroll" | SIGNED (owner 2026-07-17: keep — the Qt bug class this fix targeted does not exist on the web stack) |
| D11 | Developer tooling — memory probe | `scripts/memory_probe.py` instruments `QStandardItem`/`QImage` allocation counts via a Qt `destroyed` signal | Removed by design during the port — no PySide6/Qt-heap concept exists in a browser/Node runtime | n-a-by-design | `docs/design/web-port-tech-design.md:1318` ("probe is Qt-specific," explicit removal note); zero `memory_probe`/`MEMORY_PROBE` hits under `frontend/src` or `app/web` | SIGNED (owner 2026-07-17: keep — browser DevTools memory/performance panels are the native replacement) |
| D12 | Preview pane — full-resolution viewer window chrome | Full-res view opens as a resizable/movable modal window (`app/views/dialogs/full_res_viewer.py`) | `FullResViewer.tsx` is a fixed fullscreen `DialogPrimitive` overlay — not resizable or movable | accepted-divergence (pending sign-off) | `docs/features.md:396` ("awaits the issue owner's explicit sign-off (the issue's second ask) before any work starts"); issue [#739](https://github.com/jackal998/photo-manager/issues/739) | FIX (owner 2026-07-17): make FullResViewer draggable/resizable with persisted geometry — #739 tracks the work; D6/D7 ride the same mechanism |
| D13 | Context menu — Apply best-copy decisions (group), `match_confidence` scope | Removed from Qt entirely (PR #224); Qt's substitute is the Set Action numeric "top 1 by score" condition, which has no confidence gate either | Web-only re-introduction (#744) applies to every scored row in a group with **no** `match_confidence` filter — broader than the scan-time auto-select gate (#517/#536), which excludes `"low"`-confidence near-dups | accepted-divergence | `docs/features.md:744` ("Deliberately NO `match_confidence` filtering … owner-accepted #744 scope decision, not a gap"); `core/app_service/action_service.py`; `qa/web/scenarios/s72_apply_best_copy.py` | SIGNED (`docs/features.md:744`, owner-accepted #744 scope decision) |
| D14 | Result tree — singleton-group display architecture | Retains a singleton candidate in the transient view-model after its group collapses to one member | Orphan-skips + reclassifies via SQL-driven prune candidacy (`PruneConfirmDialog` flow) instead of a transient-VM retain | accepted-divergence | Goal audit "non-finding for the record": "singleton-group handling differs architecturally … outcome-aligned; live post-scan run would fully characterize the intermediate display" (`docs/audits/web-port-goal-audit-2026-07-16.md:107-109`) | SIGNED (owner 2026-07-17: keep — outcome-aligned suffices; revisit only if soak surfaces odd intermediate display) |
| D15 | Set Action dialog — field-wiring uniformity (Score/Resolution vs Lock) | N/A (Qt-side framing only) | Score and Resolution are plain entries in the shared field array; Lock has dedicated wiring/handling — the parity matrix's "uniformity" framing read this as a divergence | n-a-by-design | Goal audit: "Set-Action fields: Score/Resolution are plain field-array entries while Lock has dedicated wiring — matrix's uniformity framing slightly overstated, **no defect**" (`docs/audits/web-port-goal-audit-2026-07-16.md:110-112`) | N/A — audit self-correction, not a real divergence |
| D16 | Result tree — within-group secondary sort by score | Applies a secondary sort by score descending under any user-chosen column sort | `makeRowComparator` (`frontend/src/lib/resultColumns.ts:103`) has only `name`/`size` comparators — no score tiebreak | open-polish | Issue [#743](https://github.com/jackal998/photo-manager/issues/743) sub-item 1; parity matrix census T2-1 #743 evidence: "`resultColumns.ts:84-113` … only has `name`/`size` comparators, no score tiebreak" | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D17 | Result tree — group-header double-click expand/collapse | Double-clicking a group header toggles expand/collapse (in addition to single-click paths) | `GroupRow.tsx:37` wires `onClick={onToggle}` only — no `onDoubleClick` handler | open-polish | Issue #743 sub-item 2; `frontend/src/components/result/GroupRow.tsx:37` (verified live — only `onClick` present, no `onDoubleClick`) | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D18 | Result tree — empty-area (tree background) right-click menu | Right-clicking the empty tree background opens a distinct context menu | `ResultTree.tsx` wires `onContextMenu` only on `GroupRow`/`FileRow` (lines 347, 364) — never on the scroll-container/background | open-polish | Issue #743 sub-item 3; `frontend/src/components/ResultTree.tsx` (verified live — `onContextMenu` present only on row/group targets, not the outer container); parity matrix's separate "Empty area context menu" row (T1A) covers a *different*, already-parity surface (menu-bar gap absence) — this row is the 743-specific tree-background click target | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D19 | Result tree — roving arrow-key focus | ArrowDown/ArrowUp move focus/selection through the tree after manifest load (Qt s26 steps 1/3), selection preserved across model rebuilds | No arrow-key navigation exists — `ResultTree.tsx` has zero `ArrowDown`/`ArrowUp`/`activedescendant` handling; rows are non-focusable virtualized `<div>`s | open-polish | Issue [#709](https://github.com/jackal998/photo-manager/issues/709); verified live — zero matches for `ArrowDown\|ArrowUp\|activedescendant` in `frontend/src/components/ResultTree.tsx` | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D20 | Prune flow QA — Remove-after-Unlock&Apply live coverage | N/A (QA-coverage divergence, not a UI behavior gap) | The `onInteractOutside` double-`applyPrune` guard and the Remove-with-non-empty-`lockedToPrune` code path are unit-covered but never driven live — `s61`'s lock branch hardcodes `PRUNE_BTN_KEEP` on every lock variant (`qa/web/scenarios/s61_actioned_singleton_prune.py:263`), so no live scenario exercises clicking Remove after Unlock & Apply | open-polish | Issue [#713](https://github.com/jackal998/photo-manager/issues/713); duplicate of issue #702 item 4 — parity matrix explicitly notes "#702 item 4 ≡ #713 (same s61 lock-branch variant) — one gap, not two" | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D21 | Result tree — sticky-header virtualizer coordinate offset + tall-manifest scroll coverage | N/A (web-native virtualization concern; no Qt analog) | `useVirtualizer` (`ResultTree.tsx:159`) has no `scrollMargin`, so its windowing math treats content as starting at `scrollTop=0` while the actual list origin sits ~one header-height lower — masked today by `overscan:10` but incorrect; no qa scenario scans a manifest tall enough to scroll (largest fixture is 5 rows) | open-polish | Issue [#699](https://github.com/jackal998/photo-manager/issues/699); verified live — zero `scrollMargin`/`paddingStart` hits in `frontend/src`, `useVirtualizer` call at `ResultTree.tsx:159` confirmed to omit the option | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D22 | Manifest API — per-dimension scoring signal exposure | `qa/scenarios/s42_scoring.py` asserts per-dimension penalty signals (`gps_present`, filename/path regex penalties) in addition to composite score | `core/app_service/review_view.py:_build_file_row` (line 122) serializes only the composite `score`; `gps_present`/`exif_tag_count`/`xmp_derived` never cross the API boundary into web `FileRow` — narrower QA-equivalence assertion scope than desktop | open-polish | Issue [#680](https://github.com/jackal998/photo-manager/issues/680); verified live — zero hits for the three field names in `review_view.py` and `frontend/src/api/types.ts`; `qa/web/scenarios/s42_scoring.py` docstring self-documents the deferral | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D23 | Preview/cache — per-device request coordination + prefetch (#622 Phase 2) | N/A (new architecture, not a Qt-side behavior being ported) | Viewport-bounded byte-budget cache (#622 Phase 1) shipped; the `PreviewRequestCoordinator` per-device serialization/cancellation-token layer and 1-ahead prefetch (Phase 2) do not exist yet | open-polish | Issue [#622](https://github.com/jackal998/photo-manager/issues/622) "How" §Phase 2; verified live — `app/views/preview_coordinator.py` and `infrastructure/device_key.py` do not exist on disk | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D24 | Preview/cache — real-NAS performance verification (#622 Phase 3) | N/A | Phase 1's NAS-bandwidth claims (embedded-JPEG fast path, byte-budget LRU) are architecturally shipped but not yet verified against a real NAS with a 4-tuple citation (probe + SHA + args + JSON) per the project's perf-claim discipline | open-polish | Issue #622 "How" §Phase 3 ("real-NAS verification with 4-tuple citation … per `feedback_perf_claim_must_cite_artifact.md`") | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |

## Row count by class and sign-off state (final — all rows decided 2026-07-17)

| Class | Count | SIGNED | FIX | N/A |
|---|---|---|---|---|
| endorsed-omission | 4 (D1–D4) | 3 (D1, D3, D4) | 0 | 1 (D2, resolved/shipped) |
| n-a-by-design | 6 (D5, D8–D11, D15) | 5 (D5, D8–D11) | 0 | 1 (D15) |
| accepted-divergence | 3 (D12–D14) | 2 (D13, D14) | 1 (D12) | 0 |
| open-polish | 11 (D6, D7, D16–D24) | 9 (D16–D24, keep-as-tracked) | 2 (D6, D7) | 0 |
| **Total** | **24 rows** | **19** | **3** | **2** |

## Owner decision record (2026-07-17, in-chat sign-off session)

Every previously-OPEN row was decided by the owner on 2026-07-17; no row
remains OPEN. The three FIX rows share one mechanism and one tracking issue:

- **D12 → FIX**: FullResViewer becomes a draggable/resizable overlay with
  persisted geometry — the work #739 was already tracking, now unambiguously
  scoped by owner decision.
- **D6, D7 → FIX**: the Execute and Set Action dialogs adopt the same
  drag/resize/persist mechanism built for D12 (reclassed from n-a-by-design
  after the owner's retarget question established DOM dialogs *can* do this;
  "not built" ≠ "not possible").
- **D5, D8–D11 → SIGNED keep**: genuine browser-architecture limits (D5, D8
  chrome-half) or needs dissolved by better design / native tooling (D9,
  D10, D11).
- **D14 → SIGNED keep**: outcome-aligned suffices; revisit only if soak
  surfaces odd intermediate display.
- **D16–D24 → SIGNED keep-as-tracked**: confirmed divergences, each tracked
  in its cited issue, jointly non-blocking for Phase-4 cutover.

## Acceptance evidence

1. **Every OPEN row is decidable keep/fix** — each OPEN row's Web behaviour
   cell states the concrete gap and its Evidence cell points at either an
   open tracking issue (D16–D24) or the specific architectural reason no fix
   is possible (D5–D11), so the owner can answer "keep" or "point at an
   issue" for each without further research.
2. **Every evidence pointer resolves** — verified this pass, not copied
   blind:
   - Commit `eac87ca` exists (`git show --stat eac87ca`, confirmed).
   - `frontend/src/components/MenuBar.tsx:10-18` read directly; lines 10-14
     match D3/D4/D5's citations.
   - `docs/features.md:396` (FullResViewer overlay) and `:744` (keep-best
     breadth) confirmed via `grep -n`.
   - `frontend/src/lib/resultColumns.ts:103` (`makeRowComparator`),
     `frontend/src/components/result/GroupRow.tsx:37` (`onClick={onToggle}`,
     no `onDoubleClick`), and `frontend/src/components/ResultTree.tsx`
     (`useVirtualizer` at line 159, `onContextMenu` only on row/group
     targets, zero `scrollMargin`/`ArrowDown`/`ArrowUp` hits) all read
     directly this pass.
   - `core/app_service/review_view.py` grepped directly — zero
     `gps_present`/`exif_tag_count`/`xmp_derived` hits, `_build_file_row` at
     line 122.
   - `app/views/preview_coordinator.py` and `infrastructure/device_key.py`
     confirmed absent via `ls` (both `#622` Phase 2 deliverables).
   - Issues #744, #743, #739, #709, #713, #699, #702, #680, #622 all
     confirmed live and open via `gh issue view`.
3. **Commit scope** — `git diff --stat` after committing touches only this
   new file (see commit below).

## Commit

```
docs(audit): distill #744 single divergence list with sign-off column
```

`git log -1 --oneline` confirmed the commit landed; `git diff --stat
HEAD~1..HEAD` confirmed the only changed path is
`docs/audits/web-divergence-list-2026-07.md`.
