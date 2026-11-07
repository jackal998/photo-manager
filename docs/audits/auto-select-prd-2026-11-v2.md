# Auto-select PRD v2, written from the owner's answers (2026-11-06)

Base: master `4fc1dcc`. Read-only study; only this file was written. Supersedes `auto-select-prd-2026-11.md`
(v1) and `auto-select-prd-2026-11/direction-review.md` wherever they disagree with the answers.

**Spec.** `auto-select-prd-2026-11/requirements-answers-2026-11-06.md`, cited by answer id: [A1] … [H1].
The three corrections the owner made before answering (STATE.md "ALIGNMENT RESET") are cited as [R1] no
hacky rules, [R2] the user chooses the model, [R3] no new UI step. Every owner requirement cites an id;
design choices are unmarked, and our readings of an answer are labelled "our reading".

**Labels.** [measured] run or counted on real data, source named · [estimate] arithmetic from stated
inputs · [not verified] nobody checked it · [code] read with `file:line` on `4fc1dcc` · [skill] taken
from the `claude-api` skill, price table cached 2026-10-06.

## 1. Decision summary

1. Three scan modes: default = groups only; auto-select = most actions plus a confirm list;
   aggressive = every action. Nothing runs until the user presses Execute. [A1]
2. "Needs a human" is a confidence threshold, never a target ratio. Below it the row is REVIEW. [A2][C5]
3. The engine judges picture quality and metadata facts only. It never assumes what a photo shows. [A3][G3]
4. Every preference is a rule toggle in Advanced settings with our default, never hardcoded. [B-sum][G3]
5. Pure local mode ships first and must be usable on its own. [C2][G1]
6. Cloud is an optional plug-in behind the #1043 switch; the user picks provider and model. [C3][C4][E1]
7. Cloud spend per full-library scan stays under USD 1; scan time does not matter. [C7][C8]
8. The engine runs inside the scan; the view opens already marked. No new step. [C6][R3]
9. Each row shows action, a one-line reason and a confidence level. Engine picks are never locked. [D1][D3]
10. Every engine decision is written to the decision log with its reason and engine id. [D4]
11. Prerequisite: EXACT must mean pixel-identical content; a graded edit or another shot is a near-dup.
    2,748 of 11,917 EXACT rows are pHash-0 format duplicates [measured]; the gate is being built (§10). [A4]
12. Validation runs on all 312 test-set groups; the threshold is set after the numbers are seen. [F2][F3]

## 2. Product model

```
ScanDialog.tsx  Advanced settings: mode (off|auto|aggressive), engine, rule toggles
      | POST /api/scan  auto_select_enabled, auto_select_aggressive_delete
      v
scan_runner.py   hash -> scanner/dedup.py::classify -> scoring.apply_scoring_to_rows
      | rows with group_id, action, match_confidence, reason, score
      v
NEW engine stage (inside the scan)  rule toggles -> local judges -> [cloud plug-in]
      | per row: decision, reason, confidence      mode = off: stage skipped
      v
threshold:  confidence >= T -> decision written   |  < T -> REVIEW (confirm list)
      | scanner/manifest.py::write_manifest   no lock   decision_log + engine_verdict
      v
review view  action + one-line reason + confidence   user edits freely
      | user presses Execute
      v
POST /api/execute   the only path that moves or deletes a file
```

- **Default mode** writes no decision. The view shows groups and the dedup reason only. [A1]
- **Auto-select** writes decisions at or above the threshold. Rows below it stay undecided and form the
  confirm list ("needs your look"). [A1][A2] Our reading of A1: "list the items to confirm" means those
  rows get no decision.
- **Aggressive** writes a decision on every row [A1]. Our reading of A1 with C5: a below-threshold row gets
  the engine's best guess, marked low confidence; an explicit `cannot_tell` stays REVIEW. [A1][C5]
