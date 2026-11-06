# T2 burst stack — design (opus planner, 2026-11-06, master 974228a) + probe result

Owner's criterion: unrelated photos never share a group; a pick must not need a manual look. After #1040 the
largest remaining "one group, many REVIEW rows" shapes are same-moment bursts (GoPro / compact-camera
series, one frame per second, each frame ≤ 2 s and Hamming ≤ 10 from the next). They are not unrelated;
they must become one stack where nothing is a delete candidate by default (report §5.1 T2).

## Probe result (`main-t2-probe.py migration_manifest_v6.sqlite` → `main-t2-probe-output.txt`)

| counter | value | meaning |
|---|---|---|
| n242 | 242 | groups whose non-low rows disagree in time (the v5→v6 metric) |
| n242_has_video_core | **213** | the disagreeing row is a Live-Photo MOV/MP4 peer (QuickTime UTC date, no pHash) |
| n242_photo_span_le_2s | 203 | once videos are ignored the photos agree — NOT bursts, NOT unrelated |
| bursts: span > 2 s, all gaps ≤ 2 / 3 / 5 / 10 s | 11 / 13 / 14 / 15 | lower bound: REVIEW-low frames (first pair > 2 s away) are excluded by the probe, which opens fake gaps — the implementation counts them as frames |
| rows the rule relabels | 74 REVIEW + 9 format-dup EXACT → "" ; 12 byte copies stay EXACT | across the 11 strict groups |
| n242_has_undated_core / tz gap | 9 / 11 | bridged groups, untouched by design |

Correction to the v5→v6 reading: the "242 high-disagree groups" were ~88 % Live-Photo video peers, not photo
chains. The genuine burst residue is 11–15 groups (largest: 30-frame GoPro 2017-10-15 15:23:54–59; 21-frame
CIMG 2011-07-18; 16-frame CIMG 2011-07-24; 14-frame GoPro 2017-10-17; 12-row DNG trio with copies).
Small, but it is exactly the shape where every row says REVIEW and a human must look at 29 frames.

## Design (one source file: `scanner/dedup.py`)

```
classify()  scanner/dedup.py:302
  Pass 1 _classify_exact      :376   sha copies -> EXACT/high (stay EXACT)
  Pass 2 _classify_phash      :382   REVIEW, pHash-0 "format duplicate" EXACT
  Pass 3 unique / UNDATED     :389
  Pass 4 _collect_pair_edges  :402
  Pass 5 _assign_group_ids    :407 -> (taken, skipped)
  _mark_isolated_hints :410        hint_leaves = {lone for lone, _ in taken}
  _mark_taken_hints    :411
  NEW _mark_burst_stacks(rows, hint_leaves)   inserted after :411, before return :413
```

**Detection.** Per `group_id` (sorted): frames = members NOT in `hint_leaves`, with `phash` set and
`shot_date` set (`_make_row` writes `exif_date.isoformat()`, :1268); sort by
`(datetime.fromisoformat(shot_date), source_path)`. Burst ⇔ `span > BURST_MAX_GAP_S` and every consecutive
gap `≤ BURST_MAX_GAP_S`, `BURST_MAX_GAP_S = 2` shared with `_shot_time_vote`'s tolerance (:252) as one module
constant so they cannot drift. Why 2 s by construction: every high pHash edge between dated photos agrees
within 2 s or a whole half-hour shift (`_edge_confidence` :986-989, `_demotion_note` :288-299,
`_shot_time_vote` :252-275); a sorted gap > 2 s inside a high component therefore means a non-burst bridge
(undated node, tz-shifted copy, Live-Photo pair edge, complementary anchor) and the group is left alone.
Rows labelled REVIEW/low because their FIRST path-ordered pair was > 2 s away (`_classify_near_duplicates`
:961-982) are still frames (they joined through high edges; the GoPro group has 23 high + 5 low).
The pass adds no edge and merges nothing — it only removes delete eligibility.

