# photo-manager QA agent

A Claude-driven exploratory tester for the web client.

## Where this fits

The project's testing strategy has three layers (full detail in
[`../testing.md`](../testing.md)):

| Layer | What | Where it runs |
|---|---|---|
| 1 — Unit + mocks | `pytest` | CI + local |
| 2 — Real binaries (on-demand spot-tests) | `pytest -m integration` | Local only |
| **3 — `/qa-explore`** (this) | full-app exploratory testing | Local, plus the `web-scenario-batch` CI job on every web-touching PR |

Layer 1 verifies our parsers, dispatch, and pure logic against mocked
boundaries. Layer 2 is on-demand — added reactively as spot-tests when
a specific boundary bug surfaces, not maintained as a proactive suite.
**Layer 3 is the primary safety net for boundaries and user flows
combined**: it exercises what a user actually does — clicking menus,
reading dialog text, watching state transitions — AND drives the real
`exiftool` / `send2trash` / `rawpy` boundaries on happy paths via real
fixtures. Bugs that only surface here (label drift, dead buttons,
status bar regressions, dialog dismissal weirdness) are exactly what
`pytest` cannot catch by construction.

## What it is

`/qa-explore` runs the app's FastAPI server, drives the React client in
a real Chromium browser with **Playwright**, and files findings as
GitHub issues. It is a curious human tester, not a suite of assertions:
each finding is grounded in observed UI state.

The agent reads the live DOM (element text, `data-testid` attributes,
computed geometry) instead of pixel screenshots — a locator query is a
few hundred bytes of structured text per step instead of ~100 KB of
image data. Screenshots are kept as a fallback for genuine visual checks
(broken thumbnail rendering, layout glitches), not for navigation.

Each scenario has a pre-built **driver script** under
`qa/web/scenarios/sNN_<title>.py`. The driver does the canonical happy
path deterministically, asserts the load-bearing outcomes, and prints
structured `step:` / `key=value` lines to stdout. The agent reads that
output, decides whether to do follow-up free-form DOM probes, and files
findings.

## What it isn't

- **Not a fixer.** It never edits source files, runs migrations, or
  opens PRs. Findings are observations; you triage and act.
- **Not a perf benchmark.** Wall-clock numbers (e.g. scan elapsed) are
  reported descriptively, not asserted.
- **Not a regression suite.** Use `pytest` for that. The QA agent
  catches things tests don't: layout breakage, confusing copy, dead
  buttons, dialog dismissal weirdness, classifier-output sanity.

## How to invoke

In a Claude Code session at the project root:

```
/qa-explore
```

That's it. The skill walks five phases (orient → seed fixtures → plan
→ explore → triage). On first run it asks to install the QA-only
dependency (Playwright + its Chromium binary); subsequent runs skip
that step.

## Dependency

QA needs Playwright, which the app does not use at runtime. Full
instructions in [`../../qa/web/INSTALL.md`](../../qa/web/INSTALL.md):

```
.venv/Scripts/python.exe -m pip install playwright
.venv/Scripts/playwright.exe install chromium
```

The skill prompts for permission before installing either.

## Isolation: nothing leaks outside `qa/`

The server the drivers talk to is started with the QA config root:

```
PHOTO_MANAGER_HOME=qa .venv/Scripts/python.exe -m app.web.main
```

`PHOTO_MANAGER_HOME=qa` makes the app read `qa/settings.json` (which
only references `qa/sandbox/`), **not** the root `settings.json` that
points at your real photo folders. Manifest writes go to
`qa/run-manifest.sqlite` (gitignored), thumbnail cache to
`qa/.thumb-cache/` (gitignored).

Playwright drives its own Chromium profile, so the run never touches
the browser you use. Your real settings, real manifest, and any prior
scan state are never touched either. To reset QA state between runs:
`rm qa/run-manifest.sqlite qa/.thumb-cache -r`.

## What you'll be asked

The skill pauses for explicit chat approval at these moments:

