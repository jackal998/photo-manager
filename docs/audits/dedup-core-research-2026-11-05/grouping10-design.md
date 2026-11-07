# GROUPING 10: content-gated identity (tiered identity, step 1)

Date: 2026-11-08. Author: opus planner (read-only), master 0e18976.
Spec: dedup-core research §0, §1, §1.1 and §5.1 (`docs/audits/dedup-core-research-2026-11-05.md:29-100, :295-354`). Also STATE.md GROUPING 6-9 and the owner decision of 2026-11-07 (`STATE.md:562-571`), plus the 04:00 census (`STATE.md:675-689`).
Scope: GROUPING "10" is step 1 of the tiered identity model. It covers T0 byte identity, T1 pixel identity and content-verified near-duplicates, and T3 hints that are display-only. T2 bursts already shipped as #1042 and are untouched.
Out of scope: the embedding tier, `core/services/auto_select.py` semantics, and any UI change.

## Lead decisions (2026-11-08, after the planner returned; these override the text below where they differ)

- **Two iterations on one branch (`fix/grouping-10-content-gated-identity`), each verified by a real headless rescan
  of the test set before the next starts.**
  - **Iteration 1 = G5 + G6 + G8 + G9** (low evidence never unions; pixel identity as second signal; B7 contract text;
    version "10", docs, news). `_collect_pair_edges` / `_pair_pass` are NOT touched in iteration 1, so the
    stem-bridge defect is expected to survive it; the census records that.
  - **Iteration 2 = G1 + G2 + G3 + G4 + G7** (ContentIdentifier read, carry, persist; filename edges removed; pair
    cluster never bridges). Starts only after the lead's P0 exiftool census (E) has verified the tag keys on real
    files. G2 ships only if P0 shows JPEG Live stills carry a ContentIdentifier (Open 3).
- **Open 1 (s11 fixture):** assertion C is inverted to "a MOV without a matching ContentIdentifier is NOT in the
  still's group"; the positive CID case is carried by layer 1 with synthetic `content_identifier` values. No fixture
  is written with a fabricated Keys tag. Iteration 2 scope.
- **Open 2 (byte-different, pixel-identical PNG pairs):** measured by the lead before iteration 1 is reviewed (count
  of both-PNG rows demoted "both screenshots" whose `rgb32` match); the full-resolution lossless digest is a
  follow-up, not G10. Contradiction 1 stands: with B7 on such a pair is a hint, because the 32x32 signature cannot
  separate templated screenshots.
- **Verification is the lead's:** every iteration = headless rescan (auto-select off) -> `manifest-diff.py` against
  the GROUPING 9 baseline -> `grouping-health-census.py` -> contact sheets of a lost-pair sample and of every
  surviving cross-day group. No merge request to the owner until both iterations pass E with the lost-pair sample
  explained.

## Contradictions with the brief (read first)

1. **Byte-different, pixel-identical PNG pairs do not survive with B7 on.** The brief (C) expects pHash-0 PNG pairs that pass #1048 to stay grouped. Today the both-PNG demotion runs before the pixel gate (`scanner/dedup.py:754-764` vs `:768`). Such a pair is therefore already REVIEW/low under GROUPING 9 and is grouped only through a hint. Under G10 it dissolves. Only sha256 byte copies stay. The pixel gate cannot rescue it either. A 32×32 signature (`dedup.py:640-660`) cannot tell two templated text screenshots apart, which is the class-C failure itself. See G8 and Open question 2.
2. **The research's "Live video is a passenger of its still, whatever the still's tier" (`research.md:307-309`) is superseded.** The owner's 2026-11-07 decision requires real content or a device-written identity. The video is a passenger only when its ContentIdentifier equals the still's.
3. **STATE.md:565 said "no GROUPING 10 by filename"**, and that holds. G10 removes filename edges and adds the first content signal for videos (ContentIdentifier). The video↔video `high` stem edge, which the owner wanted gone, is replaced rather than kept.
4. **A filename stem cannot be content-gated without leaving a filename edge.** For still↔still the stem adds nothing beyond the pHash tiers, which compare every candidate pair anyway. So G10 deletes still↔still stem edges outright instead of gating them (G4). Content edges between those files still come from the pHash tiers. Nothing that content proves is lost.
5. **Undated bridges are not fixed by G10.** A pHash+dHash match with one undated side is `high` (no time evidence either way, `dedup.py:282-283`). An undated file can therefore still join two dated moments on different days. Some of the 27 cross-day groups with no EXACT row may survive. That is research T1 for undated pairs ("probable derivative", `research.md:304-305`) and belongs to a later step. The census in E counts them.
6. **Auto-select inputs change, but its code does not.** Grouped rows with `match_confidence = "low"` disappear (an invariant of G5). PR-3's "needs your look" set shrinks for that reason alone. Ungrouped rows are never auto-selected. `ManifestRepository` loads only grouped rows (`infrastructure/manifest_repository.py:976, :1138`).