- **The Execute boundary** already exists: decisions are data; only `POST /api/execute`
  (`app/web/routes/execute.py:94`) acts on files. Nothing new may act on files. [A1]
- **Mode and engine are independent settings.** Any mode runs with any engine. [D2]
- **Rule toggles** sit in Advanced settings, after the mode controls. [B-sum][R3]

## 3. What exists today [code]

- **Modes.** The scan request carries `auto_select_enabled` and `auto_select_aggressive_delete`
  (`app/web/models.py:282-283`); the dialog sends aggressive only with its parent
  (`frontend/src/components/ScanDialog.tsx:518-520`). Both reset on every open (`docs/features/scan.md:9`).
- **Keeper.** `core/services/auto_select.py:155` picks the top metadata score per group. The scan stamps it
  `KEEP` (`core/app_service/scan_runner.py:1369-1376`).
- **Auto-lock, which [D3] removes.** `build_auto_select_writes` locks every keeper
  (`core/services/auto_select.py:97`), written at `scanner/manifest.py:192-197` with verb
  `scan_auto_select`.
- **Aggressive delete set.** `non_keepers_for_aggressive_delete` (`core/services/auto_select.py:248-292`)
  keeps only EXACT / REVIEW_DUPLICATE rows (#536, `:199`), drops `match_confidence == "low"` (#517, `:291`)
  and protects a RAW keeper's lossy partner (#824, `:202`).
- **Scorer.** Eight metadata weights (`scanner/scoring.py:89-98`): resolution, file size, EXIF
  completeness, date provenance, GPS, filename (copy-marker penalty, `:363-369`), path, Live Photo.
  It has no pixel signal.
- **Byte copy EXACT.** `scanner/dedup.py:428-458`: equal sha256 gives EXACT/high; the keeper is the highest
  source priority, then the lex-min path (`:445-448`).
- **Format duplicate EXACT, the [A4] defect.** `scanner/dedup.py:597-684`: two lossy files with pHash
  hamming 0 and different sha256 become EXACT "format duplicate of …" (`:679-684`). The only colour check
  is a mean-colour distance gate of 30 (`:601`, `:652-654`), loose enough for a colour grade. It also lets
  consecutive shots through: on the test set, IMG_3788 vs IMG_3789 and P1011080 vs P1011083 are distinct
  captures labelled EXACT, because pHash is 0 and the shot times agree within 2 s [measured, §10].
- **RAW + lossy** stays undecided in one group (`scanner/dedup.py:615-624`, #824).
- **Burst stack (#1042).** `scanner/dedup.py:1187` marks keep-all with prefix `burst stack (keep all): `
  (`:1086`).
- **Screenshot pairs** are demoted to REVIEW/low at the label layer: `_screenshot_pair`
  (`scanner/dedup.py:284`) is applied through `_demotion_note` (`:293`, `:303`). The `duplicate_of` edge
  still groups them; only the label and confidence move.
- **Live Photo pairs** join by a pair edge (`scanner/dedup.py:1002`); the MOV is a Ref-tier passenger that
  aggressive never deletes (#536).
- **View.** `PhotoRecord` has `match_confidence` but no `reason` (`core/models.py:67`); `_build_file_row`
  (`core/app_service/review_view.py:122-165`) sends neither. No reason or confidence reaches the screen.
- **Decision log.** Verbs and sources at `infrastructure/decision_log.py:31-47`; table at `:51-71` with no
  reason, confidence or engine column; append-only triggers at `:76-83`. Under R-LOG-1 an unchanged row
  appends nothing (`docs/behaviour/rules.md:168-170`), so a keep-all verdict leaves no trace.
- **Cloud switch (#1043).** `scan.cloud_analysis_opt_in`, default false (`app/web/routes/settings.py:36-42`,
  `:56-58`), checkbox and disclosure in Advanced settings (`frontend/src/components/ScanDialog.tsx:808-844`).
  Nothing reads it.

## 4. The engine

The engine fills one decision per row from three kinds of evidence. Rule toggles (§6) run first, because
they encode the user's preferences. Mechanical gates and quality judges then decide within what the rules
leave open. [G3] No rule may name a content class such as "original vs edit". [R1][A3]

### 4.1 Local tier, built first [C2][G1]

| Signal | Decides | Cost per image | Evidence |
|---|---|---|---|
| Metadata score (§3) | completeness, date, filename, resolution [B1] | free | [code] |
| Time correctness | EXIF date vs file time vs tz shift [B1] | free | to measure |
| Defect flags: tile VoL, clip, noise MAD | veto a gross defect | ~8.2 ms | 1.00 / 0.97 / 0.98 synthetic |
| YuNet face count, size, score | people (B8); framing | ~61 ms (T1+T2) | 0.476 / 0.452 vs 0.337 [proxy] |
| FER expression, face-crop sharpness | nothing useful | same pass | 1.01× / 0.88× / 0.95× chance [proxy] |
| Open IQA / aesthetic models | quality rank | to measure | none |
| Local vision-language models | "worth keeping" [G3] | to measure | none |

Open IQA and aesthetic candidates, all **to measure**: MUSIQ, TOPIQ, CLIP-IQA, a NIMA-class model and the
LAION aesthetic predictor. The measured rows come from the Sept study (S2b, S4).

What the September study already settled [measured, Sept §6]:

- **No pixel ranker alone.** The best pixel scorer reached top-1 0.405 against a 0.378 chance line,
  p = 0.29 (S2a). Its margin was anti-correlated with correctness on the synthetic set, so a pixel margin is
  not a confidence (§5.4 there).
- **Defects work as a veto.** As a defect flag the same signals are strong on synthetic degradations (S2b).
- **Faces carry framing signal only.** YuNet is 0.23 MB and FER 4.79 MB. Eyes-open is not measurable from
  YuNet's 5 landmarks. The T1/T2 runtime venv measured 423 MB on disk, a gated install. No pet detector was
  evaluated.
- **IQA needs a heavy runtime.** `pyiqa` pulls torch, a 122 MB wheel before any weights. That is a gated
  install, and every open model needs its own size, licence and accuracy measured.

### 4.2 Cloud tier, optional [C3][C4][E3]

- **A plug-in per provider:** Claude, OpenAI, Gemini, or a local server speaking the same interface. The
  user picks provider and model in settings. [C3][C4][R2]
- **It runs only when** the #1043 switch is on and a key is set (§8). Otherwise the stage is not entered.
- **It is built only if** validation (M-2, §11) shows the local tier is not usable for some groups. It
  then sees only the groups the local tier left below the threshold. [C3]
- **It answers** a pick, `keep_all` or `cannot_tell`, with a one-line reason. `cannot_tell` means REVIEW. [C5]
- **A cache** keyed by sorted member sha256, model id, prompt version and thumbnail recipe means an
  unchanged group is never sent twice.
- **A cost preview and a hard cap** sit before the stage. The default cap is USD 1 [C7]. Groups past the cap
  stay REVIEW.

### 4.3 The cost ceiling: what fits USD 1 per full scan [C7]

Budget per group: USD 1 / 8,337 groups (v6, [measured]) = **USD 0.00012**.

Prices per million tokens, input / output [skill]: Haiku 5.5 `claude-haiku-5-5` 0.10 / 0.50 (prompts up to
100K tokens); Sonnet 5.5 `claude-sonnet-5-5` 2.00 / 10.00; Opus 5.5 `claude-opus-5-5` 4.00 / 20.00;
Fable 5.1 `claude-fable-5-1` 10.00 / 50.00. The Batches API costs 50 % [skill]. OpenAI and Gemini prices
are [not verified]. Each plug-in must state its own price before it runs.

Inputs: 3 frames per group (v6 2.96 [measured]); 1,400 image tokens per 1024 px frame [not verified]; the
skill gives no per-image formula, so `count_tokens` must measure it. Prompt 500 tokens, output 800 with
thinking [estimate]. All figures below are Batches prices, USD [estimate].

| # | Scenario | Per group | Full scan | Fits USD 1? |
|---|---|---:|---:|---|
| a | Haiku 5.5, 1024 px, thinking on, one pass | 0.000435 | 3.63 | no, 3.6× over |
| b | Haiku 5.5, ~512 px, thinking off, 60-token answer, one pass | 0.0000825 | 0.69 | yes |
| c | as b, swap-checked (two passes) | 0.000165 | 1.38 | no |
| d | Haiku 5.5, 1024 px, swap-checked, uncertain only | 0.00087 | 0.90 (1,032) | yes, ≤ 1,149 groups |
| e | Sonnet 5.5, 1024 px, one pass | 0.0087 | 72.5 | no; USD 1 = 114 groups |
| f | Opus 5.5, 1024 px, one pass | 0.0174 | 145 | no; USD 1 = 57 groups |
| g | Fable 5.1, 1024 px, one pass | 0.0435 | 363 | no; USD 1 = 22 groups |

Row b assumes ~350 image tokens per 512 px frame and a 300-token prompt [estimate].

- **What USD 0.00012 buys** on Haiku 5.5 Batches: about 2,100 input tokens plus a 60-token answer. That is
  one group of small thumbnails, with thinking off. Haiku 5.5 allows thinking off only at effort high or
  below [skill].
- **The 1,032 uncertain groups** are 1,020 v6 near-dup groups (local probe output of `main-v6-measure.py`, not tracked) plus 12
  burst groups (`docs/audits/testset-2026-11/summary.md:110`; the selector took every burst group it found)
  [measured]. The local tier's real uncertain count replaces this figure after validation.
- **Conclusion.** Only a Haiku-class model fits the ceiling. It fits either on every group with small
  thumbnails and one pass, or on about 1,100 uncertain groups at 1024 px with a swap check. Sonnet, Opus
  and Fable fit only a few dozen to about a hundred groups per USD 1. At the image-token upper bound
  (4,784 per frame, the skill's 2,576 px ceiling) the uncertain-only Haiku run costs 1.95, over the ceiling.
- **Rescans.** A rescan of unchanged groups costs nothing (cache). The cache hit rate is [not verified].
- **Cost is shown to the user** before the stage runs, as a group count and an estimate. [G3]

## 5. Confidence and REVIEW [A2][C5]

Confidence is per row, in three levels: high, medium, low. It is the lower of two parts:

1. **Grouping confidence.** This is today's `match_confidence`. A row grouped only by a weak edge cannot be
   decided with more certainty than its group. The screenshot-pair demotion counts here only while its
   B7 toggle is on (§6); by default it is off, so screenshot pairs are not capped at low. [B7]
2. **Decision confidence.** It comes from how the decision was reached:
   - A rule with a certain input is high, for example a byte copy under B3.
   - Independent judges that agree are high; two that disagree are low.
   - `cannot_tell` from any judge is low.
   - A single judge's own score margin is never used alone, because the Sept data showed it anti-correlated
     with correctness.

The levels are calibrated on the 312 labelled groups (§9). A level means "of the rows that validation put
here, this share matched the owner". That share is reported per level, never promised in advance. [F3]

- **The threshold is one setting** in Advanced settings: "Ask me to confirm when confidence is below:
  high / medium". Its default is chosen by the owner after validation. [F3][R3]
- **A row below the threshold is REVIEW.** In auto-select it carries no decision and appears in the confirm
  list (our reading of A1). In aggressive mode it gets the engine's best guess marked low confidence, and
  only an explicit `cannot_tell` stays REVIEW (our reading of A1 with C5). [A1][C5]
- **Cap or failure means REVIEW.** A cloud failure, a refusal, a malformed answer or a reached cost cap
  leaves the group REVIEW and logs why. Nothing falls back silently to another model. [C5][R2]
- **Coverage is the success measure.** Coverage is the share of groups decided above the threshold. The
  owner's target is about 90 % of "similar enough to need a choice" situations [G3]. It is reported, not
  forced; the threshold never moves to hit a ratio. [A2]

## 6. Rule toggles catalogue [B-sum]

Each rule is a named, versioned unit with an on/off switch and our default. A rule that is off is skipped;
the next rule or judge decides. The rule set id and version go into every log entry (§7). [B-sum][D4]

| Id | Rule | Default | What it does | If ignored |
|---|---|---|---|---|
| B1 | Keeper criteria | all on | each factor below feeds the judges, with its own switch | factor skipped |
| B2 | Quality vs metadata | on | quality pick and metadata pick differ: keep both | combined score decides |
| B3 | Byte copies | on | extra copies DELETE; which copy stays: see below | copies undecided |
| B4 | RAW + JPEG | on | keep both (today's #824, made visible) | judged as near-dups |
| B5 | Burst stack | on | keep all frames (today's #1042) | judged as near-dups |
| B6 | Live Photo MOV | on | the MOV gets its photo's decision | MOV kept (today) |
| B7 | Screenshot demotion | off | on: two-PNG pairs get low confidence (today) | judged like photos |
| B8 | People and pets | on | DELETE only for an EXACT copy; a near-dup is kept | no people rule |

- **B1 factors** (owner's list): sharpness and focus, exposure, composition, expression and eyes,
  resolution and format, original vs edit, completeness, metadata completeness and correctness, filename
  authenticity, time correctness. "Original vs edit" is a quality factor the judges weigh, not a rule that
  names a winner. [B1][R1]
- **B3 which copy stays.** B3 names folder priority and metadata completeness as considerations and asks
  for samples first. Our provisional default: the scan's source order, then metadata completeness.
  Validation reports how often the owner's kept copy matches it. [B3]
- **B7 screenshots are ordinary photos** [B7]. Today's two-PNG demotion becomes a toggle, default off.
  Its `duplicate_of` edge still groups the pair either way (§3).
- **Videos [G2].** A video has no pHash (`scanner/dedup.py:84`, `:627`), so it is never a pHash candidate.
  A byte-copy video follows B3, a Live Photo MOV follows its photo (B6), and any other video is REVIEW.
- **B8 defaults to protect** [B8][B-sum]. Until a people and pet signal exists (YuNet is a gated install,
  §4.1; no pet detector exists), every near-duplicate counts as possibly showing a person or pet, so only
  an EXACT copy may be DELETE. This follows B8's own reason: delete nothing while grouping is not good
  enough. Toggle off means no people rule. Validation reports the coverage this costs (§9).
- **The A4 gate protects B8 and today's aggressive delete set.** Both trust EXACT. The aggressive set
  admits EXACT rows not marked low (`auto_select.py:199`, `:291`), and today 2,748 EXACT rows are format
  duplicates [measured], some of them other shots (§10).

## 7. Output and traceability

- **In the view [D1]:** every grouped row shows the action, a one-line reason and the confidence level.
  The reason names the deciding rule or judge, for example `B3 byte copy; kept copy is in a preferred
  folder` or `quality: sharper frame (local IQA)`. It is plain text, capped at one line.
- **No lock [D3]:** engine decisions are written unlocked, so the user can change them with one click.
  This removes the keeper lock at `core/services/auto_select.py:97`. `docs/features/scan.md:10` and its
  pinned tests change in the same PR.
- **Decision log [D4]:** three nullable columns are appended to `decision_log`: `reason`, `confidence` and
  `engine` (a rule id or `provider:model@prompt-version`). They are filled on `scan_auto_select` rows.
- **Engine verdict table [D4]:** a new append-only `engine_verdict` table holds one row per group per scan:
  content key (sorted member sha256), mode, rule set version, engine, per-row decision, reason, confidence,
  cost. It records keep-all and REVIEW, which R-LOG-1 cannot record. Its triggers copy
  `infrastructure/decision_log.py:76-83`. The migration goes through the `sqlite-migration-safety` gate.
- **Execute stays separate.** Its existing log rows link back to the verdict through the content key. [A1]

## 8. Privacy

- **Disclosure [E1]:** the #1043 text is accepted. It gains one provider sentence per plug-in.
- **What is sent [E2]:** one JPEG per frame, at most 1024 px on the long edge (smaller if the cost tier
  needs it, §4.3). The thumbnail is rotated by `ImageOps.exif_transpose` first, because the Sept face pass
  saw portraits sideways without it. All EXIF, XMP, IPTC, GPS data and comments are removed. Frames carry
  labels A, B, C.
- **What never leaves:** original files, file names, folder paths, sha256 values, shot dates, the
  manifest, the decision log and settings. [E2]
- **No content exceptions [E3]:** once the user opts in, every group in scope may be sent, screenshots and
  people included. v1's proposal to never send screenshots is dropped.
- **Retention and training terms [E5]:** each plug-in shows its provider's retention period and
  training-use policy in the disclosure. Each is verified before the plug-in ships.
  - Fable 5.1 requires 30-day retention and is not available under zero data retention [skill].
  - For every other model, retention and training use are [not verified].
- **API key [E4]: OPEN.** The candidates are below; open question 1 asks the owner.
  - The SDK credential chain or an environment variable.
  - The OS credential store (Windows Credential Manager through `keyring`, a gated install).
  - A file outside `settings.json`.
  - `settings.json` is ruled out under any allowlisted key, because `GET /api/settings` echoes every
    allowlisted key (`app/web/routes/settings.py:85`, inside `get_settings` at `:62`).
  - The key is never logged; tests check only that it is non-empty.
- **Prompt injection:** text inside a photo could carry instructions. The answer must match a strict schema
  with a frame label that was sent. The reason is length-capped and rendered as plain text.
- **Proof:** a layer-1 egress test patches socket connects. With the switch off, it asserts no non-loopback
  connect and that the plug-in factory is never called. A static probe asserts that only the plug-in
  modules import an HTTP or SDK client.

## 9. Validation plan, 312 groups [C1][F1][F2][F3][F4]

Test results decide who decides [C1]. The method is ours to choose [F1], cheap and repeatable:

1. **Owner labels, blind.** Scan the test-set copy in default mode, so no engine output is shown. The owner
   reviews all 312 groups with the normal Keep and Delete actions and presses nothing else. The manifest's
   `user_decision` per row is the label set. The owner also writes a one-word note on any group they could
   not decide; those are excluded from precision and counted separately. This takes about 1–2 hours of
   owner time [estimate].
2. **Offline scoring.** The engine runs offline against that manifest and the test-set files. A script
   compares the engine's per-row decision with the label. Any engine version can be re-scored at no
   owner cost.
3. **Metrics per confidence level and per category** (fixtures, burst, exact copies, near-dup, screenshots,
   Live Photo, RAW+JPEG, undated, videos; `docs/audits/testset-2026-11/summary.md`):
   - **harmful errors:** the engine said DELETE, the owner kept. This counts most.
   - **missed deletes:** the engine kept, the owner deleted.
   - **coverage:** the share of groups decided above each candidate threshold.
   - **B3 kept-copy agreement**, **B8 detector hits**, and **B8's coverage cost**: groups left undecided
     because the protect default blocked a delete.
   - **cost per group**, for every candidate.
4. **The threshold table** goes to the owner: for each candidate threshold, harmful errors, missed deletes
   and coverage. The owner picks the threshold [F3]. Coverage is compared with the G3 target of about 90 %
   but never promised.
5. **Candidates are run in local-first order.** Each candidate is tested on the same labels:
   - first the metadata score alone (today's baseline);
   - then the defect veto;
   - then YuNet and open IQA models (gated installs);
   - then cloud models (F4 allows the thumbnails; the SDK is a gated install).
   Estimated cloud cost for 312 groups, 1024 px, swap-checked Batches: Haiku 5.5 USD 0.27,
   Sonnet 5.5 5.43, Opus 5.5 10.86, Fable 5.1 27.14 [estimate].

The test set holds 312 groups whose partition matched v6 under grouping 7 [measured,
`docs/audits/testset-2026-11/summary.md:109`], including 12 burst groups (`:110`).

## 10. Prerequisites and open measurements

1. **EXACT means pixel-identical [A4]: measured; fix being built.** Probe:
   `docs/audits/testset-2026-11/probe_exact_colour.py`. All figures in this item are [measured].
   - **v6 manifest:** 11,917 EXACT rows = 9,169 byte copies + 2,748 pHash-0 format duplicates (heic+heic
     1,974, jpg+jpg 663). 266 format-duplicate pairs differ in dimensions; 407 differ > 20 % in file size.
   - **Test set:** 65 of 85 format-duplicate pairs decoded to a 32×32 RGB signature (18 DNG files have no
     preview, 2 are unreadable): 50 identical-ish (16 with different dimensions), 6 honest re-encodes,
     9 pixel-different.
   - **Pixel-different pairs include distinct captures.** IMG_3788 vs IMG_3789 and P1011080 vs P1011083
     are consecutive shots. They are labelled EXACT because pHash is 0 and the shot times agree within 2 s.
     So the defect is wider than colour grading: today EXACT can name another shot.
   - **The gate:** merged as #1048 (branch
     `fix/exact-requires-pixel-identity`; GROUPING "7" to "8"). The
     hasher keeps a 32×32 RGB signature on `HashResult` at scan time only; it is not persisted. A format
     duplicate stays EXACT only when the dimensions are equal and the mean absolute RGB difference of the
     signatures is at most T = 6. Otherwise it becomes REVIEW_DUPLICATE/high with the reason "same pHash,
     pixels differ (edited, resized or another shot)". Byte copies are untouched.
   - **Threshold sweep on the 65 pairs:**

     | T | Stay EXACT | Pixel-different demoted | Re-encodes demoted | Identical-ish demoted |
     |---:|---:|---:|---:|---:|
     | 3 | 35 | 9 of 9 | 5 of 6 | 16 of 50 |
     | 6 | 40 | 8 of 9 | 1 of 6 | 16 of 50 |

   - **The 16 identical-ish pairs** that drop at every T are the ones with different dimensions. They are
     resized copies, removed by the equal-dimensions clause. Under the "pixel-identical" definition they
     become near-duplicates, so B8 and the aggressive delete set stop treating them as exact copies.
   - **One pixel-different pair passes at T = 6.** It needs REVIEW from another signal or a lower T. The
     full-library effect of the gate is not measured; the sweep covers the 65 test-set pairs only.
   - **Until the gate merges,** B8 must not treat a format duplicate as an exact copy, and the aggressive
     delete set (`core/services/auto_select.py:199`) still admits them.
2. **Grouping quality.** The owner's worst past errors were wrong groups [A4]. The engine decides only
   within groups, so the v6/grouping-7 partition is the floor. The 312-group labels expose any residue.
3. **On-device model availability.** `onnxruntime`, `cv2` and `torch` are absent from the venv (Sept
   Phase 0). Each install is gated. Sizes, licences and CPU time for every open IQA model are
   [not verified]. No pet detector exists.
4. **Image tokens per 1024 px and per 512 px frame** are [not verified]. Measure them with `count_tokens`
   before any cloud run.
5. **Provider retention and training terms [E5]** are [not verified], except Fable 5.1's 30-day requirement.
6. **API key storage [E4]** is OPEN (open question 1).
7. **Definitions to measure:** "filename authenticity" and "time correctness" [B1]. The scorer has a
   copy-marker filename penalty (`scanner/scoring.py:363-369`) and date provenance (`:306`). Whether they
   match the owner's meaning is checked in validation.

## 11. Phased plan (each PR ≤ 1 day; local first [G1]; gates in brackets)

1. **PR-1, why and how sure, in the view [D1].** Carry `reason` and `match_confidence` from the manifest
   through `PhotoRecord` and `_build_file_row` to the file row. Show the reason as a one-line cell or
   tooltip and the confidence as a level. No schema change. The owner sees why every row is grouped, and
   that burst groups read "keep all". [features.md entry, qa scenario]
2. **PR-2, EXACT means pixel-identical [A4].** Merged as #1048 (branch
   `fix/exact-requires-pixel-identity`).
   Any other pHash-0 pair becomes REVIEW_DUPLICATE/high; GROUPING "7" to "8" (§10). The owner sees
   graded edits, resized copies and consecutive shots as near-duplicates. [rules register, migration-free]
3. **PR-3, the three modes and the threshold [A1][A2][D3].** Remove the keeper lock. In auto-select mode,
   rows below the threshold get no decision and appear in a "needs your look" filter. Confidence is the
   grouping confidence until the engine exists. The threshold setting is added to Advanced settings and
   persisted. [features.md, rules register, qa scenario]
4. **PR-4, rule toggles v1 [B2–B8].** A settings key `scan.auto_select.rules` with our defaults, shown in
   Advanced settings. The rules are wired into `core/services/auto_select.py`. B6 and B2 are new behaviour;
   B3, B4 and B5 make today's behaviour switchable; B7's demotion becomes a toggle, default off. B8 ships
   with its protect default. [features.md, qa scenario]
5. **PR-5, traceability [D4].** Add the `decision_log` columns and the `engine_verdict` table. Reasons now
   name the rule. [`sqlite-migration-safety`]
6. **M-1, owner labels and baseline [F2].** The blind labelling pass on 312 groups (§9). The offline
   scorer reports the metadata-only baseline and the threshold table. About 1–2 hours of owner time.
7. **PR-6, local defect veto.** The S2b signals as a veto, with no new dependency, scored against M-1.
8. **M-2, local models, after a gated install [C3].** YuNet for B8, then open IQA and aesthetic
   candidates and a local vision-language model, each scored against M-1 for accuracy and CPU time.
9. **PR-7, engine selection in settings [C4][D2].** A provider/model picker that lists the installed local
   engines. Each choice is named in the reasons.
10. **M-3, cloud candidates on 312 groups [C3][F4].** Runs only if M-2 shows the local tier is not usable
    for some groups. Needs a gated SDK install and `count_tokens` first. Reports accuracy, abstain rate
    and cost per group against §4.3.
11. **PR-8, cloud plug-in behind #1043 [C3][E1].** Built only if M-3 finds a model that fixes what M-2
    could not. Thumbnail builder, strict schema, swap check where the budget allows, cache, cost preview,
    a USD 1 default cap, and the egress test (§8).
12. **PR-9, per-provider disclosure [E5].** Verified retention and training text per plug-in. This also
    lands the E4 key storage decision.

## 12. Open questions (one-word answers)

1. **API key storage [E4]:** where should a cloud API key live? **env / keystore / file**

**Not verified, collected:** image tokens per frame at any size; OpenAI and Gemini model ids and prices;
retention and training terms for every provider except Fable 5.1's 30-day rule; the cache hit rate on a
rescan; size, licence and accuracy of every open IQA or local vision-language model; the full-library
effect of the A4 gate (measured on 65 test-set pairs only); the owner-time estimate for labelling.