**Representation (option a).** Inside a burst, every frame that is not a byte copy → `action = ""`,
`match_confidence = "high"`, `reason = BURST_PREFIX + f"{n} frames over {span:.0f} s"` (+ `"; " + old` when
old reason not in ("", "unique")); `BURST_PREFIX = "burst stack (keep all): "` next to `HINT_ONLY_PREFIX`
(:1075). Byte copy test: `p = rows.get(r.duplicate_of)`; `p and p.source_hash == r.source_hash` → stays
EXACT/high, reason unchanged (:438-450); otherwise a pHash-0 format duplicate with a different sha (:670-677)
is a distinct frame → "". `n` = distinct `source_hash` among frames. `duplicate_of` / `hamming_distance`
unchanged (duplicate_of is transient, :178). UNDATED rows cannot be frames. Hint leaves untouched
(REVIEW/low keeps #517 protection; ""/UNDATED leaves keep their "; hint:" reason :1122-1123). No new column.
Cover = the UI's existing Ref winner (top-scoring tier-1 row, `core/app_service/review_view.py:35-57`), also
what auto-select locks (`core/services/auto_select.py:155-185`); not stored, not named in the reason.
Precedent: complementary RAW+lossy groups already ship as all-"" members (`docs/features/scan.md:180`,
dedup.py:608-617).

What today's consumers do with ""/high rows (all verified): auto-select keeper = top score, others not
written (`scan_runner.py:1346-1350`, `auto_select.py:94-97`); aggressive delete excludes them
(`auto_select.py:199, :290`); `apply_best_copy` / "Keep best · delete rest" untouched (`action_service.py:
313-325`, rules.md R-LOCK-7) — byte-copy EXACT rows inside the burst are still deleted (correct, T0); FE
undo-toast allowlist mirrors it (`useAppStore.ts:108, :1563`); UI shows them as "passenger" with N %
similarity (`review_view.py:94-99`, `similarityBadge.ts:127,144-145`), Action column em dash (`format.ts:91`);
sort tier 1 (`manifest_repository.py:99-106`); summary counts them under "other" (`manifest.py:241-265`);
rescore writes score only (`manifest_repository.py:1128-1137`). `reason` is NOT serialized to the UI.

**Version.** `GROUPING_STRATEGY_VERSION` "6" → "7" (:764). Rescore needs nothing.

**Edge cases.** Undated members: not frames, action kept (a stripped copy of a frame stays REVIEW; its
frame survives as ""). tz-shifted copies: gap > 2 s → not a burst, unchanged (conservative). Live-Photo MOV
peers: no pHash → excluded from frames, action unchanged, their UTC date cannot break detection.
Complementary RAW anchors: already "", dated RAW frames take part and get the burst reason; undated attach
but never anchor (:704-707). Lone-lone hint pairs: no frames, no burst.

## Tests — `tests/test_dedup_burst_stack.py` (reuse `_hr` from `tests/test_dedup_hint_edges.py:36-53`)

pHash steps at distance 2, popcount 32: `00000000ffffffff` → `00000001fffffffe` → `00000003fffffffc` →
`00000007fffffff8` (check with `(int(x,16)^int(y,16)).bit_count()`).
1. `test_chain_over_2s_becomes_one_stack` — 5 frames, distinct sha, t0..t0+8 s in 2 s steps → one group,
   every row ""/high, reason starts with BURST_PREFIX and contains "5 frames over 8 s".
2. `test_frame_first_paired_low_still_joins_stack` — path order makes a frame's first pair 4 s away
   (REVIEW/low on master) → ""/high.
3. `test_byte_copy_inside_burst_stays_exact` — plus a sha copy of frame 2 → copy EXACT/high, reason unchanged.
4. `test_identical_phash_distinct_frame_joins_stack` — same pHash, different sha, 1 s apart (format-dup
   EXACT on master) inside a chain spanning > 2 s → "".
5. `test_group_within_2s_is_untouched` — 3 frames spanning 2 s → identical to master (REVIEW stays).
6. `test_undated_bridge_is_not_a_burst` — moments t0..t0+2 and t0+10..t0+12 bridged by an undated file →
   no BURST_PREFIX anywhere.
7. `test_hint_leaf_hours_away_untouched` — burst + a low leaf 5 h apart → leaf keeps REVIEW/low + reason;
   burst still detected.
8. `test_live_photo_video_ignored_by_detection` — HEIC frames + a MOV pair_cluster peer (phash None) dated
   +8 h → still a burst; MOV action unchanged.
9. `test_burst_is_record_order_independent` — 20 `random.Random(seed)` shuffles → identical
   (action, reason, match_confidence, group_id) per path.
10. `test_group_id_unchanged_by_burst_pass` — group_id is the lex-min path of the component.
Plus `tests/test_auto_select.py::TestNonKeepersForAggressiveDelete::test_burst_frames_never_delete_eligible`
— classify() a burst + one sha copy, distinct scores, `keepers = top_score_path_per_group(rows)`, assert
`non_keepers_for_aggressive_delete(rows, keepers) == {sha copy path}`.

## Docs

New entry `### Scan grouping — same-moment bursts become a stack` in `docs/features/scan.md` (+ index row in
`docs/features.md`, regenerate with `scripts/gen_features_index.py`). Edit the Conditions line of
`### Scan grouping — low-confidence hints never chain` (scan.md:202): `"6"` → `"6"` or later (`"7"` adds the
burst entry). Docs impact line must name BOTH headings.

## Open questions for the owner

1. Fast bursts (span ≤ 2 s) keep REVIEW and stay auto-deletable — status quo; extend keep-all to them?
2. Re-encoded copies inside a burst lose auto-delete (missed cleanup, not data loss); a later T1 same-second
   derivative rule could restore it.
3. The UI shows no "burst" label (reason not serialized); a badge needs an API field; "Keep best · delete
   rest" on a burst deletes only byte copies, so its label over-promises there.
4. No span cap proposed (a 30-min timelapse is still a stack); the probe's span p90 is 8 s.
5. Report §5.1 kept T2 at DEFER until G2 is measured; this design adds no edges and only removes delete
   eligibility, so G2 is read as not gating it — owner to confirm.
6. The crossmoment metric should ignore video rows; redo the v5→v6 "242" row with photos only.
