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

**Probes** (`tests/test_ui_probes.py` + sNN soft-probe blocks)
- Cross-cutting structural invariants that *every* layer above misses
  by design — added in [#243](https://github.com/jackal998/photo-manager/issues/243).
- *Catches:* "Did the dialog dropdown drift from the tree columns?",
  "Did a callsite drop a kwarg that gates a panel?", "Did a translation
  ship as an English passthrough?", "Are two equivalent menu paths
  reaching the same destructive surface?", "Is the bridge proxy out
  of sync with its Protocol?".
- *Why a separate layer:* scenario drivers replay one canonical path
  each. Probes inspect a structural relationship — a single probe
  catches a whole class of drift across many surfaces.
- *Three flavours:*
  - Static probes (`tests/test_ui_probes.py`) — AST or YAML
    inspection, run as pytest in CI. Use `@pytest.mark.xfail(strict=True)`
    so CI tolerates known-bug probes today and flips red the moment
    the fix lands without removing the marker.
  - Live soft-probes (extension blocks in `qa/scenarios/sNN_*.py`) —
    UIA inspection for runtime state, piggy-backing on an existing
    scenario's setup. Use a `print("probe_status: …")` pattern
    instead of `failures.append` so qa-batch stays green until the
    bug is fixed; comment block documents the one-line upgrade to
    a hard failure.
  - Live exploration probes (`qa/probes/<name>.py`) — standalone
    UIA modules that launch the app, load a fixture, inspect a
    structural relationship, and exit non-zero on FAIL. Self-runnable
    via `python -m qa.probes.<name>`. Each module includes its own
    configure → launch → scan teardown (shared via
    `qa.probes._runtime.app_with_manifest`), so they don't depend
    on the scenario batch's surrounding orchestration. Use these
    for invariants that scripted scenarios architecturally can't
    cover (dropdown ↔ column diff, per-group label-count audits,
    selection-vs-manifest consistency).

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
| `scanner/dedup.py` | 95% | — | s01, s07, s10, s11, s42, s65 | low — pure logic, well-covered. Internal `rows` and `path_to_hr` dicts are str-keyed (not `Path`-keyed) so genuinely-distinct files differing only by filename case (case-sensitive NTFS dirs; rare) survive — see #170. `TestCaseSensitiveCollision` pins this. Live Photo HEIC+MOV/MP4 pairs always share a group_id via pair edges fed into union-find — `TestLivePhotoPair` covers the unique-pair-forms-group case (#88 headline regression). `HashResult.to_media_extract()` (PR 2 of #187) is pinned by `TestHashResultToMediaExtract`. #517 — the multi-hash confidence vote (`_dhash_confidence`: a pHash near-dup match is flagged `match_confidence="high"` only when an independent dHash also agrees; SHA-exact is always high) is pinned by `TestMatchConfidence`; the auto-select gate that drops `"low"`-confidence rows from the aggressive delete set lives in `core/services/auto_select.py::non_keepers_for_aggressive_delete` and is pinned by `TestNonKeepersForAggressiveDelete` in `tests/test_auto_select.py`. #526 — near-dup candidate generation uses a hand-rolled `_BKTree` (Hamming via int popcount) above `_BKTREE_MIN_CANDIDATES`, replacing the O(N²) all-pairs enumeration; `TestBKTreeStructure` pins the tree against a naive popcount scan and `TestBKTreeParity` proves the brute and BK paths yield bit-identical `classify()` output across thresholds, dHash gates, and exact-SHA interplay. The crossover benchmark is `scripts/bench_grouping.py` (dev tool, coverage-omitted). #526 PR2 — `classify(bktree_min_candidates=…)` threads a per-machine crossover floor (measured by the #486 calibration) down to `_near_dup_neighbors`; `TestBKTreeParity::test_parity_via_classify_param` pins that the public param selects the strategy without touching the module global and both forced strategies match the default. `GROUPING_STRATEGY_VERSION` is a cache-keying token folded into the scan-worker fingerprint. #538 — `_classify_near_duplicates` decouples edge collection from classification so a genuine near-dup whose only bridge is already classified is no longer orphaned (true transitive closure); pinned at layer 1 by `TestUnderGroupingFix` (orphan-now-grouped + determinism under input shuffle) and at layer 3 by **`s65_passenger_bridge`** (#544 — three real burst frames form a both-pHash+dHash bridge that yields a 2-Ref-tier "passenger" group). |
| `scanner/scoring.py` | 98% | — | s42 (scan + verify score pipeline end-to-end on a near-duplicate group) | very low — pure scorer for #187. Two-tier composite (Tier 1 format/derived penalties + Tier 2 eight weighted continuous signals); every dimension + Tier 1 + composite clamping + Live Photo MOV passenger rule + tie-break + `validate_weights` pinned by `tests/test_scoring.py` (75 cases). `apply_scoring_to_rows` (PR 4) and `ManifestRepository.rescore` (PR 4, lives in `infrastructure/`) are also pinned at layer 1 including the round-trip through SQLite. The 2 uncovered lines are defensive guards against malformed `group_rows` inputs that production callers never produce. |
| `scanner/autotune.py` | 100% | `tests/integration/test_autotune_ab.py` (GATE-2 no-regression A/B, `@pytest.mark.integration` skip-if; run locally) | `s66` + dev-rig manual checkpoint | Pure read-knee detection logic for the #551 in-pipeline ramp (`knee_from_throughput` over a `{concurrency: files/s}` map; `ReadKneeRamp` ladder state machine — acquire-time level tagging, fill-transient discard, files/s accounting, cached `knee` vs live `current_permits`). Covered at layer 1 by `tests/test_autotune.py`: knee curves (plateau→2, rising→cap, HDD→1, noisy-stable, flat-ties-small), fail-open (empty / single-rung / None / zero-rate / non-doubling gap), and the `ReadKneeRamp` state machine (ladder clamp, zero-byte skip, min-seconds gate, **completion-order invariance** (#551 F1), **fill-transient discard** (#551 F7), drained-level ignore, equal-timestamp no-divide-by-zero). No `# pragma: no cover` — the whole module is pure (no Qt/Win32/I/O). The in-pipeline ramp wiring lands in #551 Phase 2 (`scan_worker.py`, real-I/O sampling hook behind `# pragma: no cover`); the determinism qa scenario `s66` (#551 Phase 3) drives the Scan-dialog autotune control and asserts autotune still yields the known-correct grouping (the real ramp only engages above the `_RAMP_MIN_SCAN_FILES` gate, so qa-sized fixtures fall open to static). #551 Phase 4 flipped the feature to **default-ON** (opt-out), raised the short-scan floor 256 → 1584 (the conservative N=8 bound on the first-scan sub-MAX read tax) and bumped `AUTOTUNE_RECIPE_VERSION` → "2" (clean cache invalidation). The flip is gated by reachable evidence rather than the unobtainable real mis-fit NAS: **GATE-1** (`tests/test_scan_worker.py::TestScanWorkerReadKneeRamp::test_synthetic_throttled_nas_finds_knee_{2,4}` — the REAL `ReadKneeRamp`, run through the gated reader pipeline with a synthetic in-flight latency cliff, detects the knee; two distinct cliffs so "always returns 2" can't pass) and **GATE-2** (`tests/integration/test_autotune_ab.py` — median-of-5 OFF-vs-warm-ON A/B asserting `median(ON) ≤ median(OFF) × 1.10`; observed ON/OFF ≈ 0.69 on the synthetic cliff). GATE-2 bounds the *algorithm's* overhead on an idealised cliff, not real SMB/wire contention — the actual NAS-knee≈2 / HDD-knee=1 measurement is the dev-rig manual checkpoint. The orphaned #577 `OccupancyProbe` shell + its `PHOTO_MANAGER_AUTOTUNE_PROBE` env-gated wiring were deleted in #579. |
| `scanner/walker.py` | 95% | — | s09 | very low — symlink + flat-mode branches well-covered. `_has_win32_unsafe_name` flags trailing-`.`/whitespace names during the walk and emits a `loguru` warning once per unsafe path — see #169. |
| `scanner/media.py` | 95% | — | s06, s11 | very low — file-type detection covered for all listed formats |
| `scanner/manifest.py` | 96% | — | every scenario writes a manifest | low. PR 1 of #187 added 4 columns (`exif_tag_count`, `gps_present`, `xmp_derived`, `score`) via the additive migration list. Old manifests auto-migrate on load; new columns default to NULL or 0. |
| `scanner/byte_budget.py` | 94% | — | covered via the scan pipeline | Pure byte-budget gate for the #587 HASH-stage OOM fix (count alone — `hash_in_q`+`compute_inflight`=257 buffers × up to ~130 MB DNG ≈ OOM — cannot bound RAM; this caps in-flight *bytes*). The byte cost is acquired in the READER worker (`scan_worker._gated_read`/`_budgeted_read`) so the reader pool back-pressures, and released in the compute done-callback. Layer 1 by `tests/test_byte_budget.py`: accounting, admit-one-over-budget (a single file larger than the whole budget can't deadlock), **cancel-wake** (a thread blocked in `acquire()` must wake on cancel — the #492/#495/#507/#561 deadlock scar class), zero-byte no-op, release clamp/never-raise, the concurrent peak-`_inflight ≤ budget` invariant, and `default_budget_bytes` clamp (floor 256 MiB / cap 2 GiB / probe-fail fallback). The real-OS RAM probe (`_probe_total_ram_windows`/`_posix`) is `# pragma: no cover` (can't run portably on CI; exercised by monkeypatch + local runs). The pipeline WIRING is pinned by `tests/test_scan_worker.py::TestByteBudgetPipelineBound` — it drives the real thread-branch with compute held closed and asserts the reader pool is back-pressured by the budget (the pre-#587 version reads the whole library into RAM and fails it). |

### `core/`

| Module | Layer 1 | Notes |
|---|---|---|
| `core/models.py` | 100% | dataclasses |
| `core/services/sort_service.py` | 100% | pure logic |
| `core/services/interfaces.py` | 100% | dataclasses + protocols |
| `core/services/auto_select.py` | 100% | pure helper for #212. Picks the top-scored row per duplicate group; consumed by `scan_worker._run_pipeline` when the dialog's "Auto select after scan" checkbox is on. Tie-break + None-handling mirror `select_paths_top_n` (`app/views/dialogs/select_dialog.py`) so manual and auto runs converge on the same keeper. Layer 3: s49 covers the full pipeline including #239's visual-selection step — `MainWindow._load_manifest_after_scan` walks `vm.groups` for `action="KEEP"` and applies the tree selection. |

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
| `app/views/tree_model_builder.py` | 76% | s01, s06, s07, s10, s65 | uncovered 24% is `setData()` `except: pass` defensive wrappers — only triggered if Qt's setData raises, which doesn't happen in practice. Lock column (#182, supersedes #164's glyph-prefix-on-Action approach) pinned by `TestActionDisplayUnaffectedByLock` (Action returns just the decision label regardless of lock state), `TestLockDisplay` (🔒 / empty rendering), and `TestLockColumnInBuiltModel` (end-to-end via `build_model` — locked row has 🔒 in COL_LOCK and bare decision in COL_ACTION; SORT_ROLE on COL_LOCK is 0/1 so the column is sortable). #241 within-group Ref-uniqueness pinned at layer 1 by `tests/test_ui_probes.py::test_probe_similarity_column_emits_at_most_one_ref_per_group` (builds the canonical Live Photo HEIC + MOV passenger group, runs `build_model`, asserts the Similarity column has ≤1 "Ref" cell). Layer-3 equivalent isn't worth its CI cost — s11 (the Live Photo scenario) deliberately verifies via SQLite because `read_result_rows`'s y_min=600 filter elides tree cells in the windows-latest runner's smaller render. #536 — the passenger relabel (a Ref-tier non-winner renders a real similarity, not a bare "—") is pinned at layer 1 by `TestPassengerRelabel` + the build_model end-to-end test, and at layer 3 by `s65_passenger_bridge` (#544 — verifies the passenger structure forms on real burst frames). |
| `app/views/components/tree_controller.py` | 76% | s01 + every scenario that loads a manifest (real model build, expandAll, sort preservation); s40 (#143 — double-click dispatcher: group-row toggle expand); s45 (#121 — column-header sort flow + in-memory sort preservation across manifest reload via `_on_header_clicked` → `update_sort_state` → `refresh_model` → `sortByColumn`); s47 (#214 — column layout persists across launches via `save_column_state` / `restore_column_state`) | uncovered 24% is defensive `try/except` wrappers around Qt calls (header resize, expandAll, signal connect) — only triggered when Qt internals raise, which doesn't happen in practice. The double-click dispatcher (#143) is pinned at layer 1 by `tests/test_tree_controller_double_click.py` (file row → handler with path, group row → toggle expand, invalid index → no-op, `setExpandsOnDoubleClick(False)` to avoid racing Qt's default). Selection extraction + group/path resolution + sort state round-trip pinned by `tests/test_tree_controller.py` — including the `SORT_ROLE` → `DisplayRole` fallback path on group-number resolution. Column-state persistence (#214) pinned by `TestColumnStateRoundTrip` (visual-order + width round-trip, missing-key fallback, section-count mismatch skip, sidecar count sentinel write) and `TestLayoutChangeSignalConnection` (`sectionMoved` / `sectionResized` fire the save callback; `refresh_model`'s ResizeToContents cycle does NOT — the blockSignals guard around it is what prevents every manifest reload from overwriting the user's saved widths). |
| `app/views/handlers/file_opener.py` | 100% | s19 (Open Folder right-click — `open_folder_containing`); s40 is layer-3 for the dispatcher only (the file-row branch's `open_file_in_default_viewer` is layer-1 because an OS-spawned image viewer has no deterministic close-trigger across image apps and no offscreen rendering) | factored out of `context_menu.py`'s inline `_open_folder` in #143 so the right-click and double-click paths share one OS-cascade impl. `tests/test_file_opener.py` covers both helpers across all branches (Windows explorer /select,, folder fallback, non-Windows QDesktopServices, subprocess failure → QDesktopServices). |
| `app/views/workers/manifest_load_worker.py` | 100% | every load | none |
| `app/views/workers/scan_worker.py` | 83% | every scan scenario; s49 covers the new auto-select branch (#212 — top-scored row promoted to `action="KEEP"` before manifest write); s03 (#493 — two cancel samples: post-HASH + a WALK-stage cancel against a large disposable stub source so the `#491` cooperative WALK gate fires — log shows `Walking …` + `Scan cancelled.`); s63 (#475 — late-stage post-HASH cancel against a large source so the cancel lands at CLASSIFY/SCORE/WRITE; asserts the clean `Scan cancelled.` terminator AND that the output manifest is left untouched, since the WRITE gate fires before the manifest write) | minor — cancellation timing branch hard to test deterministically; s03/s63 bias WHERE in the pipeline the cancel lands by sizing the source (the standard sandbox WALKs in <50 ms so a cancel can never land inside WALK without the large stub source). The auto-select branch itself is gated by a constructor flag; the unit tests in `tests/test_auto_select.py` pin the underlying decision logic and s49 pins the in-process wiring end-to-end. #526 PR2 — the `hash_pool="auto"` calibration now also measures the grouping micro-rates (`_profile_grouping`), derives the per-machine BK-tree floor (`_derive_bktree_floor` / `_resolve_grouping_floor`), and folds the hash-recipe + grouping-strategy version tokens into `hash_pool_fingerprint`; `TestHashPoolCalibration` pins the floor crossover/clamp, fresh-vs-cached floor derivation, the legacy-cache (no group keys) fallback to the module default, and version-token cache invalidation. The micro-rates are content-independent (timed on a synthetic clustered hash set) so they live at layer 1, not qa. |
| `app/views/handlers/file_operations.py` | 84% | s01 + every scenario that loads a manifest; s12 for Save Manifest Decisions; s14 / s29 / s30 / s31 exercise the bulk-regex apply path through `set_decision_by_regex` (delete, remove-from-list deferred, right-click route, Simple-mode synthesis); s32 (#182) drives `set_decision_with_lock_check` end-to-end via the regex flow; s37 (#138, #140) drives `_on_manifest_loaded` through the new `set_baseline` plumbing; `build_match_fn` covered by `TestBuildMatchFn` + every regex-dialog driver via the live preview | uncovered 16% is QFileDialog interaction (file picker for open manifest) and a few rarely-hit error branches in the manifest open/save callbacks. Lock semantics (#182, supersedes #175) pinned at layer 1: `TestSetLockedState` (lock primitive — write/read-back, idempotent re-lock, unlock), `TestSetDecisionIsSilentDispatcher` (the low-level dispatcher applies regardless of lock — wrapping is the caller's job), `TestSetDecisionByRegexLockConfirm` (each of the three verdicts drives the right outcome: Apply Unlocked Only / Unlock & Apply All / Cancel; plus all-locked + no-locked fast paths). Status-bar baseline (#138, #140) pinned at layer 1 by `tests/test_status_bar_baseline.py::TestFileOperationsUsesBaselineForLoadSummary` — guards against a refactor wiring the post-load summary back to `show_status` (the transient path that menu hover wipes). |
| `app/views/handlers/context_menu.py` | 88% | s01 (menu probes), s15 (right-click Set Action → delete / keep, single + multi-select), s35 (#182 follow-up — right-click Lock / Unlock, single + multi-select), s19 (right-click Open Folder) | low — Open Folder is now a one-line delegation to `file_opener.open_folder_containing` (extracted in #143); the cascade itself is covered in `test_file_opener.py`. Remaining 12% is Protocol stub bodies. The `ActionHandlersImpl` bridge that context_menu calls through is unit-tested by `TestActionHandlersImplBridge` (delegation), with s15 / s35 covering end-to-end via the real menu. Without those scenarios, a missing proxy on the bridge silently no-ops the menu item — the #175 → #182 trap. |
| `app/views/handlers/action_handlers.py` | 100% | s15 (Set Action proxies), s32 / s34 (lock-confirm via FileOperationsHandler bridge), s35 (Lock / Unlock proxies) | thin proxy bridge from context_menu to FileOperationsHandler / DialogHandler; `TestActionHandlersImplBridge` pins the contract. Extracted from `main_window.py` in #182 so the bridge stays layer-1 testable without cascade-importing the QMainWindow assembly (which would tank coverage measurement). |
| `app/views/handlers/dialog_handler.py` | 90% (#293) | s12 (manifest save), s14 / s29 / s30 (regex menu / remove-from-list / right-click), s17 (scan sources), s38 (path-field validation) | layer 1 added in #293: helper extraction + fake-self thin-proxy tests. Pure-logic surface (initial-field lookup, canonical field list, per-row values dict assembly, safe records-provider invocation) lives in `dialog_handler_helpers.py` below. The remaining 10% is the Protocol stub bodies + the `ImportError`-fallback `QMessageBox.critical` (ActionDialog is an in-project hard import that can't realistically fail) + the broad `except Exception: pass` in `_get_highlighted_row_values` (defensive guard against Qt model races that production model code doesn't produce). The records-provider safe-call (`safe_call_records_provider`) is the #237-class load-bearing contract — pinned by `TestSafeCallRecordsProvider` and `test_records_provider_error_does_not_crash_dialog_open`. |
| `app/views/handlers/dialog_handler_helpers.py` | 100% (#293) | s12 / s14 / s29 / s30 / s17 / s38 | pure-logic extraction from `dialog_handler.py`: clicked-column → initial-field lookup (`resolve_initial_field`), canonical 11-field dropdown list (`default_action_dialog_fields`), three (label, col) tables (`CHILD_ROW_FIELDS` / `GROUP_ROW_FIELDS` / `TOP_ROW_FIELDS`), dict assembler from pairs (`dict_from_pairs`), and safe records-provider invocation (`safe_call_records_provider`). Same extraction pattern as `action_handlers.py` (#182), `main_window_helpers.py` (#185 / #283), `group_media_controller_helpers.py` (#185 / #285), and `preview_pane_helpers.py` (#185 / #289). |
| `app/views/dialogs/scan_dialog.py` | 92% | every scenario opens it; s17 (full source-list operations); s38 (#144 — inline error when typed path doesn't exist, error clears on next add; #216 — output Browse… opens the "Save Manifest As" dialog and Escape leaves the field untouched); s48 (#215 — geometry persists across close-and-reopen, shared with the other two resizable dialogs through `app/views/window_state.py`); s49 (#212 — "Auto select after scan" checkbox toggles and persists through QSettings, the worker reads it at scan time) | uncovered 8% is mostly worker-signal branches. The path-field validation surface (`_on_add_typed` + `_clear_path_error`) is pinned at layer 1 by `TestPathFieldEntry`; the `_browse_output` `start`-argument contract (#216 — must be absolute or empty, never a bare relative filename) is pinned at layer 1 by `TestBrowseOutputStartPath`. s38 mirrors both end-to-end via UIA — without it, a regression that broke the QLabel accessible-name surface, or one that changed `start` enough to trip Qt into a different dialog flavour on Windows, would pass layer 1 but the user still wouldn't see the right dialog. Geometry save/restore (#215) is pinned at layer 1 by `tests/test_window_state.py::TestScanDialogDoneSavesGeometry` (the `done()` hook persists a non-empty blob) and at layer 3 by s48 (Win32 MoveWindow round-trip through real QSettings, same plumbing s39 uses for the main window). Auto-select setting (#212) is pinned at layer 1 by `TestAutoSelectCheckbox` (default-off, load-from-settings, toggle round-trips through disk) and at layer 3 by s49 (UIA-toggle, scan, assert top-scored row's `action="KEEP"`). |
| `app/views/components/menu_controller.py` | 89% | s01, s18, s21, s22, s28; s14 (#244 — `assert_action_menu_gated_pre_manifest` step verifies both `Set Action by Field…` and `Execute Action…` start greyed before any manifest loads) | uncovered 11% is fallback branches in the language picker (no available locales) and a defensive guard for missing manifest-actions; the View → Language exclusivity + Yes/No confirm + dirty-flag exit prompt all unit-tested in `test_menu_controller_manifest_actions.py`. The #244 pre-manifest gating is pinned at layer 1 by `tests/test_ui_probes.py::test_probe_manifest_dependent_menu_actions_are_gated` (AST inspection of `MANIFEST_ACTIONS` membership) and by the static probe `test_probe_no_execute_mode_toggle_in_menu` (#240 — enforces the absence of the removed Execute Mode toggle so a partial revert can't silently re-add it). |
| `app/views/components/status_messages.py` | 95% | indirectly via every scenario that asserts on status-bar copy (s01, s12, s13, s14, s20, s21, s27, s29) | low — pure formatter; `test_status_messages.py` pins the output shape so qa-explore regexes stay coherent |
| `app/views/components/status_reporter_impl.py` | 100% | s37 (#138, #140 — baseline) | thin StatusReporter bridge to MainWindow. Extracted from `main_window.py` so unit tests can import it without cascade-loading the QMainWindow assembly (same trap as `action_handlers.py` from #182). |
| `app/views/components/empty_state.py` | 100% | s41 (#137 — both buttons reachable + each click opens the right dialog) | builder for the first-run empty-state container (label + two primary-action buttons). Extracted from `main_window.py` in #137 so the click-wiring contract stays layer-1 testable without cascade-loading the QMainWindow view stack — same extraction pattern as `action_handlers.py` (#182) and `status_reporter_impl.py` (#138, #140). `TestButtonWiring` pins that each button's `clicked` signal invokes the callback the caller passed in (so a refactor that accidentally swapped the two callbacks would fail layer 1, not s41); `TestWrapperVisibilityTogglesAllChildren` pins that hiding the wrapper atomically hides the label + both buttons (#42 contract). |
| `app/views/dialogs/execute_action_dialog.py` | 82% | s13 (real send2trash through the GUI), s30 (Phase A right-click parity — opens the regex dialog from the Execute tree's context menu), s33 (#166 — banner renders the flagged group number), s34 (#182 — pre-execute lock-confirm Cancel verdict), s43 (#209 — numeric threshold through Set Action's new panel writes user_decision on the matched rows), s44 (#211 — selection-scoped Execute: highlight 2 of 5 delete-decision rows + ctrl-click + Execute → only the 2 highlighted files vanish on disk, the other 3 keep their decisions intact at executed=0), s48 (#215 — geometry persists across close-and-reopen), s64 (#483 — "Execute selected" partial-execute button: 2-cluster fixture, highlight a subset of one group, click "Execute selected" → dialog STAYS OPEN, only the highlighted files vanish on disk, the un-highlighted rows keep their `delete` decisions, then a follow-up full "Execute" finishes the rest and closes the dialog) | uncovered ~18% is the actual destructive `_on_execute` flow + a few error branches in the path-not-found dialog; s13 covers the destructive happy path. Spot-add a layer-2 test only if a destructive-flow bug surfaces that's hard to reproduce via the GUI. Lock at execute stage (#182, supersedes #175) pinned by `TestExecuteDialogLock` (single-row + regex flows: each verdict for mixed-locked, all-locked uses the dialog too) and `TestExecuteRequestedLockConfirm` (pre-execute scan: no locked-delete → fast path; APPLY_ALL_UNLOCKED unlocks then executes; APPLY_UNLOCKED_ONLY clears decision on locked + executes the rest; CANCEL aborts). Banner jump-to (#166) pinned at layer 1 by `TestBannerJumpTo`: anchor rendering, `_on_jump_to_group` selects the matching group row, invalid/unknown hrefs are no-ops; the QLabel HTML-anchor click itself isn't UIA-clickable, so s33 only verifies the banner renders the right group number — the click → scrollTo dispatch stays unit-tested. Numeric-condition routing (#209) is layer-1 via the unified `_matched_paths_for_pattern` helper — same regex/cmp/top-n branches reused by all three downstream routes; layer 3 (s43) verifies the Apply path actually mutates `user_decision` end-to-end through `batch_update_decisions`. Selection-scoped Execute (#211) pinned at layer 1 by `TestExecuteHighlightedRows`: the button label tracks selection state (default ↔ highlighted, group-header selections ignored), scoped iteration deletes only highlighted paths, the lock guard scopes WITH the selection (doesn't skip; doesn't broaden), and the complete-group confirm only fires when scope covers an entire delete-decision group. "Execute selected" partial-execute (#483) pinned at layer 1 by `TestOnExecutePartialFilter` (the `paths_filter` plumbing scopes the action pass without touching out-of-scope decisions, and partial execute keeps the dialog open) and at layer 3 by s64 (live wiring: real tree selection enables the button, real send2trash removes only the highlighted files, the dialog stays open, the un-highlighted decisions survive). |
| `app/views/dialogs/locked_rows_confirm_dialog.py` | 100% | s32 (bulk regex trigger), s34 (Execute trigger) | the dialog itself is data + button wiring; `TestLockedRowsConfirmDialog` pins body text shape (count + first-5-basenames + "…and N more"), button-state (Apply Unlocked Only disabled in the all-locked degenerate case), verdict per button click, Esc→Cancel, initial-state→Cancel. |
| `app/views/dialogs/singleton_prune_confirm_dialog.py` | (via test_file_operations) | s61 (#484 — actioned-singleton classification end-to-end). The dialog fires from `FileOperationsHandler._maybe_offer_singleton_prune` at the tail of a Remove-from-List that collapses groups to singletons. s61 builds a 2-cluster fixture where one collapsed singleton is PLAIN (remaining item undecided) and one is ACTIONED (remaining item carries an un-executed `delete`), and drives three verdicts: Remove without the opt-in box → only the plain singleton pruned, actioned kept with its decision; Remove WITH the box → both pruned; Keep all → nothing pruned. Requires `ui.prune_singletons="ask"`, which s61's configure step overrides from the qa default of `"never"` (see `PRUNE_OVERRIDE_SCENARIOS` in `qa/scenarios/_config.py`). | the per-bucket `PruneVerdict` dispatch + the three preference paths (`ask` / `always` / `never`) are pinned at layer 1 by `TestSingletonPruneOffer` (mocks `SingletonPruneConfirmDialog.ask`); the dialog's own three-layout body/checkbox logic (`to_prune_verdict`) is pinned by its layer-1 dialog tests. s61 is the live wiring: that real tree-row removal produces the mixed-bucket dialog with its opt-in checkbox and that each verdict yields the right manifest outcome. |
| `app/views/layout/layout_manager.py` | 86% | s01 (initial half-screen sizing + adjust-splitter on first manifest load), s39 (#136 splitter min-width floor) | low — the `setup_main_layout` constraints (`setChildrenCollapsible(False)` + `setMinimumWidth(200)` on each child) are pinned by `test_layout_manager_splitter.py`'s splitter-floor tests. Drift would be a removed line, not a behavioural change — visible immediately in CI. |
| `app/views/main_window.py` | 74% (#185) | every scenario constructs MainWindow as a real subprocess; #141 geometry round-trip is layer-3 via s39 (window_state.ini round-trip across launches); #214 column-layout round-trip is layer-3 via s47 (same window_state.ini, separate key); close-event dirty-prompt logic is layer-3 via s28; the #468 scan-running close guard (`closeEvent` surfaces a "Scan in progress" Yes/No box when `scan_running` is True) is layer-3 via s63 — best-effort, since today's modal `ScanDialog.exec()` may swallow the main-window close, so s63 records a documented soft-probe when the box doesn't surface (the flag is explicit defense-in-depth for a future non-modal dialog); #137 empty-state action buttons via s41 (the construction-time `build_empty_state_widget` call); relocalize round-trip via s22; auto-select KEEP rows via s49 (#239) | layer 1 added in #185: thin-proxy delegations + extracted-helper composition. Pattern: one real-construction test (catches `__init__` / `_setup_components` assembly reorders) + fake-self (`SimpleNamespace`) unbound-method tests for every thin proxy on MainWindow (menu actions, `_apply_action_by_regex`, `_on_image_loaded`, `_remove_from_list_toolbar`, `UIUpdaterImpl`, `TreeDataProviderImpl`). Each maps to a real failure mode in the #175 bridge-pattern-hole class: a rename of `file_operations.X` / `dialog_handler.X` / `tree_controller.X` that drops the call site here would silently dead-end a menu item. Auto-select-after-scan dispatch (#239) is pinned at L1 by `test_load_manifest_after_scan_selects_keeper_paths` (composes `extract_keeper_paths` + `_select_rows_by_paths`). Selected-row survival across language switch (#22 class) is pinned by `test_capture_relocalize_state_captures_first_selected_file_path` + `test_apply_relocalize_state_reselects_when_path_in_state` — the *business-logic* halves of relocalize state, distinct from the Qt window-state plumbing below. Uncovered ~26%: window-state persistence (#141, #214, #215) and close-event dirty-prompt logic stay layer-3 by design (s28, s39, s47, s48) — mocking QSettings to "cover" them would be metric gaming per CLAUDE.md; log-directory openers are uniform `os.startfile` delegation; and several defensive `except: pass` branches around `saveGeometry` / `restoreState` are unreachable from honest unit tests. |
| `app/views/main_window_helpers.py` | 100% (#185) | s22 (relocalize), s39 (geometry), s49 (#239 auto-select) | pure-logic extraction from `main_window.py`: model-walk helpers (`find_path_in_model`, `find_paths_in_model`), VM-side pickers (`extract_keeper_paths`, `extract_first_selected_file_path`), and the manifest-side `count_isolated_rows` SQL query. Extracted so the load-bearing logic stays unit-testable against plain Python / `QStandardItemModel` without cascade-importing the heavy view stack — same pattern as `action_handlers.py` (#182), `status_reporter_impl.py` (#138, #140), `empty_state.py` (#137). |
| `app/views/image_tasks.py` | 100% (#293) | s05 (single-image preview), s44 (highlighted-rows preview) | layer 1 added in #293: pure-logic token format extracted to `image_tasks_helpers.py`; the dispatch surface (`_ImageTask.run` service call + signal emit, `ImageTaskRunner.request_single_preview` / `request_grid_thumbnail` pool-start) is unit-tested by 20 tests in `tests/test_image_tasks.py`. The `# pragma: no cover - best effort` `except: pass` around the signal emit is the only uncovered defensive guard — testing it would require monkeypatching the Qt signal mechanism to raise, the exact "mock-the-world to bump coverage" padding CLAUDE.md rejects. |
| `app/views/image_tasks_helpers.py` | 100% (#293) | s05 / s44 | pure-logic extraction from `image_tasks.py`: the token format that bridges `ImageTaskRunner` (producer) and `classify_image_token` (consumer in `preview_pane_helpers.py`). Both ends must agree on the `"single|"` / `"grid|"` prefix or every in-flight image load silently drops. Two helpers (`make_single_token`, `make_grid_token`) + 7 tests pin the contract from the producer side. |
| `app/views/widgets/group_media_controller.py` | 76% (#185 / #284) | s11 (Live Photo synchronised playback — real QMediaPlayer per-OS backend) | layer 1 added in #185 / #284: helper extraction (`group_media_controller_helpers.py` below) + one real-construction test + fake-self thin-proxy tests for register/unregister/cleanup/toggle/slider/state-handlers. Same pattern as `main_window.py`. The register/unregister tests pin the 7-signal connect/disconnect contract — the #175 bridge-pattern-hole class (a refactor that adds an 8th broadcast signal but forgets one half would silently dead-end on every registered player). Uncovered ~24%: Qt signal-slot real-dispatch wiring (constructor connects + `setText`/`setRange` side-effects on widgets) — these execute during the construct test but their per-call assertions live at L3 via s11, where a real QMediaPlayer per-OS backend can fire actual position/state events. |
| `app/views/widgets/group_media_controller_helpers.py` | 100% (#185 / #284) | s11 (Live Photo synchronised playback) | pure-logic extraction from `group_media_controller.py`: majority-vote (`is_majority_playing`), max-duration tracker (`should_update_master_duration`), drag-vs-playback gate (`should_track_player_position`), ratio→position math (`compute_master_position`), mute-toggle target (`compute_mute_target_volume`), and glyph resolvers (`volume_icon_for_value`, `play_button_icon_for_state`). Extracted so the load-bearing decision logic stays unit-testable against plain Python without cascade-importing the Qt media stack. |
| `app/views/widgets/video_player.py` | 87% (#293) | s11 (Live Photo synchronised playback — real `QMediaPlayer` per-OS backend, real position / state events) | layer 1 added in #293: pure-logic extraction (`video_player_helpers.py` below) + one real-construction test + fake-self thin-proxy tests for every dispatch method, signal handler, and public API surface. The construct test catches `__init__` / `_setup_ui` assembly reorders (a refactor leaving `self._audio_output` un-set before `setMuted` is called would crash on first user interaction — invisible to L3 because every video scenario hits it identically). The `_on_duration_changed` early-arrival guard (signal fires synchronously during `setSource` on some platforms, before `_setup_ui` runs) is pinned by `test_handles_early_signal_before_slider_constructed`. Uncovered ~13%: `_video_load_error` UI rendering branch (only fires when `QUrl` parsing throws, which production paths don't reach) and `cleanup`'s `RuntimeError` swallow chains (defensive against post-deletion calls; testing each branch would require monkeypatching Qt's deletion mechanism). |
| `app/views/widgets/video_player_helpers.py` | 100% (#293) | s11 | pure-logic extraction from `video_player.py`: URL-routing decision (`should_use_file_protocol`), play/volume button glyph resolvers (`play_button_glyph` / `volume_button_glyph`), and the volume scale ↔ slider position pair (`volume_float_to_slider_int` / `volume_int_to_float`). 5 helpers + 23 tests in `tests/test_video_player_helpers.py`. Same extraction pattern as the sibling helpers modules. |
| `app/views/preview_pane_helpers.py` | 99% (#185 — final PR) | s01 (single preview), s05 (huge preview), s11 (video live), s48 (preview-pane geometry round-trip) | pure-logic extraction from `preview_pane.py`: HTML info-table formatter (`format_info_html`), info-row builder (`build_info_rows`), aspect-bucket classifier (`aspect_bucket_from_resolution`), resolution-string formatter (`format_resolution_string`), grid-geometry packer (`compute_grid_geometry`), fit-to-window math (`compute_fit_width`), image-token router (`classify_image_token`), file-size accessor (`get_file_size_bytes`), grid-item normaliser (`normalize_grid_items`), and resolution-attachment loop (`attach_resolutions`). 10 helpers; 35 helper tests + 25 PreviewPane fake-self / construct tests in `tests/test_preview_pane.py`. Same extraction pattern as `action_handlers.py` (#182), `status_reporter_impl.py` (#138, #140), `empty_state.py` (#137), `main_window_helpers.py` (#185 / #283), and `group_media_controller_helpers.py` (#185 / #285). |
| `app/views/preview_pane.py` | **omit** | s01 (single preview), s05 (huge preview), s11 (video live), s48 (preview-pane geometry round-trip) | the testable surface is extracted to `preview_pane_helpers.py` (above, 99%) + 25 fake-self dispatch tests pinning the load-bearing contracts (token-mismatch race in `on_image_loaded`, state-reset in `clear`, cleanup contract in `release_file_handles`, autoplay sequencing, already-playing guard in `_on_video_tile_clicked`, fit-to-window routing, grid-geometry routing). The remaining ~330 stmts are genuine Qt-widget assembly (`show_grid` builds `QGridLayout` + per-tile `QLabel`s + click handlers; `resizeEvent` walks every tile to reassign sizes; `_on_video_tile_clicked` instantiates a real `VideoPlayerWidget`) that can't be unit-tested without mocking `QGridLayout` / `QLabel` / `VideoPlayerWidget` — the exact "mock-the-world to bump coverage" padding CLAUDE.md rejects. The owner's 2026-05-16 comment on #185 explicitly flagged this file as needing the genuine-vs-padding discipline; the testable-pure-logic extraction landed that bar. L3 scenarios s01 (selection-driven single preview), s05 (huge preview fit-on-width), s11 (video lifecycle), s48 (geometry round-trip) cover the Qt-widget surface. |
| `app/views/window_state.py` | 100% | s39 (main-window geometry round-trip across launches); s47 (#214 — column-header state round-trip across launches, same INI); s48 (#215 — three resizable dialogs round-trip across close-and-reopen within one session) | none — the QSettings INI path + off-screen guard + save/restore helpers shared by MainWindow and the three resizable dialogs (#215). Extracted from `main_window.py` so dialogs don't import the QMainWindow assembly (would be a circular import via `DialogHandler`); the off-screen guard (multi-monitor disconnect fallback) is pinned at layer 1 by `tests/test_window_state.py::TestIsRectVisibleOnAnyScreen`. |
| `app/views/dialogs/select_dialog.py` | 82% | s14 (Regex menu route), s29 (Regex remove-from-list), s30 (Regex right-click from Execute), s31 (Phase B/C Simple mode + regex-sync round-trip), s43 (#209 numeric-condition panel — threshold mode end-to-end via Execute Action route), s48 (#215 — preview-pane layout geometry persists across close-and-reopen; flat layout deliberately skips the save), s50 (#237 — numeric panel reachable from the main-window menu route — sister to s43; #238 — switches to Resolution via expand → End → Enter and asserts the panel toggles back to regex, exercising the new Resolution wiring end-to-end). The dropdown-completeness invariant for #238's added fields is pinned at layer 1 by `test_probe_select_dialog_exposes_every_filterable_tree_column`. | dropped from Phase A's 95% because the file grew through Phase B + Phase C (Simple/Regex toggle, cheatsheet, recent patterns, match-highlight delegate, `_try_parse_simple` reverse-parse) and again with #209 (numeric panel, threshold + Top-N within group, ISO-date threshold parse, pattern-encoding helpers). Layer-1 covers `TestSimpleMode`, `TestCheatsheet`, `TestRecentPatterns`, `TestMatchHighlightDelegate`, `TestTryParseSimple`, `TestRegexSyncAcrossModes`, `TestLegacyModeKeyAlias`, and the new (#209) `TestNumericPanelVisibility`, `TestThresholdEmit`, `TestTopNEmit`, `TestThresholdSelectionLogic`, `TestTopNSelectionLogic`, `TestPatternEncoding`. Uncovered ~18% is mostly `_MatchHighlightDelegate.paint` segments that only fire when an actual painter+option pair is supplied (covered by qa-explore visual paths) plus a few defensive try/except branches in the Recent menu and settings I/O. Action combo offers 5 options (delete / keep / remove / lock / unlock) — pinned by `test_action_combo_count_matches_settable_decisions_with_remove_and_lock` and `test_action_combo_includes_lock_and_unlock_options` (#164). |

### Top-level scripts

| Module | Status | Where it's covered |
|---|---|---|
| `main.py` | **omit** | qa-explore launches it as a real subprocess for every scenario |
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

Registry-completeness invariants pinned by
`tests/test_all_scenarios_registered.py` (close the gap surfaced
post-#211: `test_batch_shard.py` assumes `ALL_SCENARIOS` is correct;
nothing previously checked the input itself):

- Every `qa/scenarios/sNN_*.py` on disk is in
  `ALL_SCENARIOS`. Catches the headline failure mode where a new
  driver lands but the registration is forgotten — without this guard,
  CI silently skips the new scenario and the only thing that ever
  runs is the layer-1 unit tests.
- Every `ALL_SCENARIOS` entry has a real file (catches rename/delete
  drift).
- Every entry is also keyed in `_config.py::SCENARIO_SOURCES`
  (otherwise `configure.py` would fail at launch time, not in
  pytest output).
- No stale `SCENARIO_SOURCES` keys outlive a removed scenario.

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

## Probe inventory

`tests/test_ui_probes.py` (static, AST/YAML) + soft probes inside
scenario drivers. Each row names the invariant, the bug it catches
today (XFAIL) or its forward-defensive role (PASS), and where the
soft-probe upgrade path lives if applicable.

| Probe | Invariant | Today | Catches |
|---|---|---|---|
| `test_probe_select_dialog_exposes_every_filterable_tree_column` | Every filterable tree column appears in the Select dialog's field dropdown | PASS | Forward-defensive against [#238](https://github.com/jackal998/photo-manager/issues/238) recurring |
| `test_probe_action_dialog_receives_groups_from_main_window_callsite` | Main-window callsite of `ActionDialog` passes `groups=` so the numeric panel can show | PASS | Forward-defensive against [#237](https://github.com/jackal998/photo-manager/issues/237) recurring |
| `test_probe_similarity_column_emits_at_most_one_ref_per_group` | At most one row per group renders as "Ref"; siblings fall back to similarity % or "—" sentinel | PASS | Forward-defensive against [#241](https://github.com/jackal998/photo-manager/issues/241) recurring |
| `test_probe_no_execute_mode_toggle_in_menu` | `menu_controller.py` no longer registers an `execute_mode` action | PASS | Forward-defensive against [#240](https://github.com/jackal998/photo-manager/issues/240) recurring |
| `test_probe_action_handlers_impl_proxies_every_protocol_method` | Every method on `ActionHandlers` Protocol exists on `ActionHandlersImpl` | PASS | Future #175/#182-class bridge regression |
| `test_probe_manifest_dependent_menu_actions_are_gated` | Every menu action that requires a loaded manifest is in `MANIFEST_ACTIONS` | PASS | Forward-defensive against [#244](https://github.com/jackal998/photo-manager/issues/244) recurring |
| `test_probe_zh_tw_translations_are_not_english_passthroughs` | zh_TW values that match en values must contain CJK chars (heuristic; tiny exempt list for product names) | PASS | Forward-defensive against [#245](https://github.com/jackal998/photo-manager/issues/245) recurring |
| `test_probe_destructive_surface_inventory` | No destructive handler (Execute, set_decision, remove_from_list, show_action_dialog) is reachable from 2+ user-facing surfaces unless allowlisted in `_INTENTIONAL_DUPLICATE_SURFACES` with a written justification | PASS | Forward-defensive against [#240](https://github.com/jackal998/photo-manager/issues/240) recurring — generalised version of `test_probe_no_execute_mode_toggle_in_menu`; catches the next "two paths to one destructive surface" pattern without anyone knowing in advance which menu key to grep for. See [#302](https://github.com/jackal998/photo-manager/issues/302). |
| `s49` `step: verify_visual_selection_of_keeper` (hard live) | After scan-complete with auto-select on, the tree's selection model contains the keeper rows | PASS | Forward-defensive against [#239](https://github.com/jackal998/photo-manager/issues/239) recurring |
| `qa/probes/field_dropdown_inventory.py` (live exploration) | Result-tree column headers == Set-Action-by-Field/Regex dialog field dropdown items | PASS | Forward-defensive against [#238](https://github.com/jackal998/photo-manager/issues/238) recurring at the runtime UIA layer (the layer-1 probe pins source-level invariant; this probe verifies the running app actually exposes the dropdown the user sees) |
| `qa/probes/group_label_audit.py` (live exploration) | At most one row per group renders "Ref" in the rendered tree; no row carries both "Ref" and a "delete" decision | PASS | Forward-defensive against [#241](https://github.com/jackal998/photo-manager/issues/241) recurring at the rendered tree layer (catches drift between `build_model`'s invariant and the QSortFilterProxyModel + delegate stack the user actually sees) |

When the corresponding bug lands, the static probes flip XFAIL→XPASS-strict
and the bug-fix PR removes the marker. The soft probe is converted from
`print(probe_status: …)` to `failures.append(…)` per the comment block in
the scenario; the **"Detect probes ready for promotion"** step in
[`.github/workflows/qa-batch.yml`](../.github/workflows/qa-batch.yml) greps
`qa-batch.log` for `probe_status: PASS` and fails the job if any are
found — same forcing-function as `xfail(strict=True)` for the static
probes, so a bug-fix PR cannot merge while leaving a soft probe in its
print-only state.

Live exploration probes (`qa/probes/`) are **local-run only** at v1:
no CI wiring, no batch runner, no shard split. Run them manually
after changes that affect the relevant surface
(`python -m qa.probes.field_dropdown_inventory`,
`python -m qa.probes.group_label_audit`) and during qa-explore
sessions. Wiring into CI is deferred until the probe count grows
enough to justify a batch runner — see [#243](https://github.com/jackal998/photo-manager/issues/243)
and its follow-up issues.

---

## Probe layer — authoring a new probe

### When to add a probe

**Rule of thumb:** the bug class bit once and could plausibly recur across a different surface.

A probe targets a *structural invariant* — a relationship between two parts of the codebase that must stay in sync. If you can describe the bug as "A grew without updating B" or "callsite X stopped passing argument Y," that's a probe candidate, not a unit test.

Each of the 7 existing probes owns a different drift class:

| Pattern | Example bug |
|---|---|
| Field list grows without updating a dropdown | [#238](https://github.com/jackal998/photo-manager/issues/238) — Score / Lock / Resolution missing from Select dialog |
| Callsite drops a required keyword argument | [#237](https://github.com/jackal998/photo-manager/issues/237) — `groups=` dropped, numeric panel hidden |
| Within-group labeling emits more than one "Ref" | [#241](https://github.com/jackal998/photo-manager/issues/241) — Live Photo HEIC + MOV both labeled Ref |
| Menu action added without gating on manifest-loaded | [#244](https://github.com/jackal998/photo-manager/issues/244) — `action_by_regex` enabled before manifest opens |
| Bridge proxy not updated to match Protocol | [#175](https://github.com/jackal998/photo-manager/issues/175) / [#182](https://github.com/jackal998/photo-manager/issues/182) — menu item silently no-ops |
| Translation key copy-pasted from en.yml | [#245](https://github.com/jackal998/photo-manager/issues/245) — zh_TW value is English passthrough |
| Removed UI option reintroduced by accident | [#240](https://github.com/jackal998/photo-manager/issues/240) — Execute Mode toggle removed but probe guards the absence |

If the bug fits this pattern: file the issue, then add a probe before or in the same PR as the fix. The probe lets the fix prove it works and the probe lives on as a forward-defensive guard.

---

### Probe flavours — pick one

Three flavours exist. Pick the first one that reaches the invariant.

**1 — Static probe** (`tests/test_ui_probes.py`)

For source-level invariants you can verify by reading the AST or YAML.
Runs as `pytest` in CI on every commit. Fastest, most reliable.

When to use: callsite passes the right kwargs, list A ⊆ list B, string absent from file, class method set ⊇ Protocol method set, constant declared with expected members.

**2 — Soft live probe** (extension block inside `qa/scenarios/sNN_*.py`)

For runtime state only reachable via UIA — rendered selection, visible dropdown items, widget text after a live operation.
Piggybacks on an existing scenario's app setup. Runs in the qa-batch CI shards.

When to use: the invariant requires the app to be running and a UIA query to observe.

**3 — Live exploration probe** (`qa/probes/<name>.py`)

For invariants that scripted scenarios architecturally can't cover
(e.g. tree column headers ↔ dialog dropdown diff, per-group label-count audit).
Self-contained: launch → load fixture → inspect → exit non-zero on FAIL.
Local run only — no CI wiring at v1.

When to use: the invariant needs a live app but doesn't fit naturally into any existing scenario.

---

### Static probe — copyable skeleton

```python
@pytest.mark.xfail(strict=True, reason="Bug #NNN — remove marker when fix lands")
def test_probe_<invariant_name>():
    """One sentence: what structural relationship this probe checks.

    Forward-defensive against #NNN recurring: describe what drifts and
    what surface would break silently if left uncaught.
    """
    # Prefer AST text-parse over importing the module — see
    # "Coverage-cascade warning" below.
    src_path = REPO / "app" / "path" / "to_target.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    collected: set[str] = set()
    for node in ast.walk(tree):
        # Walk ast.Assign AND ast.AnnAssign — see "Common pitfalls".
        target_id: str | None = None
        value_node = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_id = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_id = node.target.id
            value_node = node.value
        if target_id != "MY_CONSTANT" or not isinstance(value_node, ast.Tuple):
            continue
        for elt in value_node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                collected.add(elt.value)

    assert collected, (
        "Probe could not locate MY_CONSTANT in "
        f"{src_path}. Did the declaration form or name change? "
        "Update the AST walker to match."
    )

    _REQUIRED = {"entry_a", "entry_b"}
    missing = _REQUIRED - collected
    assert not missing, (
        f"MY_CONSTANT is missing entries: {sorted(missing)}. "
        f"See #NNN."
    )
```

**Lifecycle:** when the fix lands, `strict=True` turns XPASS → CI red, forcing the PR to remove the `@pytest.mark.xfail` decorator. After the decorator is gone the probe lives on as a forward-defensive guard.

---

### Soft live probe — copyable skeleton

Inside an existing `qa/scenarios/sNN_*.py`, immediately after the setup step whose state you want to inspect:

```python
# ---------- Probe #NNN: <one-line invariant description> ----------
# Currently XFAIL: <what is broken today and why>.
# Promote when fixed: swap the two commented lines below (print → failures.append).
# The qa-batch "Detect probes ready for promotion" step greps qa-batch.log
# for "probe_status: PASS" and fails the job — so this probe cannot survive
# a merge after the target bug is closed.
print("step: probe_nnn_<invariant_name>")
observed = _some_uia_call(win)
if not _invariant_holds(observed):
    print(f"probe_status: XFAIL_KNOWN_BUG_NNN — {observed!r}")
    # failures.append(f"#NNN invariant violated: {observed!r}")  # ← promote to this
else:
    print(f"probe_status: PASS — {observed!r}")
    # ^ qa-batch greps for this line and fails the job when the bug is fixed.
# ---------- end probe #NNN ----------
```

Once promoted (bug fixed, PR merges), collapse the entire block to the single `failures.append(...)` inside the regular assertion block.

---

### Forcing-function design

Two mechanisms ensure probes never stagnate silently:

**Static probes — `xfail(strict=True)`.**
The moment the bug is fixed the probe emits XPASS. With `strict=True`, XPASS is a CI failure. The bug-fix PR cannot merge until the `@pytest.mark.xfail` decorator is removed.

**Soft live probes — the "Detect probes ready for promotion" step.**
The `qa-batch.yml` workflow greps `qa-batch.log` for `probe_status: PASS` after every shard. If any match is found, the step fails with: *"A soft probe is now passing — promote it to a hard assertion or delete the probe block."* A probe stuck in its `print()`-only state cannot survive a merge after the bug is fixed.

Both paths produce a CI-red moment that forces the probe lifecycle forward:

```
Active (bug open)  →  Triggered (bug fixed, probe not promoted)  →  Promoted (permanent guard)
   XFAIL / print         CI red                                       hard assertion
```

---

### Coverage-cascade warning

Static probes should **not** import the module they inspect unless that module is already in coverage measurement. Importing `dialog_handler.py` from a test pulls 5 heavy GUI files into coverage and tanks the 80% gate — see memory entry `feedback_test_import_cascade`.

**Default path:** parse as text via `ast.parse(path.read_text())`. This lets you inspect callsites, constant declarations, and method lists without touching the import graph.

**Exception:** if the module is already measured (check `[tool.coverage.run] omit` in `pyproject.toml`), importing it directly is fine — simpler and less fragile than re-parsing AST. Example: after #293 moved `dialog_handler_helpers.py` out of omit, probe #238 switched from AST to a direct import.

---

### Common pitfalls

#### `ast.AnnAssign` vs `ast.Assign`

Both constant declaration forms exist in this codebase:

```python
MANIFEST_ACTIONS = ("save_manifest", "execute_action")     # ast.Assign
MANIFEST_ACTIONS: tuple[str, ...] = ("save_manifest", ...)  # ast.AnnAssign
```

If your AST walker only handles `ast.Assign`, it silently sees an empty set and the probe "passes" for the wrong reason — the exact failure mode that kept probe #244 green on a broken codebase until #248 caught it. Always walk both forms. The skeleton above includes both.

#### Pywinauto UIA: `ComboBox.select()` and Qt's `maxVisibleItems=10`

Qt's `QComboBox` limits popup visibility to 10 items by default. Pywinauto's `ComboBox.select("Item Name")` only reaches items in the visible viewport — items 11+ are inaccessible via the standard call.

Fix: use the `ItemContainer` pattern to walk the full virtualised list. See the field-selection helper in `qa/scenarios/s50_*.py` for the implementation.

#### Re-find UIA widgets after panel toggles

After clicking a control that shows or hides a panel (e.g. switching between the regex panel and the numeric panel in the Select dialog), the UIA wrapper you held before the toggle is stale. `pywinauto` caches visibility on the original wrapper; after the toggle the old wrapper still reports `is_visible() == True`. Any interaction with it either silently succeeds against the hidden widget or raises a confusing error.

Fix: always re-find the target control from the window root after any show/hide trigger. Never reuse a wrapper across panel state changes — see the #251 post-mortem.

#### Exempt-list pattern for translation probes

The translation probe (`test_probe_zh_tw_translations_are_not_english_passthroughs`) uses the heuristic: `zh_value == en_value` AND contains Latin letters AND no CJK characters → flag as untranslated. Some strings are legitimately identical in both locales — product names, version format strings.

Pattern: keep a `_TRANSLATION_EXEMPT_KEYS: frozenset[str]` set in `test_ui_probes.py`. Any new entry must carry a one-line reason in the PR description (e.g. "brand name", "technical term"). Don't add entries to silence a false positive without verifying the string genuinely doesn't need translation.

---

## Open work

- **Layer 2 is on-demand**, not on the roadmap. Add a spot-test under
  `tests/integration/` (with `@pytest.mark.integration` and a
  `skip-if-binary-missing` guard) the first time a specific boundary
  bug surfaces. Don't pre-build the suite. The boundaries we touch
  (`exiftool` / `send2trash` / `rawpy` / `pillow-heif`) are stable
  enough that proactive coverage would mostly duplicate layer 3.
- **Layer-3 hardening.** [#80](https://github.com/jackal998/photo-manager/issues/80) closed: scenarios for Save Manifest (s12), Execute Action (s13, destructive), Set Action by Field (s14), and right-click context-menu decisions (s15) all merged. Each driver now also calls cross-scenario probes from `qa/scenarios/_invariants.py` (status-bar shape, manifest-actions toggle consistency, destructive-confirm shape) — no maintained extra suite, just lines added inside the existing drivers.
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
