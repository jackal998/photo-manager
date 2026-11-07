# PR-4a design: rule toggles v1 (B3, B4, B5, B7, B8)

Author: opus planner (read-only), 2026-11-08, master 1626771. Spec: `auto-select-prd-2026-11-v2.md` §6 (:216-250), §7 (:251-266), §11 item 4 (:388); PR-3 design `pr3-design.md`.

**Lead decisions (owner, 2026-11-08, binding).**
- PR-4 is split. PR-4a is the framework plus five toggles over existing behaviour: B3 byte copies, B4 RAW+JPEG keep both, B5 burst keep all, B7 screenshot demotion (default off) and B8 people and pets (default on).
- B3 keeps today's keeper pick: top score, tie-break (-score, source_path).
- The UI is a "Rules" sub-block in Advanced settings and follows the PR-3 persistence pattern.
- Defaults: B3 on, B4 on, B5 on, B7 off, B8 on.

**Scope:** PR-4a. **Out of scope:** B2 goes to PR-6 because no quality signal exists, so no no-op toggle ships. B6, the Live Photo MOV that follows its photo, goes to PR-4b. Writing to `decision_log` stays in PR-5, and R-LOG-1 is untouched.

**Recorded deviation.** The PRD §6 provisional B3 keeper rule (source order, then metadata completeness, :236-238) is not implemented. The keeper stays `top_score_path_per_group` (`core/services/auto_select.py:165-195`).

## Lead decisions on the open questions (2026-11-08, after the planner returned)

These override the matching lines below where they differ.

