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
| D6 | Execute Action dialog — window geometry persistence | `QDialog.saveGeometry()`/`QSettings` round-trip restores dialog size/position across launches | **RESOLVED (#739)** — movable by its title bar, resizable by a corner grip, `{x,y,w,h}` persisted per browser and re-clamped into the viewport on every open | resolved | Shipping anchors: `frontend/src/hooks/useOverlayGeometry.ts`, `frontend/src/lib/overlayGeometry.ts`, `frontend/src/components/execute/ExecuteDialog.tsx` (`execute-title-bar`, `execute-resize-handle`, key `pm.overlay-geometry.execute.v1`); `qa/web/scenarios/s48_dialog_geometry_persist.py`; `docs/features.md` "Web — overlay window geometry" | N/A — resolved, shipped #739 (owner FIX 2026-07-17); no longer a divergence |
| D7 | Set Action dialog — window geometry persistence | Same `QSettings` geometry round-trip as other Qt dialogs | **RESOLVED (#739)** — same mechanism as D6, its own storage key | resolved | Shipping anchors: `frontend/src/components/action/ActionDialog.tsx` (`action-title-bar`, `action-resize-handle`, key `pm.overlay-geometry.action.v1`); `qa/web/scenarios/s48_dialog_geometry_persist.py` (asserts the two keys hold different rects, so one shared key cannot pass); `docs/features.md` "Web — overlay window geometry" | N/A — resolved, shipped #739 (owner FIX 2026-07-17); no longer a divergence |
| D8 | Main window — window geometry + splitter persistence (chrome half) | `MainWindow.closeEvent` saves window position/size/maximize-state + splitter ratio, restores on next launch | No browser API to move/query the browser tab's own chrome; the **splitter** half was re-flipped into its own web-native feature (preview-panel resizable width, parity — see `docs/features.md:386`) | n-a-by-design | `qa/web/scenario_map.yml` s39_window_geometry_persist note: "window chrome belongs to the browser, not the app"; parity matrix T1B row "Main window — geometry+splitter persistence" | SIGNED (owner 2026-07-17: keep — window chrome belongs to the browser; the splitter half already shipped web-native, features.md:386) |
| D9 | Main window — dirty-flag exit prompt | Closing with unsaved decisions raises a Save/Discard/Cancel prompt | No dirty state exists to protect (decisions persist synchronously per-`PATCH`) and no interceptable custom close dialog in a browser tab | n-a-by-design | `qa/web/scenario_map.yml` s28_exit_dirty_prompt: "SKIP — desktop-only (#688) … no web analog for the Save/Back branches"; parity matrix T1B row "Exit dirty-flag prompt" | SIGNED (owner 2026-07-17: keep — synchronous per-PATCH persistence removes the dirty state the prompt existed to protect) |
| D10 | Main window — row-selection horizontal auto-scroll suppression | A historical Qt `QTreeView.autoScroll` bug scrolled the tree horizontally on selection change; suppressed as a fix | No virtualizer/programmatic scroll-on-select exists in the web tree at all — the bug class the Qt fix targets doesn't exist on this stack | n-a-by-design | Grep of `frontend/src/components/result/*` for `autoScroll\|scrollIntoView\|scrollToItem\|react-window` — zero hits; parity matrix T1B row "Main window — row selection, no horizontal auto-scroll" | SIGNED (owner 2026-07-17: keep — the Qt bug class this fix targeted does not exist on the web stack) |
| D11 | Developer tooling — memory probe | `scripts/memory_probe.py` instruments `QStandardItem`/`QImage` allocation counts via a Qt `destroyed` signal | Removed by design during the port — no PySide6/Qt-heap concept exists in a browser/Node runtime | n-a-by-design | `docs/design/web-port-tech-design.md:1318` ("probe is Qt-specific," explicit removal note); zero `memory_probe`/`MEMORY_PROBE` hits under `frontend/src` or `app/web` | SIGNED (owner 2026-07-17: keep — browser DevTools memory/performance panels are the native replacement) |
| D12 | Preview pane — full-resolution viewer window chrome | Full-res view opens as a resizable/movable modal window (`app/views/dialogs/full_res_viewer.py`) | **RESOLVED (#739)** — `FullResViewer.tsx` still OPENS filling the viewport (unchanged first open), but its title bar now drags (un-maximizing to a floating window) and its corner grip resizes; geometry persists and is clamped back into view on every open | resolved | Shipping anchors: `frontend/src/components/FullResViewer.tsx` (`fullres-title-bar`, `fullres-resize-handle`, key `pm.overlay-geometry.fullres.v1`), the shared `useOverlayGeometry`/`overlayGeometry` pair D6/D7 use; `qa/web/scenarios/s39_layout_persist.py`; `docs/features.md` "Web — overlay window geometry" | N/A — resolved, shipped #739 (owner FIX 2026-07-17); no longer a divergence |
| D13 | Context menu — Apply best-copy decisions (group), `match_confidence` scope | Removed from Qt entirely (PR #224); Qt's substitute is the Set Action numeric "top 1 by score" condition, which has no confidence gate either | Web-only re-introduction (#744) applies to every scored row in a group with **no** `match_confidence` filter — broader than the scan-time auto-select gate (#517/#536), which excludes `"low"`-confidence near-dups | accepted-divergence | `docs/features.md:744` ("Deliberately NO `match_confidence` filtering … owner-accepted #744 scope decision, not a gap"); `core/app_service/action_service.py`; `qa/web/scenarios/s72_apply_best_copy.py` | SIGNED (`docs/features.md:744`, owner-accepted #744 scope decision) |
| D14 | Result tree — singleton-group display architecture | Retains a singleton candidate in the transient view-model after its group collapses to one member | Orphan-skips + reclassifies via SQL-driven prune candidacy (`PruneConfirmDialog` flow) instead of a transient-VM retain | accepted-divergence | Goal audit "non-finding for the record": "singleton-group handling differs architecturally … outcome-aligned; live post-scan run would fully characterize the intermediate display" (`docs/audits/web-port-goal-audit-2026-07-16.md:107-109`) | SIGNED (owner 2026-07-17: keep — outcome-aligned suffices; revisit only if soak surfaces odd intermediate display) |
| D15 | Set Action dialog — field-wiring uniformity (Score/Resolution vs Lock) | N/A (Qt-side framing only) | Score and Resolution are plain entries in the shared field array; Lock has dedicated wiring/handling — the parity matrix's "uniformity" framing read this as a divergence | n-a-by-design | Goal audit: "Set-Action fields: Score/Resolution are plain field-array entries while Lock has dedicated wiring — matrix's uniformity framing slightly overstated, **no defect**" (`docs/audits/web-port-goal-audit-2026-07-16.md:110-112`) | N/A — audit self-correction, not a real divergence |
| D16 | Result tree — within-group secondary sort by score | Applies a secondary sort by score descending under any user-chosen column sort | `makeRowComparator` (`frontend/src/lib/resultColumns.ts:103`) has only `name`/`size` comparators — no score tiebreak | open-polish | Issue [#743](https://github.com/jackal998/photo-manager/issues/743) sub-item 1; parity matrix census T2-1 #743 evidence: "`resultColumns.ts:84-113` … only has `name`/`size` comparators, no score tiebreak" | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D17 | Result tree — group-header double-click expand/collapse | Double-clicking a group header toggles expand/collapse (in addition to single-click paths) | `GroupRow.tsx:37` wires `onClick={onToggle}` only — no `onDoubleClick` handler | open-polish | Issue #743 sub-item 2; `frontend/src/components/result/GroupRow.tsx:37` (verified live — only `onClick` present, no `onDoubleClick`) | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D18 | Result tree — empty-area (tree background) right-click menu | Right-clicking the empty tree background opens a distinct context menu | `ResultTree.tsx` wires `onContextMenu` only on `GroupRow`/`FileRow` (lines 347, 364) — never on the scroll-container/background | open-polish | Issue #743 sub-item 3; `frontend/src/components/ResultTree.tsx` (verified live — `onContextMenu` present only on row/group targets, not the outer container); parity matrix's separate "Empty area context menu" row (T1A) covers a *different*, already-parity surface (menu-bar gap absence) — this row is the 743-specific tree-background click target | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D19 | Result tree — roving arrow-key focus | ArrowDown/ArrowUp move focus/selection through the tree after manifest load (Qt s26 steps 1/3), selection preserved across model rebuilds | No arrow-key navigation exists — `ResultTree.tsx` has zero `ArrowDown`/`ArrowUp`/`activedescendant` handling; rows are non-focusable virtualized `<div>`s | open-polish | Issue [#709](https://github.com/jackal998/photo-manager/issues/709); verified live — zero matches for `ArrowDown\|ArrowUp\|activedescendant` in `frontend/src/components/ResultTree.tsx` | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) |
| D20 | Prune flow QA — Remove-after-Unlock&Apply live coverage | N/A (QA-coverage divergence, not a UI behavior gap) | **RESOLVED (#713 = #702 item 4)** — `s61`'s lock branch no longer hardcodes `PRUNE_BTN_KEEP`: the new `lock_apply_remove` variant drives Unlock & Apply then **Remove**, so the Remove-with-non-empty-`lockedToPrune` path and the `onOpenChange` double-`applyPrune` guard are now driven live, with both singletons asserted to finalize at `outcome='ignored'` | resolved | Shipping anchors: `qa/web/scenarios/s61_actioned_singleton_prune.py` (variant `lock_apply_remove` in `run()`; the lock branch now dispatches on `prune_action`); `qa/web/scenario_map.yml` (s61 notes, seven variants); `docs/testing.md` (s61 row). Live-verified before the label was pinned: the lock-resolved dialog is **actioned-only**, so `isMixed` is false and **no** `PRUNE_INCLUDE_ACTIONED` checkbox is rendered (the issue's How assumed one) — Remove itself opts the actioned bucket in and the observed label is `"Remove 1"`, counting ACTIONED not plain | N/A — resolved, shipped #713 (+#702 item 4); no longer a divergence |
| D21 | Result tree — sticky-header virtualizer coordinate offset + tall-manifest scroll coverage | N/A (web-native virtualization concern; no Qt analog) | **RESOLVED (#699)** — `ResultTree` measures the sticky `ColumnHeaderRow`'s rendered height (ref + layout effect + `ResizeObserver`, never a constant) and passes it to `useVirtualizer` as `scrollMargin`, positioning rows at `start - scrollMargin` so the on-screen layout is unchanged while the windowing coordinates finally equal the scroll container's own; s47 gained a synthetic 30-group / 60-row manifest — the web suite's only fixture taller than the viewport — that scrolls the tree and asserts the coordinates agree | resolved | Shipping anchors: `frontend/src/components/ResultTree.tsx` (measurement effect, `scrollMargin` option, `data-scroll-margin` on the tree root, `translateY(start - scrollMargin)`), `frontend/src/components/result/ColumnHeaderRow.tsx` (root `ref` + `result-col-header-row` testid), `frontend/src/components/ResultTree.scrollMargin.test.tsx`, `qa/web/scenarios/s47_column_layout_persist.py` (#699 phase), `docs/features.md` "Web — result-tree column sort and resize persistence". Live evidence: on the pre-fix build every rendered row sits 25.00px from its virtualizer coordinate (at scrollTop 0, mid-scroll, and back at top); 0.00px after | N/A — resolved, shipped #699 (owner SIGNED 2026-07-17 as tracked-polish); no longer a divergence |
| D22 | Manifest API — per-dimension scoring signal exposure | `qa/scenarios/s42_scoring.py` asserts per-dimension penalty signals (`gps_present`, filename/path regex penalties) in addition to composite score | **RESOLVED (#680)** — `gps_present` / `exif_tag_count` / `xmp_derived` now cross the API boundary in every `FileRow`, and the web s42 driver asserts them through `GET /api/manifest` instead of deferring. No SQLite backdoor: the columns (written since #187 but never read back) are threaded `_LOAD_ALL_SQL` → `_photo_record` → `PhotoRecord` → `_build_file_row` → `FileRow`. Assertion scope now equals desktop | resolved | Shipping anchors: `core/app_service/review_view.py:_build_file_row`, `infrastructure/manifest_repository.py:_LOAD_ALL_SQL` + `_photo_record`, `core/models.py:PhotoRecord`, `frontend/src/api/types.ts:44-46`; `qa/web/scenarios/s42_scoring.py` ("Assert 3: per-dimension scoring signals") + its `qa/web/scenario_map.yml` note; `docs/features.md` "Web — per-dimension signals on the manifest API (#680)" | SIGNED (owner 2026-07-17: keep as tracked-polish, non-blocking for cutover; work remains tracked in its cited issue) — **N/A as of 2026-09-05: the tracked polish shipped (#680), so this is no longer a divergence** |
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
  scoped by owner decision. **SHIPPED 2026-09-04 (#739)** — see the row above
  for the anchors.
- **D6, D7 → FIX**: the Execute and Set Action dialogs adopt the same
  drag/resize/persist mechanism built for D12 (reclassed from n-a-by-design
  after the owner's retarget question established DOM dialogs *can* do this;
  "not built" ≠ "not possible"). **SHIPPED 2026-09-04 (#739)**, on the one
  mechanism the owner asked for: `frontend/src/hooks/useOverlayGeometry.ts` +
  `frontend/src/lib/overlayGeometry.ts`, applied to all three surfaces.

**Status of the three FIX rows (2026-09-04):** all three are now `resolved`;
no row in this list is OPEN or outstanding. The class-summary table above
records the state at sign-off time (2026-07-17) and is deliberately left as
that historical snapshot — the per-row Class column carries the current
state.
- **D5, D8–D11 → SIGNED keep**: genuine browser-architecture limits (D5, D8
  chrome-half) or needs dissolved by better design / native tooling (D9,
  D10, D11).
- **D14 → SIGNED keep**: outcome-aligned suffices; revisit only if soak
  surfaces odd intermediate display.
- **D16–D24 → SIGNED keep-as-tracked**: confirmed divergences, each tracked
  in its cited issue, jointly non-blocking for Phase-4 cutover.
  **D22 SHIPPED 2026-09-05 (#680)** and is now `resolved` — see the row above
  for the anchors. The owner's 2026-07-17 keep-as-tracked decision stands as
  written; what changed is that the tracked work landed. The remaining rows in
  this bullet are unaffected.

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
