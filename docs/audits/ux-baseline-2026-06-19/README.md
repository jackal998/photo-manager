# UX Baseline — Main View & Key Dialogs (Phase 0)

**Date:** 2026-06-19
**Purpose:** Establish a shared, honest baseline of the current UI before any
UI/UX refinement. This is the reference both Claude Design mockups and the
eventual Qt edits aim at. **No behaviour was changed to produce this** — it is
a render of the real widgets against a synthetic manifest.

## How to regenerate

```
.venv/Scripts/python.exe scripts/render_ux_baseline.py [OUTPUT_DIR]
```

- Renders on the **native** Qt platform (offscreen lacks font glyphs → tofu).
  A window flashes briefly, then the process self-exits.
- All persisted state (`settings.json`, `window_state.ini`, QSettings column /
  geometry blobs) is sandboxed under `PHOTO_MANAGER_HOME=qa/sandbox/_disposable/ux_baseline_home`,
  so running it never touches your real dev-app layout.
- The manifest is **synthetic**. Folder columns show fabricated paths
  (`D:/iPhone/…`); the real on-disk image paths live only in the hidden
  `PATH_ROLE` and are never rendered — keeping these screenshots free of the
  machine's home path (PII).

The synthetic manifest deliberately exercises every results-tree state:
`Ref` / `—` (Live-Photo MOV) / `N%` / `100%` / `N*%` (passenger), all three
decision states (`delete` / empty-keep / `remove from list`), a 🔒 locked row,
and scored + unscored rows.

---

## The surfaces

| # | Surface | File |
|---|---|---|
| 1 | Main window — empty state | [`01_main_empty_state.png`](01_main_empty_state.png) |
| 2 | Main window — populated + expanded | [`02_main_expanded.png`](02_main_expanded.png) |
| 3 | Main window — collapsed groups | [`03_main_collapsed.png`](03_main_collapsed.png) |
| 4 | Execute Action dialog | [`04_execute_action_dialog.png`](04_execute_action_dialog.png) |
| 5 | Set Action (Field/Regex) dialog | [`05_set_action_dialog.png`](05_set_action_dialog.png) |

---

## Scorecard against the 7 QA UX axes

Axes are the project's own UX priorities (`qa-explore/project-context.md`).
Score: ✅ solid · ⚠ friction · ✗ gap. **Correctness (F) is intentionally not
in scope here — we are not changing what the app computes.**

| Axis | Verdict | Notes |
|---|---|---|
| **A — Feedback** | ✅ | Status bar ("Ready"), empty-state guidance, and the Execute summary line ("4 file(s) with decisions: 3 delete") all answer "did it work / what's the state". |
| **B — Labels** | ✅ | Copy is clear and translated. No drift spotted in this baseline. |
| **C — Discoverability** | ⚠ | Every primary verb (Scan, Open, **Execute**, Set Action) lives only in menus — no toolbar. The core workflow's actions aren't surfaced. |
| **D — Modal/state** | ✅ | Selection, expand/collapse, splitter, geometry all behave; dialogs are well-formed. |
| **E — Destructive** | ✗ | The single most consequential state — `delete` — is rendered as plain low-contrast text in the Action column, the *lowest*-salience treatment on screen. True on the destructive Execute dialog too. |
| **G — Performance** | ✅ | (Not measured here; no regression introduced.) |

**Cross-cutting (visual language):** ⚠ — entirely default Qt palette. No theme,
no type scale, no accent colour, no row striping in the main window (the Execute
dialog *does* stripe — inconsistent), and the lock indicator is an emoji 🔒 in an
otherwise monochrome text UI.

---

## Concrete friction findings

Each is **functionality-preserving** — a visual/affordance change, not a
behaviour change.

1. **Delete-state has no salience (E).** `delete` is gray text in one column.
   The app's whole job is safe triage of deletions; the delete decision should
   be unmistakable (row tint / coloured badge / strikethrough). Highest-payoff
   change. *(02, 04)*
2. **Group rows read at the same weight as file rows (B/structure).** "Group 1"
   is distinguished only by a small chevron + indent. The group is the
   scannable spine of the view; it should be visually heavier (bg band / bold).
   *(02, 03)*
3. **Similarity column overloads 5 semantics as plain text (A).** `Ref` / `—` /
   `95%` / `100%` / `97*%` share one undifferentiated text column; `—` and
   `N*%` only decode via hover tooltip. No legend. A colour/shape encoding +
   a one-line legend would make the column self-explaining (its original
   design goal). *(02)*
4. **No visual theme / foundation.** Flat default palette, no row striping in
   the main tree, no accent, emoji lock glyph. There's large headroom but no
   base to build on. *(all)*
5. **No toolbar (C).** Primary verbs are menu-only. *(02)*
6. **Folder column = full left-aligned path (info scent).** Real paths are long;
   the meaningful tail (which folder) clips first. Consider eliding head, or a
   two-line name/path treatment. *(02, 04)*
7. **Column widths crowd the preview.** Sizing all 11 columns to content lets
   the tree squeeze the preview pane to a sliver — width management is fragile.
   *(02)*
8. **Set Action dialog has a large dead vertical gap** between the Simple row
   and the Regex field, with the "preview unavailable" notice floating in the
   void; the Simple-vs-Regex relationship reads as unclear. Boxed sections
   would clarify they're alternatives. *(05)*
9. **Empty state is functional but unpolished.** Good guidance text + two
   buttons, but buttons are un-emphasized default size amid large dead space,
   and the preview pane shows a literal `(preview)` placeholder. *(01)*
10. **"Select by Field/Regex…" reads as a header bar, not a button** in the
    Execute dialog (full-width, flush). *(04)*

---

## Technical constraints (carry into any design work)

- **Qt desktop, not web.** Styling = **QSS** (a CSS subset) + per-cell
  **`QStyledItemDelegate`**. There are currently **zero custom delegates** and
  **no QSS theme** — all styling is a few inline `setStyleSheet()` calls. Rich
  cells (coloured similarity badges, delete tint) require a delegate.
- **Claude Design can't emit Qt.** It targets HTML component libraries; use it
  only to explore the *visual language* (palette, badge treatment, spacing,
  group-header weight), then translate to QSS + a delegate by hand. HTML is the
  sketchpad; `docs/features.md` + the Qt code stay canonical.
- **Guardrails:** any `app/views/**` change is CI-gated (qa scenario or
  `[qa-not-needed:]`, `features.md` update, news fragment).

---

## Suggested Phase 1 (for discussion — not started)

Ordered by payoff, all functionality-preserving:

1. **Theme foundation** — a single QSS stylesheet (palette, type scale, row
   striping, group-header weight). The missing base everything else builds on.
2. **Delete/lock salience** — a `QStyledItemDelegate` (or row-role tint) so the
   delete decision and locked rows are unmistakable. Biggest safety win.
3. **Similarity badges + legend** — encode the 5 states with colour/shape and
   add a one-line legend / header tooltip.
4. **Toolbar** — surface Scan / Open / Execute / Set Action.
5. **Polish** — empty state, Set Action grouping, Execute button restyle.

Items 1–3 are the natural first Claude Design exploration: mock the themed tree
with badges and delete-tint as HTML, agree the look, then I implement as
QSS + delegate and re-run this harness to compare before/after.
