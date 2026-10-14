# Design rulings register

One block per design ruling for the web review screen: what was decided, when,
where the decision came from, whether it shipped, and which code implements it.
The repo copy is authoritative for WHAT WAS DECIDED.

- The standalone claude.ai design project was retired on 2026-10-10. Its
  documents are archived; the rulings below were transcribed from that archive.
  Nothing here depends on the retired project remaining reachable.
- New rulings come through the `design-collab` process (the design-collab skill)
  and are added to this file with the
  next free ID in their series: `Q`, `L` and `F` for the review-screen rounds,
  `OWN-<issue>` for owner rulings, `DEV-<n>` for implementation deviations.
- Code and tests cite ruling IDs (for example `F2` or `OWN-923`) in comments
  instead of restating the decision. The ID is the link; the decision lives here.
- Status is one of `implemented`, `superseded by <ID>`, `closed (no build)` or
  `retracted`. An `implemented` ruling names its implementing code or PRs.
- `scripts/check_rulings_register.py` (CI job `rules-register`) fails on a
  duplicate ID, a missing field, a bad Status or Date, a `superseded by` that
  does not resolve, or an `implemented` ruling with no implementer.

## Review screen open questions (Q1-Q10)

### Q1 — "Action" is the decision column
- **Decision:** The Action column holds the decision control and is headed "Action" (動作). The classification column is removed and its wording moves into the similarity badge tooltip.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/ColumnHeaderRow.tsx::ColumnHeaderRow`, `frontend/src/lib/resultColumns.ts::COLUMNS`

### Q2 — One Ref cue: the ★ badge only
- **Decision:** The Ref pill is dropped. The ★ Ref badge in the Similarity column is the only Ref cue, and the keeper's filename is set at weight 600 with a 2px accent strip on the row's left edge.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/FileRow.tsx::FileRow`

### Q3 — Decision vocabulary: Keep / Delete / Skip
- **Decision:** The three decision states are Keep (empty value), Delete (`delete`) and Skip (`ignore`), used site-wide. Every user-facing delete names the Recycle Bin, and the Skip long form names the file's fate first ("leave on disk").
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/DecisionControl.tsx::DecisionControl`, `translations/en.yml`

### Q4 — Group identity: id and count
- **Decision:** The group header reads "Group N · M files". A derived folder-leaf or date-span suffix is optional polish that this ruling does not require. No kind label is shown.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/GroupRow.tsx::GroupRow`, `frontend/src/lib/groupSummary.ts`

### Q5 — "Keep best · delete rest" lives in the group header
- **Decision:** The button sits right-aligned in the group header. It has no confirm dialog and raises an undo toast of about six seconds. It never overrides a locked file, and the toast reports how many locked files were left unchanged.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/GroupRow.tsx::GroupRow`, `frontend/src/components/Toast.tsx::Toast`

### Q6 — Execute confirm: a modal with folder buckets and reason lines
- **Decision:** The Execute confirm stays a modal of at least 60vh. It groups rows by folder with per-folder subtotals and gives each file a reason line. The totals and the confirm button stay outside the scroll area, and the modal always carries "Files are moved to the Recycle Bin and can be restored."
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/dialogs/DeleteConfirmDialog.tsx::DeleteConfirmDialog`, `frontend/src/lib/deleteConfirmRows.ts`

### Q7 — Keyboard cursor is a stroke, not a tint
- **Decision:** The keyboard cursor is a stroke, an inset accent ring with a ▸ gutter caret, composed over the selection and hover tints, shown on `:focus-visible` only. F2 changed the ring width from the 2px proposed here to 1px; the stroke, the caret and the layering stand.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project); width changed by REPLY - review screen post-implementation.md (F2)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/ResultTree.tsx::ResultTree`

### Q8 — Palette contrast: adjusted accent and ink tiers
- **Decision:** The primary fill is #a85a2c, and hover goes darker to #8f4b23 with active at #7a3f1d. The dim text ink is #6b6358, and dim2 is no longer used as text. #a89f8f is kept only as the non-text `hairline` colour.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/index.css`

### Q9 — Caution role: olive-leaning, ▲ glyph, provisional
- **Decision:** The caution role uses #6b4e12 on #fdf3d9 with a #e3c264 border, a ▲ glyph, and a dashed border on chips. The owner has not accepted these values yet, so no layout may depend on them.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project); status of the values per REPLY - review screen layout.md
- **Status:** implemented
- **Implemented by:** `frontend/src/components/execute/cautionBanner.ts`, `frontend/src/components/HevcHint.tsx::HevcHint`

