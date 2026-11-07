# PR-3 design: the three scan modes and the confidence threshold (+ the #1047 aggressive half)

Author: opus planner (read-only), 2026-11-07, master 7d48ebd. Spec: `auto-select-prd-2026-11-v2.md` §2, §5 (:205-214), §11 item 3 (:384-387); owner answers A1, A2, C5, D2, D3, R3.
Lead decisions on top of the design (2026-11-07): Open 1 yes (medium derived from REVIEW_DUPLICATE); Open 3/4/5/6 as leaned; Contradiction 2 resolved as the planner did (no engine exists, so a below-threshold row in aggressive mode stays undecided, never a "best guess"); Open 2: owner chose default "high" (2026-11-07, after the census below).

Test-set census before implementation (`migration_manifest_testset.sqlite`, 2,043 files / 502 groups, scratch probe `pr3-threshold-census.py`, planned level rule applied to action + match_confidence): row levels high 691 / medium 223 / low 278 / none 509 (keepers + Ref-tier). T=high: 263 groups need a look, 239 stamped in auto mode, aggressive deletes 497 -> 316 (181 REVIEW/high non-keeper rows lose eligibility, 54 of them the #1047 "same pHash, pixels differ" rows). T=medium: 148 need a look, 354 stamped, aggressive deletes unchanged at 497 (#517 parity), #1047 rows stay eligible.

## Contradictions with the brief (read first)
1. **Two levels cannot carry a high/medium threshold.** §11 says "confidence is the grouping confidence". `match_confidence` is only high/low (scanner/dedup.py:224). Under two levels, "below high" and "below medium" both select the low rows only. The setting would do nothing, and #1047 could not be fixed: a REVIEW/high row is always admitted. This design derives a medium level from the classifier action (Q2). That departs from the §11 wording.
2. **The PRD's aggressive reading conflicts with #517 and #1047.** §2 (:57) and §5 (:208) give a below-threshold row "the engine's best guess" in aggressive mode. That would make low rows delete-eligible, which #517 forbids (auto_select.py:262-266). #1047 asks for the opposite. This design never marks a below-threshold row delete.
3. **A1 reads "auto-select decides most actions".** Today auto mode only stamps keepers. Non-keepers keep `user_decision=""` (docs/features/scan.md:10). §11 PR-3 does not ask auto mode to write deletes, so this design keeps that. See Open item 4.
4. **"Undecided" and "Keep" are the same stored value** (`""`, auto_select.py:67-73; review_view.py:104-108). So "no decision" in auto mode concretely means no KEEP stamp on the group's keeper. "Needs your look" is therefore a confidence filter, not an undecided-rows filter.
5. **The keeper lock has two side effects the PRD does not name.**
   - R-LOCK-6: a group holding a lock is never dismissed by the skip-directory reconcile (manifest_repository.py:894-907). Auto-select groups lose that protection.
   - R-LOG-1: an auto-only scan now appends nothing to the decision log, because the keeper write is `""` over `""`.
6. **A stale docs sentence.** scan.md:9 and :45 say autotune is the only persisted control in the panel. The cloud opt-in (#1043) already made that false.
7. The text of issue #1047 comes from the lead's paste. `gh issue view` was not run (no shell).

## Data path
```
ScanDialog.tsx  select confirm_below --PATCH--> settings.json scan.auto_select.confirm_below
   | POST /api/scan {auto_select_enabled, _aggressive_delete, auto_select_confirm_below}
   v
WebScanRequest.resolved_with (models.py:287)  None -> settings -> resolve_confirm_below
   v ScanConfig.auto_select_confirm_below (dtos.py:74-77)
scan_runner.run_pipeline :1367  plan_auto_select(rows, aggressive, confirm_below)
   | stamped keepers (action=KEEP), deletes            [auto_select.py, pure]
   v
write_manifest :180  build_auto_select_writes(..., lock_keepers=False)  no lock
   v
GET /api/manifest  load_review :92 [+confirm_below]  _build_file_row [+confidence_level]
   v
store.manifest.confirmBelow + row.confidence_level -> lib/needsLook.ts
   -> StatusBar toggle (count) -> resultView.needsLookOnly -> rowFilter.filterGroups
```

## Q1 Modes
Keep both wire booleans. The dialog already enforces the three states: aggressive is sent only with its parent (ScanDialog.tsx:518-520, :776, :790). The three modes map as follows:
- **off** means `auto_select_enabled=false`.
- **auto** means enabled, without aggressive.
- **aggressive** means both.

There is no `mode` field and no migration. One field is added: `auto_select_confirm_below`.

## Q2 Threshold and levels
Each row gets a level from a new pure function `row_confidence_level(row)` in core/services/auto_select.py. Rules are checked in order:

| Row | Level |
|---|---|
| `match_confidence == "low"` | low |
| `action == "REVIEW_DUPLICATE"` | medium (pixels differ, and the keeper pick is metadata-only, see B2) |
| `action == "EXACT"` or `match_confidence == "high"` | high |
| anything else (Ref-tier passenger, RAW+lossy pair, old shapes) | none, so it is ignored |

**Setting values.** The setting is `"high" | "medium"`, meaning "confirm below". Lean default: `"high"` (Open item 2). An unknown stored value resolves to `"high"`.

**Later engine.** When the engine arrives, it replaces `row_confidence_level` with min(grouping, decision). The setting key and its two values survive unchanged.

**Thresholds.** A row is below the threshold when its level ranks under T (low < medium < high). A group needs a look when any of its rows is below T.

| Row level | T=high | T=medium |
|---|---|---|
| high: EXACT, burst frame `""`/high | admitted | admitted |
| medium: REVIEW/high | below | admitted |
| low | below | below |
| none | ignored | ignored |

**Modes.** No mode ever sets a lock.

| Mode | Group with every row admitted | Group with a row below T |
|---|---|---|
| off | nothing written | nothing written |
| auto | top scorer stamped KEEP | no stamp; shown by the filter |
| aggressive | KEEP stamp, plus delete on eligible admitted non-keepers | no stamp; delete only on eligible admitted rows; below rows stay Keep; shown by the filter |

**Why the stamp is gated per group.** A stamp rewrites `row.action` (scan_runner.py:1375). Stamping only admitted groups keeps the view-time level equal to the scan-time level.

**Keeper protection.** Delete protection still uses every group's top scorer, stamped or not.

**Parity at medium.** T=medium reproduces today's #517 delete set exactly.

## Q3 Keeper lock
The lock is `is_locked=1`, which `build_auto_select_writes` writes (auto_select.py:97). The scan path applies it at scanner/manifest.py:204-208.

**What removing it changes for the user.**
- The keeper's decision buttons are no longer disabled.
- The R-LOCK-1 / R-LOCK-4 lock-confirm dialog no longer fires for auto-picked keepers.
- Bulk verbs no longer skip the keeper under R-LOCK-7.

**What removing it changes for Execute.** Nothing requires a KEEP per group. `_load_decided_rows` reads only delete/ignore rows (execute_service.py:102-115). The all-delete check is a warning, not a guard: `AllDeleteBanner` (ExecuteDialog.tsx:62, :375) reads `user_decision`. It stays.

**Apply best copy is unchanged.** `apply_best_copy` still locks its keeper (action_service.py:325, :331). It is user-invoked, not the engine (Open item 3).

## Q4 #1047 aggressive half
`non_keepers_for_aggressive_delete(rows, keepers, *, confirm_below="medium")` changes in one place. Its :291 filter becomes `not below_threshold(row, confirm_below)`.
- At T=high, a REVIEW/high Hamming-0 row with differing pixels is not eligible. An EXACT copy in the same group still is.
- At T=medium, the REVIEW/high row is eligible, as it is today.

The function's default stays `"medium"`, so existing callers and tests keep #517 semantics. The scan passes T explicitly. `apply_best_copy` passes `"medium"` explicitly.

## Q5 "Needs your look" filter
**What it shows.** Groups with at least one row whose `confidence_level` is below `manifest.confirmBelow`. The filter keeps whole groups and applies in every mode. In off mode those groups are still the uncertain ones.

**Where it lives.**
- It extends `filterGroups` and `visibleSelectedPaths` (lib/rowFilter.ts:47-67, :86-97), so the bulk verbs, `applyBulkDecision` (useAppStore.ts:1669) and the tree share one visible set.
- The control is a toggle in the status bar after the reclaim figure (StatusBar.tsx:154-159). The toolbar has about 26 px of margin at 1280 (Toolbar.tsx:103-107).
- It shows the needs-look group count over the whole manifest, the way the delete counter counts (#906). It is disabled at 0 unless it is active.

**i18n keys.**
- `web.status.needs_look` "⚑ Needs your look · {count}" / "⚑ 需要你看 · {count}"
- `web.status.needs_look_tip` "Show only the groups whose confidence is below your confirm threshold (Advanced settings). Click again to show all." / "只顯示信心低於確認門檻（進階設定）的群組。再按一次顯示全部。"
- `web.reason.confidence_medium` "Confidence: medium" / "信心：中"
- `web.scan.confirm_below_label` "Ask me to confirm when confidence is below" / "信心低於此等級時請我確認"
- `web.scan.confirm_below_high` "High — confirm every near-duplicate" / "高 — 每組近似重複都要確認"
- `web.scan.confirm_below_medium` "Medium — confirm only low-confidence matches" / "中 — 只確認低信心的配對"
- `web.scan.confirm_below_help` "Auto-select makes no decision on a group below this level; those groups appear under Needs your look." / "信心低於此等級的群組，自動選擇不會做任何決定，並列在「需要你看」。"

## Q6 Default mode
Confirmed: off mode writes nothing.
- `keepers` stays empty, so `write_manifest(keepers=None)` skips the fold and there is no migration and no decision-log entry (scan_runner.py:1367-1369, :1404; manifest.py:180).
- No stamp is written.
- The classifier still writes `action`, `reason` and `match_confidence`, which are not decisions.

## Q7 Risks and layout
**Code that reads KEEP.** Logic reads `action == "KEEP"` in `selectKeepers` only (useAppStore.ts:354-358), so an all-below scan selects nothing, which is benign. Display-only reads are `_ACTION_SORT` (review_view.py:28), the load ORDER BY (manifest_repository.py:100) and `print_summary` (manifest.py:247).

**A threshold changed after a scan.** It moves the filter but not the stored stamps. The tooltip names the threshold. A rescan is needed to re-decide.

**Scenarios that must be updated, not deleted.**
- s49_scan_auto_select.py: lock asserted at :122.
- s57_scan_auto_select_aggressive.py: lock at :144, deletes ≥1 at :180. Its near-dup fixture is all REVIEW after #1048, so at T=high it would stamp and delete nothing.
- s72 and s75 are unchanged because apply_best_copy is unchanged.

**Status bar width.** s76_toolbar_status_density may measure the strip. The strip has a flex spacer (StatusBar.tsx:161).

## Edit list (in order)
- B1 core/services/auto_select.py
  - After :199, add the constants `CONFIRM_BELOW_CHOICES = ("high", "medium")`, `DEFAULT_CONFIRM_BELOW = "high"` and `_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}`.
  - Add `resolve_confirm_below(value) -> str`, `row_confidence_level(row) -> str | None` (getattr duck-typed, Q2 order), `below_threshold(row, confirm_below) -> bool`, `groups_needing_look(rows, confirm_below) -> set[str]` and `plan_auto_select(rows, *, aggressive, confirm_below) -> tuple[set[str], set[str]]`. The last one returns (stamped keepers, deletes) and computes every row's level before any stamp.
  - :248 signature gains `*, confirm_below: str = "medium"`.
  - :291 changes as in Q4. Docstrings :262-266 gain a #1047 paragraph.
  - :53 `build_auto_select_writes(..., *, lock_keepers: bool = True)`. When it is false, `lock_states = {}`. Update the docstring :57-88.
- B2 core/app_service/dtos.py:77: add `auto_select_confirm_below: str = DEFAULT_CONFIRM_BELOW` (import the constant).
- B3 app/web/models.py:283: add `auto_select_confirm_below: Literal["high", "medium"] | None = None`. In `resolved_with` (:317-331), when the field is None, use `resolve_confirm_below(settings.get("scan.auto_select.confirm_below"))` and add it to the `update` dict. In `to_config` :357, pass it with the `or DEFAULT_CONFIRM_BELOW` fallback.
- B4 scan_runner.py:1367-1385: replace the block with `stamped, deletes = plan_auto_select(rows, aggressive=config.auto_select_aggressive_delete, confirm_below=config.auto_select_confirm_below)`.
  - Stamp `row.action = "KEEP"` on `stamped`.
  - Log `Auto-select: marked N keeper(s); M group(s) need your look (confirm below T).`
  - Aggressive log: `marked N row(s) for delete`.
  - :1404 `keepers=stamped if (stamped or deletes) else None`, so deletes in an all-below scan still fold.
  - :1405 `non_keepers_for_delete=deletes or None`.
  - :1408-1412 log becomes `Auto-select: decisions written (no keeper is locked).`
- B5 scanner/manifest.py:192: call `build_auto_select_writes(keepers, non_keepers_for_delete, lock_keepers=False)`. Fix the comments at :86-95 and :100 and the :175-179 comment (no lock).
- B6 action_service.py:324: pass `confirm_below="medium"` explicitly. Add a one-line comment that a user-invoked best-copy keeps #517 semantics and its lock.
- B7 app/web/routes/settings.py: add `"scan.auto_select.confirm_below"` to `_WEB_SETTINGS_KEYS` after :42, with a comment. Add the `DEFAULT_CONFIRM_BELOW` default at :56-58 and fix the :54-55 comment.
- B8 review_service.py:92-98: add `"confirm_below": resolve_confirm_below(settings.get("scan.auto_select.confirm_below"))` and the docstring key at :59-65.
- B9 review_view.py:19: import `row_confidence_level`. After :171, add `"confidence_level": row_confidence_level(record)`.
- F0 api/types.ts:
  - `export type ConfirmBelow = "high" | "medium"`.
  - FileRow after :60: `confidence_level?: "high" | "medium" | "low" | null` with a doc comment.
  - ManifestResponse :73: `confirm_below?: ConfirmBelow`.
  - WebScanRequest :157: `auto_select_confirm_below?: ConfirmBelow`.
- F1 new frontend/src/lib/needsLook.ts (~40 lines, pure): `rowNeedsLook(row, below)`, where null means false and an unknown level means true (fail visible); `groupNeedsLook`; `countNeedsLook(groups, below)` counts groups.
- F2 lib/rowFilter.ts: `filterGroups(groups, filterText, needsLookBelow: ConfirmBelow | null = null)` applies the group filter first, then the text filter. It returns the input by identity when both are off. `visibleSelectedPaths(..., needsLookBelow = null)`. Update the header comment at :1-23.
- F3 store/types.ts
  - ManifestState :39: `confirmBelow?: ConfirmBelow`.
  - ResultViewState after :166: `needsLookOnly?: boolean` (optional so the ResultTree test literals still typecheck).
  - After :609: `setNeedsLookOnly(on: boolean): void`.
- F4 useAppStore.ts
  - initialManifest :127-134: `confirmBelow: "high"`.
  - initialResultView :169: `needsLookOnly: false`.
  - loadManifest :335-339: `confirmBelow = data.confirm_below ?? "high"` and `needsLookOnly = false`.
  - `setNeedsLookOnly` beside :741, calling `refreshDecisionSort` like :746.
  - :1669-1673: pass `needsLookOnly ? confirmBelow : null`.
- F5 ResultTree.tsx:128-132 and Toolbar.tsx:147, :164-167: pass the same third or fourth argument.
- F6 StatusBar.tsx after :159: a `<button aria-pressed data-testid={MAIN_STATUS_NEEDS_LOOK} title={needs_look_tip}>` with `countNeedsLook(manifest.groups, confirmBelow)`. Add `MAIN_STATUS_NEEDS_LOOK = "main-status-needs-look"` to testids.ts after :35 and qa/web/testid_constants.py after :191.
- F7 ScanDialog.tsx
  - State after :247: `confirmBelow` plus a `confirmBelowTouched` ref, like :248-251.
  - Reset at :309, setting it to `"high"`.
  - Load beside :336-341, honouring only "high" or "medium".
  - req :520: `auto_select_confirm_below: confirmBelow`.
  - After :806: a native `<select data-testid={SCAN_CONFIRM_BELOW}>`, not indented, disabled while running. On change it calls `patchSettings({"scan.auto_select.confirm_below": v})` (like :819), with the help line below it.
  - Add `SCAN_CONFIRM_BELOW = "scan-confirm-below"` after testids.ts:81 and testid_constants.py:369.
- F8 lib/reason.ts: CONFIDENCE_TEXT :37-40 gains `medium`. :61-63 uses `row.confidence_level ?? row.match_confidence`.
- F9 translations: en.yml web.scan after :798, web.status after :524, web.reason after :659. zh_TW.yml after :656, :425 and :531. Parity is pinned by tests/test_web_i18n.py.

## Tests
**Backend, tests/test_auto_select.py.** The existing TestNonKeepersForAggressiveDelete cases stay unchanged; they pin that T=medium equals #517. A new class TestConfidenceThreshold:
- `test_row_confidence_level_maps_action_and_confidence` covers EXACT/high, REVIEW/high, REVIEW/low, UNDATED/low, ""/None, EXACT/None and a burst frame.
- `test_review_high_not_delete_eligible_when_threshold_high` is the #1047 case: a pixels-differ REVIEW/high row at Hamming 0 is excluded, and the EXACT copy is kept in the set.
- `test_review_high_delete_eligible_when_threshold_medium`.
- `test_low_row_never_eligible_at_either_threshold`.
- `test_plan_auto_select_auto_stamps_no_keeper_in_a_below_threshold_group`.
- `test_plan_auto_select_aggressive_never_deletes_the_unstamped_keeper`.
- `test_plan_auto_select_aggressive_mixed_group_deletes_only_admitted_rows`.
- `test_groups_needing_look_ignores_unrated_rows`.
- `test_resolve_confirm_below_falls_back_to_high`.
- `test_build_writes_without_lock_returns_no_lock_states`.

**tests/test_scan_runner.py.**
- `_read_manifest_rows` (:215) adds `action`.
- :221 becomes `test_auto_select_enabled_stamps_keeper_without_lock`: one KEEP row, no `is_locked`.
- :267 asserts a KEEP row, no lock, and deletes ≥1.
- :362-369 asserts one KEEP row, not one locked row.
- :465 precondition becomes "2 rows + 1 KEEP".
- The commit message names each flipped assertion.

**Other backend tests.**
- tests/test_decision_log.py:346 becomes `test_write_manifest_logs_aggressive_deletes_and_no_keeper_lock`: the keeper is absent from the log and only the loser has `new_decision == "delete"`.
- tests/test_review_view.py:244: required_keys adds `confidence_level`. TestReasonAndConfidenceSerialisation (:346) adds `test_review_high_row_serialises_medium_confidence_level`.
- tests/test_review_service.py: `test_load_review_returns_confirm_below_from_settings` covers unset → high, medium → medium, junk → high.
- tests/test_web_settings_routes.py beside :182: `test_confirm_below_round_trip`.
- tests/test_web_scan_routes.py beside :582, class TestConfirmBelowResolution: `test_unset_request_reads_settings`, `test_explicit_value_wins_over_settings`, `test_invalid_value_is_422`.

**Frontend vitest (CI).**
- needsLook.test.ts: "medium needs a look below high", "medium needs no look below medium", "an unrated row never needs a look", "an unknown level needs a look", "countNeedsLook counts groups, not rows".
- rowFilter.test.ts: "needs-look filter keeps whole groups with a below-threshold row", "returns groups by identity when both filters are off", "visibleSelectedPaths drops rows the needs-look filter hides".
- StatusBar.test.tsx: "needs-look toggle shows the group count and toggles the filter", "needs-look toggle is disabled at zero".
- ScanDialog.test.tsx: "confirm-below loads the saved value and sends it on Start", "confirm-below change is persisted", "confirm-below defaults to high when unset".
- reason.test.ts: "rowTooltip prefers confidence_level and labels medium".
- useAppStore.test.ts: "loadManifest stores confirm_below and clears needsLookOnly".

## QA
- **s49** sets the select to medium before Start. It asserts KEEP, `is_locked` False at :122, and prints each row's `confidence_level` as a probe, because the fixture's dHash agreement is platform-dependent.
- **s57** also sets medium. :144 flips to False and the deletes ≥1 assertion stays.
- **New s80_needs_your_look.py** runs in aggressive mode with the select set to high explicitly. It uses two sources: near-duplicates plus a temporary folder holding two byte copies of one qa/sandbox/unique JPEG.
  - The near-dup group has no KEEP, no delete and no lock.
  - The copy pair has KEEP plus one delete.
  - The status-bar toggle reads 1. Clicking it leaves exactly the near-dup group header visible.
- **Registration.** Follow s79's footprint: qa/scenario_ids.py :349 and :369, qa/web/scenario_map.yml after :523, and tests/test_check_qa_parity.py :41, :47-48 changing 78 to 79. A local run needs the owner's OK; CI runs the batch.

**Test-set measurement for the lead.** Run a scratch probe over `migration_manifest_testset.sqlite` (rows where `outcome=''`) on master and on the branch. Report:
- groups needing a look at T=high and T=medium;
- stamped keepers in auto mode per T;
- aggressive deletes on master vs the branch at T=medium (must be equal) and at T=high;
- the count of rows whose eligibility changes (expected: REVIEW/high non-keepers only).

## Docs
- **scan.md "### Scan dialog — auto-select after scan" (:5):** remove the lock and its R-LOCK composition. Add the per-group threshold gate and fix the :9 persistence claim.
- **scan.md "### Scan dialog — auto-select aggressive ("delete all others")" (:16):** condition (b) becomes "not below the confirm threshold", citing #1047.
- **scan.md "### Scan dialog — collapse Advanced Settings" (:38):** add the select to the control order. Fix :45.
- **scan.md "### Scan flow — visual selection of KEEP rows after scan" (:175):** only admitted groups carry KEEP, and there is no lock.
- **NEW scan.md "### Scan dialog — confirm threshold (setting)"**, after the cloud entry (:72).
- **NEW web-ui.md "### Web — Needs your look filter"**, after "### Web — sort by decision (Action header)" (:166).
- **review.md "### Review tree — why and how sure" (:34):** add the medium level.
- **main-window.md "### Main window — status bar baseline" (:107):** add the toggle.
- **docs/testing.md:** rows :103 and :113.
- **rules.md**, after :139:
  - R-SCAN-4 "Auto-select stamps a keeper only in a group at or above the confirm threshold and never locks it". Owner: `core/services/auto_select.py::plan_auto_select`, `scanner/manifest.py::write_manifest`.
  - R-SCAN-5 "Aggressive auto-select never marks a row below the confirm threshold for delete". Owner: `core/services/auto_select.py::non_keepers_for_aggressive_delete`.
  - Pins: the new auto_select tests and s80.
- **Generated files:** run `python scripts/gen_features_index.py` and `python scripts/gen_reference.py` (settings-keys.md, i18n-keys.md). Add `news/<PR>.feature` after the PR is created.

## Open for the lead
1. **Medium derived from REVIEW_DUPLICATE** (Contradiction 1). Lean yes: without it the setting does nothing and #1047 stays open.
2. **Default threshold.** Lean `"high"`, the safe direction for deletes, in line with B2 and B8. Under it, auto mode stamps no keeper in any near-dup group. The owner sets the final value after the test-set numbers (F3).
3. **Apply best copy keeps #517 semantics and its lock.** Lean yes, because the user is already looking at the group. That leaves a documented parity gap with the scan-time path.
4. **Auto mode writing deletes above the threshold** (A1). Lean no for PR-3. It belongs to the engine or rule PRs.
5. **The filter uses the current threshold, not the one the scan used.** Lean accept. The tooltip says so, and the manifest has no column to store the scan-time value.
6. **R-LOCK-6 protection is lost for auto-picked groups** (Contradiction 5). Lean accept. A fresh scan never walks skip directories (scan.md "### Scan walk — recycle-bin / system folders are never walked").