# PR-1 design — why and how sure, in the view (+ the #1047 badge half)

Author: opus planner (read-only), 2026-11-07, master 7b96762. Spec: `auto-select-prd-2026-11-v2.md` §7 and §11 PR-1.
Lead decisions on top of the design: option (b) tooltip + decision chips; F7 (delete-confirm wording) is IN scope.

## Contradictions with the brief (read first)
1. `PhotoRecord` already has `match_confidence` (core/models.py:67) and load() already passes it
   (infrastructure/manifest_repository.py:1070). Only `reason` is missing: SELECTed at :73, never passed at :1050-1071.
2. `gh issue view 1047` was not run (no shell); the #1047 scope comes from STATE.md:445-453.
3. The preview metadata table is pinned at exactly five rows (PreviewPane.test.tsx:369-384, s77_preview_pane.py:336),
   so no "Why" row there; the preview badge gets the tooltip.
4. The same defect sits in the Execute delete-confirm reason line: deleteConfirmRows.ts:127 says "Exact duplicate of X"
   for any percent 100, so a REVIEW row at Hamming 0 is called exact at the destructive moment → step F7.
5. A visible chip is UI work; it reuses the existing pill vocabulary and caution tokens (index.css:100-102).
6. PRD §7 says every row "shows" a one-line reason. This design puts reason + confidence in the hover tooltip on every
   row; a visible chip appears only where it changes the decision: burst → "Keep all · burst", low → "Low confidence".

## Data path
```
migration_manifest.reason / .match_confidence
  -> _LOAD_ALL_SQL (:73,:86) -> load() :1070 [+db_reason] -> _photo_record :494 [+reason]
  -> PhotoRecord (models.py:67) [+reason]
  -> _build_file_row (review_view.py:132-165) [+reason, match_confidence, keep_all]
  -> GET /api/manifest (plain dicts; review_service.py:78, action_service.py:191/321)
  -> FileRow type (types.ts:15-47) -> FileRow.tsx chip + badge title; PreviewPane badge title
```