## Edge sources, gates and union-find under G10

```
 _classify_exact        sha256 equal ------------------------------> high --+
 _classify_format_group pHash 0, all lossy, colour ok, dHash != disagree    |
                        no time/PNG note AND (dHash agree OR pixels same) > high --+
                        otherwise ---------------------------------> Hint  |
 _complementary_edges   pHash 0 with RAW: member ~ anchor same time --> high --+
                        anchor ~ anchor (times disagree) ------------> Hint  |
 _classify_near_dups    0<d<=T, colour ok, dHash != disagree:               |
                        dHash agree AND no note ---------------------> high --+
                        otherwise -----------------------------------> Hint  |
 _collect_pair_edges    stem cluster, a video on one side,                  v
                        ContentIdentifier equal on both -------> pair   union-find
                        else / still<->still --------------> (no edge)  (high only)
                                                                 |         |
                                                                 v         v
                                    _pair_pass: cluster joins iff its joined
                                    members sit in exactly ONE component
                                                                 |
      _mark_burst_stacks (relabel frames) -> _mark_hints (reason text only)
```

## Principles

- **A. Only high-confidence content evidence unions.** A union needs one of three: byte identity, two independent content signals that agree (pHash with dHash, or pHash 0 with pixel identity, and no time disagreement), or a device-written capture identity. Anything weaker is a hint. A hint never creates, grows or merges a group, and never sets an action.
- **B. A filename never creates an edge.** Stills link only through the content tiers. A video links only to a file that carries the same Apple ContentIdentifier. A video can never join two components that content kept apart.
- **C. Screenshots group as byte copies only while B7 is on.** Two PNGs that are not sha256-identical never group on pHash evidence. With B7 off they are judged like photos.

## Items

**G1. Read ContentIdentifier through exiftool.** File: `scanner/exif.py`. 1.5 h.
- Append `-ContentIdentifier` and `-MediaGroupUUID` to `_read_extract_chunk` args (`exif.py:919-942`). Leave the group unqualified so both families come back. Under `-j -G` the keys are `QuickTime:ContentIdentifier` (MOV Keys atom `com.apple.quicktime.content.identifier`), `MakerNotes:ContentIdentifier` (HEIC/JPG Apple MakerNote) and `MakerNotes:MediaGroupUUID` (older exiftool name for the same MakerNote tag). These key names are not verified in this session. P0 in E verifies them on real files before merge.
- New constant `_CONTENT_ID_KEYS` holds those three keys in that order. In `_record_to_extract` (`exif.py:982-1027`), set `content_identifier=_content_id_text(...)` from the first key present. The new helper returns `str(raw).strip().upper()`, or None for missing or empty. It mirrors `_subsec_text` at `:596-614`.
- The keys must not enter `_CENSUS_TAGS` (`exif.py:559-571`). Otherwise `exif_tag_count` would move every score, the same reason given for #820 at `:588-593`.
- `scanner/media_extract.py`: add `content_identifier: Optional[str] = None` after `offset_time_original` (`:79-80`).
- Tests go in `tests/test_scanner_exif.py`. They feed synthetic JSON records to `_record_to_extract`, with no exiftool, because the pytest job has none (only `web-eval-gates.yml:335-340` installs it):
  - each of the three keys gives the upper-cased value;
  - the QuickTime key wins over MakerNotes when both are present;
  - "" and absent both give None;
  - `exif_tag_count` is the same with and without the key.

