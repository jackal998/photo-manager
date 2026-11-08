# PR-4b design: rule B6 — a Live Photo video follows its photo

## Lead decisions (2026-11-08, after the planner returned; these override the text below where they differ)

- **Open question 1 (EXACT videos): adopt the recommendation.** A follower is skipped only when it is already in the base delete set, or when it is EXACT and B3 is off. Otherwise an EXACT byte-copy video beside its deleted photo follows the photo, so a copy folder is not left with orphan clips. R-SCAN-6 stays true.
- **Open question 2 (motion loss): guard.** A video follows its photo only when its group keeps at least one non-deleted video with the same ContentIdentifier. B6 must never delete a capture's last motion clip. The replay reports how many followers the guard withholds.
- **Open question 3 (several stills): follow only when EVERY qualifying still is in the base delete set.**
- Open question 4: no migration; the Rules v2 log line names B6. Open question 5: no B6:spared value. Open question 6: the lead replays on the GROUPING 11 manifest.
- This document lands in the repo as docs/audits/auto-select-prd-2026-11/pr4b-design.md with the PR. Fixture paths below are synthetic.


## Summary

B6 is a pure step that runs after the existing delete chain. In aggressive mode, a video that has a qualifying photo joins the delete set when that photo is in the base delete set. `_delete_blocker` stays exactly as it is (`core/services/auto_select.py:322-353`). The pairing is a new small pure module that reads the walker's filename parser. It reads only `group_id`, `action`, `content_identifier` and `source_path`, so the GROUPING 11 pixel gate does not affect it. Only the counts shift, because GROUPING 11 changes which stills are EXACT.

Four findings change parts of the brief:

- **The video's blocker is "unranked", not "action".** A Live video with a same-stem HEIC/HEIF/JPG/JPEG still in its group and folder gets `score=None` (`scanner/scoring.py:430-436`). `_delete_blocker` returns `"unranked"` before it reaches the action check (`auto_select.py:336-337`). This does not change the seam, because B6 runs after the chain either way.
- **Edited stills must be excluded.** The walker leaves them out of pair clusters (`scanner/walker.py:340-341`). If they are not excluded, the lexicographic tie-break picks `IMG_1-edited.JPG` over `IMG_1.HEIC`, because `-` sorts before `.`. The video would then follow the edited copy into delete while the original is kept.
- **"No toggle adds a delete" stops being true.** That sentence is in `core/services/auto_select_rules.py:11`, `docs/features/scan.md:106` and the PR-4a design (`docs/audits/auto-select-prd-2026-11/pr4-design.md:71`). It is a documented invariant, so it must be retired explicitly.
- **R-SCAN-7 conflicts with B6.** R-SCAN-7 says B8 on deletes "only EXACT rows" (`docs/behaviour/rules.md:162-164`). A B6 follower is a non-EXACT video, so the rule text needs an exception.

## Data flow (ASCII)

```
scan_runner.py:1434  plan_auto_select(rows, aggressive, confirm_below, rules)
        |
        v
auto_select.py  top_score_path_per_group -> keepers
        |
        v
non_keepers_for_aggressive_delete
  base = {row | _delete_blocker(row) is None}    (chain unchanged)
        |  rules.b6 False -> return base (today exactly)
        v  rules.b6 True
live_photo_pairs(rows) -> {video_path: photo_path}   (core/services/live_photo.py)
        |  keep a pair when: photo in base, video not in keepers,
        |  video.action not in _DUPLICATE_ACTIONS
        v
deletes = base | followers ---> scan_runner: non_keepers -> manifest write
        |
rule_trace (same helper) -> "B6:delete" ---> scan log tally (scan_runner.py:1460)
```

## Changes by file

