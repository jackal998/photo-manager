# Design — a lone Live Photo pair is not a group (GROUPING "8" → "9")

Author: opus planner (read-only), 2026-11-07. Brief: the owner asked why groups exist that contain only a Live Photo still + its video. Measured before this design: dev test set 16/312 groups are pair-only; full library (v6 manifest) 777/8,337 (9.3 %). Every claim cites file:line at commit b47c63c.

DESIGN — a lone Live-Photo pair is not a group (GROUPING "8" → "9"). Read-only; every claim cites file:line.

CONTRADICTIONS WITH THE BRIEF (read first)
1. No "MOV follows its photo" behaviour exists today. #88 removed action propagation (scanner/dedup.py:409-412, :1114-1117). docs/features/review.md:10 states the MOV does NOT inherit the still's decision and Execute does not delete it. No code in core/ or frontend/src cascades a decision; the only link is group membership plus score=None (scanner/scoring.py:433-436). scoring.py:423-425 claims "the MOV inherits its HEIC's KEEP/DELETE decision in the action layer", which is false and should be fixed in the docstring. B6 needs its own PR. This design keeps the MOV next to its photo whenever the photo is in a group, which is the precondition for that PR.
2. Pairing is filename-only (scanner/walker.py:337-354, exact stem + (N) + folder). No Live-Photo metadata is read. The #539 content gate can never fire for a video because the video has no pHash (dedup.py:1149-1158).
3. The pair edge is fed into union-find as "high" (dedup.py:418-420). That makes the still a high member (dedup.py:1386-1390), and so the IMG_4361 artefact happens: both stills are already joined, `_hint_may_join` returns False (dedup.py:1308), the edge is skipped, and `_mark_isolated_hints` leaves 4361 alone because its group_id is set (dedup.py:1193). The general cross-group label is documented as intended at docs/features/scan.md:223. Only the pair-only form is a defect.
4. "Same stem" pair edges also join RAW+JPG / HEIC+JPG images (dedup.py:1119-1126). The owner's finding covers image+video only, so this design scopes to that.

Q1 OPTIONS
(a) A component held together only by image↔video pair edges gets no group_id.
  - Rescore stays deterministic. It reads the stored group_id and never regroups (infrastructure/manifest_repository.py:1109, :1135).
  - load() already hides group_id NULL rows (manifest_repository.py:983, :1031).
  - No R-* rule mentions pairs. R-LOCK-6 and R-EXEC-2 (docs/behaviour/rules.md:61, :93) are unaffected.
  - Fixes the IMG_4361 artefact for free, because both stills become lone at hint time.
  - Stops auto-select locking the HEIC of every lone pair as KEEP: today it is the only scored row in its group (core/services/auto_select.py:174-185).
  - Blast radius: scanner/dedup.py, tests, docs/features/scan.md + review.md, docs/grouping-topology.md:28, qa/web/scenarios/s11_video_live.py.
(b) Keep the group but flag it.
  - Needs a new column or reason-string parsing, plus a load() filter and the frontend.
  - The still stays a high member, so the artefact and the KEEP lock remain. The data still says "group".
(c) UI-only filter in load().
  - No version bump, but the same residue as (b). The manifest lies to every other consumer (rescore, auto-select, the decision log). Rejected.

Q2 RECOMMENDATION: (a). Criterion: the owner says unrelated files never share a group and the view shows only groups that need a decision. A lone pair has nothing to decide.
  Mechanism: a third Edge kind "pair". Pair edges are applied after hints, whole-cluster at a time, and only into existing groups.

  classify() Pass 4 (dedup.py:413)
    pair_edges --kind--> "pair" if is_video(a) != is_video(b) else "high"
        |                    (scanner/media.py:109; image↔image keeps high)
        v
  _assign_group_ids (dedup.py:1337)
    pass 1  high edges      -> union, joined, high_members   (unchanged)
    pass 2  _hint_pass      -> low edges, sorted              (unchanged code;
                               a lone Live still is now LONE, not an anchor)
    pass 3  _pair_pass NEW  -> build pair clusters (union over "pair" edges
                               only, separate parent map). If ANY member is
                               in `joined`: union every edge of that cluster,
                               joined |= cluster. Else: nobody gets a group.
    assign group_id         -> lex-min root (unchanged, dedup.py:1399-1402)
        v
  _mark_isolated_hints / _mark_taken_hints / _mark_burst_stacks (unchanged)

  Why per cluster: edge-by-edge "union if one end joined" depends on order. HEIC↔MOV goes first, both lone, skip; then MP4↔HEIC joins; MOV is orphaned. Clusters are disjoint and qualify from the pass-2 state only, so the result does not depend on order.
  Edit points:
  - Edge comment dedup.py:67-71 gains "pair".
  - dedup.py:418-420: replace the hardcoded "high" with a `_pair_kind(a, b)` helper.
  - New `_pair_pass(pair_pairs, find, union, joined)` under 50 lines, next to `_hint_pass`.
  - `_assign_group_ids` splits edges by kind (dedup.py:1386, :1394).
  - Docstrings to rewrite: dedup.py:409-412, :1111-1117 ("always share a group_id" becomes conditional), :1348-1351.
  - GROUPING_STRATEGY_VERSION "9" with a history line after dedup.py:856-861. The scan fingerprint cache keys on it (core/app_service/scan_runner.py:270-278).
  - Hint consequence, desirable: a Live still is treated exactly like any other photo under the GROUPING 6 rules (scan.md:223). It no longer anchors two lone hints (a second lone hint is skipped, as for any low-only pair). It can now attach to a high group or pair with another lone still.

