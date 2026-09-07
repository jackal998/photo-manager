# qa-explore — Phase 4 driving details

This file holds the DOM-first driving channel and the per-scenario
loop — the operational core of running a qa-explore session. The main
[`SKILL.md`](SKILL.md) loads section 4.0 (tools setup) inline and points
here for sections 4.0.5 and 4.1.

## Contents

- 4.0.5 — DOM-first driving (the cheap navigation channel)
- 4.1 — Per-scenario loop

---

### 4.0.5 — DOM-first driving (the cheap navigation channel)

photo-manager is a React client served by a local FastAPI process and
opened in a real Chromium browser by Playwright. Every element the QA
drivers address carries a `data-testid`, declared once in
`frontend/src/testids.ts` and mirrored in `qa/web/testid_constants.py`.
A locator query returns structured text — a few hundred bytes — versus
~100 KB for a screenshot.

**Default this session to DOM queries, not screenshots.** A screenshot
is for *visual evidence* (rendering bugs, layout, finding-frame quotes),
never for finding the next button to click.

**One-time install** (gated; ask the user before running):

```
.venv/Scripts/python.exe -m pip install playwright
.venv/Scripts/playwright.exe install chromium
```

See [`qa/web/INSTALL.md`](../../../qa/web/INSTALL.md) for the full
setup, including what to do when the Chromium download is blocked.

**The three questions the DOM answers cheaply**

| Question | How |
|---|---|
| Is this control present / enabled? | `page.get_by_test_id(ID)` + `.is_visible()`; Radix marks a disabled menu item with `data-disabled` (`None` means enabled) |
| What does it say? | `.inner_text()` — the rendered copy, exactly what the user reads |
| Where is it, and is it occluded? | `.bounding_box()` — the only reliable way to catch sticky-header overlap and virtualizer offset bugs |

**When the DOM returns nothing useful** (a canvas, a decoded `<video>`
frame, a CSS-only visual glitch): that's your cue to fall back to a
screenshot for *that step only*. Don't abandon DOM queries for the rest
of the scenario.

**Two traps specific to this client**

- **Virtualized rows.** The result tree renders only the rows in view.
  A row that is "missing" may simply be outside the virtualizer's
  window — scroll to it before calling it absent.
- **Async writes.** A decision write is a `PATCH /api/decision`; the
  store updates optimistically. Assert against `GET /api/manifest`
  when you need the committed value, not against the rendered cell.

---

### 4.1 — Per-scenario loop

Each chosen scenario has a **pre-built driver script** at
`qa/web/scenarios/sNN_<title>.py`. The driver does the canonical happy
path deterministically, asserts the load-bearing outcomes, and prints
structured `step:` / `key=value` lines to stdout. Your job is to (a)
approve the launch, (b) run the driver, (c) read its output, (d)
optionally do free-form DOM probes for surprising states or edge cases
the driver doesn't cover.

For each scenario:

1. **Pause and ask** in chat: `"About to launch the app for scenario
   N: <title>. OK?"` — wait for explicit yes. In default-batch mode
   (Phase 3 with no user hint) or when the user explicitly requests an
   end-to-end batch run, get a single `yes batch` up front for the whole
   batch and proceed without re-prompting per scenario. The Phase-3
   default for `/qa-explore` with no args **is** the batch path — go
   straight to that prompt rather than asking which scenarios to run.

2. **Build the frontend once** if `frontend/dist` is missing or stale
   (gated — it runs `npm`):

   ```
   (cd frontend && npm ci && npm run build)
   ```

3. **Start the server once** for the whole session, in the background,
   with the QA config root:

   ```
   PHOTO_MANAGER_HOME=qa .venv/Scripts/python.exe -m uvicorn \
       app.web.main:create_app --factory --host 127.0.0.1 --port 8765
   ```

   `PHOTO_MANAGER_HOME=qa` — the app reads `qa/settings.json` (which
   only references `qa/sandbox/`) and ignores the user's root
   `settings.json` / `migration_manifest.sqlite`. Poll
   `GET /api/health` until it returns 200 before running any driver;
   don't sleep a fixed interval.

   Unlike the old desktop harness, the server boots ONCE — the drivers
   each open their own browser page against it. Kill it by PID when the
   session ends and confirm the port is free.

4. **Run the driver:**

   ```
   .venv/Scripts/python.exe -m qa.web._batch sNN_<name> \
       --base-url http://127.0.0.1:8765
   ```

   The driver is short, deterministic, and version-controlled. Read its
   output to populate findings; don't re-do the navigation by hand.

   **Optionally probe further with free-form DOM queries** if the
   driver's output suggests something worth investigating (an
   unexpected row count, a state transition that looked off, a label
   that surprises you). Use the helpers in `qa/web/_invariants.py` and
   the ids in `qa/web/testid_constants.py` — don't rebuild what's
   already there.

   Findings are textual. The "Screenshot path" line in the issue body
   is **optional and usually omitted**. If a visual is genuinely
   load-bearing for reproduction, ask the user to capture it manually
   after the run.

   **What NOT to screenshot** (these are noise; skip them):
   - finding the next element to address — that's the DOM's job
   - reading dialog text — `inner_text()` gives it to you as a string
   - successful clicks landing on the right element
   - hover states, cursor moves, focus rings
   - routine scrolling between identical states
   - the same dialog 3 times in a row while you reason about it

   **What IS worth a screenshot** (sparingly — once each):
   - the moment a *visual* finding becomes visible (broken thumbnail,
     mis-rendered preview, layout overflow, wrong icon)
   - a canvas or decoded video frame whose state the DOM can't describe
   - unexpected visual state you want to confirm before acting