**New `core/services/live_photo.py`** (about 60 lines, pure, imports only `scanner.media`)
- `live_photo_pairs(rows: Iterable) -> dict[str, str]` maps each video path to its photo's path. A row qualifies only when it is in a group, its name is not edited, and it carries a CID. Matching uses `parse_media_filename` (`scanner/media.py:168-200`) and `is_video` (`scanner/media.py:109-111`). Nothing is reimplemented.
- Rows are read with `getattr(row, "content_identifier", None)` and an empty string counts as missing. This keeps the module shape-agnostic, like `auto_select.py`.
- It lives in its own module because `auto_select.py` is already 503 lines. It also gets its own test file for the per-file coverage floor.

**`core/services/auto_select_rules.py`**
- `:22` becomes `RULES_VERSION = "v2"`.
- `:23` becomes `RULE_IDS = ("B3","B4","B5","B6","B7","B8")`.
- `:26-34` adds the field `b6: bool = False` as the LAST dataclass field, so the keyword-only call sites at `tests/test_auto_select.py:531,543,560,564,580,596,600` and `tests/test_scan_runner.py:522,555` compile unchanged and mean "B6 off". The default is the legacy direction, so it can never add a delete by omission.
- `:36-44` `to_dict` lists the ids in `RULE_IDS` order, with B6 between B5 and B7. `from_mapping` (`:47-58`) needs no change.
- `:63` `DEFAULT_RULES` passes `b6=True` explicitly. `:66` `LEGACY_RULES` passes `b6=False` explicitly.
- `:8-11` docstring: "B3, B4 and B8 only narrow the aggressive delete set. B6 is the one rule that adds a delete: a Live video follows its photo."
- **No migration.** `resolve_rule_toggles` (`:69-78`) fills a missing B6 from `DEFAULT_RULES`. `validate_rule_toggles` (`:97-99`) and the settings default (`app/web/routes/settings.py:71,77`) read `RULE_IDS` and `DEFAULT_RULES`, so they pick up B6 automatically.

**`core/services/auto_select.py`**
- `:35` also imports `live_photo_pairs`.
- New private `_live_video_followers(rows, base: set[str], keepers: set[str]) -> set[str]` (about 12 lines) returns the video paths whose pair passes three filters: the photo is in `base`, the video is not in `keepers`, and `video.action` is not in `_DUPLICATE_ACTIONS` (`:210`).
- `:412-419` `non_keepers_for_aggressive_delete` computes `base` exactly as today, returns it when `not rules.b6`, and otherwise returns `base | _live_video_followers(rows, base, keepers)`. The docstring gains a PR-4b paragraph.
- `plan_auto_select` (`:422-465`) needs no change, because it already delegates.
- `rule_trace` (`:490-501`): the loop also collects `base` (the rows whose blocker is None). After the loop, when `rules.b6`, each follower gets `trace[v] = "B6:delete"`. A follower's own blocker is "unranked" or "action", so it has no earlier entry and nothing gets overwritten. The docstring at `:478-483` gains the new value.

**`core/app_service/scan_runner.py:1460-1464`**
- The rules log line becomes `B3 marked N delete; B6 marked N delete; spared B3 N, B4 N, B8 N.`

**Comments and wire types**
- `core/app_service/dtos.py:84`: "B6 adds Live-video followers to aggressive deletes."
- `app/web/models.py:291`: the Literal becomes `Literal["B3","B4","B5","B6","B7","B8"]`.

**`core/app_service/action_service.py:330-331`**
- No code change. It already passes `LEGACY_RULES`, which now has `b6=False`, so Apply best copy stays exactly as today.

**`scanner/scoring.py:422-425`**
- Docstring fix: "The MOV keeps its own action. Under rule B6 its delete decision follows its photo (core/services/live_photo.py)." This file is outside the GROUPING 11 diff.

## Decision algorithm (pseudo-code)

