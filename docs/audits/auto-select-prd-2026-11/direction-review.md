# Auto-select PRD: direction review from the owner's product values

Date: 2026-11-06. Base: master `4fc1dcc`. Reviews `docs/audits/auto-select-prd-2026-11.md`, cited as
`PRD:<line>`. Read-only study; only this file was written. Every number is reused from the PRD or
STATE.md; none is new.

## 1. Direction in five sentences

1. Auto-select is a **visible, reasoned pick per group that the human accepts**, judged on picture
   quality; it is not a policy engine that decides on the owner's behalf.
2. Show what the app already knows first (why each group exists, which file Keep best would keep), then
   capture the owner's own decisions on the 312-group test set as labels.
3. The quality signal is a model judge; Claude tiers and specialised engines are all candidates, and the
   one that best matches the owner's labels wins, not the cheapest.
4. Nothing new deletes: every group's default stays "keep all", and accepting a pick stays a human act
   through the existing Keep best path, logged and reversible.
5. Nothing is promised before it is measured on the owner's labels, and photos go to the cloud only
   behind the #1043 switch plus a per-run "yes".

## 2. What the owner asked for vs what the PRD built

1. **Picture quality per group.** The PRD builds a metadata rule layer first (PRD:14-16, 129-133) and
   the quality judge third (PRD:17-18). Most of the effort goes where the owner did not ask.
2. **Human decision as the unit.** Accept/reject is the last PR (PRD:364-365); the verdict table has no
   consumer until then (PRD:347-349). The owner sees nothing to judge in the app until PR-7.
3. **"Too aggressive"** (STATE.md:305-308). The PRD adds a people/pet rule R3 (PRD:132), AC-6
   (PRD:319-322) and Q3, which narrows aggressive mode (PRD:379-381): a policy the owner calls theirs.
4. **"Many specialised models and engines".** M-2 measures three Claude tiers only (PRD:354-357).
5. **Quality first (Q10).** The PRD frames the model choice by price, "40× cheaper" (PRD:368-369);
   cost belongs in a cap, not in the ranking.
6. **Develop on the test set.** The PRD scores on 22 reachable Sept groups with 5 strict picks
   (PRD:111-119), "a smoke test" (PRD:119); the 312-group test set is the real bench.
7. **Transparent, traceable.** The verdict log and reasons (PRD:141, 167) are right, but no reason
   reaches the UI today (PRD:64-65).

Naming: STATE.md:306-308 calls the people/pet rule "R1"; in the PRD R1 is byte copies (PRD:130) and
people/pet is **R3** (PRD:132). This memo uses the PRD's numbers.

**The tension, resolved.** The Sept policies (people/pet never deleted, identical pairs by a metadata
rule, keep-all when frames are equal; Sept §5.6) describe what the owner **decides** as the human. They
are already honoured by construction: every row's default decision is Keep (`docs/features/scan.md:10`,
`user_decision=""`), and no new path writes a delete (S6, PRD:21-22). So R3 is **out of scope for v1**:
not a rule, and no new UI default is needed. It returns only if a later step proposes deleting
distinct captures.

## 3. Decisions on each PRD element

- KEEP **opt-in switch** #1043 (PRD:12-13, 171-186): the privacy policy as built, default OFF.
- DEFER **`disclosure_version` + full text** (PRD:174, 180-182, 343-346): the text must name the provider
  and its retention (PRD:204), and M-2 has not chosen a provider.
- KEEP **never send PNG screenshot pairs** (PRD:195-196, Q7) as the default: conservative, reversible.
- CHANGE **verdict table** (PRD:141, 167-169, 347-349): build it first as the owner's label store
  (PR 2 below), so it has a consumer on day one.
- KEEP **R1 byte copies** (PRD:130) as a displayed reason only: it is today's top-score behaviour
  (`core/services/auto_select.py:155-185`).
- DEFER **R2 original vs edit** (PRD:131): the owner's rule is "not yet settled" (Sept §5.6,
  PRD:386-387). See question 1.
- CUT **R3 people/pet** (PRD:132, 148-153) from v1: resolved in §2.
- KEEP **R4 burst keep-all** (PRD:133) as display only: #1042 already decides it; the badge is missing
  (burst design §Open questions 3).
- DEFER **`rule_layer.py::decide_group`** (PRD:129): with R2 and R3 out it wraps one existing function.
- KEEP **cloud judge** (PRD:156-160), CHANGE order and field: measure first, and add specialised engines
  next to the Claude tiers. It is the only element aimed at picture quality.
- KEEP **abstain** (PRD:158): keep-all is the owner's most common outcome (12 of 30, PRD:89).
- KEEP **swap check** (PRD:158-160): it guards the judge's 75 % clear-winner over-claim (PRD:87).
- KEEP **content-key cache** (PRD:300-303) with the backend: an unchanged group is never re-sent.
- DEFER **cost cap, Batches API** (PRD:263, 305-306): size the cap from M-2's measured cost. Keep the
  scan-log count-and-estimate line.
- DEFER **preference model** (PRD:169, 366): unchanged.
- DEFER **people/pet signal, M-1, on-device runtime** (PRD:353, 389-390): only R3 needed it.
- CUT **Q3, narrowing aggressive mode** (PRD:379-381): existing aggressive mode stays as is, unextended.
- KEEP **protected-set union** (PRD:162-165) with the judge: it matters once a pick can differ from the
  score keeper.