### Q10 — Empty-state safety line
- **Decision:** Withdrawn by the 2026-09-18 layout reply, which dropped the safety pill. No safety pill is drawn on the empty state.
- **Date:** 2026-09-14
- **Source:** REPLY - review screen open questions.md (archived design project); withdrawal in REPLY - review screen layout.md
- **Status:** retracted
- **Implemented by:** —

## Review screen layout (L1-L5)

### L1 — Flat headed table, not cards
- **Decision:** The result list stays a flat headed table. A group is framed by a band with a 4px accent strip and a 6px breath under its last row, not by card borders.
- **Date:** 2026-09-18
- **Source:** REPLY - review screen layout.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/ColumnHeaderRow.tsx::ColumnHeaderRow`, `frontend/src/components/result/FileRow.tsx::FileRow`, `frontend/src/index.css`

### L2 — Size, date and dimensions stay columns; folder sits under the name
- **Decision:** Size, date and dimensions remain sortable columns. The folder is a mono sub-line under the filename, and size and dimensions are right-aligned.
- **Date:** 2026-09-18
- **Source:** REPLY - review screen layout.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/FileRow.tsx::FileRow`, `frontend/src/lib/format.ts`

### L3 — Absolute px on a 16px root
- **Decision:** The root stays at the Tailwind 16px default, and components use absolute px as specified: rows 72px comfortable and 52px compact, thumbnails 48px and 36px, decision control 32px, group header 44px, toolbar 52px, status bar 30px, preview pane 320px by default.
- **Date:** 2026-09-18
- **Source:** REPLY - review screen layout.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/lib/density.ts`, `frontend/src/index.css`

### L4 — Which audit gaps must match
- **Decision:** Each audit id is verdicted MUST-MATCH or MAY-DIFFER, and the per-id list governs. The summary count (22 and 13) disagrees with the per-id list (25 and 10), which is open in the archive status note. H4, H5 and H6 are dropped by design, not deferred.
- **Date:** 2026-09-18
- **Source:** REPLY - review screen layout.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/App.tsx` (empty state, E1 E3 E4 E5), `frontend/src/components/Toolbar.tsx::Toolbar`

### L5 — No row checkbox; counted bulk verbs; menu bar only
- **Decision:** Bulk selection is ctrl/shift row selection, with no per-row checkbox. The toolbar verbs carry the selection count ("Set N selected: Keep · Delete · Skip") and are disabled at zero. The menu bar is the only chrome bar, with the scan summary folded into its right end. The recent-sources section is dropped.
- **Date:** 2026-09-18
- **Source:** REPLY - review screen layout.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/Toolbar.tsx::Toolbar`, `frontend/src/components/MenuBar.tsx::MenuBar`

## Review screen post-implementation (F1-F4)

### F1 — The hairline stays decorative; the unlocked padlock uses ink-muted
- **Decision:** #a89f8f stays the decorative `hairline` colour and carries no state. The unlocked padlock is drawn in #6b6358, and the locked one stays #a85a2c. Lock state must differ in glyph shape as well as ink; the unlocked glyph actually shipped is recorded in OWN-922.
- **Date:** 2026-09-20
- **Source:** REPLY - review screen post-implementation.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/LockToggle.tsx::LockToggle`

### F2 — A selection is a tint; the cursor is the only ring
- **Decision:** A selected row shows the #f1e3d0 tint with no ring. The keyboard cursor is a 1px inset #a85a2c ring. No other row state may use an accent stroke, and the only row fill besides selection is hover.
- **Date:** 2026-09-20
- **Source:** REPLY - review screen post-implementation.md (archived design project)
- **Status:** implemented
- **Implemented by:** `frontend/src/components/ResultTree.tsx::ResultTree`, `frontend/src/index.css`