**G2. A JPEG Live still goes through exiftool.** File: `core/app_service/scan_runner.py`. 1.5 h.
- JPEG skips exiftool (`scanner/hasher.py:348`; `scan_runner.py:727-729`). PIL does not parse Apple MakerNotes, so a JPEG Live still would never carry a ContentIdentifier.
- Add a pure predicate `_inmemory_extract_ok(record, outcome) -> bool`. It returns True when `outcome.inmemory_signals is not None` and no entry of `record.pair_cluster` is a video (`scanner.media.is_video`). Use it at `:727`. A JPEG with a video peer then takes the normal exiftool queue, and the two paths never both write its key.
- Cost: one exiftool read per JPEG that has a same-stem video, which is a small share of JPEGs.
- Tests in `tests/test_scan_pipeline.py` or a small new test file:
  - the predicate returns True for a lone JPEG, False for a JPEG with an MOV peer, and False for a JPEG with an MP4 peer;
  - it returns False for HEIC, whose `inmemory_signals` is None.

**G3. Carry and persist the value.** 2.5 h.
- `scanner/dedup.py`: add `content_identifier: Optional[str] = None` to `HashResult` (after `rgb32`, `:107`) and to `ManifestRow` (after `match_confidence`, `:224`). `_make_row` copies it (`:1518-1535`).
- `scan_runner.py:1327-1331`: inside the same loop, backfill `r.content_identifier` from `extracts[path].content_identifier` when it is None. Use the same pattern as `exif_date`.
- Manifest column `content_identifier TEXT`, nullable, no DEFAULT. Per `.claude/skills/sqlite-migration-safety/SKILL.md` it goes in five places:
  1. `scanner/manifest.py` `_DDL` (`:19-46`);
  2. `_INSERT`, whose column list and `?` count change together (`:53-62`), and the tuple at `:150-173`;
  3. `infrastructure/manifest_repository.py` `_MIGRATIONS` (`:112-150`);
  4. `_POST_DROP_COLUMNS` (`:170-189`, the trap documented there);
  5. the rebuild DDL (`:320-340`).
- Why persist it: the verification in E needs it, and research §5.1 T1 needs it next (`research.md:326-329`). Nothing reads it at load time.
- Tests in `tests/test_scanner_manifest.py` and `tests/test_manifest_repository.py`, copying the `phash_quality` cases:
  - a write/read round trip keeps the value;
  - an old-schema manifest gains the column with NULL values;
  - the drop-move rebuild keeps the column.

**G4. Stem edges are content-gated.** File: `scanner/dedup.py`, `_collect_pair_edges` (`:1129-1201`). 2 h.
- New signature: `_collect_pair_edges(records, rows) -> list[Edge]`. Drop the `threshold` parameter.
- For each `(hr, peer)` in `pair_cluster` where both are in `rows`:
  - **still↔still:** emit nothing (Contradiction 4).
  - **any pair with a video side** (image↔video or video↔video): emit `(a, b, "pair")` only when both `content_identifier` values are non-None and equal. Otherwise emit nothing.
- Delete `_pair_kind` (`:1122-1126`). Delete the `hamming_distance` import if it is now unused (`:49`).
- `classify` (`:425-434`) passes `pair_edges + phash_edges` straight to `_assign_group_ids`.
- Rewrite the docstring. Also fix the `_phash_quality_ok` text "and the same-stem pair edge" (`:379`).
- Tests:
  - **lone RAW+JPG pair:** one folder, same pHash, gated out by quality < 16. GROUPING 9 grouped it through the stem. G10 gives both `group_id` None.
  - **far-pHash collision:** same stem, far pHash, nothing else. No group (unchanged).
  - **video↔video:** MOV and MP4 with the same stem, distinct sha and no CID give no group. With equal CIDs and a byte copy of the MOV elsewhere, both videos join that copy's group.

**G5. Low evidence never unions.** File: `scanner/dedup.py`. 3 h.
- New type `Hint = tuple[str, str, str]`, holding `(path_a, path_b, detail)`. `Edge` kinds become only "high" and "pair". Update the comment at `:67-74`.
- **`_classify_near_duplicates`** (`:1056-1113`): compute `confidence`. If it is low, append `(a, b, detail)` to the hints and `continue` before both the edge append and the row write. The detail is `hamming=N`, plus `, <note>` when there is a demotion note, else `, dHash unavailable`.
  - A low pair therefore never writes a REVIEW row and never claims a row before a later high pair could (the in-rows guard at `:1091`). A row with both a low and a high partner is now flagged by the high one.
  - Return `(edges, hints)`.
