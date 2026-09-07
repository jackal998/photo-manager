# Web-port goal audit — 2026-07-16

Full item-by-item audit of the web port against every originally-committed goal,
contract, and parity claim, ordered by the owner before personal soak testing.
Audited tree: integration tip `6d5e3ce` (docs/web-port-feasibility; worktree
`3db6921` verified tree-identical). Method: goal extraction from the 5 source
documents + epics #641–#647 + #744 into a 572-line checklist, preserved at
[`web-port-goal-checklist-2026-07-16.md`](web-port-goal-checklist-2026-07-16.md)
(the traceable ground-truth extraction, with source anchors for every
commitment/claim), then four independent audit domains (design contracts —
opus; business logic — opus; UI/UX surface — sonnet; parity-claim recheck —
sonnet), findings requiring file:line + concrete user-visible failure
scenario. The four domain reports were session-local working artifacts;
their findings are fully incorporated into this report and are not
preserved separately.

## Verdict at a glance

| Domain | Result |
|---|---|
| Business-logic parity (8 core flows, Qt↔web) | **8/8 ALIGNED, zero divergence** |
| 5 keystone contracts | **5/5 PASS** |
| 16 binding corrections | 12 PASS · 1 SUPERSEDED · **3 VIOLATED** (2 real, 1 doc-only) |
| UI/UX surface | **1 BROKEN · 1 high-severity copy GAP · 1 systemic i18n GAP · 1 DRIFT**; state-refresh + dead-control + label sweeps otherwise clean |
| Parity-matrix claims (51 issues + 61 rows) | 38 CONFIRMED · 9 STALE (doc lag, mostly good-direction) · **1 FALSE-CLAIM (evidence error, verdict unchanged)** · 15/15 thin-evidence rows genuinely implemented |
| Scenario ledger (71) | 67 done, 4 skip — all 4 skips re-justified |
| Owner-endorsed omissions | intact & documented; 1 entry stale (List menu since SHIPPED via #776) |

**Why business-logic parity holds structurally:** both UIs converge on the same
primitives — scan builds `ScanConfig` → shared `core.app_service.scan_runner.run_pipeline`;
every decision/lock/execute/prune mutation lands in the same `ManifestRepository`
methods. The web service layer is a #NNN-tagged port of Qt's `file_operations.py`
orchestration; the reimplementation seam was checked flow-by-flow.

Key owner-concern confirmations:
- **Persistence timing ALIGNED**: Qt writes decisions to SQLite immediately per
  handler call (`file_operations.py:973`; dirty flag is UX-only, `:251`). Web's
  synchronous `PATCH /api/decision` is the identical semantics. No Save button
  needed — moot, not a gap.
- **Execute ALIGNED**: recycle-bin only (send2trash) both sides, same
  `outcome` writes, same `delete_*.csv` via shared `write_delete_log`, same
  locked-row + complete-group-delete gates.
- **s72 / ctx-apply-best-copy contradiction RESOLVED** (triple-verified,
  incl. `merge-base --is-ancestor`): the design doc's "no-op stub" note
  (tech-design §5.7, `web-port-tech-design.md:2076`) was true when written
  (2026-07-11 morning) and is now STALE; the implementation
  (`action_service.py:266-405` → route → store → ContextMenu, s72 asserts
  durable SQLite writes across reload+reopen) is real. scenario_map's `done` is
  accurate.

## Findings — fix before owner soak (severity order)

1. **[copy, HIGH] LockConfirmDialog claims permanent deletion.**
   `frontend/src/components/LockConfirmDialog.tsx:161` says files are "about to
   be permanently DELETED"; actual semantics are recycle-bin (send2trash).
   Same lie-class as #407–#410. Only outlier in a full-frontend grep.
2. **[bug, MED] "Open folder" failures are silent outside the Execute dialog.**
   `ContextMenu.tsx:150-153` → `store.revealInExplorer` writes failures to
   `execute.executeError` (`useAppStore.ts:987-996`) whose only consumer is
   `ExecuteDialog.tsx:142,501-503` (mounted only while `executeOpen`).
   Right-click → Open folder → API failure from the main tree shows nothing.
3. **[contract #14, MED — cutover-gate hole] hard-coded scenario target.**
   `scripts/check_qa_parity.py:78` `PHASE_TARGETS[4] = 67` vs live total 71
   (67 done + 4 skip); passes today by coincidence. Any future `todo` scenario
   lets the phase-4 cutover gate pass with it silently unported — the exact
   hole correction #14 ("derive at runtime") was supposed to close. Never
   actually implemented. (Independently found by two domains.)
4. **[contract #11, MED-conditional] stale preview via cache-derived ETag.**
   `app/web/routes/image.py:101-103` ETags the disk-cache file (mtime+size);
   cache key is `sha1(path|size)` (`image_service.py:244`). Overwrite a photo
   in place (same path+size) → old bytes + unchanged ETag → browser 304s the
   stale image indefinitely (`image_service.py:488-493`). Binding fix required
   source-mtime freshness; only the one-formula half landed. **Benign iff the
   library is treated as immutable — owner to confirm the assumption or order
   the fix.**
5. **[i18n, MED, systemic] 8 components hardcode English**, bypassing `t()`:
   both context menus (main tree + Execute dialog), LockConfirmDialog,
   PruneConfirmDialog, RemoveFromListConfirmDialog, SettingsDialog,
   ExecuteDialog chrome, part of FullResViewer. zh_TW users get a translated
   menu bar but all-English right-click menus and confirm dialogs for the same
   verbs. (Existing 77 `t('web.*')` callsites: en/zh_TW catalogs both complete,
   zero asymmetry.)
6. **[i18n, LOW-MED] DeleteConfirmDialog translates only 1 of 3 branches** —
   the "entire group(s) will be deleted" and "delete all files" destructive
   branches are hardcoded English.
7. **[low] Correction #8 residual**: runtime STA fail-loud assert present
   (`image_service.py:730`); companion static probe (WIC-sync-only-via-executor)
   absent. Correctness covered at runtime.

## Documentation corrections (no code risk, keep ledgers honest)

- tech-design §5.7: delete/update the stale "no-op stub" note for
  ctx-apply-best-copy (`web-port-tech-design.md:2076`).
- tech-design "~15 sites" wording residue (correction #4) — moot in practice;
  reconcile or annotate.
- Parity matrix STALE rows: #662 (CSRF shipped + issue closed 2026-07-16; NOTE
  the allowed_roots/trust-boundary sub-item is resolved-by-documented-decision
  in the tech-design security addendum, owner did not veto at PR #777 review —
  if the owner instead wants it tracked as work, file a follow-up issue),
  #658/#653 (fixed + closed), #678 (D shipped; closed 2026-07-16), #673 (List
  sub-item shipped), #744 (3 more commits incl. apply-best-copy; divergence-list
  artifact still the open deliverable), endorsed-omission entry for List menu
  ("scheduled" → shipped #776).
- Parity matrix #737 evidence error (FALSE-CLAIM): "no cache found" was wrong
  even at audit time — `transcode_service.py:96,104` predates the baseline.
  The "partial" categorization itself stands; fix the evidence text.
- Non-finding for the record: singleton-group handling differs architecturally
  (web orphan-skips + SQL-classified prune; Qt retains in transient vm) —
  outcome-aligned; live post-scan run would fully characterize the intermediate
  display. Set-Action fields: Score/Resolution are plain field-array entries
  while Lock has dedicated wiring — matrix's uniformity framing slightly
  overstated, no defect.

## What static audit cannot decide (live-run list)

- Rendered-layer issues (visual layout, Radix behavior under a real browser):
  covered by running the full `qa/web/_batch` scenario suite against a live
  isolated-home server; recommended as the mechanical gate before owner soak.
- Singleton-group intermediate display characterization (above).
- #737 HEVC first-byte latency: user-present NAS probe (pre-existing cutover
  gate, unchanged by this audit).

## Bottom line

The feared failure mode — business-logic/UX drift against the original
product — **did not materialize**: 8/8 flows aligned, all keystone contracts
hold, all 15 thin-evidence parity claims real, scenario ledger honest. The
defects that do exist are few, concrete, and enumerable: one wrong destructive-
action message, one silent error path, one cutover-gate hole, one conditional
stale-cache bug, and a systematic i18n gap on right-click/confirm surfaces.
All are small fixes. Recommendation: land findings 1–3 (+5/6 if zh_TW soak
matters) before the owner spends personal testing time; decide 4 by declaring
the immutability assumption; then run the live scenario batch as the final
mechanical gate.
