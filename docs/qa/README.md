# photo-manager QA agent

A Claude-driven exploratory tester for the PySide6 desktop app.

## Where this fits

The project's testing strategy has three layers (full detail in
[`../testing.md`](../testing.md)):

| Layer | What | Where it runs |
|---|---|---|
| 1 — Unit + mocks | `pytest` | CI + local |
| 2 — Real binaries (on-demand spot-tests) | `pytest -m integration` | Local only |
| **3 — `/qa-explore`** (this) | full-GUI exploratory testing | Local only; CI possible per [#74](https://github.com/jackal998/photo-manager/issues/74) |

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

`/qa-explore` launches `main.py`, drives the UI via the **Windows UI
Automation tree** (using `pywinauto`), and files findings as GitHub
issues. It is a curious human tester, not a suite of assertions: each
finding is grounded in observed UI state.

The agent reads the live UIA tree (button names, dialog widgets, table
rows) instead of pixel screenshots — a few hundred bytes of structured
text per step instead of ~100 KB of image data. Screenshots are kept as
a fallback for genuine visual checks (broken thumbnail rendering, layout
glitches), not for navigation.

Each scenario has a pre-built **driver script** under
`qa/scenarios/sNN_<title>.py`. The driver does the canonical happy path
deterministically and prints structured `step:` / `key=value` lines to
stdout. The agent reads that output, decides whether to do follow-up
free-form UIA probes, and files findings.

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
dependency (`pywinauto`); subsequent runs skip that step.

## Dependency

QA needs one extra Python package not used by the app at runtime:

```
.venv/Scripts/python.exe -m pip install -r qa/requirements.txt
```

`pywinauto` reads the Windows UIA tree exposed by Qt's `QAccessible`
bridge. The skill prompts for permission before installing.

## Isolation: nothing leaks outside `qa/`

The QA agent always launches `main.py` with two env vars:

```
PHOTO_MANAGER_HOME=qa  QT_ACCESSIBILITY=1  .venv/Scripts/python.exe main.py
```

- `PHOTO_MANAGER_HOME=qa` — app reads `qa/settings.json` (only
  references `qa/sandbox/`), **not** the root `settings.json` that
  points at your real photo folders. Manifest writes go to
  `qa/run-manifest.sqlite` (gitignored), thumbnail cache to
  `qa/.thumb-cache/` (gitignored).
- `QT_ACCESSIBILITY=1` — required for menu navigation. Without it,
  Qt's `QMenu` popups don't register with the UIA tree and the agent
  can't click File / Action / List menu items.

Your real settings, real manifest, and any prior scan state are never
touched. To reset QA state between runs:
`rm qa/run-manifest.sqlite qa/.thumb-cache -r`.

## What you'll be asked

The skill pauses for explicit chat approval at these moments:

| Prompt | When | Why |
|---|---|---|
| "OK to install `pywinauto`?" | Phase 4 setup, only on first run | Pulls a new package into `.venv` |
| "OK to run `make_qa_sandbox.py`?" | Phase 2, only if `qa/sandbox/` is missing or incomplete | Script writes ~2 MB of fixtures under `qa/sandbox/` |
| "About to launch main.py for scenario N: ... OK?" | Phase 4, **once per scenario** | Per project CLAUDE.md, every app launch is gated; if the user explicitly asks for a batch run, a single batch approval covers all launches |
| "OK to file these N findings as GitHub issues?" | Phase 5, batch at the end of the run | Each `gh issue create` is also re-gated per call |

Two commands are **allowlisted** (no prompt):

- `python -m qa.scenarios.configure <scenario>` — writes the right
  `qa/settings.json` for a scenario before launch
- `python -m qa.scenarios.<driver>` — runs a driver against the
  already-running app

Both are read-only against the running process; only the launch itself
crosses the security boundary.

If you say no to any prompt, the skill stops cleanly with no
side-effects beyond what's already happened.

## Expected runtime

With the UIA-first drivers (current architecture):

- Phase 1 (orient): ~10 s
- Phase 2 (fixtures): ~30 s if regen needed, else instant
- Phase 4 (explore): **~10–30 s per scenario** including launch +
  scan + driver. The whole batch (10 scenarios via `_batch.py`) runs
  end-to-end in ~80–120 s.
- Phase 5 (report): ~10–30 s per finding to file as a GitHub issue

The skill ships with 11 standard scenarios. A batch run of all 10
non-s01 scenarios via `python -m qa.scenarios._batch` is realistic;
the user can also pick subsets ("Smoke test" #1/#2/#9 etc.).

## Scenario drivers

| # | Module | Source folder(s) |
|---|---|---|
| 1 | `qa.scenarios.s01_happy_path` | `huge`, `near-duplicates`, `unique` |
| 2 | `qa.scenarios.s02_empty_folder` | `empty` |
| 3 | `qa.scenarios.s03_cancel_scan` | `near-duplicates`, `huge`, `unique` |
| 4 | `qa.scenarios.s04_corrupted` | `corrupted` |
| 5 | `qa.scenarios.s05_huge_preview` | `huge` |
| 6 | `qa.scenarios.s06_formats` | `formats` |
| 7 | `qa.scenarios.s07_format_dup` | `format-dup` |
| 8 | `qa.scenarios.s08_exif_edge` | `exif-edge` |
| 9 | `qa.scenarios.s09_walker_exclusions` | `walker-exclusions` |
| 10 | `qa.scenarios.s10_multi_source` | `multi-source-a`, `multi-source-b` |
| 11 | `qa.scenarios.s11_video_live` | `videos`, `live-photo` |

Source-folder mapping lives in `qa/scenarios/_config.py`. To add a new
scenario:

1. Add an entry to `SCENARIO_SOURCES` in `_config.py`.
2. Write `qa/scenarios/sNN_<title>.py` using helpers from `_uia.py`
   (look at `s04_corrupted.py` for the canonical short template).
3. Add a row to the table above and to the menu in `SKILL.md`.

The shared helpers in `qa/scenarios/_uia.py` cover: connecting to the
main window, opening menus (with the popup-HWND workaround), running a
scan and waiting for `Done.`, reading the result tree, and basic
title-bar dismissal.

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
this environment, and UIA covers ~all navigation needs anyway. If a
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
  `qa/settings.json` (only via `qa.scenarios.configure`), and GitHub
  issues
- Findings live as GitHub issues, **never** as committed files under
  `docs/qa/findings/`
- Hard cap of ~15 min exploration time
- Pause and ask the user if ≥10 findings accumulate before continuing
