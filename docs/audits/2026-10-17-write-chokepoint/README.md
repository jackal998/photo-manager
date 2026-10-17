# Write-chokepoint audit (2026-10-17)

An audit of "one rule, many homes". The lock rule (R-LOCK-1) and the other
write rules are enforced in several places, so each fix has missed one:
#981-#985, #999 and #1000. Tracking issue: #958.

Files:
- `design.md`: the design (Track A required, Track B optional), with the bug list and owner decisions.
- `inventory-backend.md`, `inventory-frontend.md`: the two code inventories it rests on.
- `design-review.md`: an independent, fresh-context review of the design.

Verdict (design-review.md): ADOPT WITH CHANGES. The diagnosis is right, and the
central server move is the correct lever. Three things must change before code
is written.

Track A PRs (required, in design section 7):
A1 #1000 routing, A2 Skip writes only reviewed rows, A3 decision-write chokepoint,
A4 outcome-write chokepoint, A5 delete dead unguarded writers, A6 named
confirmation for decisions, A7 Undo without blind force (#999), A8 named
confirmation for the other six verbs, A9 Execute re-checks each row's lock.

Track B issues (optional consistency work, one issue each):
B1 #1001, B2 #1002, B3 #1003, B4 #1004, B5 #1005, B6 #1006, B7 #1007,
B8 #1008, B9 #1009, B10 #1010, B11 #1011, B12 #1012, B13 #1013.

Owner decisions of 2026-10-17:
- D7: a forced re-send that meets a row locked after the dialog opened asks again for that row.
- D6: a row locked mid-Execute is left alone, keeps its lock, and is reported.
- D10: Undo of a forced Keep best asks again when the keeper is locked.

Still open: D1, D2, D3, D4, D5, D8, D9 and D11. Track B items B2, B7, B8, B9
and B12 wait on them.