### F3 — Sort by decision: the header is canonical, re-sort is deferred
- **Decision:** Built as ruled: the Action header is the canonical control, there is one sort state, lock is not a sort key, the sort is not persisted, a decision change never moves a row, a stale sort shows a 4px dot and re-sorts only on an explicit action, and the third click on the header or "Clear sort" returns to score-descending (post-implementation F3 §3; FEEDBACK item 2, "built as F3 says"). Modified by OWN-923 (order, sub-labels and menu placement) and OWN-743 (tie order).
- **Date:** 2026-09-20
- **Source:** REPLY - review screen post-implementation.md (archived design project); FEEDBACK - F1 F3 implementation deviations.md item 2 ("everything else in F3 is built as ruled")
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/ColumnHeaderRow.tsx::ColumnHeaderRow`, `frontend/src/lib/decisionSort.ts`, `frontend/src/components/result/useResortAnimation.ts`

### F4 — No user override for column shedding
- **Decision:** Column shedding stays automatic, and the question is closed with no build. If it is reopened, the recorded shape is a toolbar Columns menu with per-browser persistence.
- **Date:** 2026-09-20
- **Source:** REPLY - review screen post-implementation.md (archived design project)
- **Status:** closed (no build)
- **Implemented by:** —

## Implementation deviations and owner rulings

### DEV-1 — F3 deviations recorded by engineering
- **Decision:** Modifies F3. Engineering recorded that F3 cannot be built as written on the shipped app. There is no toolbar room for a sort control, so the mirror goes in the menu bar's View menu. The model has no undecided state, so the order is Keep, then Skip, then Delete. The owner ruling OWN-923 resolves both points.
- **Date:** 2026-09-30
- **Source:** FEEDBACK - F1 F3 implementation deviations.md (archived design project), items 2 Q1 and Q2
- **Status:** superseded by OWN-923
- **Implemented by:** —

### OWN-922 — The unlocked padlock uses the standard lock-open glyph
- **Decision:** Modifies F1. The unlocked padlock uses the icon library's standard lock-open glyph (LockOpen), not the shackle "offset right" that F1 described. Locked and unlocked stay distinguishable by glyph shape.
- **Date:** 2026-09-30
- **Source:** FEEDBACK - F1 F3 implementation deviations.md (archived design project), item 1; orchestrator decision, not objected to by the owner
- **Status:** implemented
- **Implemented by:** `frontend/src/components/result/LockToggle.tsx::LockToggle`

### OWN-923 — Sort by decision: View-menu mirror, Keep → Skip → Delete
- **Decision:** Modifies F3 and DEV-1. The sort mirror lives in the menu bar's View menu, not the toolbar. Ascending runs Keep, Skip, Delete, and descending reverses it. The sub-labels are "ACTION · KEEP FIRST" and "ACTION · DELETE FIRST" (zh_TW 「動作 · 保留優先」 and 「動作 · 刪除優先」).
- **Date:** 2026-09-30
- **Source:** FEEDBACK - F1 F3 implementation deviations.md (archived design project), item 2; owner answer to AskUserQuestion, 2026-09-30. Conflict: the code comment in frontend/src/lib/decisionSort.ts:1 and docs/testing.md:207 cite "the owner's 2026-09-29 rulings"; the ledger's 2026-09-30 date is used here.
- **Status:** implemented
- **Implemented by:** `frontend/src/components/MenuBar.tsx::MenuBar`, `frontend/src/components/result/ColumnHeaderRow.tsx::ColumnHeaderRow`, `frontend/src/lib/decisionSort.ts`

### OWN-933 — Set Action folds into the overflow menu below 1100px
- **Decision:** Below 1100px the toolbar's Set Action… moves into the ⋯ overflow menu. This replaces the earlier #916 pin, which kept it visible.
- **Date:** 2026-09-30
- **Source:** owner approval, 2026-09-30, recorded in the orchestrator ledger
- **Status:** implemented
- **Implemented by:** `frontend/src/components/Toolbar.tsx::Toolbar`, `frontend/src/lib/toolbarShed.ts` (`TOOLBAR_MANIFEST_SHED_WIDTH = 1100`)

### OWN-896 — Recursive scan on by default; an empty scan keeps the dialog open
- **Decision:** A new scan source row includes subfolders by default, and a scan that finds no media files keeps the scan dialog open with the no-files message. Pinned by R-SCAN-1 and R-SCAN-2 in `docs/behaviour/rules.md`.
- **Date:** unknown — the ruling date is not in the sources
- **Source:** owner ruling #896, recorded in the orchestrator ledger
- **Status:** implemented
- **Implemented by:** `frontend/src/components/ScanDialog.tsx::ScanDialog`

### OWN-743 — Within-group ties keep the server order
- **Decision:** Modifies F3. Rows that tie on decision keep the order the server sent, which is score-descending by default or the user's sorting default. No separate score key is applied.
- **Date:** unknown — the ruling date is not in the sources
- **Source:** orchestrator decision, not objected to by the owner (orchestrator ledger)
- **Status:** implemented
- **Implemented by:** `frontend/src/lib/decisionSort.ts`
