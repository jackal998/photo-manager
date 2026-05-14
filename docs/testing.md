# Testing strategy

Three layers, each catching a different class of bugs. CI covers exactly
one of them; the other two run locally. This document is the canonical
answer to "what's covered, what's not, why, and what's the residual
risk."

---

## The three layers

| Layer | What it catches | Where it runs | Status |
|---|---|---|---|
| **1 — Unit** (mocks + pure logic) | Refactoring bugs, contract violations, dispatch errors, parser logic | CI (`pytest`) on every commit + local | Solid (~500 tests) |
| **2 — Integration** (real `exiftool`, real `send2trash`, real `rawpy`/`pillow-heif` decoders) | Boundary error modes that are hard to reproduce via the GUI | Local only (skip when binaries absent); not on `windows-latest` | **On-demand. No maintained suite — boundaries are covered at the happy-path level by layer 3 with real fixtures.** Add a layer-2 spot-test reactively when a specific bug surfaces. |
| **3 — QA / E2E** (real GUI via `/qa-explore`) | Label drift, state-transition bugs, layout regressions, end-user flow failures | Local via `python -m qa.scenarios._batch`; CI possible per [#74](https://github.com/jackal998/photo-manager/issues/74) | Strong — drove most of the bugs found during the May 2026 sessions |

The per-file coverage gate (`scripts/check_coverage_per_file.py`) measures
**layer 1 only**. Floor is **70%**. Files with logic that's only reachable
from layers 2 or 3 belong in `[tool.coverage.run] omit` in `pyproject.toml`,
each with a comment naming the layer that DOES cover them.

### What each layer can and cannot catch

**Layer 1 — Unit tests**
- *Catches:* "Did we change the shape of our own code in a way that
  breaks dispatch / parsing / state machines?"
- *Misses:* Anything where our mock disagrees with the real third-party
  behavior. (Example: if exiftool changes its `-stay_open` protocol, our
  mock-based ExiftoolProcess tests still pass, but real users break.)

**Layer 2 — Integration tests** (on-demand, not maintained as a suite)
- *Why on-demand:* the boundary count here is small (`exiftool`,
  `send2trash`, `rawpy`, `pillow-heif`) and stable. Layer 3
  (qa-explore) already exercises every boundary on the happy path
  using real fixtures. A maintained layer-2 suite would mostly
  duplicate that coverage.
- *When to add:* a specific boundary bug surfaces — e.g. exiftool
  ships a breaking protocol change, `send2trash` fails on a locked
  file, `rawpy` chokes on a real-world DNG that qa-explore can't
  conveniently set up. Each spot-test then lives forever as a
  regression guard.
- *Catches (when present):* the specific failure mode the test was
  written for. Boundary error paths that are painful to trigger
  through the GUI go here.
- *Misses:* anything you haven't written a spot-test for. By design.

**Layer 3 — QA scenarios** (`/qa-explore`)
- *Catches:* The button text changed. The menu item is no longer
  greyed out pre-manifest. The status bar shows the wrong count. The
  Ref row is at the bottom of its group.
- *Misses:* Anything not exercised by the scripted scenario path.

A bug in production likely lives at **a layer not currently asserted**.
Knowing which layer you're skimping on is more important than the headline
coverage number.

---

## Per-module coverage map

Numbers from the most recent green CI run on master. **Layer 1 %** is the
unit-test coverage; **omit** means the file is intentionally not measured
at layer 1 (cell value points at where it IS covered). **Residual risk**
calls out what would be uncaught even with a green CI.

### `scanner/`

| Module | Layer 1 | Layer 2 (integration) | Layer 3 (qa-explore) | Residual risk |
|---|---|---|---|---|
| `scanner/exif.py` | 97% (uses `-j -G` JSON output; mocks emit realistic JSON shape via `_make_mock_et`. Records bind to paths by `SourceFile` identity, not position — drift bug class structurally impossible. Static fixtures in `tests/fixtures/exiftool_outputs/` snapshot real exiftool JSON output for the edge-case + mixed-format batches. PR 2 of #187 added `batch_read_extracts` for the scoring-signal census — GPS, XMP-DerivedFrom, XMP:Rating, EXIF/QuickTime census tags — same `-j -G` shape, `-fast` dropped because GPS/XMP live past the first IFD.) | spot-add only | s01, s04, s06, s08, s42 (real exiftool, happy path + scoring signals) | exiftool protocol drift between versions. Static fixtures will fail loudly if the JSON shape changes — that's the early-warning. The uncovered lines are a defensive `except` in the stderr drain thread (race during process close) and a defensive `int()` coercion for malformed XMP:Rating values. |
| `scanner/media_extract.py` | 100% | — | s42 (via the scan pipeline) | very low — canonical extraction schema for #187. Three-state sentinel convention (`None`=not attempted, `False`=checked-absent, `True`/value=present) is the load-bearing contract; pinned by tests in `tests/test_media_extract.py` and `test_batch_read_extracts_gps_present_false_when_no_gps_tags`. `merge_extracts` precedence (rawpy>PIL for dims, exiftool>PIL for exif_date) is also pinned. |
| `scanner/hasher.py` | 73% | spot-add only | s06, s07, s11 (real fixtures, happy path) | uncovered tail (~27%) is rawpy / HEIC fallback paths only reachable with real raw files. Layer 3 covers the formats we ship fixtures for; spot-add a layer-2 test only if a real-world RAW format misbehaves. |
| `scanner/dedup.py` | 94% | — | s01, s07, s10, s11, s42 | low — pure logic, well-covered. Internal `rows` and `path_to_hr` dicts are str-keyed (not `Path`-keyed) so genuinely-distinct files differing only by filename case (case-sensitive NTFS dirs; rare) survive — see #170. `TestCaseSensitiveCollision` pins this. Live Photo HEIC+MOV/MP4 pairs always share a group_id via pair edges fed into union-find — `TestLivePhotoPair` covers the unique-pair-forms-group case (#88 headline regression). `HashResult.to_media_extract()` (PR 2 of #187) is pinned by `TestHashResultToMediaExtract`. |
| `scanner/scoring.py` | 98% | — | s42 (scan + apply best-copy to a near-duplicate group) | very low — pure scorer for #187. Two-tier composite (Tier 1 format/derived penalties + Tier 2 eight weighted continuous signals); every dimension + Tier 1 + composite clamping + Live Photo MOV passenger rule + tie-break + `validate_weights` pinned by `tests/test_scoring.py` (75 cases). `apply_scoring_to_rows` (PR 4) and `ManifestRepository.rescore` (PR 4, lives in `infrastructure/`) are also pinned at layer 1 including the round-trip through SQLite. The 2 uncovered lines are defensive guards against malformed `group_rows` inputs that production callers never produce. |
| `scanner/walker.py` | 95% | — | s09 | very low — symlink + flat-mode branches well-covered. `_has_win32_unsafe_name` flags trailing-`.`/whitespace names during the walk and emits a `loguru` warning once per unsafe path — see #169. |
| `scanner/media.py` | 95% | — | s06, s11 | very low — file-type detection covered for all listed formats |
| `scanner/manifest.py` | 96% | — | every scenario writes a manifest | low. PR 1 of #187 added 4 columns (`exif_tag_count`, `gps_present`, `xmp_derived`, `score`) via the additive migration list. Old manifests auto-migrate on load; new columns default to NULL or 0. |

### `core/`

| Module | Layer 1 | Notes |
|---|---|---|
| `core/models.py` | 100% | dataclasses |
| `core/services/sort_service.py` | 100% | pure logic |
| `core/services/interfaces.py` | 100% | dataclasses + protocols |

### `infrastructure/`

| Module | Layer 1 | Layer 2 | Layer 3 | Residual risk |
|---|---|---|---|---|
| `infrastructure/manifest_repository.py` | 99% | — | every scenario, s42 | very low. `is_locked` column persistence (#164) is round-tripped in `TestIsLockedPersistence` (4 cases): default-False on load, additive migration on pre-#164 DBs, ``batch_update_lock_state`` write+read, and unlock-after-lock. PR 1 of #187 added 4 scoring columns via the same additive migration pattern; round-trip pinned by `TestScoringSchemaMigration` (3 cases) and the score-loads-into-PhotoRecord tests. PR 4's `rescore(weights)` is pinned by `TestManifestRepositoryRescore` (5 cases) — including weight validation, custom-weights flipping the winner, and `NULL` preservation for Live Photo MOV passengers. |
| `infrastructure/settings.py` | 100% | — | every scenario | none |
| `infrastructure/i18n.py` | 93% | — | s22 (live language switch — Yes-confirm, in-place MainWindow rebuild, locale persistence in settings.json) | low — uncovered branches are defensive `except (OSError, yaml.YAMLError)` paths in `available_locales()` and a couple of guards. The `test_zh_tw_has_every_key_present_in_english` test pins parity between the en and zh_TW catalogs at PR time, so a missing translation never ships silently. |
| `infrastructure/delete_service.py` | 93% | spot-add only | s13 (planned per #80) covers happy-path real send2trash | recycle-bin behavior on networked drives untested; error paths exercised via mocks. Spot-add a layer-2 test for specific bug cases (locked file, network drive, permission denied). |
| `infrastructure/utils.py` | 89% | spot-add only | s08 (real EXIF on real fixtures) | DNG fallback only mocked. If a real DNG ever returns metadata in a shape we don't anticipate, that's the moment to add a layer-2 spot-test pinning the parse. |
| `infrastructure/image_service.py` | **omit** | depends on running `QApplication` for image decode | s01, s05 | full responsibility on layer 3 |
| `infrastructure/logging.py` | **omit** | module-level loguru sink setup; no executable surface | — | none — touched implicitly when other tests import |

### `app/viewmodels/`

| Module | Layer 1 | Notes |
|---|---|---|
| `app/viewmodels/main_vm.py` | 96% | grouping logic well-covered |

### `app/views/`

| Module | Layer 1 | Layer 3 | Residual risk |
|---|---|---|---|
| `app/views/constants.py` | 100% | — | none |
| `app/views/media_utils.py` | 100% | — | none |
| `app/views/tree_model_builder.py` | 76% | s01, s06, s07, s10 | uncovered 24% is `setData()` `except: pass` defensive wrappers — only triggered if Qt's setData raises, which doesn't happen in practice. Lock column (#182, supersedes #164's glyph-prefix-on-Action approach) pinned by `TestActionDisplayUnaffectedByLock` (Action returns just the decision label regardless of lock state), `TestLockDisplay` (🔒 / empty rendering), and `TestLockColumnInBuiltModel` (end-to-end via `build_model` — locked row has 🔒 in COL_LOCK and bare decision in COL_ACTION; SORT_ROLE on COL_LOCK is 0/1 so the column is sortable). |
| `app/views/components/tree_controller.py` | 76% | s01 + every scenario that loads a manifest (real model build, expandAll, sort preservation); s40 (#143 — double-click dispatcher: group-row toggle expand) | uncovered 24% is defensive `try/except` wrappers around Qt calls (header resize, expandAll, signal connect) — only triggered when Qt internals raise, which doesn't happen in practice. The double-click dispatcher (#143) is pinned at layer 1 by `tests/test_tree_controller_double_click.py` (file row → handler with path, group row → toggle expand, invalid index → no-op, `setExpandsOnDoubleClick(False)` to avoid racing Qt's default). Selection extraction + group/path resolution + sort state round-trip pinned by `tests/test_tree_controller.py` — including the `SORT_ROLE` → `DisplayRole` fallback path on group-number resolution. |
| `app/views/handlers/file_opener.py` | 100% | s19 (Open Folder right-click — `open_folder_containing`); s40 is layer-3 for the dispatcher only (the file-row branch's `open_file_in_default_viewer` is layer-1 because an OS-spawned image viewer has no deterministic close-trigger across image apps and no offscreen rendering) | factored out of `context_menu.py`'s inline `_open_folder` in #143 so the right-click and double-click paths share one OS-cascade impl. `tests/test_file_opener.py` covers both helpers across all branches (Windows explorer /select,, folder fallback, non-Windows QDesktopServices, subprocess failure → QDesktopServices). |
| `app/views/workers/manifest_load_worker.py` | 100% | every load | none |
| `app/views/workers/scan_worker.py` | 91% | every scan scenario | minor — cancellation timing branch hard to test deterministically |
| `app/views/handlers/file_operations.py` | 84% | s01 + every scenario that loads a manifest; s12 for Save Manifest Decisions; s14 / s29 / s30 / s31 exercise the bulk-regex apply path through `set_decision_by_regex` (delete, remove-from-list deferred, right-click route, Simple-mode synthesis); s32 (#182) drives `set_decision_with_lock_check` end-to-end via the regex flow; s37 (#138, #140) drives `_on_manifest_loaded` through the new `set_baseline` plumbing; `build_match_fn` covered by `TestBuildMatchFn` + every regex-dialog driver via the live preview | uncovered 16% is QFileDialog interaction (file picker for open manifest) and a few rarely-hit error branches in the manifest open/save callbacks. Lock semantics (#182, supersedes #175) pinned at layer 1: `TestSetLockedState` (lock primitive — write/read-back, idempotent re-lock, unlock), `TestSetDecisionIsSilentDispatcher` (the low-level dispatcher applies regardless of lock — wrapping is the caller's job), `TestSetDecisionByRegexLockConfirm` (each of the three verdicts drives the right outcome: Apply Unlocked Only / Unlock & Apply All / Cancel; plus all-locked + no-locked fast paths). Status-bar baseline (#138, #140) pinned at layer 1 by `tests/test_status_bar_baseline.py::TestFileOperationsUsesBaselineForLoadSummary` — guards against a refactor wiring the post-load summary back to `show_status` (the transient path that menu hover wipes). |
| `app/views/handlers/context_menu.py` | 88% | s01 (menu probes), s15 (right-click Set Action → delete / keep, single + multi-select), s35 (#182 follow-up — right-click Lock / Unlock, single + multi-select), s19 (right-click Open Folder) | low — Open Folder is now a one-line delegation to `file_opener.open_folder_containing` (extracted in #143); the cascade itself is covered in `test_file_opener.py`. Remaining 12% is Protocol stub bodies. The `ActionHandlersImpl` bridge that context_menu calls through is unit-tested by `TestActionHandlersImplBridge` (delegation), with s15 / s35 covering end-to-end via the real menu. Without those scenarios, a missing proxy on the bridge silently no-ops the menu item — the #175 → #182 trap. |
| `app/views/handlers/action_handlers.py` | 100% | s15 (Set Action proxies), s32 / s34 (lock-confirm via FileOperationsHandler bridge), s35 (Lock / Unlock proxies) | thin proxy bridge from context_menu to FileOperationsHandler / DialogHandler; `TestActionHandlersImplBridge` pins the contract. Extracted from `main_window.py` in #182 so the bridge stays layer-1 testable without cascade-importing the QMainWindow assembly (which would tank coverage measurement). |
| `app/views/dialogs/scan_dialog.py` | 90% | every scenario opens it; s17 (full source-list operations); s38 (#144 — inline error when typed path doesn't exist, error clears on next add; #216 — output Browse… opens the "Save Manifest As" dialog and Escape leaves the field untouched) | uncovered 10% is mostly worker-signal branches. The path-field validation surface (`_on_add_typed` + `_clear_path_error`) is pinned at layer 1 by `TestPathFieldEntry`; the `_browse_output` `start`-argument contract (#216 — must be absolute or empty, never a bare relative filename) is pinned at layer 1 by `TestBrowseOutputStartPath`. s38 mirrors both end-to-end via UIA — without it, a regression that broke the QLabel accessible-name surface, or one that changed `start` enough to trip Qt into a different dialog flavour on Windows, would pass layer 1 but the user still wouldn't see the right dialog. |
| `app/views/components/menu_controller.py` | 89% | s01, s18, s21, s22, s28 | uncovered 11% is fallback branches in the language picker (no available locales) and a defensive guard for missing manifest-actions; the View → Language exclusivity + Yes/No confirm + dirty-flag exit prompt all unit-tested in `test_menu_controller_manifest_actions.py` |
| `app/views/components/status_messages.py` | 95% | indirectly via every scenario that asserts on status-bar copy (s01, s12, s13, s14, s20, s21, s27, s29) | low — pure formatter; `test_status_messages.py` pins the output shape so qa-explore regexes stay coherent |
| `app/views/components/status_reporter_impl.py` | 100% | s37 (#138, #140 — baseline) | thin StatusReporter bridge to MainWindow. Extracted from `main_window.py` so unit tests can import it without cascade-loading the QMainWindow assembly (same trap as `action_handlers.py` from #182). |
| `app/views/components/empty_state.py` | 100% | s41 (#137 — both buttons reachable + each click opens the right dialog) | builder for the first-run empty-state container (label + two primary-action buttons). Extracted from `main_window.py` in #137 so the click-wiring contract stays layer-1 testable without cascade-loading the QMainWindow view stack — same extraction pattern as `action_handlers.py` (#182) and `status_reporter_impl.py` (#138, #140). `TestButtonWiring` pins that each button's `clicked` signal invokes the callback the caller passed in (so a refactor that accidentally swapped the two callbacks would fail layer 1, not s41); `TestWrapperVisibilityTogglesAllChildren` pins that hiding the wrapper atomically hides the label + both buttons (#42 contract). |
| `app/views/dialogs/execute_action_dialog.py` | 83% | s13 (real send2trash through the GUI), s30 (Phase A right-click parity — opens the regex dialog from the Execute tree's context menu), s33 (#166 — banner renders the flagged group number), s34 (#182 — pre-execute lock-confirm Cancel verdict) | uncovered 17% is the actual destructive `_on_execute` flow + a few error branches in the path-not-found dialog; s13 covers the destructive happy path. Spot-add a layer-2 test only if a destructive-flow bug surfaces that's hard to reproduce via the GUI. Lock at execute stage (#182, supersedes #175) pinned by `TestExecuteDialogLock` (single-row + regex flows: each verdict for mixed-locked, all-locked uses the dialog too) and `TestExecuteRequestedLockConfirm` (pre-execute scan: no locked-delete → fast path; APPLY_ALL_UNLOCKED unlocks then executes; APPLY_UNLOCKED_ONLY clears decision on locked + executes the rest; CANCEL aborts). Banner jump-to (#166) pinned at layer 1 by `TestBannerJumpTo`: anchor rendering, `_on_jump_to_group` selects the matching group row, invalid/unknown hrefs are no-ops; the QLabel HTML-anchor click itself isn't UIA-clickable, so s33 only verifies the banner renders the right group number — the click → scrollTo dispatch stays unit-tested. |
| `app/views/dialogs/locked_rows_confirm_dialog.py` | 100% | s32 (bulk regex trigger), s34 (Execute trigger) | the dialog itself is data + button wiring; `TestLockedRowsConfirmDialog` pins body text shape (count + first-5-basenames + "…and N more"), button-state (Apply Unlocked Only disabled in the all-locked degenerate case), verdict per button click, Esc→Cancel, initial-state→Cancel. |
| `app/views/layout/layout_manager.py` | 86% | s01 (initial half-screen sizing + adjust-splitter on first manifest load), s39 (#136 splitter min-width floor) | low — the `setup_main_layout` constraints (`setChildrenCollapsible(False)` + `setMinimumWidth(200)` on each child) are pinned by `test_layout_manager_splitter.py`'s splitter-floor tests. Drift would be a removed line, not a behavioural change — visible immediately in CI. |
| `app/views/main_window.py` | not measured (no test imports it; QMainWindow assembly cascades into the whole view stack) | every scenario constructs MainWindow as a real subprocess; #141 geometry round-trip is layer-3 via s39 (window_state.ini round-trip across launches); close-event dirty-prompt logic is layer-3 via s28; #137 empty-state action buttons via s41 (the construction-time `build_empty_state_widget` call) | window-state persistence (#141) is intentionally NOT mock-tested — the save/restore paths are uniform plumbing around Qt's own `saveGeometry` / `restoreGeometry`, and mocking QSettings to "cover" them would be metric gaming per CLAUDE.md. s39 round-trips real QSettings through a real `closeEvent` and a real re-launch, which is what actually catches a #141 regression. Same shape for #137: the click-wiring contract is layer-1 in `app/views/components/empty_state.py`'s tests; s41 only asserts MainWindow actually uses the builder and reaches the right dialog from each button. |
| `app/views/dialogs/select_dialog.py` | 86% | s14 (Regex menu route), s29 (Regex remove-from-list), s30 (Regex right-click from Execute), s31 (Phase B/C Simple mode + regex-sync round-trip) | dropped from Phase A's 95% because Phase B + Phase C grew the file from ~160 → ~500 lines (Simple/Regex mode toggle, cheatsheet grid, recent patterns, custom match-highlight delegate, `_try_parse_simple` reverse-parse). The new branches are unit-tested in `tests/test_select_dialog.py` (`TestSimpleMode`, `TestCheatsheet`, `TestRecentPatterns`, `TestMatchHighlightDelegate`, `TestTryParseSimple`, `TestRegexSyncAcrossModes`, `TestLegacyModeKeyAlias`); uncovered ~14 % is the `_MatchHighlightDelegate.paint` segments that only fire when an actual painter+option pair is supplied (covered by qa-explore visual paths) plus a few defensive try/except branches in the Recent menu and settings I/O. The action combo now offers 5 options (delete / keep / remove / lock / unlock) — pinned by `test_action_combo_count_matches_settable_decisions_with_remove_and_lock` and `test_action_combo_includes_lock_and_unlock_options` (#164). |

### Top-level scripts

| Module | Status | Where it's covered |
|---|---|---|
| `main.py` | **omit** | qa-explore launches it as a real subprocess for every scenario |
| `scan.py` | **omit** | manual smoke before release; underlying `scanner.*` is layer-1 tested |
| `review.py` | **omit** | manual; underlying `scanner.*` is layer-1 tested |
| `run_all_linters.py` | **omit** | dev tooling, not user-facing |

---

## Adding tests for new features

Three triggers, three test homes:

1. **Pure logic** (no external deps)
   → unit test under `tests/`
   → must clear 70% per-file
   → run on every commit via CI

2. **Touches a boundary** (subprocess, filesystem semantics, third-party
   lib whose behavior varies by version — exiftool, rawpy, pillow-heif,
   send2trash)
   → unit test for our side, mocking the dependency
   → qa-explore scenario covers the happy path (this is the primary
   safety net — see Layer 3)
   → **consider** a layer-2 spot-test only if you can name a specific
   boundary failure mode that's hard to trigger through the GUI
   (e.g. exiftool returning malformed output, send2trash on a locked
   file). Default action: don't write one. Layer 2 is on-demand, not
   an obligation.

3. **User-facing flow** (button, dialog, menu, status bar, manifest
   review)
   → extend an existing `qa/scenarios/sNN_*.py` driver, OR add a new
   one and register it in `qa/scenarios/_batch.py:ALL_SCENARIOS` and
   `qa/scenarios/_config.py:SCENARIO_SOURCES`
   → optionally a layer-1 unit test for any pure logic that backs the
   UI behavior
   → if the change touches a behavior several scenarios already
   exercise (status-bar shape, menu enable lifecycle, destructive
   confirm semantics), reach for `qa/scenarios/_invariants.py` instead
   of duplicating asserts. Each existing driver calls one or two of
   these probes — adding a new probe there benefits every scenario
   for free.

---

## Changing UI labels (and not breaking the QA batch)

User-facing strings live in `translations/<locale>.yml` (the i18n
catalog), not in Python literals. The qa-explore drivers couple to
those English values via three surfaces: **`qa/scenarios/_uia.py`
constants** (button titles, dialog titles, menu items),
**`qa/scenarios/_invariants.py`'s menu-item table** (hardcoded menu
labels for the manifest-action invariant), and **inline strings inside
individual scenario files** (status-bar regex, dialog body
substrings).

When you rename a button or change a dialog title:

1. **Update `translations/en.yml`** — that's the single source of
   truth for what the app shows.
2. **Update every other `translations/*.yml`** with the matching
   value (or accept that older locales temporarily fall back to
   English until a translator catches up).
3. Grep `qa/` for the old string (`grep -rn "Old Label" qa/`). That's
   your blast radius. Update every match.
4. Run the affected scenario(s) targeted: `python -m
   qa.scenarios._batch sNN_xyz` — fast iteration vs. the full batch.
5. If you forget steps 1–3, [`tests/test_uia_label_coupling.py`](../tests/test_uia_label_coupling.py)
   catches it at PR time. The test scans `app/*.py` AND every
   `translations/*.yml` for each user-facing label constant in
   `_uia.py`, so a stale constant fails CI.

**What the lint test does NOT catch:**

- Inline strings inside individual `qa/scenarios/sNN_*.py` files
  (status-bar regex like `r"Removed N items from list"`, dialog body
  substrings). Those are matched by intent rather than exact text and
  live in arbitrary positions. Status-bar copy is centralized through
  `app/views/components/status_messages.report_count`; the existing
  `tests/test_status_messages.py` pins the formatter so callers (and
  the regex they're matched by) stay coherent.
- Auto IDs (`SCAN_AID_*`) — those are computed from the QObject
  hierarchy at runtime. Renaming a class breaks the auto_id without
  any source-text drift visible to a static check.
- A constant could exist in `app/` but in an unrelated context — the
  lint only verifies the string is present, not that it labels the
  right widget.

For comprehensive verification before merge, run the full batch:
`python -m qa.scenarios._batch`. The lint test is the cheap, fast,
CI-runnable subset that catches the most common drift class.

---

## Authoring new QA scenarios

Read [`qa/scenarios/AUTHORING.md`](../qa/scenarios/AUTHORING.md)
before adding a new `sNN_*.py` driver. It captures the patterns we
landed on and the no-go traps we hit while building the qa-batch CI
workflow ([#74](https://github.com/jackal998/photo-manager/issues/74)
/ [#128](https://github.com/jackal998/photo-manager/pull/128)) — every
landmine in there cost a real iteration cycle.

Co-located with the drivers so it's one Glob away when you're working
in `qa/scenarios/`.

---

## CI dialog-driving — `PHOTO_MANAGER_QT_FILE_DIALOG` ([#129](https://github.com/jackal998/photo-manager/issues/129))

The native Windows `IFileSaveDialog` / `IFileOpenDialog` modal loop
only pumps COM messages — not regular `WM_*` — and GitHub-hosted
Windows runners don't deliver synthesized mouse or keyboard input to
it. So `PostMessage(BM_CLICK)`, `PostMessage(WM_KEYDOWN, VK_RETURN)`,
UIA `Invoke`, and `click_input` all return success on the runner but
the Save / Open action never fires (full iteration history in
[#128](https://github.com/jackal998/photo-manager/pull/128)).

**Resolution.** The `qa-batch` workflow sets
`PHOTO_MANAGER_QT_FILE_DIALOG=1`. When that env var is `1`,
`main.py` applies `Qt.AA_DontUseNativeDialogs` before constructing
`QApplication`, so every `QFileDialog` in the process becomes Qt's
widget-based dialog — which responds to UIA normally. Local users
get the native dialog as before (env var unset → no behavior change).

**Cross-platform value.** The same env var works for future macOS
hosted-runner CI: the analogous `NSSavePanel` synthesized-input
limitation lifts the same way — one switch, every platform. No
per-OS QA-helper rewrite needed.

The `_uia.py` filename-Edit and action-button locators carry parallel
branches for both tree shapes — native Common Item Dialog
(`ComboBox > Edit`, 2nd-from-rightmost bottom-row button) and Qt
`QDialogButtonBox` (standalone `QLineEdit`, topmost button in the
buttonBox). See `_find_filename_edit` and
`_find_native_dialog_action_button` docstrings.

---

## Layer 3 sharding in CI ([#188](https://github.com/jackal998/photo-manager/issues/188))

The qa-batch workflow runs as 5 parallel jobs via `strategy.matrix.shard:
[1, 2, 3, 4, 5]`. Each job invokes
`python -m qa.scenarios._batch --shard N --total-shards 5`. Selection is
sorted-stride in `qa.scenarios._batch.select_shard` over `ALL_SCENARIOS`.

Invariants pinned by `tests/test_batch_shard.py`:

- **Pairwise disjoint, union complete** — every scenario runs exactly once
  across the five shards.
- **s23a and s23b stay on the same shard** — s23b reads settings s23a
  wrote. The selector pairs them into a single unit before striding.
- **Balanced** — shard sizes differ by ≤2 (the s23 pair perturbs the
  standard floor/ceil split by +1 in whichever shard owns it; today at
  M=5 the sizes are 9/8/8/8/7).

Why 5? Per-shard fixed overhead (~1.5 min for checkout + pip + exiftool +
sandbox build) is paid per shard in parallel. With 40 scenarios at ~25s
each, the per-shard wall-clock equation is
`fixed + scenarios/shards × 25s` — past ~5 shards the fixed-overhead
floor dominates and additional shards mostly burn runner minutes for
diminishing wall-clock wins. Well under GitHub's
[20-concurrent-job free-tier cap](https://docs.github.com/en/actions/reference/actions-limits)
and the [256-jobs-per-matrix hard limit](https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs).

The `concurrency` key includes the shard number so the five shards of
the same PR don't auto-cancel each other; each shard's artifact name
(`qa-batch-log-shard{N}`) is similarly suffixed because
`actions/upload-artifact@v4` rejects duplicates within a run.

Running a shard locally for debugging:

```
.venv/Scripts/python.exe -m qa.scenarios._batch --shard 1 --total-shards 5 --dry-run
.venv/Scripts/python.exe -m qa.scenarios._batch --shard 1 --total-shards 5
```

An explicit positional list (`python -m qa.scenarios._batch sNN_xyz …`)
still works and overrides sharding — handy for targeted iteration.

---

## Open work

- **Layer 2 is on-demand**, not on the roadmap. Add a spot-test under
  `tests/integration/` (with `@pytest.mark.integration` and a
  `skip-if-binary-missing` guard) the first time a specific boundary
  bug surfaces. Don't pre-build the suite. The boundaries we touch
  (`exiftool` / `send2trash` / `rawpy` / `pillow-heif`) are stable
  enough that proactive coverage would mostly duplicate layer 3.
- **Layer-3 hardening.** [#80](https://github.com/jackal998/photo-manager/issues/80) closed: scenarios for Save Manifest (s12), Execute Action (s13, destructive), Set Action by Field/Regex (s14), and right-click context-menu decisions (s15) all merged. Each driver now also calls cross-scenario probes from `qa/scenarios/_invariants.py` (status-bar shape, manifest-actions toggle consistency, destructive-confirm shape) — no maintained extra suite, just lines added inside the existing drivers.
- **CI for layer 3.** [#74](https://github.com/jackal998/photo-manager/issues/74) tracks running `qa.scenarios._batch` on UI-touching PRs. Gated on layer-3 reliability — flaky required CI is worse than no CI.

---

## Maintenance

This document should be updated when:
- A module's coverage drops by >5pp (regression worth noting)
- A module is added to or removed from `omit`
- A new layer-2 / layer-3 test home is added (boundary, scenario)
- A residual-risk note becomes stale (e.g., an integration test now
  covers what was previously local-only)

The per-module table is hand-maintained for now. If keeping it in sync
with `coverage.json` becomes a chore, generate it via
`scripts/check_coverage_per_file.py` (extension is straightforward).
