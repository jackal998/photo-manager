# Authoring qa-explore scenario drivers

Read this before adding a new `sNN_*.py` driver. It's the
distilled-down version of what cost us 9 CI iterations on PR
[#128](https://github.com/jackal998/photo-manager/pull/128) — every
landmine here has a real moment in commit history.

The drivers exist to catch UI-side drift the unit tests can't (label
text, state transitions, dialog dismissal). Each one is short and
canonical: one happy path, one or two negative branches, structured
`step:` / `key=value` stdout for the LLM agent to read.

---

## Before you start

1. **Read 2–3 nearby drivers**, not all of them. Pick whichever is
   structurally closest to what you're building (menu invocation? scan
   + load + assert? right-click context menu?). The patterns are stable
   across drivers; copy idioms rather than reinventing.
2. **Read `qa/scenarios/_uia.py`** for available helpers. If a helper
   matching your need already exists, use it. If you're tempted to add
   a new helper, read the existing ones near it first — chances are
   one of them is one parameter away from what you want.
3. **Look up the GitHub issue body** for the scenario you're building
   (`gh issue view N`). The issue authors have usually pre-thought the
   shape and named the helpers needed.
4. **Don't touch `app/` source unless the issue explicitly says so.**
   QA scenarios test what's there. If you find a bug, file it; don't
   fold an app-side fix into a scenario PR (s21/#118 is the rare
   exception — it caught a bug whose fix made the test green).

## Use the helpers

These are the patterns we converged on the hard way. Reach for these
before rolling your own.

| Need | Helper | Why |
|---|---|---|
| Open File/Action/List/Log menu, click an item | `menu_path(win, MENU_X, ITEM)` | Handles the Qt-popup-as-separate-toplevel-window plumbing. |
| Click a dialog button by name | `_find_dialog_button(dlg, title)` | Picks bottom-most match. **Don't** `dlg.child_window(title=..., control_type="Button")` for `"Close"` / `"Cancel"` / `"OK"` — title-bar buttons collide on en-US Windows and `__resolve_control` times out. |
| Click a button in a *native* OS dialog | `_find_native_dialog_action_button(dlg)` | 2nd-from-rightmost in the bottom row. Locale-tolerant ("Save" / "Open" / "存檔" / "存档" all work). |
| Find the Save/Open dialog's filename Edit | `_find_filename_edit(dlg)` | Locale-independent (only ComboBox with an editable Edit child). |
| Set a path or text into an Edit | `edit.iface_value.SetValue(s)` | Bypasses keyboard, focus, and IME (zh-TW bopomofo eats Latin keystrokes; this avoids it). **Don't** use `send_keys` for free text. |
| Click a row in the result tree | `left_click_tree_row` / `ctrl_click_tree_row` / `right_click_tree_row` | Locates the row by basename, scopes the search to the result tree's own descendants (post-#98 fix). |
| Scan + close-and-load to set up a manifest | `open_scan_dialog` → `run_scan_and_wait` → `close_and_load_manifest` | Standard preamble. |
| Wait for a status-bar message | `_invariants.assert_status_bar_matches(win, regex)` | Polls within a window, prints `inv:` line. |
| Wait for a non-Photo-Manager dialog (Notepad, Explorer, etc.) | `wait_for_dialog(pid, title, timeout=N)` | Polls top-level windows. |
| Read manifest user_decision after a mutation | `sqlite3.connect(MANIFEST_PATH)` + `SELECT source_path, user_decision FROM migration_manifest` | s15 / s20 / s21 are the templates. |

If you add a new scenario-runtime helper, put it in `_uia.py` (not in
the scenario file) so the next driver can reuse it.

## No-go patterns

Each of these cost an iteration cycle. Avoid them in new drivers.

### ❌ Hardcoded `.venv/Scripts/python.exe`

```python
PY = str(REPO / ".venv" / "Scripts" / "python.exe")  # breaks on CI
```

CI runners don't have a `.venv/`. Use `sys.executable` everywhere
that needs to spawn another Python (matches the venv locally, the
runner's Python on CI, conda / pyenv-win wherever else).

### ❌ `send_keys("{ENTER}")` to activate a default button

```python
filename_edit.iface_value.SetValue(path)
send_keys("{ENTER}")  # global to whatever Windows says is foreground
```

`send_keys` is system-global — it doesn't target a window. Foreground
drifts on CI (and during slow operations locally). The Enter routinely
misses the dialog and goes to the main window or a stray popup,
silently no-opping.

We hit this **twice**: s17's phantom Start Scan (`setDefault(True)`
swallowed Enter from the path field) and s12's save dialog. The fix
both times was the same:

```python
btn = _find_dialog_button(dlg, "Apply")        # or _find_native_dialog_action_button
btn.click_input()                              # or btn.invoke() for Qt buttons
```

`click_input` targets a specific window, so foreground drift can't
redirect it.

### ❌ `child_window(title="Close")` (or any name that collides with OS chrome)

```python
btn = dlg.child_window(title="Close", control_type="Button")  # ambiguous on en-US
```

zh-TW renders the title-bar Close as `"關閉"` so locally there's only
one match and this happens to work. en-US Windows uses `"Close"` for
both the title-bar and dialog button → `__resolve_control` times out.
Same shape applies to `"Cancel"`, `"OK"`, `"Yes"`, `"No"`.

```python
btn = _find_dialog_button(dlg, "Close")  # bottom-most → form button
```

### ❌ Polling on dialog-close as a success signal

```python
while not dialog_gone(): time.sleep(0.2)  # returns before the file exists
```