- **`_classify_format_group`** (`:722-792`):
  - The demotion-note branch (`:754-764`) writes no row and emits the hint `same pHash, <note>`.
  - The low-confidence `_format_review_row` branches (`:770-781` with `confidence == "low"`) emit the hint `same pHash, pixels differ` or `same pHash, pixel check unavailable`, with `, dHash unavailable` appended.
  - Return `([], hints)`. Extract the per-duplicate verdict into `_format_verdict(keeper, dup, ...) -> ManifestRow | Hint | None`, because the function is already over 50 lines.
- **`_complementary_edges`** (`:821-838`): the anchor↔anchor low edge becomes the hint `same pHash, shot N h apart`. Return `(edges, hints)`. `_classify_phash` (`:556-617`) concatenates both lists.
- **`_assign_group_ids`** (`:1412-1486`): union every high edge, then call `_pair_pass` (G7). Delete `_hint_may_join`, `_hint_pass` and `_mark_taken_hints` (`:1249-1263, :1332-1375`). `_row_edges` (`:1204-1211`) returns only high edges, so delete its low branch. No grouped row carries `low` any more.
- **`_mark_hints(rows, hints)`** replaces `_mark_isolated_hints` (`:1218-1246`). It runs after `_mark_burst_stacks`, so a burst reason stays first. It visits rows in sorted path order:
  - A hint whose two ends share a non-None `group_id` is redundant and is dropped.
  - Each remaining end takes its first partner by sorted path. The text is `low-confidence match with <partner name> (<detail>[, +K more])`.
  - **Ungrouped row:** the reason becomes `<base>; ` + `HINT_ONLY_PREFIX` + text. Action stays `""` or `UNDATED` from Pass 3. `match_confidence = "low"`. `duplicate_of` stays None.
  - **Grouped row:** the reason becomes `<base>; hint: ` + text. Action and `match_confidence` are untouched, so a hint cannot move auto-select.
- `_mark_burst_stacks` and `_burst_frames` lose the `hint_leaves` parameter (`:1266-1329`), which is now always empty.
- New tests go in `tests/test_dedup_grouping10.py`:
  - **low-only pair:** two lone JPEGs 36 h apart, dHash agreeing. Both `group_id` None, action "", `low`, reason contains `HINT_ONLY_PREFIX`, the partner name and "h apart".
  - **lone file next to a byte-copy pair:** the lone file stays None and the pair is [2].
  - **torii (three moments × byte copies):** [2,2,2], with no REVIEW_DUPLICATE row. Each keeper's reason contains "hint: low-confidence match with" and its `match_confidence` is unchanged.
  - **portrait + screenshots shape:** HEIC+MP4 with no CID, two byte-identical PNGs, and one PNG near-dup of them. Only the byte-copy PNG pair groups. The HEIC, the MP4 and the third PNG are None.
  - **invariant:** over one mixed fixture (all of the above plus a burst), no row with a `group_id` has `match_confidence == "low"`.
  - **determinism:** 20 shuffles of the mixed fixture give identical `(group_id, action, reason, match_confidence)` per row.

**G6. Pixel identity counts as the second signal.** File: `_classify_format_group` (`:769`). 0.5 h.
- Change the rule from `confidence = "high" if vote == "agree" else "low"` to high when `vote == "agree"` or `same_pixels is True`.
- In production dHash is always present: the hasher computes it with pHash (`dedup.py:89`), and a `disagree` drops the pair earlier at `:747`. The change therefore only touches dHash-missing inputs, where a 1,024-pixel colour match outranks a missing dHash.
- Test: a pHash-0 HEIC+JPEG with no dHash and equal `rgb32` is EXACT/high and grouped. With different `rgb32` it gives no group, plus a hint.

**G7. A pair cluster never bridges components.** File: `_pair_pass` (`:1378-1409`). 1 h.
- New signature: `_pair_pass(pair_pairs, find, union, joined) -> list[str]`.
- For each cluster, in sorted root order, let `roots = {find(m) for m in members if m in joined}`:
  - **zero roots:** nothing joins (lone pair, as in GROUPING 9);
  - **one root:** union the whole cluster;
  - **two or more roots:** no union. The cluster's unjoined members are returned. `classify` appends `; pair link skipped: its stills are in different groups` to their reasons.