## Q1 Data
Add `reason: str = ""` to PhotoRecord. Row dict gains `reason` (str, "" for NULL), `match_confidence`
("high"/"low"/None) and `keep_all` (bool = reason.startswith(BURST_PREFIX), derived server-side from
scanner/dedup.py:1191 so no English prefix is copied into TypeScript; scanner.dedup imports only stdlib + scanner.media /
phash_distance / walker, safe in the headless view). TS fields optional (≈25 fixtures build FileRow literals; precedent
#680 types.ts:35-46). JSON growth ≈115 B per grouped row (estimate). Reasons carry basenames, never paths
(dedup.py:465,743,769,1085).

## Q2 Presentation — option (b) + decision chip
Reject a new "Why" column: default requiredWidth 992 px (resultColumns.ts:74-85,:184-217); +160 px sheds dims AND date
(date is a primary sort :146-148); raw reason text in a column is the debug text the owner rejected. The badge title
already holds the classification (FileRow.tsx:104-108,:272) → becomes: classification, reason (omitted when "" or
"unique"), "Confidence: high/low", one per line.

## Q3 Badge
similarityBadge.ts:141 derives "exact" from `percent === 100` → `action === "EXACT" ? "exact" : "near"`, with `action`
a new REQUIRED second parameter. A REVIEW row at Hamming 0 keeps the label "100%" in the blue near pill; its tooltip
gives the reason. Backend review_view.py:78-88 unchanged.

## Q7 Risks
Nothing reads the badge or similarity for logic. Non-test reads are display only: FileRow.tsx:102,111,270,
PreviewPane.tsx:147,299,312, SimilarityLegend.tsx:78,99, deleteConfirmRows.ts:74,93,118 (kind "ref" unchanged).
Aggressive eligibility reads action, confidence and score only (auto_select.py:199,285-292; action_service.py:308).
No delete eligibility changes. Layout: the name line is `flex-wrap` (FileRow.tsx:237); a chip would wrap to a third
line and break the fixed row height in lib/rowMetrics.ts → drop the wrap.

## Edit list (in order)
- B1 core/models.py:63-67: add `reason: str = ""` after match_confidence; fix the :65-66 comment ("not serialised to the web API").
- B2 manifest_repository.py:422: param `db_reason: "str | None" = None`; :494 `reason=db_reason or ""`; :1070 `db_reason=row["reason"]`.
- B3 review_view.py: `from scanner.dedup import BURST_PREFIX` near :19; in `_build_file_row` after :162 add keys
  `reason`, `match_confidence` (getattr, None default) and `keep_all = reason.startswith(BURST_PREFIX)`.
- F0 types.ts after :46: optional `reason?: string; match_confidence?: "high" | "low" | null; keep_all?: boolean;` + doc comment.
- F1 similarityBadge.ts:134-141: signature `(similarity, action: string)`; percent case per Q3; update doc table :123-126 and the :50 "byte-identical" comment.
- F2 callers pass `row.action`: FileRow.tsx:111,:270; PreviewPane.tsx:147,:299; SimilarityLegend.tsx:34-64 LEGEND_ROWS gain
  `action` (ref "", exact "EXACT", near "REVIEW_DUPLICATE", indirect "", none ""), passed at :78.
- F3 new frontend/src/lib/reason.ts (~50 lines, pure): `verdictChip(row)` → "keep_all" | "low_confidence" | null;
  `verdictChipLabel(chip, t)`; `confidenceLabel(level, t)` (unknown value printed verbatim, like format.ts:93);
  `rowTooltip(row, t)` (newline-joined title per Q2, uses classificationLabel).
- F4 FileRow.tsx:272 `title={rowTooltip(row,t)}`; :237 classes → `flex items-center gap-1 min-w-0`; `min-w-0` on the
  basename span :238; after :249 render `<span data-verdict-chip={chip} title=…>` classes
  `flex-shrink-0 rounded-[5px] border px-1.5 py-px text-[10px] font-medium`; keep_all `border-hairline text-ink-muted`;
  low `border-caution-line bg-caution-bg text-caution-ink`.
- F5 PreviewPane.tsx:298 badge span `title={rowTooltip(row,t)}`; no MetaRow.
- F6 translations/en.yml after :651, zh_TW.yml after :524, under web: a `reason:` block — keep_all_chip "Keep all · burst" /
  "全部保留 · 連拍"; low_confidence_chip "Low confidence" / "低信心"; confidence_high "Confidence: high" / "信心：高";
  confidence_low "Confidence: low" / "信心：低". Parity pinned by tests/test_web_i18n.py:90-113; passthrough probe
  tests/test_source_probes.py:492.
- F7 deleteConfirmRows.ts: `action: string` on DeleteConfirmRow (:20-32); `action: row.action` at :90; "action" in the Pick
  at :115; :127 → `if (row.action === "EXACT")` so a REVIEW row at 100 falls to reason_near ("100% match — same group");
  fixtures deleteConfirmRows.test.ts:189-245 and DeleteConfirmDialog.test.tsx:46,296.

## Tests
Backend, tests/test_review_view.py: `_FakeRecord` (:27-46) gains `reason: str = ""`, `match_confidence: str | None = None`;
required_keys in test_file_row_keys (:240-254) gains the three keys; new class TestReasonAndConfidenceSerialisation using
`_row` (:291-296): test_reason_and_confidence_serialised_from_record, test_burst_reason_sets_keep_all (imported
BURST_PREFIX), test_burst_byte_copy_is_not_keep_all (EXACT, "exact duplicate of a.jpg"),
test_null_reason_serialises_empty_and_confidence_none. tests/test_manifest_repository.py next to :1401:
test_reason_and_match_confidence_load_from_db_into_photo_record (write_manifest then load; a dropped kwarg leaves the default).
Frontend vitest (CI only; worktree has no node_modules): similarityBadge.test.ts:64-76 cases gain an action column
(percent 100 + "EXACT" → exact) + "maps a REVIEW row at Hamming 0 to near, not exact (#1047)"; pass action at :91.
FileRow.test.tsx:71-78 FLIPS (makeRow default REVIEW_DUPLICATE :27 encodes the bug): give it action "EXACT" and add
"review row at hamming 0" → near; :270-280 stay green. New in FileRow.test.tsx: "renders the keep-all chip on a burst
row", "renders the low-confidence chip", "renders no chip on a high-confidence row", "badge title carries the reason and
confidence". New lib/reason.test.ts: "verdictChip prefers keep_all over low", "verdictChip is null for a high-confidence
row", "rowTooltip puts classification, reason and confidence on separate lines", "rowTooltip omits 'unique' and empty
reasons", "confidenceLabel prints an unknown level verbatim". SimilarityLegend.test.tsx:36 and s74_daylight_theme.py:1296
stay green. No new testids (`data-verdict-chip` follows the `data-sim-state` precedent).

## Q5 QA
qa/web/scenarios/s07_format_dup.py: add reason + match_confidence to the probe print (:176-181); Assertion G after :274:
the JPG reason starts with "near-duplicate (hamming=", match_confidence in ("high","low"), keep_all False; the HEIC reason
== "unique" (the JPG confidence is not pinned because the dHash claim at :239-240 is unverified). No DOM assertion. Local
run needs the owner's OK; CI runs the batch.

## Q6 Docs
- review.md "### Similarity column" line 30: replace the sentence that the badge keys on the percentage with: only an
  EXACT row wears the purple pill; a REVIEW row at distance 0 reads 100% in the blue near pill (#1047); extend "Hovering
  the badge shows the row's classification in words" with "+ reason + confidence".
- NEW heading in review.md after Similarity column: "### Review tree — why and how sure" (Area/Entry point/Trigger/
  Behaviour/Conditions/Related). Then `python scripts/gen_features_index.py` (features.md:33: index rows are generated).
- scan.md "### Scan grouping — same-moment bursts become a stack" (:243): rows wear the Keep all · burst chip.
- scan.md "### Scan grouping — EXACT requires pixel identity" (:204): a demoted row shows the near pill.
- With F7: execute-action.md "### Execute Action — complete-group delete confirm" (:55).
- news/<PR>.feature after create.