Q3 BEHAVIOUR (G8 → G9; "low" = low-confidence match)
| Case | G8 today | G9 |
| Lone Live Photo (still+MOV, no dup) | group of 2, both "" unique, still locked KEEP under auto-select | both group_id NULL, not shown, unscored |
| Still is a byte copy of X | group {still, X, MOV} | same; MOV score None, never delete-eligible |
| Still is a high near-dup of Y | group {still, Y, MOV} | same |
| Still low-hints another lone Live still (IMG_4361→4359) | two pair-groups; 4361 REVIEW label points across groups | one group {4359, 4361, both MOVs}; 4361 REVIEW low, its label stays in its group; 4359 marked low hint; #517 keeps 4361 off auto-delete |
| Still low-hints a high member of group G | skipped, label points across | still+MOV attach to G as a hint leaf |
| Still's hint skipped (target is a low leaf) | REVIEW stays in the pair group | still and MOV NULL; still reset to ""/UNDATED with "hint only (not grouped): " (dedup.py:1195-1197) |
| Video same-stem with an unrelated image (false pair) | always grouped | lone: no group (fixed). Image in a real group: video tags along (unchanged; needs metadata, follow-up issue) |
| Video↔video same stem (MOV+MP4) | grouped | stays "high"-grouped (two transcodes = a real duplicate). OWNER QUESTION. |
| RAW+JPG / HEIC+JPG same stem | grouped | unchanged (out of scope). Count pair-only image↔image components in v6 before deciding. |

Q4 TESTS
New file tests/test_dedup_live_pair.py:
- test_lone_live_photo_pair_gets_no_group (both None, actions/reasons unchanged)
- test_mov_joins_group_when_still_is_byte_copy
- test_mov_joins_group_when_still_is_high_near_dup
- test_two_lone_live_stills_with_low_hint_form_one_group_with_movs (the 4359/4361 shape; assert the duplicate_of target shares the row's group_id)
- test_lone_live_still_attaches_to_high_member_with_its_mov
- test_live_still_with_skipped_hint_is_reset_and_ungrouped
- test_live_still_is_not_a_hint_anchor (X and Y lone both hint the still: one pairs, the other is isolated)
- test_false_stem_pair_without_other_edges_gets_no_group (DSC_0001.JPG + .MP4)
- test_cluster_attaches_whole_when_any_member_grouped (HEIC+MOV+MP4 with MP4 a byte copy; parametrize over all 6 record orders, same group_ids)
- test_image_image_same_stem_pair_still_groups (scope guard)
Existing tests that flip:
- test_dedup.py:362 test_unique_pair_forms_group: invert and rename to test_unique_pair_forms_no_group. This reverses #88's headline test on purpose; say so in the commit message.
- test_dedup.py:540 and :436: add a byte copy of the still, or invert.
- Verify test_dedup.py:394 and test_dedup_burst_stack.py:164 still pass unchanged.
- tests/test_auto_select.py: a MOV in a dup-still group has score None and is absent from keepers and from non_keepers_for_aggressive_delete. A lone pair yields no keeper.
qa: s11_video_live.py assertions C/D assume a lone pair group (s11:9-10, :27-28).
- Add a byte copy of IMG_0001.HEIC in a second subfolder and assert the MOV shares that group.
- Assert a lone pair is absent from /api/manifest.
- Layer-3 change: run locally before push, and ask the owner first because local QA hijacks focus.
Docs:
- NEW heading in docs/features/scan.md: "### Scan grouping — a lone Live Photo is not a group".
- Version text "8" → "9" at scan.md:204, :224, :235.
- scan.md:214: the pair edge groups a video only when its still is grouped.
- review.md:10: add "A Live Photo whose still has no duplicate is not shown."
- docs/grouping-topology.md:28.

Q5 MEASUREMENT
- Test set: owner rescans (≈ 4 min), then compare_manifests.py g8 → g9. Expect the 16 pair-only groups to go NULL (32 rows). Group count 312 → 296 minus one for each hint-merge (two pair groups become one; count those separately). No EXACT/REVIEW change outside those groups.
- v6: "777 fewer" is an upper bound. Hint-merges subtract; REVIEW-labelled cross-pointing pair groups (excluded by the "both unique" filter) add. Predict with a script that rebuilds clusters from paths via parse_media_filename; confirm on the next full rescan.
- Follow-up check: no G9 group contains a video whose same-stem still is absent from that group (SQL over stem+dir).

UNSURE / NOT DONE: I did not run anything (read-only). The video↔video and image↔image scope calls are mine and are flagged above. The B6 follow behaviour is not designed here; it needs its own PR.