- Clusters are disjoint, and a guarded union never merges two components, so the result is independent of order.
- Tests:
  - **January/June stem cluster:** JPG+MOV+MP4+DNG in one folder. The JPG and the DNG each have a byte copy elsewhere, and their pHashes are far apart.
    - With no CIDs: two groups {JPG, copy} and {DNG, copy}, and both videos None.
    - With the JPG, MOV and MP4 sharing one CID: the videos join the JPG group, and the DNG group is separate.
    - With all four sharing one CID: two groups, videos None, and the skip text in their reasons.
  - **orders:** all 24 orders of the four cluster files give the same result.

**G8. Keep B7's contract honest.** 0.5 h code, 1 h docs.
- No logic change. `_screenshot_pair` and `_demotion_note` stay (`:292-316`). Under G5 the note now means "no group" rather than "low but grouped".
  - **B7 on:** two PNGs group only as a byte copy, and the match appears as a hint.
  - **B7 off:** PNGs are judged like photos. High near-dup chains and pixel-identical format duplicates group, while time disagreement still demotes.
- `classify(screenshot_demotion=)` and the B7 toggle stay. Update the comment at `core/services/auto_select_rules.py:61` (comment only).
- Test: two PNGs with pHash 0, distinct sha and equal `rgb32`.
  - B7 on: no group, and "both screenshots" in both reasons.
  - B7 off: EXACT/high and grouped.

**G9. Version, docs, news.** 1 h.
- `GROUPING_STRATEGY_VERSION = "10"` with a history line at `dedup.py:890-893`. Its only code consumer is the calibration fingerprint at `scan_runner.py:270-278`, which invalidates once.
- `docs/features/scan.md` (`docs_guard.py` requires this):
  - rewrite "low-confidence hints never chain" (`:259-266`) as "low-confidence matches never group", entry point `_mark_hints`;
  - extend "a lone Live Photo is not a group" (`:239-248`) with the ContentIdentifier rule;
  - update the B7 lines (`:100, :104, :264`);
  - fix the stem mention in the contrast-gate entry (`:250-257`) if it has one.
- `docs/grouping-topology.md`: fix the pair-edge paragraph.
- Optionally fix the comment drift in the comment block at `core/services/auto_select.py:199-209` (comment only).
- Add `news/<PR>.bugfix`.

## Migration

The migration is additive only: `content_identifier TEXT NULL` in the five places in G3. Old rows read NULL. A GROUPING 9 manifest opened after the upgrade keeps its GROUPING 9 groups until a rescan, because no grouping version is stored in the manifest. There is no structural migration and no backfill.

## Tests that flip

Rule for the implementer: every flip must fit one of these rows. Any other red test means stop and report.