5. **Be a human, not a script.** The driver covers the deterministic
   happy path. Your job on top of it is to walk the app the way a
   first-time user would — and notice the moments a real user would
   hesitate, doubt, re-read a label, or look for a button that isn't
   there. Drive the categories from the "What users actually care
   about" section above, in roughly this order:

   **Feedback check (Category A)** — after every state-changing
   action (scan, save, execute, open manifest, set action), pause
   for one beat and ask: *did anything visibly tell me that worked?*
   Look at the status bar, dialog, log preview, results count.
   "Nothing changed visibly" is a finding.

   **Label check (Category B)** — read every visible button label,
   menu item, status-bar text, and dialog body once. Note anything
   that:
   - has a double space, weird capitalization, or a stale term from
     an earlier design ("SKIP", "MOVE", "priority")
   - hardcodes a plural ("1 pairs", "1 items")
   - includes a mystery suffix or version sigil with no tooltip

   **Discoverability check (Category C)** — pretend you opened the
   app for the first time. Time how long until you find the path to
   start a scan. Try keyboard-only (Alt+letter, Tab, arrows, Enter,
   Esc) — does the app cooperate?

   **Modal/state check (Category D)** — open a dialog, then try to
   click behind it. Close the app while a manifest is loaded with
   unsaved changes, reopen — same window size, position, columns,
   selection? Right-click in odd places (empty area, header,
   unselected row) — is the menu sensible for that spot?

   **Destructive check (Category E)** — before clicking "Yes" on
   any destructive prompt, read the count in the body. Does it
   match what you'd expect for the current selection? Try the
   destructive flow with mixed locked/unlocked selection. Try
   re-scanning when there are pending decisions — are you warned?

   **Correctness check (Category F)** — glance at the results
   table after the driver finishes. Do dates look right for the
   data you fed in? Are thumbnails missing? Is anything grouped
   that shouldn't be, or not grouped that should be?

   **Speed check (Category G)** — note when a spinner runs longer
   than feels right. Capture a wall-clock estimate. "Felt slow"
   is allowed as a finding — be specific about how long and what
   you were doing.

   **Edge probes** (run these only if budget permits, after the
   above):
   - empty input, huge input
   - escape mid-operation
   - double-click, rapid clicks
   - resize the window to extremes
   - open a context menu, dismiss it, reopen it
   - try the same action twice in a row

6. **Note findings as you go, classified into two buckets.** Keep a
   running list in your reasoning. For each one, decide on the spot:

   - **Correctness finding** — there is a measurable wrong behavior:
     a count that's off, a state that didn't update, a feature that
     silently no-ops, a label that's literally broken (double space,
     wrong plural), a file that should be grouped but isn't.
     *These get filed as GitHub issues in Phase 5.*
   - **UX-friction note** — there is a judgment call about how
     something feels: a splitter ratio that seems cramped, copy that
     "could be clearer", a confirmation that "could have a count",
     a wait that "felt long". *These get batched as review notes
     for the human in Phase 5 — do NOT file as separate tickets.*

   If you can't tell which bucket a finding belongs in, it's a
   UX-friction note. Reserve issue-filing for things with measurable
   wrong behavior. (See [`feedback_qa_explore_ceiling.md`](../../../../../.claude/projects/C--Users-J-repository-photo-manager/memory/feedback_qa_explore_ceiling.md)
   in memory for the rationale: the 2026-05-06 gap-fill pass filed
   10 issues; only 2 turned out to be real defects, and the other 8
   were defensible UX-judgment calls that should have been one
   batched review note.)

   **Carve-out — a deterministic driver failing consistently across
   re-runs is correctness, not friction.** The driver embodies a
   contract about a specific UI shape; a stable failure across 2+
   independent runs means that shape changed in a way the harness
   considers wrong. File it as correctness with **medium confidence**
   and note in the body that manual verification is recommended —
   don't demote it to friction just because user-impact is unverified.
   On the 2026-05-15 release scan, s12 (Save Manifest) failed both
   runs with "expected 2 bottom-row buttons, got 1"; I classified it
   as friction; the user manually verified it was a real "did I lose
   work?" UX bug ([photo-manager#230](https://github.com/jackal998/photo-manager/issues/230))
   that should have been filed in the first pass. Subjective polish
   observations still default to friction; harness-detected
   deterministic anomalies don't.

   Screenshots are optional and usually omitted — see step 4. If a
   finding is visually load-bearing, ask the user to capture it
   manually after the run.

7. **Between scenarios, reset state, not the process.** The server
   stays up; each driver creates its own temp manifest. If a driver
   leaves a modal open or the page wedged, close the page and open a
   fresh one rather than restarting the server — a restart hides the
   state bug that caused it, which is itself a finding.

   If the SERVER wedges (health stops answering), that IS a finding:
   record it, then ask the user before killing the process by PID.
   Never kill by image name — the user may have their own Python
   processes running.