- KEEP **AC-1 to AC-5 and AC-11 to AC-14** (PRD:310-318, 332-337): mechanical privacy and autonomy gates.
- CUT **AC-6** (PRD:319-322): it depends on R3; AC-5 (no delete writes) covers the risk.
- CHANGE **AC-7, AC-8, AC-10** (PRD:323-331): score them on the owner's test-set labels.
- CUT **AC-9** (PRD:327-328): it measures agreement with an older model, not with the owner.
- DEFER **AC-15** (PRD:338) with the cap.
- CHANGE **labelling round L-1** (PRD:358-359): in the app, on the 312 test-set groups, via PR 2.

## 4. The first three PRs (each ≤ 1 day, each visible in the app)

**PR 1: why this is a group.** The dedup `reason` is SELECTed
(`infrastructure/manifest_repository.py:73`), but `PhotoRecord` has no `reason` field
(`core/models.py:13-67`) and `_build_file_row` sends neither it nor `match_confidence`
(`core/app_service/review_view.py:132-165`). Carry both through. On a burst-stack header, show a
"Burst · keep all" badge in place of the over-promising "Keep best · delete rest". No schema change.
Gates: the features.md entry and a qa scenario.
- **Owner sees:** each row's tooltip says why it is grouped (byte copy, near-duplicate, burst of N frames
  over S s), and the test set's 12 burst groups read "keep all".

**PR 2: the owner's decision becomes a label.** A "Reviewed" control on each group header appends one
row to the PRD's append-only verdict table (PRD:141; triggers as in `infrastructure/decision_log.py`)
with source = owner. The row holds the content key (sorted member sha256, PRD:300), the keep set at that
moment, and an optional "unsure" flag and note. It records "keep all" too, which closes the R-LOG-1 gap
(an unchanged row logs nothing: `docs/behaviour/rules.md:170`, PRD:66-68). Gate: migration-safety skill.
- **Owner sees:** "Reviewed ✓" per group and a reviewed count. They label the 312 groups with the
  decisions they already make. No suggestion is shown yet, so the labels are not anchored on one.

**PR 3: the suggested keeper, honestly labelled.** Mark the file Keep best would keep:
`top_score_path_per_group` (`core/services/auto_select.py:155`), the ranking `apply_best_copy` uses
(`core/app_service/action_service.py:313`). Its reason reads "best metadata (score)", never "best
picture". Today **Ref** is chosen among Ref-tier rows only (`core/app_service/review_view.py:35-57`),
the keeper among all scored rows, so the two can name different files. Burst groups show "keep all"
and the cover instead.
- **Owner sees:** one "Suggested" file per group, with its reason. Accept stays the existing Keep best
  button. The PR body reports how often the suggestion falls in the owner's PR 2 keep sets.

## 5. The judge UX (answers Q1 without asking)

```
#1043 switch OFF --------------------------> "Suggested" = metadata keeper (PR 3)
#1043 switch ON --> scan ends --> judge only groups not in the cache
                        |   scan log: "Cloud analysis: N groups, ~$X"
                        v
        "Suggested" = judge pick + one-line reason | "keep all" | "can't tell"
                        v
        owner presses Keep best (same button) --> decision log + verdict row
```

The switch is the consent, so there is no second button to explain: while it is ON, the judge runs after
every scan on groups it has not seen. Its answer lands in the same "Suggested" marker as PR 3, with the
model or engine named in the reason. The judge never writes a decision. With the switch OFF nothing is
sent. One code change comes with the judge: Keep best keeps the suggested file, which is where the
protected-set union (PRD:162-165) and AC-4 apply.

## 6. What must be measured before anything is promised (test set, PR 2 labels)

1. **Baseline:** how often the metadata keeper falls in the owner's keep set, and how often the owner
   keeps all. This is the bar a judge must beat; the Sept rates are stratified and do not transfer
   (PRD:95-96).
2. **Ref vs keeper:** the count of groups where the two differ (§4, PR 3).
3. **Judge candidates (M-2, gated):** each Claude tier and each specialised engine that the Q10 research
   shortlists, on the same groups. Report precision of non-abstain picks against the keep set, abstain
   rate on keep-all groups, swap-check disagreement, tokens per 1024 px frame (not verified, PRD:394)
   and cost per group. Use the PRD bars (AC-10: 0.80 precision, 0.70 abstain, PRD:329-331) until the
   owner sets them (PRD:339, Q4).
4. **Provider terms:** retention and training use are not verified (PRD:227-231, 396). Check them before
   the disclosure names a provider.
5. **Egress:** AC-1 (PRD:310-311) passes before any backend PR merges.
6. **Scope fix:** the PRD counts bursts as undecided (PRD:290-293) while R4 decides them (PRD:133).
   This memo sends a burst to the judge for its cover only.

A "no manual look" claim, meaning one action that accepts many suggestions, is made only for a source
that clears item 3's bar. It stays recycle-bin, undoable and human-initiated.

## 7. Questions that still need the owner (one-word answers)

1. Same photo, one file is an edit: keep which by default? **original / edit / ask** (unblocks R2, Q8)
2. After PR 2's labels exist, may a one-off measurement send the test-set groups' 1024 px
   EXIF-stripped thumbnails to the shortlisted cloud models? **yes / no**
3. If a suggestion source reaches the agreed precision on your labels, may one button accept all its
   suggestions, still recycle-bin and undoable? **yes / no**