| File::test | New expectation |
|---|---|
| test_dedup_hint_edges.py::TestTorii::test_cross_moment_rows_are_low_review_and_name_their_partner | no REVIEW_DUPLICATE rows. Each keeper's reason has "hint: low-confidence match with" and " h apart". |
| ::TestLeafAttach::test_lone_file_attaches_as_low_leaf | lone is None, `low`, `HINT_ONLY_PREFIX in reason`. Sizes are [2]. |
| ::TestNoChainingThroughALeaf::test_leaf_joins_exactly_one_group_by_sorted_edge_order | [2,2], and middle is None |
| ::TestTwoSingletonsByLowHint::test_two_singletons_joined_only_by_low_edge_form_one_pair | no groups. Both rows None and `low`. |
| ::TestHintChainStopsAtLonePairs::test_four_screenshot_chain_gives_two_pairs_not_one_group | no groups |
| ::…::test_lone_file_hinting_a_low_only_pair_stays_isolated | all three None. x has `duplicate_of` None, "s2.png" in its reason, `low`. |
| ::TestIsolatedHintIsUndecided (both tests) | `HINT_ONLY_PREFIX in reason` instead of `startswith`. Action stays "" or UNDATED. |
| ::TestHintWithAnotherEdgeKeepsAction | x is None with action "" |
| ::TestHintsDoNotChainThroughALeaf (3 tests) | with copy [2]; without copy []; core-leaf-far [2] with b and c None |
| ::TestComplementaryRawPairsRespectShotTime::test_disagreeing_times_pair_as_a_low_hint_not_a_certain_group | no group. Both reasons carry the hint. |
| ::…::test_cr2_and_its_conversion_stay_together_across_a_moment | [2]. m0 is None, `low`, with "x.cr2" in its reason. |
| test_dedup_time_gate.py::test_disagreeing_shot_times_demoted_to_review_low, ::test_near_dup_two_days_apart_demoted_to_review_low, ::test_two_pngs_same_phash_demoted_to_review_low | action "", `group_id` None, `low`, the note in the reason |
| ::test_two_png_near_dups_demoted_to_review_low | "both screenshots" in reason instead of `endswith` |
| test_dedup_rule_toggles.py::test_shot_time_demotion_survives_screenshot_off | no REVIEW rows. Both rows `low` with "shot 48.0 h apart". |
| ::TestDefaultsAreGrouping9 | renamed to …Grouping10, body unchanged |
| test_dedup_burst_stack.py::test_hint_leaf_hours_away_untouched | leaf action "", None, `low`. The burst assertions stay. |
| test_dedup_live_pair.py::test_two_lone_live_stills_with_low_hint_form_one_group_with_movs | no groups |
| ::test_lone_live_still_attaches_to_high_member_with_its_mov | still and MOV None. {a, a_copy} is [2]. |
| ::test_live_still_is_not_a_hint_anchor | neither X nor Y grouped |
| ::test_cluster_attaches_whole_when_any_member_grouped | rewritten as the G7 CID cases. The current body pins the bridge defect. |
| ::test_mov_joins_group_when_still_is_byte_copy, ::test_mov_joins_group_when_still_is_high_near_dup | fixture gains an equal `content_identifier` on still and MOV. Add a twin without CID that expects the MOV to be None. |
| test_dedup.py::TestLivePhotoPair (4 grouped tests), ::TestGroupId::test_live_photo_pair_shares_group_id, ::TestSameStemCaptureGate::test_live_photo_pair_preserved_when_video_has_no_phash | fixture gains equal CIDs. The `_hr` helper gains a `content_identifier=None` parameter. |
| test_dedup.py::TestMatchConfidence::test_low_confidence_when_dhash_missing | b action "", None, `low`, "dHash unavailable" in reason |
| test_dedup.py::TestNearDuplicate (flagged / mean-colour-match / missing-mean-colour), ::TestGroupId::test_near_duplicate_pair_shares_group_id | fixture gains an agreeing dHash, because the subject is the mean-colour or distance gate and production always has a dHash. Expectations unchanged. |
| test_dedup.py::TestGroupId::test_transitive_grouping_three_files, ::TestUnderGroupingFix::test_bridge_orphan_now_grouped | agreeing dHash; all three in one group, which restores #538's original intent. The shuffle test stays. |
| test_dedup.py::TestSameStemCaptureGate::test_same_stem_raw_jpg_identical_phash_still_grouped, test_dedup_live_pair.py::test_image_image_same_stem_pair_still_groups | still grouped through the complementary pHash-0 edge; docstring only |

These must stay green unchanged:
- TestHighEdgesUnchanged; the 8 h, 1 s and missing-EXIF EXACT cases in test_dedup_time_gate.py;
- every byte-copy and format-duplicate EXACT test;
- test_dedup_exact_pixels.py;
- test_dedup_burst_stack.py apart from the leaf test;
- TestBKTreeParity;
- test_lone_live_photo_pair_gets_no_group and test_false_stem_pair_without_other_edges_gets_no_group.

TestComplementaryGroupEdge and test_dedup_quality_gate.py were not read in full. The rule above applies to them. Expected scenario outcomes:
- qa s65 stays unchanged, because its links are already high.
- qa s11 depends on P0 (Open question 1).

## E. Verification plan (lead-run)

**P0, before implementing G1-G3.** Run exiftool over the test-set root with `-j -G -ContentIdentifier -MediaGroupUUID`. Report:
- the exact keys returned and `exiftool -ver`;
- for every same-folder same-stem image↔video set in the GROUPING 9 manifest, counts by type pair (HEIC+MOV, HEIC+MP4, JPG+MOV, JPG+MP4, MOV+MP4) of: both CIDs equal, one missing, both missing, unequal;
- the same read on `qa/sandbox/live-photo/*`.

This measures the Live recall loss, which is unknown today. Takeout MP4s may carry no Keys atom.

**Rescan.** Run headless on the test set with default rules, then `compare_manifests.py` against the GROUPING 9 keepsake. These quantities must change:

| Quantity (test set, GROUPING 9) | Expected under G10 |
|---|---|
| groups whose every duplicate-action row is `low` (100) | 0 |
| grouped rows with `match_confidence='low'` | 0, which is G5's invariant |
| rows with reason "…; hint: low-confidence match…" and an attached leaf (99) | group NULL, reason contains "hint only (not grouped)" |
| groups whose still rows split into ≥2 components under "sha equal OR pHash d ≤ threshold" (stem-bridged) | 0. Any residue must be a CID cluster whose stills all share one non-NULL `content_identifier`. |
| cross-day groups with no EXACT row (27) | ≈0. List each residue's cause by counts only: half-hour-multiple shift, undated bridge (Contradiction 5), or Live video UTC date. |
| PNG-only groups (91) | only groups where every member shares one sha256 |

These must not change:
- every GROUPING 9 group made only of sha256-identical members, with identical member sets;
- the count and member sets of EXACT rows whose reason starts "exact duplicate of";
- format-duplicate EXACT rows, including those 480 min apart;
- the 154 burst rows in 20 groups;
- REVIEW/high action counts. Reason targets may differ (G5 flagging order).

**Lost pairs.** Take every pair (a, b) with an equal non-NULL `group_id` under GROUPING 9 where G10 separates them or leaves either NULL.
- Bucket each pair by G10 reason: hint only, pair link skipped, video without CID, PNG non-copy, or other. Tracked output carries counts and manifest `id`s only.
- Draw a 30-pair random sample to a gitignored `*.local.txt` with paths, for contact sheets.
- Any pair in "other" is a finding to explain before merge.

## Risks

- **Recall loss is deliberate.** The losses are:
  - the 100 low-only groups and 99 attached hint rows;
  - time-disagreeing pHash matches, including copies whose EXIF date was rewritten;
  - non-byte PNG pairs with B7 on;
  - Live videos without a matching CID;
  - dark or gated-out RAW+JPG pairs that GROUPING 9 joined only by stem.

  Under the owner's criterion this is the acceptable direction. An ungrouped row is never auto-deleted, and the hint stays in the manifest's reason. The cost is visibility: ungrouped rows are absent from review (`manifest_repository.py:976`), so the user no longer sees these candidates.
- **Non-Apple or stripped Live Photos never pair.** This follows the owner's "pairing must use the real content".
- **Without exiftool there is no Live pairing at all.** This is the existing missing-exiftool mode at `scan_runner.py:1302-1309`. Its warning text should name pairing.
- **CI coverage is split.** pytest has no exiftool, so layer 1 uses synthetic `content_identifier` values. Only the web batch exercises a real read.
- **G2 changes which tool scores a JPEG Live still.** Parity is pinned by tests/test_scanner_exif.py. Expect `exif_date_tag` provenance to differ on those rows only.
- **The tag names are unverified.** P0 must confirm them before G1 merges.

## Open questions for the lead

1. **s11 fixture.** If the fixture HEIC and MOV carry no equal CID, assertion C (MOV in the still's group) becomes false under G10.
   - A MOV Keys tag can be written in CI scenario setup with exiftool.
   - An Apple MakerNote cannot be created in a non-Apple HEIC.
   - So either the fixture is replaced by a PII-clean real Live pair, or C is inverted to "MOV ungrouped without a CID", with a CID case carried only by layer 1.
2. **Pixel-identical PNG copies with different bytes.** Measure their count: both-PNG rows with reason "same pHash as … but both screenshots". If material, the follow-up is a full-resolution decoded-pixel digest for lossless formats, which needs a hash recipe bump. It is not part of G10.
3. **G2 and G3 are separable.** Ship them, or defer G2 if P0 shows JPEG Live stills carry no CID anyway.

## Effort

The estimate is about 19 hours.

| Item | Hours |
|---|---|
| G1 | 1.5 |
| G2 | 1.5 |
| G3 | 2.5 |
| G4 | 2 |
| G5 | 3 |
| G6 | 0.5 |
| G7 | 1 |
| G8 | 1.5 |
| G9 | 1 |
| Test flips and new tests | 3 |
| s11 and P0 support | 1.5 |

Order: G5 and G6 first, because they are self-contained and alone dissolve the low-only groups. Then G1 to G4 and G7. Then G8 and G9.