Qt and OS dialogs frequently complete their work *after* the dialog
closes — the save handler writes the manifest *after*
`getSaveFileName` returns. Polling the close signal returns before
the user-visible outcome lands.

```python
while not Path(target).exists(): time.sleep(0.2)  # polls actual outcome
```

Use the artefact, the status bar regex, or a manifest-table query —
something the user themselves would observe.

### ❌ `SendMessage` to anything in a modal loop you don't control

```python
ctypes.windll.user32.SendMessageW(btn_hwnd, BM_CLICK, 0, 0)  # synchronous
```

Synchronous. Blocks until the receiver pumps. The native Windows
`IFileSaveDialog` modal loop only pumps COM messages; our SendMessage
sat queued forever and the driver hung for the full subprocess
timeout. Use `PostMessageW` if you must, but see the next item — for
native Common Item Dialogs it doesn't help anyway.

### ❌ Driving native Windows Common Item Dialog on CI

`QFileDialog.getSaveFileName` / `getOpenFileName` open a Windows
`IFileSaveDialog` / `IFileOpenDialog` whose modal loop only pumps COM
messages, AND the GitHub-hosted runner doesn't deliver synthesized
mouse / keyboard input. `PostMessage(BM_CLICK)`, `PostMessage(WM_KEYDOWN,
VK_RETURN)`, UIA `Invoke`, and `click_input` all return success but
the dialog never closes. See [#129](https://github.com/jackal998/photo-manager/issues/129)
for the full diagnostic.

If your scenario must drive a native dialog: locally it'll work; on
CI it won't. Document the asymmetry in the helper docstring + in
`docs/testing.md`'s "Known CI limitations" section. Don't try to
defeat the platform.

### ❌ Assuming `setCellWidget`'d Qt controls show up in UIA

QTableWidget's `setCellWidget(row, col, widget)` doesn't surface the
widget in the UIA accessibility tree — neither under the table nor
under the dialog. s17 hit this on the source-list ↑/↓/× buttons and
the Recursive checkbox.

Fall back to clicking inside the parent `DataItem.rectangle()` by
pixel coords. State of the embedded control (e.g. "is the checkbox
checked?") is unreadable through UIA — verify behaviorally instead
(does a subsequent scan use the right depth?).

### ❌ Skipping the local pre-check before push

CI iterations are 5–7 minutes. Local iterations are seconds. Always
run the affected scenario(s) locally before pushing — the
fast-path:

```
python -m qa.scenarios._batch sNN_xyz [sMM_abc ...]
```

For driver-helper changes that affect multiple scenarios, run each
of the touched ones individually first to confirm no regression.

## Local + CI parity is non-negotiable

Local Windows is the primary entry point; CI is supplementary. Every
change to `_uia.py` must keep local behaviour identical (or better).
Common traps:

- **Assuming `_focus()` is a no-op locally.** Post-#126 it polls until
  `GetForegroundWindow()` matches, with up to 1.5s timeout. If the
  dialog is already foreground it returns immediately, BUT calling
  `_focus()` at the wrong moment (e.g. between `SetValue` and a
  follow-up action) can perturb state. If you add a `_focus()` and
  it breaks local, suspect the timing rather than the call itself.
- **Adding a sleep that masks a real bug.** Bumping `time.sleep(0.2)`
  to `0.8s` because "CI is slower" is sometimes correct, but if a 4×
  bump fixes it, the underlying assumption was probably wrong (e.g.
  polling the wrong success signal). Verify by stress-testing locally.
- **Locale-specific success.** zh-TW desktop and en-US runner aren't
  the same environment. If your assertion compares to an English
  string, consider whether the OS dialog might show it translated.

## Debugging a scenario on CI

When a scenario fails on CI but passes locally:

1. **Add diagnostics before trying fixes.** Each CI iteration is 5–7
   minutes; spending 30s adding a `print(f"  picked_btn={...}")` makes
   the next iteration informative rather than another guess. The
   iteration 6 → 9 cycles converged because each one printed concrete
   data we couldn't see locally.
2. **Don't blame the most recent change reflexively.** Iteration 4
   regressed local s12 and I pinned it on a `_focus()` call that was
   actually fine — the real bug was an unrelated polling change. When
   something regresses, revert changes one at a time and rerun.
3. **Check `subprocess.TimeoutExpired.stdout` / `.stderr`.** The batch
   runner now surfaces these on hangs. If you're working with
   subprocess-spawned drivers elsewhere, do the same.

## Pre-existing flakes (not your fault)

- **Inter-scenario File-menu-popup miss** ([#105](https://github.com/jackal998/photo-manager/issues/105) /
  [#107](https://github.com/jackal998/photo-manager/issues/107)).
  Sometimes the next scenario's `menu_path(win, MENU_FILE, ...)` can't
  open the popup because the previous teardown didn't fully release
  foreground. Retry once before declaring a real failure.
- **`s12_save_manifest` on CI** ([#129](https://github.com/jackal998/photo-manager/issues/129)).
  Known-blocked by IFileSaveDialog limitation; locally green. Not a
  regression.

## When you commit

Commit message for a new scenario should:
- Cite the issue number (`Closes #N`)
- Describe what the scenario probes (which app code path)
- Note what it's *distinct from* (e.g. s20 vs s15 vs s21 all touch
  remove-from-list but via different handlers)
- Mention any new helper added in `_uia.py`

PR description should include:
- A test plan with the targeted-scenario command
- A note if any pre-existing flake bit during testing (set
  expectation, don't pretend it didn't happen)

That's it. The cookbook here is short because the helper inventory
in `_uia.py` is well-developed — most new scenarios are 100–200 lines
that wire existing helpers together.
