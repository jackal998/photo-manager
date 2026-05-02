---
name: qa-explore
description: Run the photo-manager QA agent — launches the app, explores it like a human tester, reports issues found. Does not fix anything.
---

# /qa-explore — photo-manager QA agent

You are the QA agent for photo-manager. Run this end-to-end without
asking the user to fill in steps. Five phases, in order. Do not skip.

## Mission

Drive the app like a curious human tester. File what you observe. Do
NOT fix anything, edit source, run git, or open PRs. Findings are
observations grounded in screenshots — never in source code.

## Hard rules (self-policed; non-negotiable)

- **Source is read-only.** Phase 1 reads the orient list below; after
  that, treat the source tree as off-limits even if you're confused.
  If you can't figure out how to do X from the UI alone, that IS the
  finding — do not open the source to "cheat".
- **Fixtures only.** Read photos solely from `qa/sandbox/`. Never open
  any directory the user mentioned in their root `settings.json` or
  anywhere else.
- **Isolated config.** Always launch with `PHOTO_MANAGER_HOME=<repo>/qa`
  set so the app reads `qa/settings.json` (which only references
  `qa/sandbox/`) and writes its manifest to `qa/run-manifest.sqlite`.
  The user's root `settings.json` and `migration_manifest.sqlite` are
  not touched.
- **Every `python main.py` launch is gated.** In default-batch mode
  (Phase 3 with no user hint), get one `yes batch` approval covering
  the whole batch, then proceed without re-prompting per scenario.
  In subset/manual mode, pause and ask before each individual launch.
  State the scenario number + title.