- **Open 5 / Effort — B7 STAYS in PR-4a, but its default is ON (today's behaviour), not the PRD's off.** The owner's
  scope for PR-4a is "toggles over existing behaviour"; the planner found that B7 off changes default grouping (PNG
  chains merge, Contradiction 4) and may reopen dedup-core class B (PNG screenshot false groups), which is exactly the
  core criterion ("unrelated photos never land in one group"). So: `DEFAULT_RULES` has `b7=True`; no
  `GROUPING_STRATEGY_VERSION` bump (the default classify call is unchanged); every "B7 off" test and the R-SCAN-10
  wording stay as written; the b7 checkbox ships checked. **Flipping the default to off is PR-4b's decision**, after a
  rescan census of the test-set copy shows how many groups and rows change (Open 3 goes to the owner with that census).
  `LEGACY_RULES` (T,T,T,T,F) and `DEFAULT_RULES` (T,T,T,T,T) therefore differ only in B8.
- **Open 4 — accepted: `apply_best_copy` ignores the toggles** (passes `LEGACY_RULES`). Its label promises "Keep best ·
  delete rest" on a group the user is looking at; B4's #824 protection stays unconditional there as today.
- **Open 2 — accepted: B3 covers every EXACT row**, byte copies and pixel-identical format duplicates alike
  (Contradiction 1).
- **Open 1 — deferred: auto mode still writes no deletes.** B3 acts in aggressive mode only in v1; whether auto mode
  should delete exact copies is a later rule PR (owner's call, not blocking).
- **Noted, not changed in v1:** a user-chosen non-default toggle (B5 off, B7 off) changes classification without a
  GROUPING bump; the version describes the default scan. Record this in the features entry.

## Contradictions found in the PRD text (read first)
1. **B3 says "byte copies", but B8 and A4 say "EXACT".** Since #1048, EXACT covers byte copies (`scanner/dedup.py:461-467`) and pixel-identical format duplicates (`:764-772`). This design makes B3 govern every EXACT row. If it covered byte copies only, turning B3 off would still delete format duplicates, which contradicts "copies undecided". See Open 2.
2. **B3 "extra copies DELETE" implies that auto mode writes deletes.** Today only aggressive mode deletes (PR-3 Contradiction 3, `auto_select.py:418-423`). Because the owner scoped PR-4a to existing behaviour, B3, B4 and B8 act only in aggressive mode. See Open 1.
3. **B4 "if ignored: judged as near-dups" overstates what is possible.** A RAW's complementary JPEG carries no duplicate action, and the #536 allowlist (`auto_select.py:209`) never admits it. So turning B4 off can only remove #824's displaced-keeper protection (`:275-318`). Judging RAW+JPEG as near-dups would be new engine behaviour.
4. **B7 is not label-only.** PRD §3 (:90-92) and §6 (:239-240) say only the label and confidence move.
   - That holds on the format-duplicate path (`dedup.py:736-746`).
   - On the near-dup path, the demotion also makes the union-find edge a low hint (`:1061-1063`, `:1092-1095`), and hints never chain (`:1308-1325`).
   - So B7 off, the default, lets PNG chains merge into one group (compare `tests/test_dedup_hint_edges.py:195`). The default scan's grouping therefore changes, and B1 below bumps GROUPING.
5. **R-SCAN-5 conflicts with B8 on.** It says "At medium the set is the #517 set unchanged" (`docs/behaviour/rules.md:150`). B8 on by default makes that false, so the wording changes (see Rules register).
6. **§6 (:219) says "the rule set id and version go into every log entry".** No log column exists (PR-5). PR-4a writes the version to the scan log only.

## Data path
```
ScanDialog.tsx > ScanRulesBlock  checkbox --PATCH--> settings.json scan.auto_select.rules
   | POST /api/scan {..., auto_select_rules: {B3,B4,B5,B7,B8}}
   v
WebScanRequest.resolved_with  None -> settings -> resolve_rule_toggles (junk -> default)
   v to_config: ScanConfig.auto_select_rules (RuleToggles, frozen)
scan_runner.run_pipeline
   |-- classify(..., burst_stacks=B5, screenshot_demotion=B7)   every mode, scan time
   |      -> rows: action / reason / match_confidence / group_id
   |-- [auto_select_enabled] plan_auto_select(rows, aggressive, confirm_below, rules)
   |      -> stamped (KEEP), deletes  (B3/B4/B8 narrow the delete set only)
   |      -> rule_trace -> one scan-log tally line (PR-5 hook)
   v
write_manifest(keepers, non_keepers_for_delete)  unchanged, no lock
   v
GET /api/manifest  unchanged
```

## Decisions on the five questions
1. **The wire shape is one JSON object key, `scan.auto_select.rules`, with value `{"B3":bool,"B4":bool,"B5":bool,"B7":bool,"B8":bool}`.** One allowlist entry, one validator and one PATCH per change keep it simple. A partial or older stored object resolves per id, so PR-4b can add B6 with no migration.
   - The request field is `auto_select_rules`, and the ScanConfig field is `auto_select_rules: RuleToggles`.
   - The two booleans and `confirm_below` are unchanged.
   - The key stays `scan.auto_select.*` per §11, even though B5 and B7 act in every mode (see 3).
2. **Off semantics.** A rule only removes rows from the delete set or leaves classifier verdicts alone. No toggle ever adds a delete.
   - **B3 off.** No EXACT row is delete-eligible. The keeper stamp is unchanged, because the stamp is the score pick and not B3. This only matters in aggressive mode.
   - **B4 off.** `_raw_displaced_keepers` protection is skipped, so a displaced lossy EXACT or REVIEW row can become eligible. RAW+lossy complementary rows stay ineligible under #536. Aggressive mode only.
   - **B5 off.** `_mark_burst_stacks` is skipped. Frames keep their classifier verdicts: REVIEW_DUPLICATE high or low, EXACT, or a hint. Grouping is unchanged, because the burst pass adds no edge (`dedup.py:1295-1297`).
     - At T=high, a former burst group now needs a look: its REVIEW rows are medium.
     - With B8 on, no burst frame is deleted, except an EXACT copy as today.
     - B5 is scan-time only.
   - **B7 on (non-default).** This is today's behaviour: a both-PNG pair is capped at low and its edge is a hint.
   - **B7 off (default).** Only the shot-time demotion remains (`_demotion_note`, `:308-310`). PNG pairs are judged like photos, become EXACT/high or REVIEW/high, and can chain. B7 is scan-time only.
   - **B8 on (default).** A delete-eligible row must have `action == "EXACT"`. REVIEW is never eligible. Stamps are untouched.
   - **B8 off.** No people rule applies.
   - **B8 × confirm_below.** The eligibility predicate is an AND chain, checked in this order: base filters (ranked, not keeper, duplicate action, not low), then `not below_threshold`, then B4, B3 and B8.
     - At **high**, B8 changes nothing, because REVIEW is already below the threshold. The trace attributes those rows to the threshold, not to B8.
     - At **medium**, B8 on overrides the threshold's relaxation for deletes. Medium then changes only which groups are stamped and which need a look.
     - The precedence rule is that a rule can veto a threshold-admitted delete but never admit a below-threshold row.
3. **Scan time versus review time.**
   - B5 and B7 run inside `classify`, in all three modes, because they shape what the default view shows. Toggling them after a scan needs a rescan, and both the UI text and the docs say so.
   - B3, B4 and B8 run in `plan_auto_select`, also at scan time. A change takes effect at the next scan.
   - **`apply_best_copy` ignores the toggles.** It passes `LEGACY_RULES` explicitly. This deviates from the lead's lean (Open 4), for these reasons:
     - Its button reads "Keep best · delete rest" (s75), and the user is choosing on a group they can see.
     - B8 there would make the label untrue for every near-dup group.
     - It would also flip s72's `deleted >= 1` (`s72_apply_best_copy.py:138`) and s75's expected deletes (`s75_keep_best_button.py:184`).
     - B4 protection stays unconditional there, as today.
4. **Traceability.**
   - `RULES_VERSION = "v1"` lives in the new `core/services/auto_select_rules.py`.
   - A pure `rule_trace(...)` returns a map from path to `"B1:keep" | "B1:delete" | "B3:delete" | "B3:spared" | "B4:spared" | "B8:spared"`. B1 is the metadata keeper score; it has no toggle in v1.
   - The scan log prints the version, the rule states and the tally. Nothing is written to the manifest `reason`: that is the classifier's "why grouped" text, and it would go stale when the user edits the decision.
   - PR-5 maps the trace onto `decision_log.engine` and `decision_log.reason` with no rework.
   - `decision_log` is untouched, so it is not made worse.
5. **Validation.**
   - **On read** (`resolve_rule_toggles`, used by `resolved_with` and the dialog): junk resolves to the default per id, and unknown ids are ignored. This mirrors `resolve_confirm_below` (`auto_select.py:220-228`), so a bad file never crashes a scan.
   - **On write:** PATCH `/api/settings` and POST `/api/scan` refuse unknown ids and non-boolean values with 422. A partial object is accepted and merged over the defaults.

## Backend items
- **B1 `scanner/dedup.py`**
  - `_demotion_note(a, b, screenshot_demotion: bool = True)` (`:302-313`): the PNG branch at `:311` runs only when the flag is true.
  - Thread the keyword `screenshot_demotion: bool = True` through these functions and pass it at every `_demotion_note` call:
    - `classify` (`:316`, `:396-400`);
    - `_classify_phash` (`:546`, `:594-596`, `:601-604`);
    - `_classify_format_group` (`:667`; calls at `:694`, `:702`, `:736`);
    - `_complementary_edges` (`:777`, `:806`);
    - `_classify_near_duplicates` (`:990`, `:1061`).
  - `classify` gains `burst_stacks: bool = True`, and `:428` becomes `if burst_stacks: _mark_burst_stacks(...)`.
  - **Function defaults keep GROUPING 9 behaviour** (the PR-3 precedent, `auto_select.py:325`). Every existing dedup test stays valid, and the scan passes the user's rules explicitly.
  - Bump `GROUPING_STRATEGY_VERSION` from "9" to "10" (`:870`). The default scan's grouping changes (Contradiction 4). The only consumer is the calibration fingerprint (`scan_runner.py:270-279`), so the cost is one universal calibration re-measure.
  - **Tests:** new `tests/test_dedup_rule_toggles.py` (docstring `GROUPING_STRATEGY_VERSION "10"`; reuse `_hr` and `_rows` from `tests/test_dedup_hint_edges.py`, `P0..P4` and `_five_frames` from `test_dedup_burst_stack.py`):

    | Test | Assertion |
    |---|---|
    | `test_burst_off_keeps_classifier_verdicts` | f1..f4 are `REVIEW_DUPLICATE`, and no reason starts with `BURST_PREFIX` |
    | `test_burst_off_leaves_group_ids_unchanged` | the group_id map equals the burst-on map |
    | `test_screenshot_demotion_off_format_pair_is_not_low` | two PNGs, same pHash and time: match_confidence `"high"` and `"both screenshots"` not in the reason |
    | `test_screenshot_demotion_off_near_dup_png_is_high` | match_confidence `"high"` |
    | `test_screenshot_demotion_off_chain_forms_one_group` | the `SHOT` s1..s4 chain gives `_group_sizes == [4]` |
    | `test_shot_time_demotion_survives_screenshot_off` | two PNGs two days apart stay `"low"` with `"; shot"` |
    | `test_classify_defaults_are_grouping_9` | the default call equals `burst_stacks=True, screenshot_demotion=True` row for row |
- **B2 new `core/services/auto_select_rules.py`** (~90 lines, pure, imports nothing from `scanner`)
  - `RULE_IDS = ("B3","B4","B5","B7","B8")` and `RULES_VERSION = "v1"`.
  - `@dataclass(frozen=True) class RuleToggles: b3: bool; b4: bool; b5: bool; b7: bool; b8: bool`, with `to_dict()` (uppercase ids) and `classmethod from_mapping(m, base)`.
  - `DEFAULT_RULES = RuleToggles(True, True, True, False, True)`.
  - `LEGACY_RULES = RuleToggles(True, True, True, True, False)` (master behaviour).
  - `resolve_rule_toggles(value) -> RuleToggles`: a non-mapping gives `DEFAULT_RULES`; per known id, a strict `bool` wins and anything else takes the default; unknown ids are ignored.
  - `validate_rule_toggles(value) -> None`: raises `ValueError` on a non-dict, an unknown id, or a non-bool value (`isinstance(v, bool)`, so `1` is refused). A partial object is accepted.
  - **Tests:** new `tests/test_auto_select_rules.py`:

    | Test | Assertion |
    |---|---|
    | `test_default_rules_match_prd` | `to_dict() == {"B3":True,"B4":True,"B5":True,"B7":False,"B8":True}` |
    | `test_legacy_rules_match_master` | only B8 is False |
    | `test_resolve_merges_partial_over_defaults` | `{"B3":False}` turns off B3 only |
    | `test_resolve_ignores_unknown_and_non_bool` | `{"B9":True,"B4":"no"}` gives `DEFAULT_RULES` |
    | `test_resolve_non_mapping_gives_defaults` | None, `"x"` and `[]` |
    | `test_validate_rejects_unknown_id` | raises |
    | `test_validate_rejects_non_bool` | `1` and `"true"` raise |
    | `test_validate_accepts_partial` | `{"B8": False}` passes |
- **B3 `core/services/auto_select.py`**
  - Add `_delete_blocker(row, keepers, raw_protected, *, confirm_below, rules) -> str | None`. Checks run in order and return the first blocker, or `None` when the row is eligible:
    1. unranked;
    2. keeper;
    3. not a duplicate action (`:209`);
    4. low (#517);
    5. `below_threshold`;
    6. `"B4"` when `rules.b4` and the row is in `raw_protected`;
    7. `"B3"` when not `rules.b3` and the action is EXACT;
    8. `"B8"` when `rules.b8` and the action is not EXACT.
  - `non_keepers_for_aggressive_delete(..., confirm_below="medium", rules: RuleToggles = LEGACY_RULES)` (`:321-378`) returns rows whose blocker is None. It is set-identical to master under `LEGACY_RULES`. Its docstring gains a PR-4a paragraph.
  - `plan_auto_select(..., rules: RuleToggles = LEGACY_RULES)` (`:381-423`) passes `rules` through. Stamps are unchanged.
  - New `rule_trace(rows, stamped, *, aggressive, confirm_below, rules) -> dict[str, str]`, as in decision 4. `spared` and `delete` entries appear only when `aggressive` is set.
  - **Tests:** `tests/test_auto_select.py`, new class `TestRuleToggles`. The existing `TestNonKeepersForAggressiveDelete` and `TestConfidenceThreshold` stay unchanged.

    | Test | Assertion |
    |---|---|
    | `test_function_defaults_are_legacy_rules` | the default call equals `rules=LEGACY_RULES` |
    | `test_b3_off_spares_exact_rows` | an EXACT non-keeper is not in the set; a REVIEW/high row still is, with B8 off at medium |
    | `test_b3_off_keeps_the_keeper_stamp` | `stamped` still contains the keeper |
    | `test_b4_off_drops_raw_displaced_protection` | reviewer trio (RAW keeper plus lossy EXACT): the lossy row enters the set |
    | `test_b8_on_excludes_review_at_medium` | REVIEW/high is out and the EXACT copy is in |
    | `test_b8_on_changes_nothing_at_high` | the set with B8 on equals the set with B8 off at high |
    | `test_b8_off_at_medium_equals_517_set` | equals the legacy result |
    | `test_rule_trace_attributes_rules` | `"B3:delete"`, `"B8:spared"`, `"B4:spared"`, and a REVIEW row at high has no `B8` entry |
- **B4 `core/app_service/dtos.py:76-82`**
  - Add `auto_select_rules: RuleToggles = DEFAULT_RULES`, with a comment: "B5/B7 act at classify in every mode; B3/B4/B8 narrow aggressive deletes".
- **B5 `app/web/models.py`**
  - After `:286`: `auto_select_rules: dict[Literal["B3","B4","B5","B7","B8"], StrictBool] | None = None`.
  - In `resolved_with` (`:328-341`): when the field is None, use `resolve_rule_toggles(settings.get("scan.auto_select.rules")).to_dict()`; otherwise use `RuleToggles.from_mapping(field, DEFAULT_RULES).to_dict()`. Add it to `update`.
  - In `to_config` (`:366-370`): `auto_select_rules=RuleToggles.from_mapping(self.auto_select_rules or {}, DEFAULT_RULES)`.
  - **Tests:** `tests/test_web_scan_routes.py`, new `TestRuleTogglesResolution` next to `:582`:

    | Test | Assertion |
    |---|---|
    | `test_unset_request_reads_settings` | stored `{"B8": False}` resolves to `B8 False` |
    | `test_explicit_partial_merges_over_defaults` | an explicit partial object fills the missing ids with defaults |
    | `test_unknown_rule_id_is_422` | POST `{"B9":true}` |
    | `test_non_bool_rule_value_is_422` | `"yes"` and `1` |
    | `test_to_config_carries_rule_toggles` | returns a `RuleToggles` instance |
- **B6 `app/web/routes/settings.py`**
  - Add `"scan.auto_select.rules"` after `:40` with a comment.
  - Add a default of `DEFAULT_RULES.to_dict()` to `_WEB_SETTINGS_DEFAULTS` (`:61-64`).
  - Add `_WEB_SETTINGS_VALIDATORS = {"scan.auto_select.rules": validate_rule_toggles}`. Run it inside the `:120-125` validate-all loop and turn a `ValueError` into a 422 before any `set`.
  - Fix the "no per-key type checks" comment at `:43-46`.
  - **Tests:** `tests/test_web_settings_routes.py` next to `:182`:

    | Test | Assertion |
    |---|---|
    | `test_auto_select_rules_round_trip` | unset returns the default dict; after a PATCH the object reads back and `on_disk["scan"]["auto_select"]["rules"]` matches |
    | `test_auto_select_rules_patch_rejects_junk_and_writes_nothing` | 422 for `{"B9":true}` and `{"B3":1}`; `settings.json` is unchanged |
- **B7 `core/app_service/scan_runner.py`**
  - Before classify (`:1338`): log `Rules {RULES_VERSION}: B3 on, B4 on, B5 on, B7 off, B8 on.`
  - In the classify call (`:1341-1349`): add `burst_stacks=config.auto_select_rules.b5, screenshot_demotion=config.auto_select_rules.b7`.
  - In the plan call (`:1376-1380`): add `rules=config.auto_select_rules`. Then, when aggressive, log `Auto-select rules: B3 marked N delete; spared B3 N, B4 N, B8 N.` from `Counter(rule_trace(...).values())`.
  - **Tests:** `tests/test_scan_runner.py::TestAutoSelectPipeline`. The existing tests at `:222` and `:272` stay unchanged: their fixture is byte copies, which stay deletable under B3 and B8.

    | Test | Assertion |
    |---|---|
    | `test_rules_b3_off_writes_no_delete_for_byte_copies` | one KEEP and zero delete |
    | `test_default_config_rules_are_prd_defaults` | `ScanConfig(...).auto_select_rules == DEFAULT_RULES` |
    | `test_classify_receives_rule_flags` | monkeypatch `scanner.dedup.classify` with a recorder (imported at call time, `:557`); the recorded kwargs are `burst_stacks=True, screenshot_demotion=False` |
    | `test_rules_line_is_logged` | the bus log contains `"Rules v1: "` |
- **B8 `core/app_service/action_service.py:324-328`**
  - Pass `rules=LEGACY_RULES` explicitly, with a comment: user-invoked, label "Keep best · delete rest", toggles are engine rules (decision 3).
  - **Test:** the existing `tests/test_web_action_routes.py::TestApplyBestCopy` stays unchanged. That is the pin.
- **B9 `qa/web/_invariants.py:515-522`**
  - `reset_scan_persistence` also PATCHes `"scan.auto_select.rules": DEFAULT_RULES` (literal dict). The batch shares one `settings.json` (`:486-488`), so without this a toggle leaks into the next scenario.

**Existing tests that must stay unchanged** (library defaults are legacy):
- `tests/test_dedup_time_gate.py:95`, `:138`, `:175`;
- `tests/test_dedup_hint_edges.py:195`, `:203`, `:292`, `:301`;
- all of `tests/test_dedup_burst_stack.py`;
- `tests/test_auto_select.py` (all existing classes);
- `tests/test_topn_keeper_parity.py`;
- `tests/test_scan_runner.py:222`, `:272`;
- s72, s75, s80. B8 changes nothing at high, and s80's EXACT rows are still deleted.

**Existing tests that must change:**
- `tests/test_check_qa_parity.py:41-49`: the count goes from 79 to 80 and the docstring adds "79 -> 80 by PR-4a, which added s81_rule_toggles".
- s57 (`qa/web/scenarios/s57_scan_auto_select_aggressive.py`): untick `scan-rule-b8` before Start. Its "every eligible duplicate deleted at medium" expectation is the #517 set, which B8 on narrows. The commit message names this.

## Frontend items
- **F0 `frontend/src/api/types.ts`**
  - After `:69`: `export type RuleId = "B3" | "B4" | "B5" | "B7" | "B8"` and `export type RuleToggles = Record<RuleId, boolean>`.
  - In `WebScanRequest` after `:168`: `auto_select_rules?: RuleToggles`.
  - In `SettingsMap` after `:227`: `"scan.auto_select.rules"?: Partial<Record<string, unknown>> | null`, with a doc comment.
- **F1 new `frontend/src/lib/ruleToggles.ts`** (~30 lines, pure)
  - `RULE_IDS` and `DEFAULT_RULES` (B7 false, the rest true).
  - `rulesFromSettings(v: unknown): RuleToggles`: only strict booleans for known ids are honoured; everything else takes the default.
- **F2 store:** no change. Rules are dialog-local state plus a setting, and the manifest view reads nothing new.
- **F3 new `frontend/src/components/scan/ScanRulesBlock.tsx`** (~110 lines)
  - It is a separate component because `ScanDialog.tsx` is already 1,133 lines, over the 800 cap.
  - Props: `{rules, disabled, aggressiveOn, onChange(next: RuleToggles)}`.
  - It renders a `border-t` sub-section like "Grouping sensitivity" (`ScanDialog.tsx:917-919`): heading `web.scan.rules_label` with testid `scan-rules`, then one `Checkbox` per rule (testids `scan-rule-b3`, `-b4`, `-b5`, `-b7`, `-b8`).
  - Each checkbox has its label, plus a `text-xs text-ink-muted` help line below it.
  - B3, B4 and B8 labels use `text-ink-muted` while `!aggressiveOn`. They stay enabled, because they are saved preferences.
- **F4 `frontend/src/components/ScanDialog.tsx`**
  - State next to `:247-248`: `rules` (`DEFAULT_RULES`) and `rulesTouched` ref.
  - Reset at `:313-314`.
  - Load next to `:349-356`: `if (!rulesTouched.current) setRules(rulesFromSettings(settings["scan.auto_select.rules"]))`.
  - The request at `:536` adds `auto_select_rules: rules`, and `rules` joins the deps at `:567-578`.
  - Render `<ScanRulesBlock>` after the confirm-below block (`:872`) and before the cloud opt-in (`:874`), per PRD §2 :62 ("after the mode controls").
  - `onChange` sets the touched flag, updates state, and calls `patchSettings({"scan.auto_select.rules": next})` with all five ids, using a `console.warn` catch like `:843-847`.
  - There is no reset-to-defaults button in v1.
- **F5 testids:** `SCAN_RULES = "scan-rules"` and `SCAN_RULE_B3..B8 = "scan-rule-b3".."scan-rule-b8"`, after `testids.ts:83` and `qa/web/testid_constants.py:378`.
- **F6 i18n, `web.scan.*`:** add after `en.yml:805` and `zh_TW.yml:663`. The implementer writes zh_TW in the same register as `:660-663`; parity is pinned by `tests/test_web_i18n.py`. English strings:

  | Key | English string |
  |---|---|
  | `rules_label` | "Rules" |
  | `rules_help` | "Our defaults. Turn a rule off to let the scan judge those files like any other duplicate." |
  | `rule_b3_label` | "Exact copies: mark the extra copies for delete" |
  | `rule_b3_off` | "When off, exact copies are left undecided. Used by Also mark all other files for delete." |
  | `rule_b4_label` | "RAW + JPEG: keep both" |
  | `rule_b4_off` | "When off, the JPEG a RAW keeper outranks can be marked for delete. Used by Also mark all other files for delete." |
  | `rule_b5_label` | "Burst: keep every frame" |
  | `rule_b5_off` | "When off, burst frames are judged as near-duplicates. Takes effect at the next scan." |
  | `rule_b7_label` | "Screenshots: treat two matching PNGs as uncertain" |
  | `rule_b7_off` | "When off, two matching PNGs are judged like photos. Takes effect at the next scan." |
  | `rule_b8_label` | "People and pets: delete only exact copies" |
  | `rule_b8_off` | "When off, a near-duplicate can be marked for delete at the Medium threshold. Used by Also mark all other files for delete." |
- **F7 vitest**
  - `lib/ruleToggles.test.ts`: "rulesFromSettings returns the defaults when unset", "rulesFromSettings honours only strict booleans for known ids", "DEFAULT_RULES has B7 off and the rest on".
  - `ScanDialog.test.tsx` (next to `:513-560`): "rules block shows the defaults when unset", "rules load the saved values and are sent on Start", "a rule change is persisted with all five ids", "a late settings load does not overwrite a rule the user already changed", "decision rules read muted while aggressive delete is off".

## QA scenario: `qa/web/scenarios/s81_rule_toggles.py` (WEB-ONLY)
**Fixtures.** Two sources, with the helpers copied from s80 (`:58-98`):
- `qa/sandbox/near-duplicates/`: mostly REVIEW_DUPLICATE, plus possibly one EXACT re-encode;
- a temp folder holding two byte copies of `qa/sandbox/unique/unique_00.jpg`.

Read rows through `/api/manifest`, including `confidence_level`.

1. **Phase 1, defaults, aggressive, select Medium.**
   - Assert that b3, b4, b5 and b8 show checked and b7 shows unchecked (`data-state`).
   - Start, then assert:
     - the copy pair has one KEEP and one delete;
     - no `REVIEW_DUPLICATE` row is marked delete (B8 overrides medium);
     - an EXACT row inside the near-dup group may be deleted.
   - Soft probe: print every near-dup row's `confidence_level`. If no REVIEW row is `medium`, print `probe_status: s81 B8 unproven (no medium REVIEW row on this platform)` and do not fail. The fixture's dHash agreement is platform-dependent (PR-3 QA note).
2. **Phase 2, a second scan to a new output path.**
   - Reopen the dialog, tick auto and aggressive (they reset), and assert the select still reads `medium` (persisted).
   - Untick b3 and b8, Start, and click `SCAN_RESCAN_CONFIRM_DISCARD` (the s27 pattern, `s27_rescan_confirm.py:231-240`).
   - Assert:
     - the copy pair has one KEEP and zero delete (B3 off; the stamp is unchanged);
     - no EXACT row anywhere is marked delete;
     - every REVIEW row at `medium` level is marked delete. This is the positive control that B8 off restores #517; it is soft when no medium row exists.
3. **Phase 3.** Reopen the dialog and assert that b3 and b8 are unchecked (persisted).
4. **Cleanup.** In `finally`, PATCH the rules defaults and `confirm_below: "high"`, then `rmtree`.

**What CI cannot prove.** B4 (no RAW-keeper fixture), B5 (no burst fixture under qa/sandbox) and B7 (one PNG, `qa/sandbox/formats/fmt_png.png`). These are layer 1 only (B1/B3 tests). A live run needs the owner's OK; CI runs the batch.

**Registration**, following s80's footprint:
- `qa/scenario_ids.py` after `:353` (with a comment) and in the done set after `:374`;
- `qa/web/scenario_map.yml` after `:528`;
- `tests/test_check_qa_parity.py`, as above.

## Rules register (`docs/behaviour/rules.md`, SCAN, after :153)
- **R-SCAN-5 edit.** The last sentence becomes: "At `medium` with the people-and-pets rule off, the set is the #517 set unchanged; with it on (`R-SCAN-7`), only EXACT rows are eligible."
- **R-SCAN-6 "With the exact-copies rule off, aggressive auto-select marks no EXACT row for delete".**
  - Rule: "The exact-copies rule (B3, default on) covers byte copies and pixel-identical format duplicates. Off leaves them undecided; the keeper stamp is unchanged."
  - Owner: `core/services/auto_select.py::non_keepers_for_aggressive_delete`, `core/services/auto_select_rules.py::RuleToggles`.
  - Pins: `TestRuleToggles::test_b3_off_spares_exact_rows`, `tests/test_scan_runner.py::TestAutoSelectPipeline::test_rules_b3_off_writes_no_delete_for_byte_copies`, s81.
- **R-SCAN-7 "With the people-and-pets rule on, aggressive auto-select marks only EXACT rows for delete, at either threshold".**
  - Rule: "Default on. A rule can veto a delete the threshold admits; it never admits a row below the threshold."
  - Owner: `non_keepers_for_aggressive_delete`.
  - Pins: `test_b8_on_excludes_review_at_medium`, `test_b8_on_changes_nothing_at_high`, s81.
- **R-SCAN-8 "With the RAW + JPEG rule on, the best non-RAW row of a RAW-kept group is never marked for delete".**
  - Owner: `core/services/auto_select.py::_raw_displaced_keepers`.
  - Pins: `test_b4_off_drops_raw_displaced_protection` plus the existing #824 test in `TestNonKeepersForAggressiveDelete` (the implementer copies its exact name).
- **R-SCAN-9 "With the burst rule off, a same-moment burst keeps its classifier verdicts; groups are unchanged".**
  - Owner: `scanner/dedup.py::classify`.
  - Pins: `tests/test_dedup_rule_toggles.py::test_burst_off_keeps_classifier_verdicts`, `::test_burst_off_leaves_group_ids_unchanged`.
- **R-SCAN-10 "With the screenshot rule off (default), a match between two PNGs is not capped at low confidence; a shot-time disagreement still is".**
  - Owner: `scanner/dedup.py::_demotion_note`.
  - Pins: `::test_screenshot_demotion_off_near_dup_png_is_high`, `::test_shot_time_demotion_survives_screenshot_off`.
- **R-SCAN-11 "A stored rule value resolves per rule to its default when unreadable; the API refuses unknown ids and non-boolean values with 422".**
  - Owner: `core/services/auto_select_rules.py::resolve_rule_toggles`, `app/web/routes/settings.py::patch_settings`, `app/web/models.py::WebScanRequest`.
  - Pins: `tests/test_auto_select_rules.py::test_resolve_ignores_unknown_and_non_bool`, `tests/test_web_settings_routes.py::test_auto_select_rules_patch_rejects_junk_and_writes_nothing`, `tests/test_web_scan_routes.py::TestRuleTogglesResolution::test_unknown_rule_id_is_422`.

## Docs
- **New `docs/features/scan.md` entry "### Scan dialog — rules (setting)"**, after "confirm threshold (setting)" (`:83-90`). It covers:
  - the five rules with defaults and off effects;
  - that B5 and B7 act in every mode and need a rescan;
  - that B3, B4 and B8 act in aggressive mode only;
  - that Apply best copy ignores the rules;
  - the persistence and 422 behaviour;
  - citations of R-SCAN-6..11.
- **Edits elsewhere in `docs/features/scan.md`:**
  - "auto-select aggressive" (`:22`): (b)'s screenshot cap applies only while B7 is on; (d) applies only while B4 is on; new (e) B3 and (f) B8.
  - "collapse Advanced Settings" (`:43`, `:45`): the control order and the list of persisted controls.
  - "auto-select after scan" (`:9`): the list of persisted controls.
  - "low-confidence hints never chain" (`:240`): PNG links are low only while B7 is on.
  - "same-moment bursts become a stack" (`:251`): only while B5 is on.
- **`docs/features/web-ui.md`**, "Web — Context menu: Apply best-copy decisions to this group": add one sentence saying the rules do not apply.
- **`docs/testing.md`:** rows `:98` (dedup) and `:113` (auto_select), plus a new row for `core/services/auto_select_rules.py`.
- **Generated files:** run `python scripts/gen_features_index.py` and `python scripts/gen_reference.py` (settings keys, i18n keys). Add `news/<PR>.feature` after the PR is created.

**Noticed, not changed.** For byte copies, the classifier writes "exact duplicate of <anchor>", with the anchor picked by source priority then lex-min (`dedup.py:455-465`). The auto-select keeper is the top score instead. So the stamped KEEP row can carry a reason naming the row that gets deleted. This is existing behaviour, and PR-5's rule-named reasons are the place to fix it.

## Effort

| Item | Hours |
|---|---|
| B1 dedup flags, GROUPING bump and tests | 2.0 |
| B2 rules module and tests | 1.0 |
| B3 blocker refactor, trace and tests | 2.0 |
| B4–B7 wiring and route/runner tests | 2.0 |
| B8, B9 | 0.3 |
| F0–F7 | 2.5 |
| s81, s57 tweak, registration | 1.5 |
| Docs, rules register, generated files | 1.2 |
| **Total** | **12.5** |

This is over one working day. **Proposal: move B7 to PR-4b with B6** (B1 above except the B5 half, the R-SCAN-10 entry and the b7 checkbox), which saves about 2.5 h. B7 is the only toggle that changes default grouping. It needs a GROUPING bump and a test-set census by rescan, because the manifest alone cannot replay classify.

PR-4a then ships the framework plus B3, B4, B5 and B8, with `RuleToggles` already carrying `b7` fixed to True until PR-4b. A second cut, if needed, is to drop `rule_trace` to a counts-only log line, which saves about 0.5 h.

## Open questions
1. **(Owner) Should auto mode, not only aggressive mode, mark B3 exact copies for delete?** §6 reads that way. The lean is a later rule PR, per the owner's "existing behaviour" scope.
2. **(Owner) Should B3 cover pixel-identical format duplicates as well as byte copies?** The lean is yes (Contradiction 1).
3. **(Owner) Does B7 default off reopen dedup-core class B (PNG screenshot false groups)?** It relies only on the pHash quality gate. The lean is to accept after a census of groups and rows that change on a rescan of the test-set copy, before merge.
4. **(Lead) Apply best copy ignores the toggles**, against your B4/B8 lean (decision 3).
5. **(Lead) Move B7 to PR-4b** (Effort).