```python
def live_photo_pairs(rows):
    stills = defaultdict(list)          # (group_id, parent, clean_stem) -> [(row, mf)]
    videos = []
    for row in rows:
        if row.group_id is None or not cid(row):
            continue
        path = Path(row.source_path)
        mf = parse_media_filename(path)
        if mf.is_edited:                # walker.py:340 semantics
            continue
        key = (row.group_id, str(path.parent), mf.clean_stem)
        (videos.append((row, mf, key)) if is_video(row.source_path)
         else stills[key].append((row, mf)))
    pairs = {}
    for video, vmf, key in videos:
        cands = [(r, m) for r, m in stills.get(key, ()) if cid(r) == cid(video)]
        if cands:
            photo, _ = min(cands, key=lambda c: (c[1].number != vmf.number,
                                                 c[0].source_path))
            pairs[video.source_path] = photo.source_path
    return pairs

def _live_video_followers(rows, base, keepers):
    by_path = {r.source_path: r for r in rows}
    return {v for v, p in live_photo_pairs(rows).items()
            if p in base and v not in keepers
            and getattr(by_path[v], "action", "") not in _DUPLICATE_ACTIONS}
```

How the edge cases fall out:

- **Same group.** This is part of the key. CIDs are already upper-cased at extract time (`scanner/exif.py:632-636`), so equality is the pairing test.
- **Not aggressive.** There is no `base`, so nothing changes.
- **B6 off.** The function returns `base`, which is today's set exactly.
- **Video is the group keeper.** This happens when the still has a different `(N)` marker or a non-Live suffix, so the video is scored (`scoring.py:433`). It is never deleted.
- **Video already EXACT.** It is skipped, as the lead decided. Open question 1 covers what this leaves behind.

## Tests

**`tests/test_auto_select.py`**
- Add `content_identifier: Optional[str] = None` to `_Row` (`:16-30`).
- New class `TestLiveVideoRule`, using `DEFAULT_RULES` at `confirm_below="high"` unless stated otherwise.
- Base scene, used by tests 1, 2 and 4:
  - `/a/IMG_1.HEIC`: group g1, score 0.90, EXACT, confidence high, CID "C1". This is the keeper.
  - `/b/IMG_1.HEIC`: g1, 0.60, EXACT, high, "C1". This is the deleted copy.
  - `/a/IMG_1.MOV` and `/b/IMG_1.MOV`: g1, score None, action "", CID "C1".

| Test | Fixture change | Assertion |
|---|---|---|
| `test_b6_video_follows_deleted_photo` | base scene | `deletes == {"/b/IMG_1.HEIC", "/b/IMG_1.MOV"}`; trace for `/b/IMG_1.MOV` is `"B6:delete"` |
| `test_b6_video_of_keeper_photo_stays` | base scene | `/a/IMG_1.MOV` is not in deletes and has no trace entry |
| `test_b6_cid_mismatch_or_missing_not_paired` | parametrized: `/b/IMG_1.MOV` CID "C2"; video CID None; `/b/IMG_1.HEIC` CID None (the still then stays EXACT and is deleted) | `/b/IMG_1.MOV` is not in deletes in all three cases |
| `test_b6_off_is_today` | `rules=RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=True)`, so b6 takes its default False | `deletes == {"/b/IMG_1.HEIC"}`; equals the `LEGACY_RULES` set with B8 toggled to match |
| `test_b6_byte_copy_video_untouched` | `/b/IMG_1.MOV` is EXACT, score None | not in deletes, and no `"B6:delete"` trace |
| `test_b6_video_without_same_stem_still` | `/b/clip.MOV` with CID "C1", plus a cross-folder case where the video is in `/c/` and the still in `/b/` | not in deletes |
| `test_b6_never_deletes_a_keeper_video` | `/b/IMG_1(1).MOV` scored 0.95 (the keeper); `/b/IMG_1.HEIC` deleted, same CID | the video is not in deletes |
| `test_b6_photo_spared_by_b4_or_threshold_keeps_video` | the photo is REVIEW_DUPLICATE at high, so the threshold spares it | the video is not in deletes |
| `test_b6_not_aggressive_no_change` | base scene, `aggressive=False` | `deletes == set()` |

**New `tests/test_live_photo_pairs.py`**, the pure pairing:

