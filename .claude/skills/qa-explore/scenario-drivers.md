# qa-explore — scenario driver authoring

This file holds the QA scenario authoring conventions —
what a `qa/scenarios/sNN_*.py` driver looks like, how to
structure it, how to name slots. The main [`SKILL.md`](SKILL.md)
points here when Claude is extending or adding a scenario.

---

## Scenario drivers

Each scenario has a pre-built driver under `qa/scenarios/`. Drivers
are version-controlled, deterministic, and print structured `step:` /
`key=value` lines to stdout. Run a single driver with
`.venv/Scripts/python.exe -m qa.scenarios.<module>` while the app is
running.

For the canonical, always-current list of scenarios see
[`qa.scenarios._batch.ALL_SCENARIOS`](../../../qa/scenarios/_batch.py)
or `Glob("qa/scenarios/s*.py")`. The directory is the source of
truth — this doc no longer enumerates scenarios inline, because
the table drifted twice and the maintenance cost outweighed the
value (#323). Slot numbers go `sNN_<short_slug>.py`; slots are
append-only (gaps from retired scenarios stay gaps so re-numbering
doesn't churn git history and external issue references). Each
driver's module docstring describes what it covers.

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

When you build a NEW scenario driver, add it to `ALL_SCENARIOS` in
`qa/scenarios/_batch.py` (the canonical list used by the batch
runner and CI) AND to `SCENARIO_SOURCES` in `qa/scenarios/_config.py`
(folder mapping). Keep drivers short — they should encode the
canonical happy path, nothing more. Open-ended exploration is the
LLM's job, on top of the driver's output.

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