| Prompt | When | Why |
|---|---|---|
| "OK to install Playwright + Chromium?" | Phase 4 setup, only on first run | Pulls a package into `.venv` and downloads a browser |
| "OK to run `make_qa_sandbox.py`?" | Phase 2, only if `qa/sandbox/` is missing or incomplete | Script writes ~2 MB of fixtures under `qa/sandbox/` |
| "About to launch the app for scenario N: ... OK?" | Phase 4, **once per scenario** | Per project CLAUDE.md, every app launch is gated; if the user explicitly asks for a batch run, a single batch approval covers all launches |
| "OK to file these N findings as GitHub issues?" | Phase 5, batch at the end of the run | Each `gh issue create` is also re-gated per call |

Running a driver against an already-started server is **allowlisted**
(no prompt):

- `python -m qa.web._batch <scenario>` — runs one driver
- `python -m qa.web._batch` — runs the whole batch

Drivers are read-only against the running process except where a
scenario is marked destructive; only the launch itself crosses the
security boundary.

If you say no to any prompt, the skill stops cleanly with no
side-effects beyond what's already happened.

## Expected runtime

- Phase 1 (orient): ~10 s
- Phase 2 (fixtures): ~30 s if regen needed, else instant
- Phase 4 (explore): **~10–30 s per scenario** — the server boots once
  and every driver reuses it, so per-scenario cost is browser + scan.
- Phase 5 (report): ~10–30 s per finding to file as a GitHub issue

The canonical scenario list is `qa.scenario_ids.ALL_SCENARIOS` (71 ids
as of the Phase-4 cutover). The whole batch runs via
`python -m qa.web._batch`; the user can also pick subsets by naming
ids on the command line.

## Scenario drivers