| Test | Assertion |
|---|---|
| `test_number_marker_preferred` | With stills `IMG_1.HEIC` and `IMG_1(1).HEIC` (same group and CID), `IMG_1(1).MOV` pairs with `IMG_1(1).HEIC` |
| `test_lexicographic_tiebreak` | With `IMG_1.HEIC` and `IMG_1.JPG` (same number), the video pairs with `IMG_1.HEIC` |
| `test_edited_still_excluded` | With `IMG_1-edited.JPG` (same CID) and `IMG_1.HEIC`, the video pairs with `IMG_1.HEIC` |
| `test_ungrouped_rows_ignored` | Rows with `group_id=None` produce no pair |
| `test_empty_string_cid_is_missing` | An empty-string CID produces no pair |

**Changed tests**
- `tests/test_auto_select_rules.py:19,24,28-29`: the dicts include `"B6": True` for the defaults and `"B6": False` for legacy. Version becomes "v2". Ids become the six-tuple.
- New `test_describe_rules_order`: the output is `"Rules v2: B3 on, B4 on, B5 on, B6 on, B7 on, B8 on."`.
- `tests/test_scan_runner.py:582`: use `f"Rules {RULES_VERSION}: "`.
- `tests/test_web_settings_routes.py:199,202` and `tests/test_web_scan_routes.py:653-655`: add B6 to the expected dicts.

## Frontend, qa and docs

**Frontend**
- `frontend/src/api/types.ts:72`: add `"B6"` to `RuleId`.
- `frontend/src/lib/ruleToggles.ts:7,9-15`: add B6 to `RULE_IDS` and `B6: true` to the defaults.
- `frontend/src/components/scan/ScanRulesBlock.tsx`:
  - Import `SCAN_RULE_B6` (`:6-13`).
  - Add a spec after B5 (`:65`) with `aggressiveOnly: true`. Label key `web.scan.rule_b6_label`, text "Live Photos: the video follows its photo". Help key `web.scan.rule_b6_off`, text "When off, a Live Photo's video is kept even when its photo is marked for delete. Used by Also mark all other files for delete."
  - Update the comment at `:19`.
- Translations: add both keys next to `translations/en.yml:816-817` and `translations/zh_TW.yml:674-675`. Suggested zh_TW: 「原況照片：影片跟著照片走」 and 「關閉時，即使照片被標記為刪除，原況照片的影片仍會保留。「同時將其他檔案標記為刪除」時使用。」
- Frontend tests:
  - `ScanDialog.test.tsx:574` adds `SCAN_RULE_B6` to the checked-by-default loop.
  - `ScanDialog.test.tsx:600,615` add `B6: true` to the dicts.
  - `ScanDialog.test.tsx:603` title becomes "all six ids".
  - `ruleToggles.test.ts:23` adds B6 to the defaults dict.
- Testid: add `SCAN_RULE_B6 = "scan-rule-b6"` with a docstring after `qa/web/testid_constants.py:392`. Then run `python scripts/gen_testid_ts.py`. Never hand-edit `frontend/src/testids.ts`.

**qa: s81 (`qa/web/scenarios/s81_rule_toggles.py`)**
- `:75`: add `"B6": True` to `_DEFAULT_RULES`.
- `:184`: add `SCAN_RULE_B6` to the defaults loop.
- `:214-216`: phase 3 also expects B6 checked.
- Update the docstring (`:15-32`) to say the B6 positive path is layer-1 only. No sandbox fixture carries an Apple ContentIdentifier (`qa/web/scenarios/s11_video_live.py:12`), and a still's MakerNotes CID cannot easily be synthesized. File a follow-up issue for a fixture that carries a CID.
- s57 needs no change, because its fixtures have no CID.

**Docs**
- `docs/features/scan.md`:
  - `:98`: add `scan-rule-b6` to the testid list.
  - `:99-100`: "five" becomes "six".
  - New bullet after `:103`: "B6 Live Photos. On (the default): in aggressive mode a video whose photo is marked for delete is marked too. A qualifying photo is in the same group and folder, has the same stem, is not edited, and has the same ContentIdentifier. A video that is itself EXACT, is the keeper, or has no such photo is untouched. Off keeps the video, as before. Aggressive only."
  - `:106`: "Only B6 adds a delete."
  - `:107`: add B6 to the log tally.
  - `:109`: "R-SCAN-5 to R-SCAN-12".
  - `:22` item (a): add "except a B6 follower, see (g)".
  - `:23`: add item "(g) B6".
