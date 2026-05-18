# qa-explore — Phase 4 driving details

This file holds the UIA-first driving channel and the per-
scenario loop — the operational core of running a qa-explore
session. The main [`SKILL.md`](SKILL.md) loads section 4.0
(tools setup) inline and points here for sections 4.0.5 and
4.1.

## Contents

- 4.0.5 — UIA-first driving (the cheap navigation channel)
- 4.1 — Per-scenario loop

---

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

**Native (non-Qt) Windows dialogs are fully locale-translated.**
QFileDialog opens a Windows Common Item Dialog whose control names are
in the OS display language: on zh-TW Windows the filename ComboBox is
`檔案名稱:`, not `File name:`. **Don't hard-code English titles for
controls in native dialogs.** Use locale-independent discovery — for
the Save dialog filename Edit, find "the only `ComboBox` descendant
that contains an `Edit`" (the `Save as type:` ComboBox has no editable
Edit, so this picks the right one regardless of locale). Qt-managed
widgets (everything inside the main window, scan dialog, message
boxes) keep their English names because those come from
photo-manager's source — locale-translation only hits the OS dialogs.

**Hosted CI uses Qt's non-native QFileDialog ([#129](https://github.com/jackal998/photo-manager/issues/129)).**
The qa-batch workflow sets `PHOTO_MANAGER_QT_FILE_DIALOG=1`, which
makes `main.py` apply `Qt.AA_DontUseNativeDialogs` before constructing
`QApplication` so every `QFileDialog` becomes Qt's widget-based dialog.
Qt's dialog responds to UIA normally; the native dialog's COM modal
loop on hosted runners silently drops synthesized input. The
`_find_filename_edit` and `_find_native_dialog_action_button` helpers
in `_uia.py` carry parallel branches for both tree shapes — native
(`ComboBox > Edit` + 2nd-from-rightmost bottom-row button) and Qt
(standalone `QLineEdit` + topmost button inside `QDialogButtonBox`) —
so new file-dialog scenarios inherit dual support automatically.
Local users get the native dialog as before; the env var only flips
under qa-batch. The same flip will unblock macOS `NSSavePanel` on
future hosted macOS CI — one switch, every platform.

**Setting Edit values: prefer UIA `ValuePattern.SetValue` over typing.**
Two reasons:
1. **IMEs intercept keystrokes.** `pywinauto.keyboard.send_keys("hello")`
   on a system with a phonetic IME active (bopomofo, pinyin, hangul,
   kana) gets eaten by the IME and produces phonetic glyphs instead
   of the Latin string. Modifier-key combos (Ctrl+A/Ctrl+V/Enter)
   bypass IME, but free text doesn't. The user's session may have
   any IME active — your driver can't assume Latin keystrokes land.
2. **Focus is fragile.** Typing requires the right widget to have
   focus *at the moment of the keystroke*. Native dialogs steal
   focus, popups steal focus, taskbar tooltips steal focus.

`ValuePattern.SetValue` is a UIA-level write that bypasses keyboard,
focus, and IME entirely:

```python
filename_edit.iface_value.SetValue(str(target_path))
```

Use it whenever you need to set an Edit's content. Reserve `send_keys`
for keystrokes the application interprets *as keystrokes* (Enter to
confirm, Esc to cancel, Ctrl+S, arrow keys for navigation). The
existing `qa/scenarios/_uia.save_manifest_via_native_dialog` is the
reference pattern — copy from it.

**Foreground-lock pitfall — prefer existing `_uia` helpers over inline
clicks.** Windows enforces a foreground-lock heuristic: when a
background process (the batch runner, your `Bash` invocation) calls
`SetForegroundWindow` while another window owns foreground, Windows
*silently no-ops* the call. The change is asynchronous, so a naive
"call `set_focus()`, sleep 50 ms, click" sequence sometimes fires the
click before the photo-manager window is actually foreground — the
click lands on the terminal/IDE and the photo-manager click is lost.
The symptom moves with whichever click fluked: "menu popup didn't
appear", "dialog didn't appear within Ns", "row not selected", etc.

The shared helpers in `qa/scenarios/_uia.py` already handle this:
`_focus()` polls `GetForegroundWindow()` until the target HWND
matches (re-issuing `set_focus()` every 200 ms), and the
click-then-wait helpers (`open_menu`, `right_click_tree_row`,
`mark_all_via_regex`, `execute_and_confirm`,
`_click_btn_and_wait_for_dialog`) verify the expected popup/dialog
actually appeared and retry on miss. **Use them.** Reach for inline
`pywinauto` only for read-only probes (`descendants`, `window_text`,
`is_enabled`, `rectangle`) — those are observation, not state change,
and don't suffer the race. Any inline click that expects a popup or
dialog should be wrapped in the same verify-and-retry shape; if you
find yourself writing one, lift it into a helper instead.

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

   Wait ~2 seconds before invoking the driver — the window takes a
   moment to appear. (The batch runner uses a ctypes EnumWindows poll
   instead of a fixed sleep; for one-off manual launches, a brief
   sleep is fine.)

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

7. **Close the window cleanly between scenarios.** Click the X button
   or use `Alt+F4`. If it hangs:
   - Take a screenshot of the hang (this is itself a finding)
   - Ask the user before running `taskkill /F /IM python.exe`
     (state-changing → gated)