The list is not duplicated here — it drifted twice and the maintenance
cost outweighed the value (#323). The two registries ARE the list:

- [`qa/scenario_ids.py`](../../qa/scenario_ids.py) — `ALL_SCENARIOS`,
  the ordered ids, each with a comment saying what it covers;
- [`qa/web/scenario_map.yml`](../../qa/web/scenario_map.yml) — id →
  driver module, `status`, and a `notes` field recording what the
  scenario asserts and what it deliberately omits.

`Glob("qa/web/scenarios/s*.py")` gives the files. Slot numbers go
`sNN_<short_slug>.py` and are append-only: gaps from retired scenarios
stay gaps so re-numbering doesn't churn git history or break external
issue references.

To add a new scenario:

1. Add the id to `ALL_SCENARIOS` in `qa/scenario_ids.py`.
2. Write `qa/web/scenarios/sNN_<title>.py` — copy the nearest existing
   driver; they share a fixed shape (`run(page, base_url)`, a
   `failures: list[str]`, testids from `qa/web/testid_constants.py`).
3. Add the row to `qa/web/scenario_map.yml` with `status: done` and a
   `notes` field.

`tests/test_all_scenarios_registered.py` fails if those three drift
apart. The shared helpers in `qa/web/_invariants.py` cover the
cross-scenario assertions (status-bar shape, manifest-gated menu
consistency, destructive-confirm shape) — reach for them instead of
duplicating asserts.

## Output

Findings are filed as **GitHub issues**, not committed to git. Each
issue is titled `[QA] <one-line title>` and contains:

- Severity, Category, Scenario
- Steps to reproduce
- Expected vs Actual
- Heuristic (Nielsen # for UX findings)
- Confidence (high / medium / low)
- Footer: `Filed by /qa-explore on YYYY-MM-DD`

Screenshots are intentionally **not** attached or referenced — the
`computer-use` `save_to_disk` flag doesn't surface a stable path in
this environment, and DOM locators cover ~all navigation needs anyway. If a
specific finding benefits from a visual, grab one manually with the
Windows snipping tool.

### Approval flow at the end of a run

The agent prints all findings as a numbered list, then asks once:

> OK to file these N findings as separate GitHub issues? Reply
> `yes` for all, `yes except 2,4` to skip some, or `no` to skip all.

Each `gh issue create` call is also gated individually by the
project's `.claude/settings.json` (so you re-confirm per filing).
Belt and suspenders.

The agent **does not** open PRs, edit source, or close issues —
those are triage decisions you make from the issues list.

## Triage tips

- Start with **high+ severity AND high confidence** issues — those
  are most likely real bugs worth fixing now.
- **Low confidence** issues often turn out to be timing artifacts of
  the LLM-driven exploration loop. Reproduce manually before
  prioritizing; close as "could not reproduce" if it doesn't recur.
- **UX / copy issues** rarely block work but are the easiest wins
  — batch them between feature work.
- **a11y issues** without a WCAG citation are weaker; treat them as
  prompts to do a real keyboard-only walkthrough.

## Fixture set

Generated by `scripts/make_qa_sandbox.py` (idempotent; pass `--force`
to regenerate). Output lives under `qa/sandbox/` and is committed.
Total disk usage ~2.2 MB; the QA agent never reads photos outside
this tree.

### Core scenarios

| Subdir | Files | Purpose |
|---|---|---|
| `empty/` | 0 (+`.gitkeep`) | Empty-state UX |
| `unique/` | 10 distinct JPEGs | Happy-path scan |
| `near-duplicates/` | 5 JPEGs (one base, 5 quality levels) | Duplicate group review |
| `corrupted/` | 1 truncated JPEG | Hash/EXIF error path |
| `huge/` | 1 ~50 MP JPEG | Large-image perf and preview |

### Format and metadata coverage

| Subdir | Files | Probes |
|---|---|---|
| `formats/` | heic, png, gif, webp, tiff | Per-format pHash, EXIF read paths, GIF (no EXIF) |
| `exif-edge/` | 6 JPEGs | Timezone offset, sub-second, CreateDate fallback, DateTime tag fallback, zero-date sentinel, dash sentinel |
| `format-dup/` | jpg + heic of same scene | FORMAT_DUPLICATE classifier (HEIC preferred over JPEG) |
| `multi-source-a/`, `multi-source-b/` | 2 + 3 JPEGs (1 byte-shared, 1 near-dup, 1 unique) | Cross-source priority, EXACT_DUPLICATE across sources |
| `walker-exclusions/` | 2 JPEGs + sidecar.json + Thumbs.db + desktop.ini | Walker skip rules |

### Video and Live Photo

| Subdir | Files | Probes |
|---|---|---|
| `videos/` | dummy.mp4, dummy.mov (20 bytes each — `ftyp` box only) | Video extension routing, SHA-256 path, undated-video edge case |
| `live-photo/` | IMG_0001.HEIC + IMG_0001.MOV pair | Walker pairing logic, dedup action propagation across pair |

**Video caveat — known limitation.** The `videos/` and `live-photo/`
fixtures contain only a minimal `ftyp` box (no actual video stream).
This is enough to exercise the walker, hasher (SHA-256 only — pHash
is correctly skipped for videos), and exiftool's date-fallback chain
(returns None → UNDATED), but **does not test video preview or
playback** in the UI. Synthesizing real decodable video would require
adding ffmpeg as a dependency (~50 MB), which we've intentionally
avoided. If you want preview/playback coverage, drop a small
license-clean MP4 into `videos/` manually before a run.

## Constraints baked into the skill

(See `.claude/skills/qa-explore/SKILL.md` for the full text.)

- Source code is read-only and only during Phase 1 orientation
- No git operations of any kind from inside `/qa-explore`
- Writes restricted to `qa/sandbox/**` (only via `make_qa_sandbox.py`),
  `qa/settings.json` (written by the driver setup), and GitHub
  issues
- Findings live as GitHub issues, **never** as committed files under
  `docs/qa/findings/`
- Hard cap of ~15 min exploration time
- Pause and ask the user if ≥10 findings accumulate before continuing
