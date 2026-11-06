# Auto-select — feasibility analysis and PRD (2026-11)

Date: 2026-11-06. Base: master `55f4948` (#1042 merged). Read-only study; only this file was written.

Evidence labels: **[measured]** run or counted on real data, with its source named · **[proxy]**
agreement with the Sept-2026 Opus vision judge, not with a person · **[estimate]** arithmetic from
stated assumptions · **[not verified]** nobody has checked it · **[code]** read with `file:line`.
Model ids and prices come from the `claude-api` skill (price table cached 2026-10-06), nowhere else.

## 1. Decision summary

1. **Built first, as PR #1043 (open):** the opt-in "cloud analysis" switch, setting-only, default
   OFF (owner decision 2026-11-06). No backend consumes it. PR-1b in §10 lists what it still lacks.
2. **Build next:** a metadata **rule layer** that gives every group either a decision with a written
   reason or the verdict "undecided". It encodes the owner's own policies: people/pet photos are never
   proposed for deletion, identical pairs are decided by metadata, equal frames mean keep all.
3. **Then measure, then build:** a cloud VLM judge for undecided groups only, behind the switch,
   with an explicit abstain, a swap check, and a cache keyed by member sha256.
4. **Do not build:** a pixel ranker (Sept verdict S2a stands), auto-delete on any quality signal (S6
   stands), a preference model before a few hundred logged verdicts exist.
5. **Autonomy does not widen.** A judge pick can protect a file or choose a stack cover. It can never
   add a file to a delete set.
6. **The one gate:** with the switch OFF, the judge code path is not entered and the auto-select and
   judge path opens zero non-loopback connections. A test proves it (AC-1).

## 2. Problem statement (the owner's terms)

Goal 1, "unrelated photos are never grouped", is in a trustworthy state after PRs #1030–#1042. The
v5→v6 rescan dissolved the mega-chains, split the torii frames by moment, and turned screenshot
chains into pairs or hints, with EXACT unchanged at 11,917 rows [measured, STATE.md §Results v6].

Goal 2 is this document: **pick the keeper of a group, or the cover of a burst stack, without a
manual look.** Today the keeper is the top metadata score, which never ran on the June manifest
(`score` NULL on all 40,774 rows, Nov study D7 [measured]). Every group still needs a look before a
delete is trusted. Any model or engine is allowed, on-device or cloud, under a strict opt-in.

## 3. What is already in place [code, master 55f4948]

- **T0 byte copy:** `scanner/dedup.py:308` `classify` and `:428` mark sha256 copies EXACT/high.
- **T1 pHash match:** `scanner/dedup.py:536`. It is high only when dHash agrees and no demotion
  note applies (`:996`). Shot times > 2 s apart (`:258`) and two PNG screenshots (`:284`) demote.
- **Contrast gate:** `scanner/hasher.py:150` with `scanner/dedup.py:513`; near-flat frames draw no
  pHash edge (recipe "5", `scanner/hasher.py:132`).
- **T3 hint:** `scanner/dedup.py:1085`, `:1249`; a low edge never merges groups (#1040); grouping "7" `:774`.
- **T2 burst stack (#1042):** `scanner/dedup.py:1187` `_mark_burst_stacks`, prefix at `:1086`.
  Dated frames spanning > 2 s with every gap ≤ 2 s get action "" and reason "burst stack (keep all)".
- **Confidence column:** `infrastructure/manifest_repository.py:145` persists `match_confidence`.
- **Scorer:** `scanner/scoring.py:57-85` penalties, `:89-98` eight metadata weights, final score at
  `:454`. No pixel, face or user signal.
- **Keeper and delete set:** `core/services/auto_select.py:155` takes the top score per group.
  `:248-292` limits deletes to EXACT/REVIEW (#536), not-low confidence (#517) and the #824 RAW rule.
- **Callers:** scan time picks at `core/app_service/scan_runner.py:1369-1383`; `write_manifest`
  writes and locks the keepers, logged at `:1410`. Review time uses
  `core/app_service/action_service.py:220` and `:302-324` ("Keep best · delete rest").
- **Ref winner:** `core/app_service/review_view.py:35` `pick_ref_winner`. #1042 did not touch it. In
  a burst stack the Ref label falls on the top-score frame, because burst frames are "" (Ref-tier).
- **Decision log:** `infrastructure/decision_log.py:33-47` verbs, `:51-71` table, `:76-83`
  append-only triggers. `manifest_repository.py:755` `write_txn` requires the verb.
- **Rules and docs:** `docs/behaviour/rules.md:168-172` (R-LOG-1: an unchanged row appends nothing);
  `docs/features/scan.md:5` and `:16` (auto-select, aggressive).

Three gaps matter for auto-select.

- **The dedup `reason` never reaches the UI.** It is a manifest column (`scanner/manifest.py:29`),
  and `core/app_service/review_view.py` never serialises it. So no pick can show its reason today.
- **The decision log cannot hold a suggestion.** It has no reason, suggestion or model column. Under
  R-LOG-1 a user who accepts a suggestion that matches the current state leaves no row. The learning
  substrate therefore needs its own verdict record (§5).
- **No people/pet signal exists.** `scanner/exif.py` reads no face-region or person tag. The only
  related metadata column is `xmp_derived` (`scanner/exif.py:581-585`). The `make` / `model` /
  `content_identifier` fields of Sept verdict S1 are not persisted yet. The migration list holds
  `subsec_time_original` and `offset_time_original` only (`manifest_repository.py:112-152`).

## 4. Evidence from the two studies

**Sept 2026 study** (`visual-autoselect-feasibility.md` at `b14b59f`; 150 groups, 491 member rows):

| Finding | Value | Label |
|---|---|---|
| Best pixel scorer top-1, all 121 scored groups | 0.405 against a chance line of 0.378, p = 0.29 | proxy |
| Same scorer on clear-winner groups | 0.587 against 0.358, p = 0.0006 | proxy |
| Same scorer on near-identical edit pairs | 0.277 against 0.437 (below chance) | proxy |
| Any margin threshold reaching 0.95 agreement | none, in all 36 rows | proxy |
| Judge rank-1 = human pick, strict picks | 5 of 8 (pixel signal 3 of 8) | measured, n = 30 |
| Judge rank-1 = human pick, extended picks | 9 of 12 (pixel signal 6 of 12) | measured |
| Human sided with the pixel signal in a head-to-head | 0 of 12 | measured |
| Judge "clear winner" over-claim | 75 % strict, 55 % generous | measured |
| Notes saying exposure and sharpness are equal | 20 of 30 | measured |
| Human outcome keep-all / keep-none / undecided | 12 / 3 / 3 of 30 | measured |
| Face signals that decide a group at all | 0.107–0.364 of groups; eyes-open not measurable | proxy |
| Judge run cost, Opus at low effort, 10 groups per call | USD 9.6989 for 150 groups | measured |

The human's rules were **policy-level**, not ranking-level [measured, Sept §5.6]. People and pet
photos are not considered for deletion. Identical pairs are decided by a metadata rule that the
owner had not written yet. Keep-all is the default when frames are equal. The 30-group sample is
stratified, so no rate in it estimates a library-wide rate.

**Nov 2026 study** (`dedup-core-research-2026-11-05.md`): one 64-bit pHash was answering four
questions at once. Tiered identity (T0–T3), the decision log, a rule layer with visible reasons, an
opt-in VLM judge and a later Bradley-Terry model are ranked in that order (§5). Its VLM cost figures
of USD 7 / 144 / 287 per 10,000 groups are estimates; §8 re-derives them from the skill's prices.

**Label reachability today** [measured: probes `prd-labels.py`, `prd-reach*.py` (kept in
`docs/audits/auto-select-prd-2026-11/`), run 2026-11-06, `os.path.exists` on every labelled path; the
group-portrait row is `prd-reach2.py`]:

| Label set | Total | Fully reachable on disk now |
|---|---:|---:|
| Proxy groups in `visual-gt.csv` | 150 | 99 (plus 1 with ≥ 2 members left) |
| Distinct member files | 479 | 369; 110 sit under a recycle-bin folder the walker now skips |
| Human-labelled groups | 30 | 22 |
| Human strict picks / extended picks | 8 / 12 | 5 / 6 |
| Human keep-all / keep-none / undecided | 12 / 3 / 3 | 11 / 3 / 2 |
| Human "subject is people or pet" | 11 | 7 |
| Human identical-pair metadata-rule groups | 4 | 2 |
| Human grouping complaints | 5 | 4 |
| Proxy groups tagged "group portrait" | 11 | 8 |

Re-measuring on these human labels rests on 5 strict picks: a smoke test, not evidence (§10, L-1).

## 5. Architecture proposal

```
scanner/dedup.py::classify ---- group_id, action, match_confidence, reason ----+
  T0 sha256 copy | T1 pHash high | T2 burst stack (#1042) | T3 hint            |
        v                                                                      |
scanner/scoring.py::apply_scoring_to_rows ---- score per row                   |
        v                                                                      |
NEW core/services/rule_layer.py::decide_group     -- decided + reason -------+ |
  R1 byte copies only: keeper = top score (today's T0 behaviour)             | |
  R2 original vs edit: xmp_derived, edit-name markers, RAW vs export         | |
  R3 people/pet present: every distinct capture protected                    | |
  R4 reason "burst stack (keep all): ": keep all, cover = pick_ref_winner    | |
        | undecided                                                          | |
        v                                                                    | |
settings "scan.cloud_analysis_opt_in" is True? -- no --> keep all, no call --+ |
        | yes: 1024 px, EXIF stripped, frame labels A/B/C only               | |
        v                                                                    | |
NEW core/services/cloud_judge.py -- cache key: member sha256 set + model     | |
        | pick + reason, or abstain                                          v v
NEW table auto_select_verdict (append-only) ----> review UI: cover + reason
        | user accepts / rejects with one key
        v
verdict rows + infrastructure/decision_log.py --> preference model, only at N >= 200
```

**Rule layer.** Pure Python over persisted columns, so `rescore()`-style re-runs need no file I/O.
It never deletes. Its output per group is `decided(rule_id, keeper_or_cover, reason)` or
`undecided`. R3 needs a people/pet signal, and none exists today (§3). Three sources, each **to
measure**: XMP face regions or person keywords from other photo apps; a people/pet flag from the
judge itself, only while the switch is ON; an on-device face detector (YuNet is 0.23 MB, but its
runtime was a 423 MB gated install [measured, Sept §2c]; no pet detector was evaluated). Without a
signal R3 cannot fire. R4 covers only rows whose reason starts with the #1042 burst-stack prefix;
every other equal-frame case is undecided and goes to the judge's `keep_all` answer.

**Judge.** Only undecided groups go to the judge; byte-identical and same-capture groups are R1/R2
territory, which leaves near-duplicate distinct captures. The judge returns a strict-schema answer: a
frame label or `keep_all` / `cannot_tell`, a one-line reason, and a people/pet flag. A swap check
runs a second pass with the frames reversed; if the passes disagree the verdict is abstain, because
VLM position bias is documented (Nov study §4).

**Autonomy rule.** The protected set becomes the score keeper ∪ the rule layer's keeper or cover
(R2, R4) ∪ the judge pick. The delete set is the unchanged `non_keepers_for_aggressive_delete` minus
that union, so the rule layer and the judge can only remove files from a delete set. Neither writes
`user_decision="delete"` and neither locks on its own.

**Learning substrate.** `auto_select_verdict` records every suggestion and the user's accept or
reject, even when the accept changes no manifest value (the R-LOG-1 gap in §3). The preference model
(Nov §5.2 S-E) stays DEFER until N ≥ 200 verdicts (Q9), then needs a learning curve at 50/100/200.

## 6. The opt-in cloud switch — UX contract

- **Keys:** `scan.cloud_analysis_opt_in` (bool, default `false`), built in PR #1043, and
  `scan.cloud_analysis_disclosure_version` (int), still to add. Both persist, unlike the auto-select
  checkbox, which resets every time (`docs/features/scan.md:9`).
- **Where:** the Scan dialog's **Advanced settings** section, after the aggressive option, as built
  in #1043 (Advanced block at `frontend/src/components/ScanDialog.tsx:719-745` on master). Both keys
  join `_WEB_SETTINGS_KEYS` (`app/web/routes/settings.py:17-45`).
- **Default:** OFF on every new install and on every upgraded settings file.
- **Turning ON:** the checkbox shows the full disclosure, only the user can tick it, and the
  disclosure version is stored with the tick. A higher disclosure version reads as OFF until the
  user ticks it again.
- **When OFF:** the judge tier does not run. Nothing is sent, queued or estimated. There is no
  silent fallback to a pixel ranker, another model or another engine. Undecided groups stay
  "keep all".
- **ON without an API key:** the scan log says
  `Cloud analysis is on but no API key is set; skipped.` and no call is made.

**What leaves the machine:** one JPEG per frame of an undecided group, resized to 1024 px on the long
edge after `ImageOps.exif_transpose`. Orientation is applied first, because the Sept face pass saw
portraits sideways without it [measured, Sept §9 item 7]. All EXIF, XMP, IPTC and GPS data and all
comments are removed. Frames carry labels A, B, C. The prompt text is fixed and versioned.

**What never leaves:** the original files, file names, folder paths, sha256 values, shot dates, GPS,
camera serials, the manifest, the decision log, and settings. Two PNG screenshots (`_screenshot_pair`)
are proposed never to be sent, because screen text is often private (open question Q7).

**Disclosure draft (English).**
> Cloud photo analysis (off by default). When this is on, photo-manager sends small copies of the
> photos in duplicate groups it could not decide to Anthropic's Claude service over the internet,
> to suggest which one to keep. Each copy is at most 1024 pixels on its long side and has all
> metadata removed: no location, date, camera or file name. Your original files, folder names and
> the app's database never leave this computer. Suggestions never delete anything. You can turn this
> off at any time. [Retention sentence, to fill in after §7's retention check.]

**Disclosure draft (繁體中文).**
> 雲端照片分析（預設關閉）。開啟後，photo-manager 會把無法自動決定的重複群組中的
> 照片縮小副本，透過網際網路傳送到 Anthropic 的 Claude 服務，以建議要保留哪一張。
> 每張副本長邊最多 1024 像素，並移除所有中繼資料：不含位置、日期、相機或檔名。
> 你的原始檔案、資料夾名稱與本程式的資料庫都不會離開這台電腦。建議不會刪除任何檔案。
> 你可以隨時關閉。〔資料保留期間說明：待第 7 節確認後補上。〕

**How to prove it.**

- A layer-1 egress test patches `socket.socket.connect` and `socket.create_connection` with a
  recorder. It runs the post-scan pipeline with auto-select ON, the switch OFF, and a fake judge
  registered. It asserts no non-loopback connect, and that the judge factory was never called.
- A static probe in `tests/test_source_probes.py` asserts that only `core/services/cloud_judge.py`
  imports the HTTP or SDK client, and that its entry function reads the switch first.
- A settings round-trip test follows `tests/test_web_settings_routes.py:165-180`. A layer-3 scenario
  extends `qa/web/scenarios/s17_scan_dialog_widgets.py`: box unchecked by default, disclosure shown.

## 7. Privacy and security review of the cloud path

- **Data minimisation:** as in §6. Image content cannot be anonymised: faces, house numbers and
  scene text go with the pixels, which is why the disclosure says "photos", not "features".
- **Retention, to verify with the provider before PR-5.** Per the skill, Fable 5.1 requires 30-day
  retention and has no zero data retention (ZDR), so it is not proposed. For Opus 5.5 "nothing new is
  documented - treat Claude Opus 5.5 as Claude Opus 5" (skill `shared/model-migration.md:2044`), and
  Opus 5's own terms are not stated. ZDR is set per organisation or workspace, not per request. The
  standard retention period and the training-use policy are **not verified**.
- **Key storage:** use the SDK's credential chain (`ANTHROPIC_API_KEY` env var or an `ant auth`
  profile). Never use `settings.json`. `GET /api/settings` echoes every allowlisted key
  (`app/web/routes/settings.py:58-72`), so a key stored there would be readable on the loopback API.
  The key is never logged. Tests check only that it is non-empty.
- **Dependency:** `requirements.txt` has no `anthropic` package; adding it is a gated install.
- **Injection:** image text could carry instructions. The answer must match a strict schema with a
  label from the frames sent; the reason is length-capped and rendered as plain text.
- **Failure modes:**

  | Failure | Behaviour |
  |---|---|
  | Network error, 429 or 5xx | SDK retries, then the group stays undecided and is logged |
  | `stop_reason: "refusal"` | Abstain, with the category recorded |
  | Malformed answer | Abstain |
  | Retired model id | Fails loudly; no automatic substitute |
  | Cost cap reached | Stage stops; the rest stays undecided |

  The skill recommends server-side `fallbacks` on Opus 5.5. That conflicts with "no silent
  fallback" unless the serving `response.model` is logged on every verdict (Q6).

## 8. Cost model

Prices from the skill, USD per million tokens, input / output:

| Model | Id | Price | Note |
|---|---|---|---|
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0.10 / 0.50 | prompts ≤ 100K tokens |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2.00 / 10.00 | |
| Claude Opus 5.5 | `claude-opus-5-5` | 4.00 / 20.00 | thinking cannot be disabled; default effort medium |
| Claude Fable 5.1 | `claude-fable-5-1` | 10.00 / 50.00 | not proposed: no ZDR, 30-day retention required |

The Batches API costs 50 % of every token and runs asynchronously [skill].

**Assumptions:**

| Input | Value | Label |
|---|---|---|
| Frames per group | 3 (v6: 24,690 grouped rows in 8,337 groups = 2.96) | measured |
| Prompt text | 500 tokens | estimate |
| Output, thinking included | 800 tokens | estimate |
| Image tokens at 1024 px (no formula in the skill; use `count_tokens`) | about 1,400 | not verified |
| Image tokens, upper bound | 4,784 (the skill's 2,576 px ceiling) | estimate, skill ceiling |

**Per group, one pass, real time, USD [estimate]:**

| Model | Image ≈ 1,400 tokens | Image = 4,784 (bound) |
|---|---:|---:|
| Haiku 5.5 | 0.00087 | 0.00189 |
| Sonnet 5.5 | 0.0174 | 0.0377 |
| Opus 5.5 | 0.0348 | 0.0754 |

A swap-checked batch run costs the same as one real-time pass: two passes at half price.

**Worked example, the owner's library, swap-checked batch, USD [estimate]:**

| Scope | Groups | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 |
|---|---:|---:|---:|---:|
| Every group (v6, worst case) | 8,337 | 7.25 – 15.8 | 145 – 314 | 290 – 629 |
| Undecided only: near-dups plus bursts | 1,031–1,035 | 0.90 – 1.96 | 18.0 – 39.0 | 36.0 – 78.0 |

That scope is 1,020 v6 near-dup groups [measured, `main-v6-measure-output.txt:43`] plus 11–15 burst
groups (`main-t2-burst-design.md:20`).

**Reality check [measured, Sept].** The Opus judge run cost USD 0.0647 per group: 150 groups at
3.27 members, low effort, 10 groups per call. The model generation and its price that day are not
recorded in the parts of the study read here. At that rate about 1,033 groups cost USD 67, near
this table's upper bound. Read the Opus estimates as possibly low by up to 2×.

**Cache.** The key is `sha256(sorted member source_hash) + model id + prompt version + thumbnail
recipe version`. An unchanged group is never re-judged on a rescan. A regrouped group gets a new key,
which is correct, because it is a new question. The cache hit rate on a routine rescan is not
verified.

**Guard.** Before the judge stage starts, the scan log prints the group count and the estimated cost.
A hard per-run cap (setting, default proposed USD 5) stops the stage.

## 9. Acceptance criteria (mechanically checkable)

1. **AC-1, egress:** with the switch OFF there are zero non-loopback connects and the judge factory
   is never called. Checked by the layer-1 egress test (§6).
2. **AC-2, default:** OFF on a fresh settings file and on an upgraded one; disclosure shown; version
   stored. Checked by the pytest round-trip, vitest and the s17 extension.
3. **AC-3, key:** the API key never appears in `_WEB_SETTINGS_KEYS`, settings files or logs (probe).
4. **AC-4, autonomy:** on every fixture, the delete set with the rule layer and the judge together
   is a subset of today's delete set. One property test runs both paths through
   `non_keepers_for_aggressive_delete`.
5. **AC-5, no delete writes:** no judge or rule path writes `user_decision="delete"` (static probe).
6. **AC-6, people/pet:** zero proposed deletions of a distinct capture in the 7 reachable human
   people/pet groups and the 8 reachable group-portrait proxy groups (§4), counted on today's
   aggressive delete set minus the §5 protected set. Today's path alone fails it (REVIEW/high
   near-dups with people are marked, `auto_select.py:285-292`); it passes only once R3 or Q3 lands.
7. **AC-7, keep-all:** abstain or keep-all on ≥ 9 of the 11 reachable human keep-all groups. Same
   offline run.
8. **AC-8, picks (smoke):** judge rank-1 = human pick on ≥ 4 of 5 reachable strict picks. Same run,
   reported with n and never as a rate.
9. **AC-9, picks (proxy):** judge top-1 = Sept proxy rank-1 on ≥ 0.70 of the reachable clear-winner
   proxy groups, from the 99 reachable groups.
10. **AC-10, picks (human):** on the L-1 round (≥ 60 undecided groups), precision of non-abstain
    picks ≥ 0.80, and abstain on ≥ 0.70 of the frames-equal groups. Scored by a script; 0.80 is the
    Sept S7 reopen bar.
11. **AC-11, identical frames:** byte-identical groups never reach the judge (rule-layer unit test).
12. **AC-12, logging:** every rule or judge verdict is written to `auto_select_verdict` with rule id
    or model id, prompt version, reason and content key. Unit test plus a row count after a scan.
13. **AC-13, visible reason:** the review UI shows the cover's or keeper's reason (vitest, layer 3).
14. **AC-14, cache:** a rescan over unchanged groups makes 0 API calls (counting fake judge).
15. **AC-15, cap:** the cost cap stops the stage and leaves the rest undecided (priced fake judge).

AC-6 to AC-10 use thresholds proposed here. The owner sets the final values (Q4).

## 10. Phased plan (each PR ≤ 1 day; a measurement step before each model step)

1. **PR-1b, finish #1043.** #1043 ships the key, checkbox, a short en/zh_TW help text, allowlist,
   docs, tests and the s17 extension. Deltas: add `scan.cloud_analysis_disclosure_version`; add the
   provider and the §7 retention sentence to the help text once verified; keep the placement after
   the aggressive option unless the owner moves it.
2. **PR-2, verdict store.** Append-only `auto_select_verdict` table with triggers like
   `decision_log.py:76-83`, a content-key helper and a read API, carried across rescans. Gate: the
   migration-safety skill.
3. **PR-3, rule layer v1.** `decide_group` with R1, R2 and R4, writing verdicts; R3 says "no signal".
4. **PR-4, reasons in the UI.** Serialise the verdict reason in `review_view.py`; show it under the
   group header and on the cover. Gate: a qa scenario.
5. **M-1, people/pet census.** Read-only exiftool probe of grouped v6 files for face/person tags.
6. **M-2, judge measurement.** Haiku 5.5, Sonnet 5.5 and Opus 5.5 on the 99 proxy and 22 human
   groups, swap-checked, `count_tokens` first. Reports AC-6 to AC-9, the abstain rate and the
   measured cost per group. Gate: **the owner's yes**, because photos leave the machine and the SDK
   in a scratch venv is a gated install.
7. **L-1, human label round v2.** A fixed instrument with keep-all, tie, people/pet and edit-relation
   options, over ≥ 60 groups from the rule layer's undecided set. About 1 hour of owner time.
8. **PR-5, cloud judge backend.** `cloud_judge.py` behind the switch: thumbnail builder, strict
   schema, abstain, swap check, Batches API, and the egress test (AC-1). Gate: **gated dependency
   install**.
9. **PR-6, cache, preview and cap.** Content-key cache, scan-log cost line, hard cap (AC-14, AC-15).
10. **PR-7, accept or reject.** One key per group, a verdict row, and the §5 protected set: score
    keeper ∪ rule keeper or cover ∪ judge pick (AC-4). Gate: a rules-register update.
11. **Later:** the preference model at N ≥ 200 verdicts, learning curve first (reopens Sept S5).

M-2 decides the model. Haiku 5.5 is 40× cheaper than Opus 5.5 per token, but no Haiku-class judge was
ever measured on these photos. Only the Opus proxy has a human calibration.

## 11. Open questions for the owner, and what is not verified

**Open questions.**

1. Should the judge run automatically after a scan when the switch is ON, or only on a button press?
2. The key namespace: `scan.cloud_analysis_opt_in` as asked, while the existing Scan-dialog key is
   `ui.scan_dialog.autotune_read_knee`. The proposal keeps `scan.`, because this is a policy, not a
   UI preference.
3. Until a people/pet signal exists, should aggressive auto-select stop proposing deletes of distinct
   captures, keeping only T0 and same-capture T1? Today REVIEW/high near-dups are eligible
   (`auto_select.py:285-292`).
4. The final thresholds for AC-6 to AC-10.
5. Is a 1024 px long edge still the intended size if M-2 shows a smaller size is as good?
6. Server-side model fallbacks: allow them with the serving model logged, or forbid them?
7. Should two-screenshot groups never be sent?
8. The identical-pair metadata rule. In September the owner said it was not yet settled. R2 needs a
   priority order between original, edited copy, metadata completeness and post-processing potential.
9. N for the preference model (proposed 200).
10. People signal: the VLM flag only (needs the switch), or the 423 MB on-device runtime (a gated
    install)?

**Not verified.**

- Image tokens per 1024 px frame (the skill states only the 2,576 px ceiling); the cache hit rate
  on a routine rescan.
- Anthropic's retention period and training-use policy for standard API inputs; Opus 5.5 ZDR terms.
- Any Claude 5.5-generation model's accuracy, position bias and abstain behaviour as a judge. Only
  an older Opus judge was measured, and its generation and price are not recorded here.
- Prevalence of face-region or person metadata in the library (M-1).
- Whether the 110 recycle-bin label paths still exist under another path. The 5 reachable strict
  picks are not a sample of anything.