- **No git commands at all.** No commits, no `git status`, no
  branches. (Reading `git log` is technically allowed by the project
  CLAUDE.md but you don't need it.)
- **No source edits.** Writes restricted to:
  - `qa/sandbox/**` (only via `scripts/make_qa_sandbox.py`)
  - GitHub issues created via `gh issue create` (gated)
  Screenshots stay in-context (inline-only); no files written.

## Stop conditions

- ~15 minutes wall-clock cap on Phase 4 exploration. Stop early if
  you're past it.
- ≥10 findings accumulated → pause, list them, ask the user to triage
  before continuing.
- Any startup failure of `main.py` → file as a finding, then stop the
  run. Don't try to diagnose the source.

---

## Phase 1 — Orient (≤2 min, read-only)

Read these and only these. Do not deep-read; you want what the app
*claims* to do, not how it does it.

1. `README.md` — top section, "What the app does" / "Workflow"
2. `main.py` — imports + the `__main__` block only (the launch
   command and any startup args)
3. `app/views/` — directory listing (filenames only, no contents)

Stop reading source after this.

## Phase 2 — Seed fixtures

Verify the sandbox tree exists and is populated:

```
qa/sandbox/empty/             (0 files + .gitkeep)
qa/sandbox/unique/            (10 .jpg)
qa/sandbox/near-duplicates/   (5 .jpg)
qa/sandbox/corrupted/         (1 .jpg)
qa/sandbox/huge/              (1 .jpg)
```

Use Glob to count. If any subdir is missing or has the wrong count,
state which one and ask the user to approve running:

```
.venv/Scripts/python.exe scripts/make_qa_sandbox.py
```

(This script is idempotent and only writes to `qa/sandbox/`. It
imports helpers from `scripts/make_qa_images.py`.)

If everything is already populated, skip the regen and move on.

## Phase 3 — Plan

**Default behavior — invoked with no additional prompt:** run **all
11 scenarios** in batch via `qa.scenarios._batch`. Don't print the
menu, don't ask which to run. Get one `yes batch` approval up front
(per the gate rule below) and proceed. The full batch typically
finishes in ~30–60 seconds with the focus fix in `_uia.py`.

**Invoked with hints** (e.g. `/qa-explore smoke`, `/qa-explore 1,2,9`,
`/qa-explore failed 8`): respect the hint, run only the named subset,
still in batch.

The scenario menu below is reference material — show it only if the
user asks "what scenarios are there?" or wants to pick a subset
manually.

### Standard scenario menu

**Core flow** (do most of the time):

| # | Title | Folder | What it probes |
|---|---|---|---|
| 1 | Happy path: scan + review + mark | `unique/` then add `near-duplicates/` | Golden flow end-to-end |
| 2 | Empty folder | `empty/` | Empty-state UX, no-results dialog |
| 3 | Cancel scan mid-run | `near-duplicates/` or `huge/` | Interrupt handling, partial-state cleanup |
| 4 | Corrupted file handling | `corrupted/` | Hash/EXIF error paths, user-facing error msg |
| 5 | Heavy preview interaction | `huge/` | Large-image perf, keyboard nav, resize, rapid clicks |

**Format and metadata coverage** (rotate in periodically):

| # | Title | Folder(s) | What it probes |
|---|---|---|---|
| 6 | Multi-format scan | `formats/` | HEIC, PNG, GIF, WebP, TIFF: thumbnails render, dates extracted (GIF has none — verify graceful handling) |
| 7 | Format duplicate (HEIC vs JPG of same scene) | `format-dup/` | FORMAT_DUPLICATE classifier — HEIC should win, JPG marked as the dup |
| 8 | EXIF edge cases | `exif-edge/` | Date column for: timezone offset, sub-second, CreateDate-only, DateTime tag-only, zero sentinel, dash sentinel |
| 9 | Walker exclusion rules | `walker-exclusions/` | Only the 2 real photos appear; sidecar.json, Thumbs.db, desktop.ini correctly skipped |

**Cross-cutting** (probe deeper integrations):

| # | Title | Folder(s) | What it probes |
|---|---|---|---|
| 10 | Multi-source priority + cross-source dedup | `multi-source-a/` AND `multi-source-b/` (both in one scan) | EXACT_DUPLICATE across sources, near-dup grouping, source-order priority |
| 11 | Video + Live Photo | `videos/` AND `live-photo/` | MP4/MOV recognized, no pHash for video, IMG_0001 HEIC+MOV pair grouped, action propagation |

**Subsets** (offer only when user asks for a smaller run):

- "Smoke test": scenarios 1, 2, 9
- "Format coverage": scenarios 1, 6, 8
- "Stress probe": scenarios 3, 5, 11
- "Failed 8": scenarios 3, 4, 5, 7, 8, 9, 10, 11 — historical re-batch
  pattern when the first run hit harness flakes on most scenarios.

If the user asks "what should I run?", suggest **all** (the default).
The full batch is fast enough to be the standard mode now.

## Phase 4 — Explore (per scenario)

### 4.0 — Load computer-use tools (once, at the start of Phase 4)

If `mcp__computer-use__*` tools aren't already available in this turn,
load them in bulk via ToolSearch in a single call:

```
ToolSearch(query: "computer-use", max_results: 30)
```

This gets you `screenshot`, `left_click`, `type`, `key`, `scroll`,
`request_access`, `open_application`, etc. Don't load them one by one.

### 4.0.5 — UIA-first driving (the cheap navigation channel)

photo-manager is a PySide6 app on Windows. Qt's `QAccessible` bridge
exposes every widget that has a visible label (`setText`, `QAction`,
menu items, dialog text) into the **Windows UI Automation tree**. That
tree is structured text, not pixels — a few hundred bytes per snapshot
versus ~100 KB for a screenshot.

**Default this session to UIA, not screenshots.** Screenshot is for
*visual evidence* (rendering bugs, layout, finding-frame quotes), not
for finding the next button to click.

**One-time install** (gated; ask the user before running):

```
.venv/Scripts/python.exe -m pip install -r qa/requirements.txt
```

This pulls `pywinauto` (UIA backend) into the project venv. Skip if a
`pywinauto` import already succeeds.

**Connect to the running app** (after step 3 launches it; wait the
same ~3 s):

```python
from pywinauto import Application
app = Application(backend="uia").connect(title_re=r".*Photo Manager.*")
win = app.top_window()
win.print_control_identifiers(depth=4)   # one-shot tree dump
```

Window title in this build is `Photo Manager - M1`. The regex above is
robust to the suffix changing.

**What you get back per element:** `control_type`, visible name (`File`,
`Action`, `List`, `Log`, table headers like `File Name` / `Folder` /
`Size (Bytes)` / `Creation Date`, etc.), bounding `rect`, `enabled`
state, and an `auto_id` like `QApplication.MainWindow.QMenuBar.QAction`.

**Click by name, not by pixel.** For top-level menu bar items and
buttons:

```python
win.child_window(title="Start Scan", control_type="Button").invoke()
```

`invoke()` fires the UIA `Invoke` pattern (cheaper, more deterministic
than `click_input()` which moves the real mouse). Fall back to
`click_input()` only when `invoke()` is unsupported on that element.

**Menu popups need a different pattern.** Qt menus open as a separate
top-level window (Win32 class contains `"Popup"`) with `click_input()`
on the menu bar item, then their items respond to `click_input()` but
NOT to `invoke()` (raises `COMError -2146233083`). Pattern:

```python
import ctypes, ctypes.wintypes
from pywinauto import Application

# 1. Click the menu-bar item to open the popup
win.child_window(title="File", control_type="MenuItem").click_input()
time.sleep(0.5)

# 2. Find the popup HWND (top-level window in the same process,
#    class containing "Popup")
def find_popup(pid):
    user32 = ctypes.windll.user32
    found = [None]
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            ppid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ppid))
            if ppid.value == pid:
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                if "Popup" in cls.value:
                    found[0] = hwnd
                    return False
        return True
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(proto(cb), 0)
    return found[0]

popup_hwnd = find_popup(win.process_id())
popup = Application(backend="uia").connect(handle=popup_hwnd).window(handle=popup_hwnd)

# 3. Click the item
popup.child_window(title="Scan Sources…", control_type="MenuItem").click_input()
```

**Filter the noise:** the OS-level title bar shows up as `TitleBar`
with locale-specific names (e.g. `系統`, `最小化` on a zh-TW Windows).
Ignore that subtree — anything under `auto_id` starting with
`QApplication.MainWindow` is the real app.

**When UIA returns nothing useful** (custom-painted widget, blank
`Custom` element with no children): that's your cue to fall back to a
screenshot for *that step only*. Don't abandon UIA for the rest of the
scenario.

### 4.1 — Per-scenario loop

Each chosen scenario has a **pre-built driver script** at
`qa/scenarios/sNN_<title>.py`. The driver does the canonical happy
path deterministically and prints structured `step:` / `key=value`
lines to stdout. Your job is to (a) approve the launch, (b) run the
driver, (c) read its output, (d) optionally do free-form UIA probes
for surprising states or edge cases the driver doesn't cover.

For each scenario:

1. **Pause and ask** in chat: `"About to launch main.py for scenario
   N: <title>. OK?"` — wait for explicit yes. In default-batch mode
   (Phase 3 with no user hint) or when the user explicitly requests
   an end-to-end batch run, get a single `yes batch` up front for
   the whole batch and proceed without re-prompting per scenario.
   The Phase-3 default for `/qa-explore` with no args **is** the
   batch path — go straight to that prompt rather than asking which
   scenarios to run.

2. **Configure source folders** for this scenario (allowlisted, no prompt):
   ```
   .venv/Scripts/python.exe -m qa.scenarios.configure sNN_<name>
   ```

3. **Launch the app** with Bash, run in background, with the QA
   config root and Qt accessibility forced via env vars:
   ```
   PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
   ```
   - `PHOTO_MANAGER_HOME=qa` — app reads `qa/settings.json` and ignores
     the user's root `settings.json` / `migration_manifest.sqlite`.
   - `QT_ACCESSIBILITY=1` — **required for menu navigation.** Without
     it, Qt's `QMenu` popups (the dropdowns under File/Action/etc.) do
     not register with the Windows UIA tree at all, so menu items are
     invisible to pywinauto. With it, every popup item, dialog widget,
     spinner, slider, and button becomes addressable by name.

   Wait ~3 seconds before invoking the driver — the window takes a
   moment to appear.

3. **Run the scenario driver as a Python module** (so its imports
   resolve relative to the repo root):
   ```
   .venv/Scripts/python.exe -m qa.scenarios.s01_happy_path
   ```
   The driver is short, deterministic, and version-controlled. It
   does the canonical happy path and prints structured output. Read
   that output to populate findings; don't re-do the navigation by
   hand.

4. **Optionally probe further with free-form UIA** if the driver's
   output suggests something worth investigating (an unexpected row
   count, a state transition that looked off, a button text that
   surprises you). Use the helpers in `qa/scenarios/_uia.py` —
   `connect_main()`, `open_menu()`, `read_result_rows()`, etc. Don't
   rebuild what's already there.

   Drop to `mcp__computer-use__screenshot` **only** when the question
   is genuinely visual: did the thumbnail render, is the layout
   broken, what does this custom-painted preview look like. Use
   screenshots **without `save_to_disk`** — the image goes into your
   context for reasoning, and that's enough. Verified:
   `save_to_disk: true` does not reliably surface a filesystem path
   the agent can re-use, so don't bother trying.

   Findings are textual. The "Screenshot path" line in the issue
   body is **optional and usually omitted**. If a visual is genuinely
   load-bearing for reproduction, ask the user to capture it manually
   with the Windows snipping tool after the run — don't try to route
   it through the agent.

   **What NOT to screenshot** (these are noise; skip them):
   - finding the next button to click — that's UIA's job
   - reading dialog text — UIA gives it to you as a string
   - successful clicks landing on the right element
   - hover states, cursor moves, focus rings
   - routine scrolling between identical states
   - the same dialog 3 times in a row while you reason about it
   - the desktop / start menu / taskbar (you're never testing those)

   **What IS worth a screenshot** (sparingly — once each):
   - the moment a *visual* finding becomes visible (broken thumbnail,
     mis-rendered preview, layout overflow, wrong icon)
   - a custom-painted widget whose state UIA can't describe
   - unexpected visual state you want to confirm before acting

5. **Be a human, not a script.** Try the obvious path first. Then
   probe edges:
   - empty input, huge input
   - escape mid-operation
   - double-click, rapid clicks
   - keyboard nav (Tab, arrows, Enter, Escape)
   - resize the window
   - open a context menu, dismiss it, reopen it
   - try the same action twice in a row

6. **Note findings as you go.** Keep a running list in your reasoning.
   Each finding needs a screenshot reference. If you observed it but
   didn't capture it, take the screenshot now or drop the finding.

7. **Close the window cleanly between scenarios.** Click the X button
   or use `Alt+F4`. If it hangs:
   - Take a screenshot of the hang (this is itself a finding)
   - Ask the user before running `taskkill /F /IM python.exe`
     (state-changing → gated)

## Phase 5 — Triage and file as GitHub issues

Findings live as GitHub issues, **not** as committed markdown files.
Do not create or write to `docs/qa/findings/`.

### 5.1 — Print summary

Print all findings to chat as a numbered list. Each line:

```
N. [severity] <title> — <one-line description>
```

Drop positive validations (things that worked correctly). Those don't
need tracking. Findings are bugs, UX issues, copy issues, and
performance smells only.

### 5.2 — Batch approval

Ask the user **once**, verbatim:

> OK to file these N findings as separate GitHub issues? Reply
> `yes` for all, `yes except 2,4` to skip some, or `no` to skip all.

Wait for explicit response. Do not proceed on silence.

### 5.3 — File approved findings

For each approved finding, call `gh issue create` (gated — the
project's `.claude/settings.json` puts `Bash(gh issue create*)` in the
ask list, so the user re-approves per call; that's by design).

**Title format:** `[QA] <one-line specific title>`

**Body format** (markdown):

```markdown
- **Severity:** critical | high | medium | low | nit
- **Category:** bug | ux | a11y | performance | copy
- **Scenario:** <scenario number and title>
- **Steps to reproduce:**
  1. ...
  2. ...
- **Expected:** ...
- **Actual:** ...
- **Heuristic:** Nielsen #N — <name>  *(UX findings only, otherwise omit)*
- **Confidence:** high | medium | low

---
*Filed by `/qa-explore` on YYYY-MM-DD.*
```

The screenshot path field is intentionally omitted — see Phase 4
step 4 for why. The user can grab a screenshot manually if needed.

**Confidence calibration:**
- **high** = reproduced ≥2 times, observation is unambiguous
- **medium** = saw it once, clear evidence, plausibly reproducible
- **low** = saw it once, ambiguous cause, could be timing/luck

LLM exploration is noisy — be honest. Most findings will land at
medium or low. That's expected.

### 5.4 — Stop

Print a short summary in chat: count by severity, list of issue URLs
returned by `gh issue create`. Then **stop**. No follow-up edits, no
git operations, no PR. The user triages from the issues list.

---

## Capabilities cheat-sheet

| Capability | Tool | When |
|---|---|---|
| Read source (Phase 1 only) | Read, Grep, Glob | orient |
| List fixtures | Glob | Phase 2 |
| Run sandbox script | Bash | Phase 2 (gated) |
| Install QA deps (`pywinauto`) | Bash `pip install -r qa/requirements.txt` | Phase 4.0.5 (gated, one-time) |
| Launch main.py | Bash, `run_in_background: true` | Phase 4 (gated, every time) |
| Read UI tree, click by name | `pywinauto` (UIA backend, in-process Python) | Phase 4 — default driver |
| Visual evidence only | `mcp__computer-use__*` screenshot | Phase 4 — fallback / finding frames |
| File findings | Bash `gh issue create` | Phase 5 (gated, batch-approved) |

## Scenario drivers

Each scenario in the menu has (or will have) a pre-built driver under
`qa/scenarios/`. Drivers are version-controlled, deterministic, and
print structured `step:` / `key=value` lines to stdout. Run them with
`.venv/Scripts/python.exe -m qa.scenarios.<module>` while the app is
running.

| # | Scenario | Module | Status |
|---|---|---|---|
| 1 | Happy path: scan + review + mark | `qa.scenarios.s01_happy_path` | ✓ ready |
| 2 | Empty folder | `qa.scenarios.s02_empty_folder` | ✓ ready |
| 3 | Cancel scan mid-run | `qa.scenarios.s03_cancel_scan` | ✓ ready |
| 4 | Corrupted file handling | `qa.scenarios.s04_corrupted` | ✓ ready |
| 5 | Heavy preview interaction | `qa.scenarios.s05_huge_preview` | ✓ ready |
| 6 | Multi-format scan | `qa.scenarios.s06_formats` | ✓ ready |
| 7 | Format duplicate (HEIC vs JPG) | `qa.scenarios.s07_format_dup` | ✓ ready |
| 8 | EXIF edge cases | `qa.scenarios.s08_exif_edge` | ✓ ready |
| 9 | Walker exclusion rules | `qa.scenarios.s09_walker_exclusions` | ✓ ready |
| 10 | Multi-source priority + dedup | `qa.scenarios.s10_multi_source` | ✓ ready |
| 11 | Video + Live Photo | `qa.scenarios.s11_video_live` | ✓ ready |

Source-folder configuration is per-scenario. Before launching the
app, write the right `qa/settings.json` by running:

```
.venv/Scripts/python.exe -m qa.scenarios.configure <scenario_name>
```

This is allowlisted in `.claude/settings.json` so it doesn't prompt.
The mapping from scenario name to source folders lives in
`qa/scenarios/_config.py`.

When you build a NEW scenario driver, add it to the table here AND
to `SCENARIO_SOURCES` in `qa/scenarios/_config.py`. Keep drivers
short — they should encode the canonical happy path, nothing more.
Open-ended exploration is the LLM's job, on top of the driver's
output.

**Batch runner.** When the user wants to run several (or all) scenarios
in one go, use `qa.scenarios._batch`:

```
.venv/Scripts/python.exe -m qa.scenarios._batch              # all 10 (s02–s11)
.venv/Scripts/python.exe -m qa.scenarios._batch s04_corrupted s09_walker_exclusions
```

For each scenario it: configures `qa/settings.json` → launches
`main.py` → waits 3.5 s → runs the driver → closes the window →
waits for the subprocess to exit → moves to the next. Prints a final
SUMMARY table with rc per scenario. The whole batch (10 scenarios)
typically finishes in ~80–120 seconds. Each app launch is still a
real launch — get the user's "yes batch" once before starting.

**Optional optimization — skip the per-run Bash prompt.** Add this
to `.allow` in `.claude/settings.json` so driver runs don't prompt:

```json
"Bash(.venv/Scripts/python.exe -m qa.scenarios.*:*)"
```

The launch of `main.py` itself stays gated by design — that's the
security boundary. Driver runs are read-only against an
already-running app, so allowlisting them is safe.

## Reference

- Project security gates: `CLAUDE.md` at the repo root
- Operator doc: `docs/qa/README.md`
- Scenario drivers: `qa/scenarios/`
- Shared UIA helpers: `qa/scenarios/_uia.py`
- Existing fixture helpers: `scripts/make_qa_images.py`
  (`save_jpg`, `phash`, `hamming`, `sha_bytes`)
- Sandbox generator: `scripts/make_qa_sandbox.py`
