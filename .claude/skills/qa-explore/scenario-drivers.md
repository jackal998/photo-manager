# qa-explore — scenario driver authoring

This file holds the QA scenario authoring conventions —
what a `qa/scenarios/sNN_*.py` driver looks like, how to
structure it, how to name slots. The main [`SKILL.md`](SKILL.md)
points here when Claude is extending or adding a scenario.

---

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
| 12 | Save Manifest Decisions | `qa.scenarios.s12_save_manifest` | ✓ ready |
| 13 | Execute Action (destructive — sends to recycle bin) | `qa.scenarios.s13_execute_action` | ✓ ready |
| 14 | Set Action by Field/Regex (menu-bar path) | `qa.scenarios.s14_action_by_regex` | ✓ ready |
| 15 | Right-click context menu Set Action → delete / keep | `qa.scenarios.s15_context_menu` | ✓ ready |
| 16 | File → Open Manifest async load (happy + error path) | `qa.scenarios.s16_open_manifest` | ✓ ready |
| 17 | Scan dialog widgets (add / remove / reorder / recursive) | `qa.scenarios.s17_scan_dialog_widgets` | ✓ ready |
| 18 | Log menu (Open Latest Log / Delete Log / Log Dir / Delete Log Dir) | `qa.scenarios.s18_log_menu` | ✓ ready |
| 19 | Right-click context menu → Open Folder (explorer.exe /select integration) | `qa.scenarios.s19_context_menu_open_folder` | ✓ ready |
| 20 | Right-click multi-selection → Remove from List (file-multi / group + file) | `qa.scenarios.s20_multi_remove_from_list` | ✓ ready |
| 21 | List menu → Remove from List (no-selection / single / multi) | `qa.scenarios.s21_list_menu_remove` | ✓ ready |
| 23a | Scan dialog: GUI mutates settings, persists via Start Scan (#122) | `qa.scenarios.s23a_set_settings` | ✓ ready |
| 23b | Scan dialog: fresh launch reloads what s23a wrote (#122) | `qa.scenarios.s23b_verify_settings` | ✓ ready |
| 24 | Open manifest whose source files were deleted after scan (stale paths, #123) | `qa.scenarios.s24_stale_manifest_paths` | ✓ ready |
| 25 | Right-click on empty area / menu bar / unselected row → no Qt popup (#124) | `qa.scenarios.s25_empty_area_context_menu` | ✓ ready |
| 26 | Keyboard-only navigation: tree arrows, Alt+F mnemonic, scan dialog Tab cycle, Esc (#125) | `qa.scenarios.s26_keyboard_navigation` | ✓ ready |
| 27 | Re-scan with pending decisions → confirmation prompt (#142) | `qa.scenarios.s27_rescan_confirm` | ✓ ready |

The table above lists the original menu (s01–s27). The full set has
grown well past it — see
[`qa.scenarios._batch.ALL_SCENARIOS`](../../../qa/scenarios/_batch.py)
or `Glob("qa/scenarios/s*.py")` for the canonical, always-current
list. Don't trust this table for completeness; trust the directory.

Several drivers also call cross-scenario invariant probes from
`qa/scenarios/_invariants.py` — they assert that the status bar matches
an expected shape after a manifest-changing action, that all
manifest-gated menu items toggle as one set, and that destructive
confirmation prompts have Yes/No buttons + a count in the body. Those
probes print `inv: <name> ok=<bool> ...` lines to stdout. Failures
escalate to the driver's existing FAIL/return-1 path.

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

If your driver needs a NEW shared helper that issues a click and
expects a window to appear afterwards (popup, dialog, context menu),
mirror the verify-and-retry shape of `_click_btn_and_wait_for_dialog`
or `right_click_tree_row` — fire the click, check the expected
window appeared within a short per-attempt timeout, and retry up to
3× on miss. "Click and assume" is a known flake source on Windows
(see the foreground-lock note in Phase 4.0.5); treat the
verify-and-retry pattern as a hard requirement for new helpers, not
a stylistic choice.

**Cleanup convention — drivers that spawn external shell windows
(Notepad, Explorer, etc. via `os.startfile`, `explorer.exe`,
`QDesktopServices::openUrl`) MUST clean them up before returning.**
Otherwise each batch run leaves windows piled up on the operator's
desktop. The pattern, used by s18 and s19:

```python
baseline = _uia.list_top_level_windows(_uia.DEFAULT_SHELL_CLASSES)
# … perform the click …
time.sleep(1.0)
closed = _uia.close_new_shell_windows(baseline)
print(f"  closed_shell_windows={[(c, t) for _h, c, t in closed]!r}")
```

`close_new_shell_windows` sends `WM_CLOSE` (NEVER `taskkill` on
explorer.exe — that nukes the user's whole shell). The default class
allowlist (`DEFAULT_SHELL_CLASSES = ("CabinetWClass", "Notepad",
"Notepad++")`) covers the windows we know how to close safely; if a
user has a different default text editor (VSCode, Sublime), those
windows leak — document the residual in the driver header.

**Batch runner.** When the user wants to run several (or all) scenarios
in one go, use `qa.scenarios._batch`:

```
.venv/Scripts/python.exe -m qa.scenarios._batch              # full sweep
.venv/Scripts/python.exe -m qa.scenarios._batch s04_corrupted s09_walker_exclusions
```

For each scenario it: configures `qa/settings.json` → launches
`main.py` → polls (ctypes `EnumWindows`) until the main window is
visible (max 8 s; typically <2 s) → runs the driver → closes the
window → waits for the subprocess to exit → moves to the next.
Prints a final SUMMARY table with rc per scenario. The full batch
(52 scenarios as of 2026-05-19 — see
[`qa.scenarios._batch.ALL_SCENARIOS`](../../../qa/scenarios/_batch.py)
for the canonical list) typically finishes in a few minutes
wall-clock locally and ~5 minutes per shard on CI's 5-way matrix.
Each app launch is still a real launch — get the user's "yes batch"
once before starting.

**Optional optimization — skip the per-run Bash prompt.** Add this
to `.allow` in `.claude/settings.json` so driver runs don't prompt:

```json
"Bash(.venv/Scripts/python.exe -m qa.scenarios.*:*)"
```

The launch of `main.py` itself stays gated by design — that's the
security boundary. Driver runs are read-only against an
already-running app, so allowlisting them is safe.

