# qa-explore — project priority context

This file holds the project-history-driven priority list used by
the qa-explore skill. The main [`SKILL.md`](SKILL.md) points
here for the "what users actually care about" background.

Read this BEFORE starting Phase 4 — it tells you what the user
values and what past pain has informed scenario priorities.

---

## What users actually care about (from project history)

These categories recur across closed bug/UX issues — they're what
users have actually complained about on this project. Weight your
exploration toward these, not toward exotic edge cases that no one
has hit.

### A. "Did it work?" — feedback after every action

The single most common class of complaint: doing something and not
being sure whether it succeeded.

- Status bar empty when no manifest loaded ([#138](https://github.com/jackal998/photo-manager/issues/138))
- Status bar context wiped permanently when any menu opens ([#140](https://github.com/jackal998/photo-manager/issues/140))
- Empty-folder scan never logs "Done." — looks hung ([#56](https://github.com/jackal998/photo-manager/issues/56))
- Empty-folder scan shows error icon instead of neutral result ([#51](https://github.com/jackal998/photo-manager/issues/51))
- "Close & Load" button missing after a zero-file scan ([#86](https://github.com/jackal998/photo-manager/issues/86))
- Scan dialog "+ Add" silently fails for non-existent paths ([#144](https://github.com/jackal998/photo-manager/issues/144))
- Re-scan while manifest loaded silently overwrites ([#142](https://github.com/jackal998/photo-manager/issues/142))

**As a tester, after every action ask:** *did anything visibly
acknowledge that?* The absence of an error is not feedback. If you
need to peek at the log file to know whether something worked,
that's a finding.

### B. Copy, labels, wording

Small wording bugs land at high frequency because they're visible on
every screen.

- "Close  Load" with a double space ([#54](https://github.com/jackal998/photo-manager/issues/54))
- "1 pairs to review" — hardcoded plural ([#109](https://github.com/jackal998/photo-manager/issues/109))
- "M1" suffix in window title with no explanation ([#41](https://github.com/jackal998/photo-manager/issues/41))
- "priority" wording in scan folder list confused users ([#213](https://github.com/jackal998/photo-manager/issues/213))
- Stale "SKIP" / "MOVE" wording from legacy design ([#180](https://github.com/jackal998/photo-manager/issues/180))

**As a tester, read every label out loud.** If you'd hesitate over
what one means without context, that's a finding. Watch for plurals,
double spaces, mystery suffixes, jargon from old designs.

### C. Discoverability — "where is the button?"

- No first-run / empty-state guidance ([#42](https://github.com/jackal998/photo-manager/issues/42), [#137](https://github.com/jackal998/photo-manager/issues/137))
- File picker had no text path entry / paste ([#40](https://github.com/jackal998/photo-manager/issues/40))
- "List" menu opened nothing — no submenu, no items ([#52](https://github.com/jackal998/photo-manager/issues/52))
- Top-level menus lacked Alt-key mnemonics ([#135](https://github.com/jackal998/photo-manager/issues/135))

**As a tester, on first launch, ask:** *what do I click first?* If
you walked into the app cold, would you find the start-a-scan path
inside 10 seconds? Try keyboard-only (Alt+letters, Tab, arrows) —
does the app cooperate?

### D. Modal / state behavior — "what is the app's mode right now?"

- Execute Action dialog was non-modal — main-window menus stayed clickable ([#139](https://github.com/jackal998/photo-manager/issues/139))
- Two-step delete confirm felt redundant ([#30](https://github.com/jackal998/photo-manager/issues/30))
- Window position / size not persisted across launches ([#141](https://github.com/jackal998/photo-manager/issues/141))
- Failed Open Manifest disabled actions on the previously-loaded one ([#108](https://github.com/jackal998/photo-manager/issues/108), [#110](https://github.com/jackal998/photo-manager/issues/110))
- Right-click on empty area / menu bar produced an irrelevant menu ([#124](https://github.com/jackal998/photo-manager/issues/124))

**As a tester, try ordinary mistakes:** open a dialog, click behind
it, try to use the main window. Close the app, reopen — same shape,
same column widths, same selection? Right-click in odd places — do
you get a menu that makes sense for *that* spot?

### E. Destructive actions — "did I lose work?"

- Re-scan silently overwrote pending decisions ([#142](https://github.com/jackal998/photo-manager/issues/142))
- Locked files could be removed from the delete list ([#208](https://github.com/jackal998/photo-manager/issues/208))
- Locked-confirm dialog fired incorrectly with mixed locked + unlocked ([#207](https://github.com/jackal998/photo-manager/issues/207))
- Save Manifest data-loss with uncheckpointed WAL ([#91](https://github.com/jackal998/photo-manager/issues/91))

**As a tester, before any "Yes" on a destructive prompt, ask:** *do I
know exactly what gets deleted, and is that what I meant?* Try the
destructive flow with locks set, with multi-selection that includes
locked items, with unsaved manifest changes pending — does the count
in the prompt match the count you'd expect?

### F. Real-data correctness

- Live Photo HEIC+MOV pair not grouped ([#88](https://github.com/jackal998/photo-manager/issues/88))
- Scan summary undercounted skipped files ([#87](https://github.com/jackal998/photo-manager/issues/87))
- exiftool batch returned wrong/empty dates for some files ([#145](https://github.com/jackal998/photo-manager/issues/145))
- DNG resolution wrong in scanner and preview ([#32](https://github.com/jackal998/photo-manager/issues/32))
- Sort by Similarity used group_number, not what users expected ([#29](https://github.com/jackal998/photo-manager/issues/29))

**As a tester, glance at the results table after every scan:** does
anything look obviously wrong for the data you put in? Wrong date,
missing thumbnail, missing file, weird sort order, group that
shouldn't be a group, file that should be grouped but isn't?

### G. Performance felt by a human

- NAS load was slow before manifest metadata caching ([#15](https://github.com/jackal998/photo-manager/issues/15))
- SQLite without WAL was slow ([#18](https://github.com/jackal998/photo-manager/issues/18))
- Hosted CI native dialog COM modal silently dropped input ([#129](https://github.com/jackal998/photo-manager/issues/129))

**As a tester, notice when a spinner runs longer than you'd expect.**
"Felt slow" with a wall-clock estimate is a valid finding. Whether a
3-second wait is too long for *this* action is a judgment a logic
check cannot make — that's why you're here.