- `docs/behaviour/rules.md`:
  - New R-SCAN-12, "With the Live Photo rule on, aggressive auto-select marks a Live video for delete when its photo is marked". Owning code: `core/services/live_photo.py::live_photo_pairs` and `auto_select.py::_live_video_followers`. Pins: the `TestLiveVideoRule` tests.
  - R-SCAN-7 (`:164`): add "a B6 Live-video follower is the one non-EXACT exception".
- `docs/testing.md:113`: add B6 to the row and add a row for the new module.
- Add a `news/<PR>.feature` fragment.

## Risks and open questions for the lead

1. **EXACT Live videos are never deleted by B3 or B6.** A byte-copy video beside its still is also `score=None` (`scoring.py:433` ignores action), so `_delete_blocker` says "unranked" and B3 never deletes it. With "B6 never touches EXACT", `/b/IMG_1.HEIC` is deleted while its EXACT `/b/IMG_1.MOV` stays as a leftover in the copy folder.
   - Check with the replay: count EXACT videos with `score IS NULL` that have a qualifying photo in the delete set.
   - **Recommendation:** replace the EXACT exclusion with two checks. Skip a video already in `base`. Skip an EXACT video when `not rules.b3`. That keeps R-SCAN-6 true and makes the census's 63 byte-copy videos follow their photo.
2. **Motion loss when the kept copy has no video.** If the photo is deleted as EXACT of a still in another folder that has no video with the same CID, B6 deletes the only motion clip. The census does not split the 67 EXACT photos this way.
   - Check with the replay: for each B6 follower, does the group keep a non-deleted video with the same CID?
   - If the count is above zero, an optional guard is to follow only when one does. The PRD's literal rule does not guard this.
3. **Picking one photo versus all photos.** The lead's rule picks one photo. A video next to a kept JPG and a deleted HEIC would follow the HEIC.
   - A safer one-line variant: follow only when EVERY qualifying still is in `base`.
   - It changes results only when two or more stills qualify. Check with the replay: count videos with at least two candidates.
4. **The default turns on without notice.** A stored v1 object without B6 resolves to B6 on at the next scan, which adds deletes in aggressive mode. The owner approved "B6 as scoped" (STATE.md:847), and the scan log names `Rules v2 ... B6 on`, so no migration is needed.
5. **Trace symmetry (optional).** A `"B6:spared"` value when B6 is off would make the off state measurable in the log. The lead's spec asks only for `"B6:delete"`, so it is left out here.
6. **The counts depend on GROUPING 11.** Eleven false EXACT stills become REVIEW there, so the B6 counts on the iteration-4 manifest will not match a GROUPING 11 rescan. Replay both, or state which one was used.
7. **Unverified by me.** I ran nothing. Every claim above comes from reading the code. The census numbers are quoted from STATE.md:842-846.

## Effort

| Part | Hours |
|---|---|
| Pairing module, seam, `rule_trace` and log line | 1.5 |
| Layer-1 tests, new and updated | 1.5 |
| Frontend, translations, testid regeneration, frontend tests | 1.0 |
| s81 and docs (scan.md, rules.md, testing.md, news) | 1.0 |
| Replay checks for open questions 1 to 3 plus the lead's verification | 0.5 |
| **Total** | **about 5.5** |

Files involved:
- core/services/auto_select.py
- core/services/auto_select_rules.py
- core/services/live_photo.py (new)
- scanner/scoring.py
- core/app_service/scan_runner.py
- app/web/models.py
- frontend/src/components/scan/ScanRulesBlock.tsx
- qa/web/scenarios/s81_rule_toggles.py
- docs/features/scan.md
- docs/behaviour/rules.md