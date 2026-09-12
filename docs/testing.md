# Testing strategy

Three layers, each catching a different class of bugs. CI covers two of
them; layer 2 runs locally, on demand. This document is the canonical
answer to "what's covered, what's not, why, and what's the residual
risk."

---

## The three layers

| Layer | What it catches | Where it runs | Status |
|---|---|---|---|
| **1 — Unit** (mocks + pure logic) | Refactoring bugs, contract violations, dispatch errors, parser logic | CI (`pytest`) on every commit + local | Solid (~500 tests) |
| **2 — Integration** (real `exiftool`, real `send2trash`, real `rawpy`/`pillow-heif` decoders) | Boundary error modes that are hard to reproduce through the UI | Local only (skip when binaries absent); not on `windows-latest` | **On-demand. No maintained suite — boundaries are covered at the happy-path level by layer 3 with real fixtures.** Add a layer-2 spot-test reactively when a specific bug surfaces. |
| **3 — QA / E2E** (the real app) | Label drift, state-transition bugs, layout regressions, end-user flow failures | `web-eval-gates.yml` on **every** PR (Playwright `qa/web/scenarios/`, plus web-dom-probe / qa-parity-counter / bench-sanity); locally `python -m qa.web._batch` | **BLOCKING** since [PR #817](https://github.com/jackal998/photo-manager/pull/817) ([#773](https://github.com/jackal998/photo-manager/issues/773)); 71/71 scenarios per `check_qa_parity.py --phase 4` — this is what guarantees the shared backend (`core/`, `scanner/`, `infrastructure/`) is exercised end-to-end on every PR |

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
  through the UI go here.
- *Misses:* anything you haven't written a spot-test for. By design.

**Layer 3 — QA scenarios** (`/qa-explore`)
- *Catches:* The button text changed. The menu item is no longer
  greyed out pre-manifest. The status bar shows the wrong count. The
  Ref row is at the bottom of its group.
- *Misses:* Anything not exercised by the scripted scenario path.

**Probes** (`tests/test_source_probes.py` + `tests/test_web_dom_probes.py`
+ sNN soft-probe blocks)
- Cross-cutting structural invariants that *every* layer above misses
  by design — added in [#243](https://github.com/jackal998/photo-manager/issues/243).
- *Catches:* "Did the dialog dropdown drift from the tree columns?",
  "Did a testid in `frontend/src/testids.ts` drift from the driver
  constant that looks it up?", "Did a translation ship as an English
  passthrough?", "Are two equivalent menu paths reaching the same
  destructive surface?".
- *Why a separate layer:* scenario drivers replay one canonical path
  each. Probes inspect a structural relationship — a single probe
  catches a whole class of drift across many surfaces.
- *Two flavours:*
  - Static probes (`tests/test_web_dom_probes.py`) — AST / TS / YAML
    inspection, run as pytest in CI. Use `@pytest.mark.xfail(strict=True)`
    so CI tolerates known-bug probes today and flips red the moment
    the fix lands without removing the marker.
  - Live soft-probes (extension blocks in `qa/web/scenarios/sNN_*.py`) —
    DOM inspection for runtime state, piggy-backing on an existing
    scenario's setup. Use a `print("probe_status: …")` pattern
    instead of `failures.append` so the batch stays green until the
    bug is fixed; the comment block documents the one-line upgrade to
    a hard failure.

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
| `scanner/exif.py` | 97% (uses `-j -G` JSON output; mocks emit realistic JSON shape via `_make_mock_et`. Records bind to paths by `SourceFile` identity, not position — drift bug class structurally impossible. Static fixtures in `tests/fixtures/exiftool_outputs/` snapshot real exiftool JSON output for the edge-case + mixed-format batches. PR 2 of #187 added `batch_read_extracts` for the scoring-signal census — GPS, XMP-DerivedFrom, EXIF/QuickTime census tags (`XMP:Rating` is queried as a census-completeness tag only; #786 dropped its dead value field — parsed since #187, never consumed) — same `-j -G` shape, `-fast` dropped because GPS/XMP live past the first IFD. #786 also added `extract_pil_scoring_signals` — the in-memory JPEG-only equivalent of the same signals, ported from the hasher's already-open PIL Image (no second file-open). Date fallback is the EXACT `_JSON_DATE_KEYS` EXIF-portion precedence: EXIF:DateTimeOriginal -> XMP:DateTimeOriginal -> EXIF:CreateDate -> XMP:CreateDate (a fresh-context review caught a HIGH: the first cut only checked the two EXIF tags and silently dropped the date for any JPEG whose date lives only in XMP; fixed same-PR with `_xmp_tag_value`/`_xmp_date_string` converting XMP's ISO-8601 dates to the EXIF colon format before reusing `parse_exif_date`). Layer 1: `TestExtractPilScoringSignalsDateChain/Gps/Census`, `TestXmpHasLocalName`, `TestXmpTagValueAndDateString`, `TestInMemoryJpegWiring` in `tests/test_scanner_exif.py` (synthetic PIL images, no exiftool). Layer 2: `tests/integration/test_scanner_exif_parity.py` (`@pytest.mark.integration`, skip-if-missing exiftool) proves field-for-field parity (now including `exif_date_tag`) against REAL exiftool on every `qa/sandbox/exif-edge/*.jpg` + `scoring-mixed/*.jpg` fixture plus three test-time-generated files (full 14-tag census, real Lightroom-style `xmpMM:DerivedFrom`, XMP-only date with no EXIF date tags — the regression fixture for the HIGH above).) | spot-add only | s01, s04, s06, s08, s42 (real exiftool, happy path + scoring signals) | exiftool protocol drift between versions. Static fixtures will fail loudly if the JSON shape changes — that's the early-warning. The uncovered lines are a defensive `except` in the stderr drain thread (race during process close). Residual risk on the in-memory JPEG path: the XMP parity fixtures are synthetic (PIL-saved packets, not a real Lightroom/Photoshop export) — a real derivative file's XMP byte-level shape (different quoting/whitespace/nesting) could still surprise `_xmp_has_local_name`, though its XML-parse-first-then-substring-fallback design is deliberately tolerant of shape variation. HEIC stays on exiftool — pillow-heif exposes no `"xmp"` info key for any real fixture in this repo's corpus, so xmp_derived parity for HEIC couldn't be verified against real data (see `hasher._INMEMORY_EXIF_TYPES`). #820 added `SubSecTimeOriginal` / `OffsetTimeOriginal` on BOTH paths (exiftool selectors + PIL tag ids `0x9291` / `0x9011`), pinned at layer 1 by `TestSubsecExiftoolPath` and `TestSubsecPilBypassPath`. No layer shifted. The load-bearing case is a **type** one, measured rather than assumed: exiftool's `-j` returns SubSec as a JSON *int* for 728 of 824 real dated NAS files and as a *str* only for the 96 carrying a leading zero, so `_subsec_text` coerces with `str()` — an `isinstance(..., str)` guard would silently drop 88 % of them, and the leading zero is significant (`"05"` is 50 ms). Residual risk: real-exiftool parity for these two tags is not in the layer-2 parity fixture, because no `qa/sandbox/` file carries them; the evidence is the live NAS probe recorded in the #820 PR, not a checked-in fixture. |
| `scanner/media_extract.py` | 100% | — | s42 (via the scan pipeline) | very low — canonical extraction schema for #187. Three-state sentinel convention (`None`=not attempted, `False`=checked-absent, `True`/value=present) is the load-bearing contract; pinned by tests in `tests/test_media_extract.py` and `test_batch_read_extracts_gps_present_false_when_no_gps_tags`. #786 removed the dead, unit-tested-only `merge_extracts()` combinator (its exif_date precedence contradicted the pipeline's real precedence — PIL wins, exiftool/in-memory-JPEG only backfills `None`, see `scanner/dedup.py::HashResult.to_media_extract` and `core/app_service/scan_runner.py`'s post-hash EXIF backfill); each producer now writes one complete extract per file into the pipeline's `extracts` dict, so there is nothing left to merge. |
| `scanner/hasher.py` | 80% | spot-add only | s06, s07, s11 (real fixtures, happy path) | uncovered tail (~20%) is rawpy / HEIC fallback paths only reachable with real raw files. Layer 3 covers the formats we ship fixtures for; spot-add a layer-2 test only if a real-world RAW format misbehaves. #822/#826 added `TestExifOrientationBeforeHashing` (EXIF-Orientation fixtures written with PIL — the rotated/upright pair hashes to distance 0), `TestRawSingleLibRawOpen` (the LibRaw **open count** and the preview's working resolution, observed against a fake rawpy handle because a real ProRAW DNG is 124–129 MB and ships in no fixture set), and `TestHashRecipeParity` (an Orientation-1 JPEG's phash/dhash/mean-color pinned to their pre-#822 hex, so any later recipe drift is loud). The RAW *decode* itself is still real-file-only. |
| `scanner/dedup.py` | 95% | — | s01, s07, s10, s11, s42, s65 | low — pure logic, well-covered. Internal `rows` and `path_to_hr` dicts are str-keyed (not `Path`-keyed) so genuinely-distinct files differing only by filename case (case-sensitive NTFS dirs; rare) survive — see #170. `TestCaseSensitiveCollision` pins this. Live Photo HEIC+MOV/MP4 pairs always share a group_id via pair edges fed into union-find — `TestLivePhotoPair` covers the unique-pair-forms-group case (#88 headline regression). `HashResult.to_media_extract()` (PR 2 of #187) is pinned by `TestHashResultToMediaExtract`; #786 extended it to layer in `HashResult.inmemory_signals` (JPEG's in-memory scoring signals) when present, pinned by `TestInMemoryJpegWiring` (both in `tests/test_scanner_exif.py`). #517 — the multi-hash confidence vote (`_dhash_confidence`: a pHash near-dup match is flagged `match_confidence="high"` only when an independent dHash also agrees; SHA-exact is always high) is pinned by `TestMatchConfidence`; the auto-select gate that drops `"low"`-confidence rows from the aggressive delete set lives in `core/services/auto_select.py::non_keepers_for_aggressive_delete` and is pinned by `TestNonKeepersForAggressiveDelete` in `tests/test_auto_select.py`. #526 — near-dup candidate generation uses a hand-rolled `_BKTree` (Hamming via int popcount) above `_BKTREE_MIN_CANDIDATES`, replacing the O(N²) all-pairs enumeration; `TestBKTreeStructure` pins the tree against a naive popcount scan and `TestBKTreeParity` proves the brute and BK paths yield bit-identical `classify()` output across thresholds, dHash gates, and exact-SHA interplay. The crossover benchmark is `scripts/bench_grouping.py` (dev tool, coverage-omitted). #526 PR2 — `classify(bktree_min_candidates=…)` threads a per-machine crossover floor (measured by the #486 calibration) down to `_near_dup_neighbors`; `TestBKTreeParity::test_parity_via_classify_param` pins that the public param selects the strategy without touching the module global and both forced strategies match the default. `GROUPING_STRATEGY_VERSION` is a cache-keying token folded into the scan-worker fingerprint. #538 — `_classify_near_duplicates` decouples edge collection from classification so a genuine near-dup whose only bridge is already classified is no longer orphaned (true transitive closure); pinned at layer 1 by `TestUnderGroupingFix` (orphan-now-grouped + determinism under input shuffle) and at layer 3 by **`s65_passenger_bridge`** (#544 — three real burst frames form a both-pHash+dHash bridge that yields a 2-Ref-tier "passenger" group). #824 — the exact tier's two no-decision branches (RAW+lossy complementary, all-RAW) now emit `_complementary_edges` instead of returning nothing, so a RAW original and a *renamed* lossy export share a group_id while both stay `action=""`; pinned by `TestComplementaryGroupEdge`, which covers the headline pair, the all-RAW bucket, order-independence of the lex-min anchor, the unchanged all-lossy path, the #536 no-auto-delete guarantee, and both false-positive halves (mean-color #462 and dHash #524 still refuse the edge). No new layer-3 driver: the resulting group renders through the existing Ref + `N*%` passenger path that same-stem RAW+JPG pairs already exercise in s65. |
| `scanner/scoring.py` | 98% | — | s42 (scan + verify score pipeline end-to-end on a near-duplicate group) | very low — pure scorer for #187. Two-tier composite (Tier 1 format/derived penalties + Tier 2 eight weighted continuous signals); every dimension + Tier 1 + composite clamping + Live Photo MOV passenger rule + tie-break + `validate_weights` pinned by `tests/test_scoring.py` (75 cases). `apply_scoring_to_rows` (PR 4) and `ManifestRepository.rescore` (PR 4, lives in `infrastructure/`) are also pinned at layer 1 including the round-trip through SQLite. The 2 uncovered lines are defensive guards against malformed `group_rows` inputs that production callers never produce. |
| `scanner/autotune.py` | 100% | `tests/integration/test_autotune_ab.py` (GATE-2 no-regression A/B, `@pytest.mark.integration` skip-if; run locally) | `s66` + dev-rig manual checkpoint | Pure read-knee detection logic for the #551 in-pipeline ramp (`knee_from_throughput` over a `{concurrency: files/s}` map; `ReadKneeRamp` ladder state machine — acquire-time level tagging, fill-transient discard, files/s accounting, cached `knee` vs live `current_permits`). Covered at layer 1 by `tests/test_autotune.py`: knee curves (plateau→2, rising→cap, HDD→1, noisy-stable, flat-ties-small), fail-open (empty / single-rung / None / zero-rate / non-doubling gap), and the `ReadKneeRamp` state machine (ladder clamp, zero-byte skip, min-seconds gate, **completion-order invariance** (#551 F1), **fill-transient discard** (#551 F7), drained-level ignore, equal-timestamp no-divide-by-zero). No `# pragma: no cover` — the whole module is pure (no toolkit/Win32/I/O). The in-pipeline ramp wiring lands in #551 Phase 2 (`core/app_service/scan_runner.py`, real-I/O sampling hook behind `# pragma: no cover`); the determinism qa scenario `s66` (#551 Phase 3) drives the Scan-dialog autotune control and asserts autotune still yields the known-correct grouping (the real ramp only engages above the `_RAMP_MIN_SCAN_FILES` gate, so qa-sized fixtures fall open to static). #551 Phase 4 flipped the feature to **default-ON** (opt-out), raised the short-scan floor 256 → 1584 (the conservative N=8 bound on the first-scan sub-MAX read tax) and bumped `AUTOTUNE_RECIPE_VERSION` → "2" (clean cache invalidation). The flip is gated by reachable evidence rather than the unobtainable real mis-fit NAS: **GATE-1** (`tests/test_scan_pipeline.py::TestPipelineReadKneeRamp` — the REAL `ReadKneeRamp`, run through the gated reader pipeline with a synthetic in-flight latency cliff, detects the knee; two distinct cliffs so "always returns 2" can't pass) and **GATE-2** (`tests/integration/test_autotune_ab.py` — median-of-5 OFF-vs-warm-ON A/B asserting `median(ON) ≤ median(OFF) × 1.10`; observed ON/OFF ≈ 0.69 on the synthetic cliff). GATE-2 bounds the *algorithm's* overhead on an idealised cliff, not real SMB/wire contention — the actual NAS-knee≈2 / HDD-knee=1 measurement is the dev-rig manual checkpoint. The orphaned #577 `OccupancyProbe` shell + its `PHOTO_MANAGER_AUTOTUNE_PROBE` env-gated wiring were deleted in #579. |
| `scanner/walker.py` | 95% | — | s09 | very low — symlink + flat-mode branches well-covered. `_has_win32_unsafe_name` flags trailing-`.`/whitespace names during the walk and emits a `loguru` warning once per unsafe path — see #169. #821 renamed `_is_in_skip_directory` → the public `has_skip_directory_ancestor` (now shared with `ManifestRepository.reconcile_skip_directory_rows`) and made it pure-string: `TestHasSkipDirectoryAncestor` (3 cases) pins backslash parsing, the ancestor-not-final-component rule, and the root bound; `TestWalkerSkipDirectories` gained the Synology `#recycle` case. |
| `scanner/media.py` | 95% | — | s06, s11 | very low — file-type detection covered for all listed formats. `is_video(path: str) → bool` (added V1 video-playback) is the canonical video-extension check, re-exported by `core/app_service/review_view.py`. `scanner/scoring._VIDEO_SUFFIXES` is a separate (narrower) set — consolidation is a tracked follow-up. |
| `scanner/manifest.py` | 96% | — | every scenario writes a manifest | low. PR 1 of #187 added 4 columns (`exif_tag_count`, `gps_present`, `xmp_derived`, `score`) via the additive migration list. Old manifests auto-migrate on load; new columns default to NULL or 0. #651 — `write_manifest(keepers=…, non_keepers_for_delete=…)` folds the post-scan auto-select keeper-lock/decision writes onto the same tmp connection before the single `os.replace`, so the manifest is atomic *including* auto-select — no rows-without-keeper-lock incoherence window. `TestAutoSelectPipeline` (`tests/test_scan_runner.py`) pins one-read coherence, fresh-scan rollback (injected mid-write failure → no partial manifest), and re-scan clobber (a failed re-scan leaves the prior manifest byte-identical). `keepers=None` (auto-select off) is the byte-identical pre-#651 path. The schema-migration ALTERs are shared with `ManifestRepository.ensure_schema` via `migrate_manifest_schema(conn)`. #820 added 2 nullable TEXT columns (`subsec_time_original`, `offset_time_original`) the same additive way, with the `_DDL` / `_INSERT` / `ManifestRow` companion edits; `test_subsec_and_offset_written_to_new_manifest` pins that a fresh manifest carries them from its own DDL and that the burst case (same `shot_date`, different sub-second) round-trips. |
| `scanner/byte_budget.py` | 94% | — | covered via the scan pipeline | Pure byte-budget gate for the #587 HASH-stage OOM fix (count alone — `hash_in_q`+`compute_inflight`=257 buffers × up to ~130 MB DNG ≈ OOM — cannot bound RAM; this caps in-flight *bytes*). The byte cost is acquired in the READER worker (`scan_runner._gated_read` / `_budgeted_read`) so the reader pool back-pressures, and released in the compute done-callback. Layer 1 by `tests/test_byte_budget.py`: accounting, admit-one-over-budget (a single file larger than the whole budget can't deadlock), **cancel-wake** (a thread blocked in `acquire()` must wake on cancel — the #492/#495/#507/#561 deadlock scar class), zero-byte no-op, release clamp/never-raise, the concurrent peak-`_inflight ≤ budget` invariant, and `default_budget_bytes` clamp (floor 256 MiB / cap 2 GiB / probe-fail fallback). The real-OS RAM probe (`_probe_total_ram_windows`/`_posix`) is `# pragma: no cover` (can't run portably on CI; exercised by monkeypatch + local runs). The pipeline WIRING is pinned by `tests/test_scan_pipeline.py::TestByteBudgetPipelineBound` — it drives the real thread-branch with compute held closed and asserts the reader pool is back-pressured by the budget (the pre-#587 version reads the whole library into RAM and fails it). |

### `core/`

| Module | Layer 1 | Notes |
|---|---|---|
| `core/models.py` | 100% | dataclasses |
| `core/services/sort_service.py` | 100% | pure logic |
| `core/services/interfaces.py` | 100% | dataclasses + protocols |
| `core/services/auto_select.py` | 100% | pure helper for #212. Picks the top-scored row per duplicate group; consumed by `core.app_service.scan_runner.run_pipeline` when the scan dialog's "Auto select after scan" checkbox is on. Tie-break + None-handling mirror `select_paths_top_n` (`core/app_service/action_resolve.py`) so manual and auto runs converge on the same keeper. Layer 3: s49 covers the full pipeline including #239's visual-selection step — the client selects the `action="KEEP"` rows in the result tree once the post-scan manifest loads. #824 — `non_keepers_for_aggressive_delete` gained `_raw_displaced_keepers`: when a group's keeper is a RAW (extension-typed, `RAW_EXTENSIONS` + `.tif`/`.tiff` to match `get_file_type`), the best non-RAW row is protected alongside it, so the new exact-tier complementary edge cannot demote a JPEG export into the delete set. It guards BOTH consumers at once — the scan-time aggressive path and `core/app_service/action_service.apply_best_copy` (#744), which shares the helper. Pinned by four cases in `TestNonKeepersForAggressiveDelete` (spared-displaced-keeper, still-deletes-a-lower-ranked-duplicate, all-lossy unaffected, `.tiff` counts as RAW) plus two end-to-end regressions in `tests/test_dedup.py::TestComplementaryGroupEdge` that drive classify → score → both consumers. |
| `core/constants.py` | 100% | Toolkit-free home for `IGNORE_DECISION` (web-port PR 4a), so the drift tests (`test_web_qt_free.py`, `test_review_service.py`) import it without dragging in a UI layer. The desktop-only view-role constants that used to live alongside it went with the client in #646. |

### `core/app_service/`

| Module | Layer 1 | Notes |
|---|---|---|
| `core/app_service/scan_runner.py` | 84% (full suite) | Toolkit-free scan pipeline (extracted in web-port PR-B). `run_pipeline(config, cancel_token, bus)` is the single entry point. All module-level helpers (`_time_hash_executor`, `_profile_process_pool`, `_profile_grouping`, `_derive_bktree_floor`, `_stratified_sample`, `_valid_hash_pool_rates`, `hash_pool_fingerprint`, `store_hash_pool_rates`, `_assign_process_pool_to_kill_job`, `_StageTracker`, `_calibrate_hash_pool`, `_resolve_grouping_floor`) live here as the single source of truth; The `TestHashPoolCalibration` tests in `test_scan_pipeline.py` import from `core.app_service.scan_runner` and call the live functions directly — these are the load-bearing calibration tests (floor crossover/clamp, fresh-vs-cached floor derivation, legacy-cache fallback, version-token cache invalidation, #609 multi-device+NAS shortcut). Happy-path and pre-cancel tests in `tests/test_scan_runner.py`. `tests/test_scan_runner.py::test_importing_scan_runner_does_not_import_pyside6` pins the no-UI-toolkit seam in a subprocess. Uncovered ~16%: the pipeline's HASH+EXIF+CLASSIFY+SCORE+WRITE stages run in all scan-worker tests but coverage is distributed across the full suite; targeted at just `test_scan_runner.py` alone it's lower because those tests drive only the happy-path + cancel. #651 — the post-scan auto-select pass (keeper locks + optional aggressive-delete decisions) is now handed to `write_manifest(keepers=…, non_keepers_for_delete=…)` instead of a separate post-write `apply_auto_select_decisions` call, so keeper locks land atomically with the manifest; pinned by `TestAutoSelectPipeline` (atomicity + rollback + re-scan clobber). #786 — `_route_outcome` now checks `HashResult.inmemory_signals` (set only for JPEG) and, when present, writes the extract straight into `extracts` instead of queueing to `exif_queue` — JPEG never touches the exiftool subprocess. `TestPostHashCancelKillsExif` (`test_scan_pipeline.py`) was updated to use fake `.mov` files instead of JPEGs so its exiftool-wedge scenario still exercises the queued path. |
| `core/app_service/events.py` | **omit** | `ScanProgressBus` is a `runtime_checkable` Protocol — its stub method bodies (`...`) cannot be invoked from unit tests. Covered by the real `SseScanBus` in `app/web/routes/scan.py` and by `_SpyBus` in `tests/test_scan_pipeline.py`. |
| `core/app_service/dtos.py` | 100% | plain `@dataclass` — every field exercised by the construction in `test_scan_runner.py` and `TestHashPoolCalibration._make_config`. |
| `core/app_service/cancel_token.py` | 100% | pinned by `tests/test_cancel_token.py`; also exercised by every cancel test in `test_scan_pipeline.py` via `worker._cancel_token`. |
| `core/app_service/execute_service.py` | 84% (full suite) | Headless execute/remove/prune/save backing the Phase 2C1 `POST /api/{execute,remove,prune,save}` routes (`app/web/routes/execute.py`, 77%). Stateless (own `ManifestRepository` per call); reuses `DeleteService` (send2trash), `write_delete_log` (audit CSV), `ManifestRepository.finalize_outcome` / `remove_from_review`. D7 per-file outcome write keeps the DB crash-consistent. **Every deleted/ignored `source_path` is re-validated against `allowed_roots` via `path_safety.is_under_roots` before any filesystem op** — the 2C1 adversarial-review ship-blocker fix: an in-root manifest carrying an out-of-root `source_path` row can no longer delete that file. Pinned by `tests/test_web_execute_service.py` + `tests/test_web_execute_routes.py` (out-of-root refusal regression, locked-gate 409, concurrency mutex 409 via a real two-request race, per-file consistency on a mid-batch missing file, save in-place + save-as copy). **#686 prune additions:** `prune_singletons` gained an explicit-`paths` mode (`TestPruneSingletons` — prunes only the named singletons, ignores `include_actioned`, skips locked, drops non-singletons), the web analog of Qt's `_apply_singleton_prune(to_prune)`; `classify_singletons` (`TestClassifySingletons`) + `POST /api/prune/candidates` (`TestPostPruneCandidates`) return the `{plain, actioned, locked}` buckets the web prune flow offers from — needed because the review view drops single-member groups, so the FE can't classify singletons itself. Cross-cutting CSRF / manifest-root trust-boundary hardening is tracked in #662 (out of 2C1 scope — also spans the 2B1 `PATCH` routes). |
| `core/app_service/path_safety.py` | 82% | Pure headless `is_under_roots(path, allowed_roots)` predicate (resolve both sides, `relative_to`, `False` on empty/malformed/escape). Single source of truth shared by `app/web/routes/_path_guard.validate_under_roots` (the HTTP 400/403 wrapper) and `execute_service`'s per-row delete guard, so the route-envelope check and the per-file delete check can't diverge. Exercised by the execute route/service tests + the existing `_path_guard` 403 parametrized suite. |
| `core/app_service/action_resolve.py` | ~95% (full suite) | Toolkit-free pattern dispatcher — the single home for the Set-Action-by-Field matching rules. Public API: `resolve_matched_paths(groups, field, pattern) -> list[str]` — dispatches empty-pattern guard, `__cmp__:op:value`, `__top_n__:n:order`, and plain case-insensitive regex (`re.error` propagates). Encode/decode helpers (`encode_cmp_pattern`, `encode_top_n_pattern`, `decode_cmp_pattern`, `decode_top_n_pattern`) also exported. Layer-1 coverage in `tests/test_action_resolve.py` (`TestThresholdSelectionLogic`, `TestTopNSelectionLogic`, `TestPatternEncoding`, plus the `TestResolveMatchedPaths` dispatcher). Consumed by `action_service.bulk_decide`. |
| `core/app_service/action_service.py` | ~90% (full suite) | Headless bulk-decide service backing `POST /api/action/bulk-decide`. `bulk_decide(manifest_path, field, pattern, action, *, allowed_roots, force_locked, preview) -> dict`. Loads `MainVM` directly (raw `PhotoGroup`/`PhotoRecord` objects) so `resolve_matched_paths` can filter on live attributes. **Every resolved `source_path` is re-validated against `allowed_roots` via `is_under_roots` before any DB write** — the 2C1 adversarial-review ship-blocker fix: an in-root manifest carrying out-of-root rows cannot mutate those records. Locked-row gate (`ValueError(("locked_paths", [...]))`) fires for decision actions when `force_locked=False` and matched rows have `is_locked=True` (reads `is_locked` from the already-loaded VM objects, never from the client). Preview path returns the affected set without any write. Pinned by `tests/test_web_action_routes.py` (plain-regex, `__cmp__`, `__top_n__`, preview, empty-pattern, `__lock__`, out-of-root filter, locked 409, force_locked 200). |
| `app/web/routes/action.py` | ~90% (full suite) | `POST /api/action/bulk-decide` route. Validates manifest path against `allowed_roots` (403), checks manifest on disk (404), runs `bulk_decide` in `run_in_executor`. Error map: `re.error` → 400 `invalid_pattern`; `ValueError(("locked_paths",...))` → 409; other `ValueError` → 422; `FileNotFoundError` → 404. Returns `BulkDecideResult`. Pinned by `tests/test_web_action_routes.py`. |
| `core/app_service/settings_migration.py` | 100% | Toolkit-free #258 legacy-settings migration (extracted in web-port PR 4a). `resolve_source_entries(settings)` returns the canonical `sources.list` shape, falling back to the legacy `sources.{iphone,takeout,jdrive}` keys so an upgrader carrying only the legacy keys doesn't silently lose their source list. Consumed by the web settings loader (`app/web/routes/settings.py` resolves it on GET, closing the same gap on the web side). Pinned by `tests/test_settings_migration.py`. |

### `infrastructure/`

| Module | Layer 1 | Layer 2 | Layer 3 | Residual risk |
|---|---|---|---|---|
| `infrastructure/manifest_repository.py` | 98% | — | every scenario, s42 | very low. `is_locked` column persistence (#164) is round-tripped in `TestIsLockedPersistence` (4 cases): default-False on load, additive migration on pre-#164 DBs, ``batch_update_lock_state`` write+read, and unlock-after-lock. PR 1 of #187 added 4 scoring columns via the same additive migration pattern; round-trip pinned by `TestScoringSchemaMigration` (3 cases) and the score-loads-into-PhotoRecord tests. PR 4's `rescore(weights)` is pinned by `TestManifestRepositoryRescore` (5 cases) — including weight validation, custom-weights flipping the winner, and `NULL` preservation for Live Photo MOV passengers. #584 added the `outcome` column: `TestFinalizeOutcome` (7 cases: deleted→outcome='deleted'/executed=1; ignored→outcome='ignored'/executed=0; multi-path; noop empty; untouched sibling; fail-loud; noop-empty). Visibility predicate changed from `executed=0` + Python filter to `WHERE outcome=''`; `TestRemoveFromReview` updated to assert outcome='ignored' and uses `_DDL_WITH_OUTCOME` for pre-seeded rows. #820 appended `subsec_time_original` / `offset_time_original`, pinned by `TestSubsecTimeOriginalMigration` (4 cases) against a `_DDL_PRE_820` fixture that reproduces the exact schema on users' disks today: columns added, pre-existing rows read back NULL with every other stored value intact, a leading-zero value round-trips, and — the case a naive migration gets wrong — the columns **survive the #433 `dest_path` copy-table rebuild**, which recreates the table from a hardcoded DDL and would silently drop any column missing from `_POST_DROP_COLUMNS`. Neither column is in `_LOAD_ALL_SQL` or `rescore()`'s SELECT: no UI column and no scoring weight reads them yet, so adding them there would be dead code. #821 added `reconcile_skip_directory_rows` (dismisses pre-#482 recycle-bin / system-folder rows when a manifest is opened) — pinned in its own file `tests/test_manifest_skip_reconcile.py` (8 cases: the stale row leaves the review set and persists `outcome='ignored'`; Synology `#recycle`; the false-positive guard for lookalike folder/file names; the missing-file row still loads unchanged; idempotence; `deleted` never re-opened as `ignored`; a READ-ONLY manifest still opens with a WARNING and its rows live — the reconcile is fail-soft because it is housekeeping inside someone else's `load()`; and a row finalised `deleted` in the window between the candidate SELECT and the dismissal UPDATE keeps `deleted`/`executed=1`, which is why the write is guarded with `AND outcome=''` and keyed on `id`). Kept out of `test_manifest_repository.py` so the reconcile's fixture manifests — built through the production `write_manifest` path, i.e. WITHOUT the `outcome` column — stay next to the tests that depend on that shape. #680 added `exif_tag_count` / `gps_present` / `xmp_derived` to `_LOAD_ALL_SQL` and threaded them onto `PhotoRecord` (they were stored since #187 but never read back), so `GET /api/manifest` can serialise the per-dimension scoring signals. Pinned by `test_scoring_signals_load_from_db_into_photo_record` (both a signal-bearing and a signal-free row, so a row cannot inherit its neighbour's values) and `test_exif_tag_count_null_survives_load`. That NULL case is the load-bearing one: `PhotoRecord` defaults these to `None`/`False`, which are also legitimate readings, so a column silently dropped from the SELECT reads as "no GPS, no XMP, census never ran" on every row with nothing raising. Layer 3: the **web** s42 driver (`qa/web/scenarios/s42_scoring.py`) now asserts the three values through the API, reaching the desktop driver's assertion scope without a SQLite backdoor. |
| `infrastructure/settings.py` | 100% | — | every scenario | none |
| `infrastructure/i18n.py` | 93% | — | s22 (live language switch — the UI re-renders in place, locale persisted in settings.json) | low — uncovered branches are defensive `except (OSError, yaml.YAMLError)` paths in `available_locales()` and a couple of guards. The `test_zh_tw_has_every_key_present_in_english` test pins parity between the en and zh_TW catalogs at PR time, so a missing translation never ships silently. |
| `infrastructure/delete_service.py` | 93% | spot-add only | s13 (planned per #80) covers happy-path real send2trash | recycle-bin behavior on networked drives untested; error paths exercised via mocks. Spot-add a layer-2 test for specific bug cases (locked file, network drive, permission denied). |
| `infrastructure/utils.py` | 89% | spot-add only | s08 (real EXIF on real fixtures) | DNG fallback only mocked. If a real DNG ever returns metadata in a shape we don't anticipate, that's the moment to add a layer-2 spot-test pinning the parse. |
| `infrastructure/image_service.py` | **omit** | depends on running `QApplication` for image decode | s01, s05 | full responsibility on layer 3. Still carries a real layer-1 suite (`tests/test_image_service.py`) for the pure pieces — byte-budget LRU, cache-key derivation, and (#622 Phase 2) the 5 s per-path source-mtime stat TTL in `_MtimeStatCache`, whose clock, stat function and entry cap are injected so both the TTL and the eviction are tested without sleeping, without patching stdlib, and without allocating the production cap. The bound is covered from both sides — 10k distinct paths with the clock advanced past the TTL, and a 500-path burst inside one TTL where the expiry sweep finds nothing and oldest-first eviction has to carry it alone. The module-global `_mtime_cache` is reset by an autouse fixture in `tests/conftest.py`; without that reset one test's `tmp_path` stat would still be cached when the next test reuses the path inside 5 s. |
| `infrastructure/device_key.py` | 90% (#622 Phase 2) | — | s01, s05 (preview click path); every scan scenario via the HASH stage | very low — `device_key` + `is_remote_drive` extracted verbatim from `scanner/workers.py` so the preview coordinator and the scanner's HASH stage bucket by ONE definition (a second implementation would let the two disagree about how many concurrent reads a NAS may take, invisibly to every other test). `tests/test_device_key.py` holds the tests that moved with it, plus `TestDeviceKeyReexport`, which pins that `scanner.workers` re-exports the same objects AND that patching the defining module still reaches callers through the re-export — the monkeypatch seam every platform-independence test in that file depends on. The only uncovered lines are the `# pragma: no cover` Win32 `WNetGetConnectionW` / `GetDriveTypeW` boundaries, which cannot run on Linux CI. |
| `infrastructure/transcode_service.py` | `tests/test_transcode_service.py` (≥70%) | CI-safe: cache-key determinism, mtime-sensitivity, cache-hit short-circuit (no ffmpeg), per-key lock, atomic rename naming, and (#854) binary resolution — the shipped `_internal/ffmpeg/` sub-directory, a user's replacement set in `<exe dir>/ffmpeg/`, the loose legacy locations, PATH, and nothing — plus H.264 encoder selection from a faked `ffmpeg -encoders` listing (the bundled LGPL build has no libx264). The packaged layout itself (PyInstaller placement, the bundled binaries running, the real `/api/media?transcode=h264` round-trip) is verified by a local release build, not by layer 1 — see the #854 entry in `docs/features.md`. Real ffmpeg encode is gated by `tests/integration/test_transcode_integration.py` (runs in `bench-sanity` CI job via `apt-get install ffmpeg`). Residual risk: concurrent Semaphore(2) deadlock on exception is unit-tested; real NAS latency / disk-full / corrupted ffmpeg output are dev-rig verifiable only. |
| `infrastructure/logging.py` | **omit** | module-level loguru sink setup; no executable surface | — | none — touched implicitly when other tests import |

### `app/web/`

The web API modules are excluded from the main `[tool.coverage.run] source` list
in `pyproject.toml` (they live under `app/` which IS in source, but the lifespan
and `__main__` blocks are marked `# pragma: no cover`).

| Module | Layer 1 | Notes |
|---|---|---|
| `app/web/main.py` | `tests/web/test_static_mount.py` (SPA mount contract) | The SPA static-mount branch (`frontend_dist` param, `dist.exists()` guard, `StaticFiles` mount) is covered by `TestStaticMountPresent` and `TestStaticMountAbsent` in `tests/web/test_static_mount.py`. The `_lifespan` context manager and `__main__` block are `# pragma: no cover` (require a live server; covered by the `web-dom-probe` CI job and local runs). The `create_app()` factory shape itself is the contract being tested. |
| `launcher.py` (repo root) | `tests/test_launcher.py` (97%) | The desktop entry point — uvicorn boot plus the pywebview window; `PHOTO_MANAGER_WEB_SMOKE` selects the headless boot-and-exit path `release.yml` uses. **Not** coverage-omitted: it IS measured at layer 1. Layer 1 covers the real decision + contract logic — the WebView2-runtime loud-fail (a silent-blank-window failure mode; the `winreg` `pv`-probe for present / absent / `0.0.0.0`-placeholder), the single-worker/daemon/factory uvicorn boot contract, health-gated startup, and the loopback-only (never `0.0.0.0`) window URL + graceful-shutdown-on-close. `webview.create_window`/`start` and the real uvicorn thread are injected/mocked so no window opens in CI. **Residual risk:** the actual native WebView2 window + real HEVC playback is a **manual Windows smoke** (`python launcher.py`), the same manual-checkpoint shape as the release-build smoke. |
| `app/web/routes/i18n.py` | `tests/test_web_i18n.py` (93%) + `tests/test_web_i18n_callsite_keys.py` (static probe) | `GET /api/i18n/{locale}` returns `{locale, strings, available}`; `strings` is filtered to the **`web.*` namespace only** (the ~300 Qt-only desktop keys are never served — keeps the payload ~1 KB and avoids leaking internal desktop copy). Catalog + available-locales are `lru_cache`-d (static files). 404 for unknown locales; the translations-dir-missing 500 branch is defensive dead-code (the one uncovered line). `test_web_i18n_callsite_keys.py` is a static probe asserting every `t('web.…')` call-site key in `frontend/src` exists in `en.yml` (a typo'd key would otherwise render English forever with no signal). Layer-3: `qa/web/scenarios/s22_language_switch.py` and `s58_language_switch_preserves_manifest.py` exercise the full client-side locale-switch flow (toggle click → PATCH /api/settings → GET /api/i18n/zh_TW → re-render). Frontend store logic (`initI18n`/`setLocale` persist+refetch) is unit-covered in `frontend/src/i18n/useI18nStore.test.ts`. |
| `app/web/routes/settings.py` | `tests/test_web_settings.py` (if present; see Notes) | `GET /api/settings` returns only the `_WEB_SETTINGS_KEYS` allowlist (prevents future sensitive-key leaks). `PATCH /api/settings` validates all keys before any write (validate-all-then-apply), rejects unknown keys with 400. `PHOTO_MANAGER_HOME` env override controls the settings.json path — see Settings isolation note in the web scenario layer section below. Layer-3: s22 and s58 call `PATCH /api/settings ui.locale` in their restore steps; the round-trip is exercised implicitly. |
| `app/web/routes/media.py` | `tests/test_web_media_route.py` (≥70%) | `GET /api/media` — raw-byte streaming endpoint with HTTP Range/206 support for video (and generic media) files. V1: direct file streaming (path guard, 400/403/404/416). V2: `transcode=h264` query param routes the SOURCE through `TranscodeService.get_transcoded_path` (run_in_executor, cached H.264) then serves the cached file via the same `_serve_file` helper — no Range logic duplicated. `TranscodeUnavailable` → 501; `TranscodeError` → 500. Layer-1 covers all V1 Range branches + V2 transcode-hit with pre-placed cache (no ffmpeg), 501 when ffmpeg absent, path-guard still active on the source for transcode requests. Layer-2 integration (`tests/integration/test_transcode_integration.py`, ffmpeg-gated): real HEVC→H.264 output, ffprobe confirms codec_name==h264, faststart atom order. Layer-3: `s69_video_playback.py` (VP9 decode + playback) + `s70_video_transcode_fallback.py` (synthetic error → swap-once, codec-independent) + `s71_grid_video_tiles.py` (group-grid: per-tile decode + GMC group-play broadcast + master-scrub convergence). |
| `frontend/src/components/GroupGrid.tsx` | Layer 3: `qa/web/scenarios/s71_grid_video_tiles.py` — grid renders with N tiles for a 2-member video group, `grid-container` testid visible after group-row click. No layer-1 unit tests (React component assembly; the testable pure-logic helpers are in `frontend/src/lib/groupMediaSync.ts`). |
| `frontend/src/components/VideoTile.tsx` | Layer 1: `frontend/src/components/VideoTile.test.tsx` (vitest/jsdom) — no `<video>` before click (click-to-mount, no eager decode); after click a `<video>` with the `{testId}-video` data-testid mounts and registers with the provider on mount + unregisters on unmount. Layer 3: `qa/web/scenarios/s71_grid_video_tiles.py` — click mounts `<video>`, readyState >= 2, currentTime > 0. Probe: `tests/test_web_dom_probes.py::test_probe_video_tile_video_element_carries_video_testid` pins the `-video` data-testid so s71's locator can't silently drift. Per-tile transcode-fallback state is the `[useTranscode, videoFailed, canPlay]` triple from `PreviewPane.tsx`, replicated per tile (hooks rules require a child component per tile). |
| `frontend/src/components/GroupMediaController.tsx` | Layer 1: `frontend/src/components/GroupMediaController.test.tsx` (vitest/jsdom) — out-of-order `durationchange` keeps the slider max at the MAX; master shrinks to the surviving max when the longest player detaches; the threshold autoplay guard calls `playAll` EXACTLY ONCE at `expectedPlayerCount` and not again on churn; listeners detach on unregister and on controller unmount. Layer 3: `qa/web/scenarios/s71_grid_video_tiles.py` — group pause/play broadcast reaches both MOUNTED tiles; `gmc-progress-slider` real pointer drag seeks all tiles to within 1.5 s of the read-back target (seek commits on release). Pure helper logic covered by `frontend/src/lib/groupMediaSync.test.ts`. |
| `frontend/src/lib/groupMediaSync.ts` | `frontend/src/lib/groupMediaSync.test.ts` — unit tests for all 6 pure helpers: `isMajorityPlaying`, `shouldUpdateMasterDuration`, `shouldTrackPlayerPosition`, `computeMasterPosition`, `computeMuteTargetVolume`, `volumeIconForValue` / `playButtonIconForState`. These helpers are the load-bearing ported-from-Qt logic; covering them at layer 1 prevents silent drift from the Qt reference. |
| `app/web/routes/*.py` | Various `tests/test_web_*.py` | Each route module has its own test file (e.g. `tests/test_web_scan_api.py`, `tests/test_web_execute_routes.py`). |

### `qa/web/`

`qa/web/` modules are in `[tool.coverage.run] omit` — they are the layer-3 web
driver harness (Playwright scenario runners, testid constants, invariants) and
cannot be exercised inside the layer-1 test process.

| Module | Where it IS covered |
|---|---|
| `qa/web/testid_constants.py` | Static shape checked by `tests/test_web_dom_probes.py::TestTestidConstants` (CI, no browser); live DOM checked by `test_shell_testids_present` (web-eval-gates CI, `web_probe` marker). |
| `qa/web/_pw.py` | Import isolation checked by `TestImportIsolation`; runtime usage in `web-dom-probe` CI and local Playwright runs. |
| `qa/web/_invariants.py` | Import isolation checked by `TestImportIsolation`; runtime usage in `web-dom-probe` CI and local Playwright runs. **Write-settling contract (#850):** a helper that clicks an affordance which stages state does not return until the API write that click caused has responded — `set_row_decision` waits for its `PATCH /api/decision`, and `click_context_item` waits for the write listed in `_CTX_ITEM_WRITES` (decision / lock / apply-best-copy; items that open a submenu or sit behind a confirm sheet are deliberately absent). The wait is Playwright's `expect_response` on the real response, never a sleep, and a 4xx is not a failure (a locked row answers 409 by design, #733) while a 5xx is. So a scenario may read `GET /api/manifest` immediately after a write helper without racing it — that race was the moving "expected decision 'delete', got ''" family in the then-advisory `web-scenario-batch` job (that job is blocking as of PR #817). |
| `qa/web/scenario_map.yml` | Count/key parity checked by `TestScenarioMapParity` (CI). |
| `qa/web/scenarios/s22_language_switch.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. Exercises: **en preamble** (forces `ui.locale=en` so the baseline is self-healing after a crashed prior run), English baseline (toolbar text), click `main-lang-toggle` EN→zh_TW, assert `main-scan-button`/`main-execute-button` re-render live, assert persistence after `page.reload()`, restore locale via `PATCH /api/settings`. |
| `qa/web/scenarios/s58_language_switch_preserves_manifest.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. Exercises: en preamble, scan near-duplicates sandbox, capture pre-switch file row order, click `main-lang-toggle`, assert result tree count+order unchanged after switch (#428 regression guard), restore locale. |
| `qa/web/scenarios/s31_simple_mode_regex.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. Exercises: scan near-duplicates sandbox; click `main-action-button` to open `action-dialog`; set `action-field-combo`="File Name"; fill `action-simple-op`="contains" + `action-simple-text`="q9"; assert write-through to `action-regex-input` ("q9"); assert `action-match-counter` shows non-zero count (live preview round-trip); set `action-action-combo`="delete"; click `action-btn-apply`; confirm `execute-all-delete-confirm-yes`; assert via `GET /api/manifest` that `neardup_00_q95.jpg` has `user_decision="delete"` and other four rows unchanged. Web parity: 8 scenarios done (`status: done` in scenario_map.yml). Qt mode-toggle radio and keyboard/mnemonic probes (D9/D10/B12) omitted — no web equivalent. ActionDialog component covered for the first time at layer 3; backend `POST /api/action/bulk-decide` covered at layer 1 by `tests/test_web_action_routes.py`. |
| `qa/web/scenarios/s61_actioned_singleton_prune.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. #686 mixed-bucket singleton prune on the `"ask"` path — seven variants (remove_plain / remove_both / keep / lock_cancel / lock_unlocked_only / lock_apply / lock_apply_remove). **Cancel-discriminator (#702):** `lock_cancel` and `lock_unlocked_only` both HOLD the locked A_keep (resolvePruneLock treats the two verdicts identically) so their raw sqlite state is the same; the discriminating assertion is a `POST /api/prune/candidates` read confirming A_keep is STILL in the `locked` bucket — a mis-wire of either button to the unlock-apply verdict would unlock the row and is caught here, not by the sqlite read. **Mechanism (the load-bearing divergence):** the web execute-tree + remove are single-select, so a single gesture collapses only ONE group; the mixed (plain+actioned) offer is built by ACCUMULATION — finalize-remove A_drop under `prune="never"` (collapses cluster A silently), flip to `"ask"`, finalize-remove B_drop (collapses cluster B) so `maybeOfferPrune` classifies BOTH current singletons via `POST /api/prune/candidates`. The execute dialog auto-closes after each remove, so `_execute_remove` re-opens it (also picks up the renumbered survivor group). Asserts the actioned opt-in checkbox + dynamic "Remove N" label (mixed layout) and the D6 lock gate (`LockConfirmDialog` op="prune"). **Remove-after-Unlock&Apply (#713 = #702 item 4):** `lock_apply_remove` is the only variant that clicks Remove on the lock-confirmed path, so it is the live drive of `computePruneSet` with a NON-EMPTY `lockedToPrune` plus an opted-in actioned bucket — BOTH singletons finalize to `outcome='ignored'` (A_keep via the fold, B_keep via Remove). The lock-resolved dialog is **actioned-only** (A_keep has left the unlocked buckets), so `isMixed` is false and `PruneConfirmDialog` renders **no** opt-in checkbox — verified live before the assertion was pinned; the variant asserts that absence, which is what would catch a regression that left A_keep in the `plain` bucket. **Outcomes read via direct sqlite** — a held singleton (`outcome=''`) is a single-member group the review view filters out, so `GET /api/manifest` cannot observe it (the same reason the FE classifies singletons via the backend, not its own groups). Two generated clusters (`qa/web/_prune_fixtures.py`, ported from the Qt PIL generator). `set_prune_pref` opts each variant into the pref it needs; `reset_scan_persistence` resets `ui.prune_singletons`→`"never"` between scenarios (the cluster-E isolation seam). |
| `qa/web/scenarios/s67_locked_singleton_prune_always.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. #686 D6 regression guard on the `"always"` path — a locked singleton must STILL pass the lock gate before pruning. One generated cluster: lock KEEP, stage DROP delete, set `prune="always"`, finalize-remove DROP → group collapses to the locked KEEP singleton → the `LockConfirmDialog` op="prune" fires. V1 Cancel holds KEEP (`outcome=''`); V2 Unlock&Apply prunes it (`outcome='ignored'`). In BOTH the `PruneConfirmDialog` must NEVER appear (absence-checked) — the `"always"` path skips it. That absence check is **forward-defensive** (#702): the `"always"` path provably never constructs a `prunePrompt`, so the dialog can only materialise via a future regression; the live D6 correctness is carried by the lock-gate `wait_for` + the sqlite outcome assertion (both with real failure modes). Outcomes via sqlite (held singleton invisible to the manifest API); KEEP stays on disk (ignored, not deleted). Sister of s61's `"ask"` path. |
| `qa/web/scenarios/s12_save_manifest.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. Qt's **Save Manifest Decisions** reframed as the web's live-persistence **durability guarantee** (#673): the web has no explicit Save step (PATCH /api/decision commits synchronously; GET /api/manifest is stateless), so this verifies the guarantee Qt's Save exists for — stage Delete via the `DecisionControl`, assert the VALUE round-trips (stricter than Qt s12's table-exists-only), then `page.reload()` (wipes the Zustand store → empty state) + reopen the same `.db` and assert the decision survives three ways: re-rendered control shows 'Delete', GET /api/manifest still 1 decision, and a **read-only sqlite read** confirms the `SQLite format 3` header + `migration_manifest` table (Qt structural parity) + `user_decision='delete'` committed on disk. No FE source change — exercises existing live-persist behaviour. The save-as/export-snapshot product question is deferred (filed as a follow-up). |
| `qa/web/scenarios/s38_scan_dialog_invalid_path.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. Qt #144 (a typed bad path must not silently no-op) ported via the web scan-**failure** surface: fill source row 0 with a non-existent path + a valid output, Start Scan → assert a `role="alert"` scoped to `scan-dialog` **names** the offending leaf + the dialog stays open; then re-fill with a real (empty) temp dir, Start Scan → assert the alert clears + the dialog closes (recovery — the failed state is not sticky). Anchors on the semantic `role="alert"` (no testid / FE source change). DIVERGENCES: validation **timing** (web fails the running scan; Qt blocks pre-`+ Add` — exact-parity pre-scan `GET /api/fs/stat` validation deferred as #678 item C) and Qt #216's native "Save Manifest As" Browse dialog (no web analog; output Browse uses the FsBrowser save-mode picker covered by s17). |
| `qa/web/scenarios/s73_action_seed_and_page_scroll.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. Two owner-trial defects that only a live browser can decide. **#894 (app shell never scrolls):** runs at a **620×400** viewport and **selects a row** before measuring — both are load-bearing, not incidental. The result tree is `contain: strict`, so its virtualised rows contribute zero to layout and can never push the page; only the preview pane's min-content height can, and it is empty until a row is picked. At 1280×720 the pre-fix build looked fine (the pane's ~424px still fit), which is exactly why the bug shipped. Asserts `documentElement.scrollHeight <= innerHeight`, tree `scrollHeight > clientHeight`, then the user-visible symptom directly: `window.scrollTo(0, 1e5)` leaves `scrollY` at 0 and the `<header>` at the same y. Pre-fix measurement on the same fixture: root 571px vs a 400px viewport, header dragged to y=−138. **#893 (Set-Action row seed):** restores the viewport to 1280×800 first (the row context menu is taller than 400px, so its lower items are unclickable at the #894 window — a real but separate clipping issue), right-clicks a file row → "Set Action by Field…" → asserts the Simple text equals the row's basename and the regex line equals its **JS**-escaped form (`frontend/src/lib/regexEscape.ts`; Python's `re.escape` disagrees on `/` and `-`, so the scenario carries its own escaper). Finally fills the regex line and reads it back — both inputs are controlled by the Zustand pattern, so surviving that round trip proves the seed is a starting value and not a lock, which a jsdom test cannot settle for a Radix dialog. |
| `qa/web/scenarios/s71_grid_video_tiles.py` | `web-scenario-batch` CI (blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817)) + local Playwright run. GroupGrid multi-tile video feature end-to-end, driving the REAL coordinated surface. Scans `clip.mp4` + `clip_twin.mp4` (byte-identical, s05 duplicate-to-observe pattern) → 1 EXACT-dup video group of 2 members. **Branch 1 (grid renders):** click `row-group-{N}` → assert `grid-container` visible, exactly 2 `grid-video-tile-{gid}-{basename}` containers visible, `gmc-bar` controller bar visible. **Branch 2 (per-tile decode, both tiles):** click tile 1 → assert `{tile1}-video` mounts, `readyState >= 2` (HAVE_CURRENT_DATA), `duration > 0`, `currentTime > 0` (click-to-play autoplays); click tile 2 → assert `{tile2}-video` mounts + plays. (Faithful to Qt: a tile is a static poster until clicked; broadcast reaches only MOUNTED tiles, so both are clicked before the group-broadcast branches.) **Branch 3 (group pause/play broadcast):** click `gmc-play-pause` → assert BOTH videos paused; record a paused baseline; click `gmc-play-pause` again → assert BOTH videos advance beyond baseline. **Branch 4 (master scrub via the React-tracked slider setter + keyup):** pause both, then set `gmc-progress-slider` to ~50% via the native value setter and fire `input`/`keyup` (a raw range-input pointer drag is unreliable in headless Chromium; `keyup` exercises the real `onKeyUp`→`seekAll` release path) → read back the committed slider value → poll each tile's `currentTime` within 1.5 s tolerance (keyframe-tolerance; seek commits on release). Open-risk annotations in module header: GROUP_ROW_NOT_SELECTABLE (`selectedGroupId` store field + group-row click handler required), THUMBNAIL_SOURCE (video tiles use placeholder, no `/api/image` poster), CLICK_TO_MOUNT_BROADCAST (broadcast reaches only mounted tiles — both clicked first, matches Qt thumbnail-until-click, avoids an N-way HEVC decode storm), MASTER_SCRUB (release-commit via keyup; React-tracked slider setter). |

---

### web_probe layer

The `web_probe` pytest marker (`pyproject.toml`) identifies live Playwright
probes that need a **built frontend** (`frontend/dist/index.html`) and a
**Chromium installation** (`python -m playwright install chromium`).

- **Where they run:** the `web-dom-probe` job in `.github/workflows/web-eval-gates.yml`
  (**blocking** since [PR #817](https://github.com/jackal998/photo-manager/pull/817) —
  [#773](https://github.com/jackal998/photo-manager/issues/773) step 3 removed
  `continue-on-error` from every job in that workflow). Not in `tests.yml`.
- **Env gate (`PHOTO_MANAGER_RUN_WEB_PROBE=1`):** the live probe is `skipif`-gated on
  this env var — the same opt-in pattern as the `integration` marker. It is **off by
  default** so the probe does **not** run in the normal local `pytest`. Reason:
  Playwright's sync API leaves asyncio event-loop state that pollutes the asyncio-based
  unit tests (`tests/test_web_scan_bus.py`, the execute mutex) when they share a process.
  The `web-dom-probe` CI job sets the env var; nothing else does.
- **Auto-skip (defence in depth):** even with the env var set, the `pw_empty_state_page`
  fixture in `tests/conftest.py` calls `pytest.skip()` with a clear reason when
  playwright is not installed or `frontend/dist/index.html` is absent. Main CI (which
  lacks playwright and a built frontend) skips cleanly — no errors.
- **Running locally:**
  ```
  # One-time setup (already done if you followed the dev setup guide):
  pip install playwright
  python -m playwright install chromium
  cd frontend && npm run build && cd ..

  # Then (note the env gate — without it the probe skips):
  PHOTO_MANAGER_RUN_WEB_PROBE=1 pytest tests/test_web_dom_probes.py -m web_probe -v
  ```
- **Current probes:** `test_shell_testids_present` — asserts that the 8
  unconditional shell testids (`MAIN_STATUS_BAR`, `MAIN_EMPTY_STATE`,
  `MAIN_SCAN_BUTTON`, `MAIN_EXECUTE_BUTTON`, `MAIN_LANG_TOGGLE`,
  `MAIN_SETTINGS_BUTTON`, `MAIN_MANIFEST_INPUT`, `MAIN_MANIFEST_OPEN`)
  all appear in the DOM when the app is in the no-manifest empty state.
  This grows in later phases as more testids are confirmed unconditional.

---

### Web scenario layer (`qa/web/scenarios/`)

The web port has a dedicated layer-3 equivalent for the React/FastAPI surface.
Each driver in `qa/web/scenarios/<name>.py` exports `def run(*, base_url: str) -> None`
and is registered in `qa/web/scenario_map.yml` with `status: done` and a
`playwright_module:` dotted path.  The runner (`qa.web._batch`) iterates
`ALL_SCENARIOS` (`qa/scenario_ids.py` — the client-agnostic id registry), looks up each entry in the map, and:

- **`status: done`** → imports and calls `run(base_url=…)`.  `AssertionError` → FAIL;
  uncaught exception → ERROR; normal return → PASS.
- **`status: todo` or `status: skip`** → SKIP (never FAIL), so the job stays green
  while scenarios are being ported.
- Playwright not installed → every entry degrades to SKIP.

**CI job:** `web-scenario-batch` in `.github/workflows/web-eval-gates.yml`
(**blocking** since [PR #817](https://github.com/jackal998/photo-manager/pull/817) —
[#773](https://github.com/jackal998/photo-manager/issues/773) step 3 removed
`continue-on-error` from every job in that workflow).  The job builds
the frontend, runs `scripts/make_qa_sandbox.py`, starts `uvicorn` on port 8765,
polls `GET /api/health` for up to 20 s, then calls
`python -m qa.web._batch --base-url http://127.0.0.1:8765`.

**Running locally** (server must already be running):

```bash
# One-time setup:
pip install playwright
python -m playwright install chromium
cd frontend && npm run build && cd ..
python scripts/make_qa_sandbox.py

# Start the server in one terminal:
python -m app.web.main

# Run the batch in another terminal:
python -m qa.web._batch --base-url http://127.0.0.1:8765

# Or run a single scenario by name:
python -m qa.web._batch s01_happy_path --base-url http://127.0.0.1:8765
```

**Parity target and current status:**

| Phase | Target | Status |
|---|---|---|
| Phases 1–3 | Scaffold plus a growing ported subset | Done |
| Phase 4 cutover | Every `ALL_SCENARIOS` id has a `status: done` web driver | Done — 71/71 per `check_qa_parity.py --phase 4` |

**Settings isolation for web scenarios that write `ui.locale`:**
`s22` and `s58` both call `PATCH /api/settings` to write `ui.locale`, which
persists to the server's on-disk `settings.json`.  The server reads settings
from `<repo_root>/settings.json` by default; if the env var `PHOTO_MANAGER_HOME`
is set to a relative path it is resolved against the repo root and the settings
file lives there instead (`app/web/routes/settings.py::_load_settings`).
To prevent live runs from clobbering the real user settings, the batch runner
or CI job launching the server **should set `PHOTO_MANAGER_HOME` to a temp
directory** (e.g. `PHOTO_MANAGER_HOME=/tmp/qa-home uvicorn app.web.main:create_app ...`).
Both scenarios restore `ui.locale` to `"en"` in their `finally` blocks regardless
of test outcome, so back-to-back runs in the same server process are safe even
without the env override — but the override remains the recommended approach for
CI to avoid touching the real settings.json.

The parity counter is enforced by `scripts/check_qa_parity.py` (CI job
`qa-parity-counter` in the same workflow); it fails if the ported count drops
below the floor for the current `WEB_PORT_PHASE` repo variable.

A scenario graduates from `status: todo` to `status: done` once its driver is
written and the CI `web-scenario-batch` job has run it green on the PR that
introduces it.  The `playwright_module` field must be set at the same time
(the batch runner silently SKIPs an entry whose `playwright_module` is null
even when `status: done`).

---

### Top-level scripts

| Module | Status | Where it's covered |
|---|---|---|
| `run_all_linters.py` | **omit** | dev tooling, not user-facing |
| `scripts/check_features_scenario_citations.py` | **omit** (scripts/*) | Guards `docs/features.md`: every `qa/web/scenarios/` citation must name a file that exists, and zero citations may point at the removed desktop tree. Added in [#646](https://github.com/jackal998/photo-manager/issues/646) — on PR A's tip it reports 133 dead citations. Run in CI by `tests.yml` on every PR (its own step, after `pytest`), so a dead citation cannot merge; run it locally after renaming or deleting a driver. |
| `scripts/check_testing_doc_modules.py` | **omit** (scripts/*) | Guards THIS document: every module row in the per-module map above must resolve against `git ls-files`. Added in [#646](https://github.com/jackal998/photo-manager/issues/646), where the cutover left 36 rows describing coverage for files that no longer existed — a stale row reads as coverage. Run in CI by `tests.yml` on every PR (its own step, after `pytest`), so a dead row cannot merge; run it locally after moving or deleting a module. |
| `scripts/memory_probe.py` | **omit** (scripts/*) | `tests/test_memory_probe.py` covers disabled no-op, enabled JSONL schema, the tracked-allocation counter lifecycle, and the ImportError guard pattern; the ctypes Windows memory query and the referrer-dump path need a live app and are exercised by manual probe runs against the fixture. **Residual: the tracked-allocation half (`track_qt_alloc`) has had no caller since #646 removed the desktop client — dead instrument, pending removal** |
| `scripts/generate_probe_fixture.py` | **omit** (scripts/*) | dev tool; deterministic output verified by running it and loading the result into `memory_probe` harness |
| `scripts/bench_web_port.py` | **omit** (scripts/*) | web-port cutover gate (T6): asserts the scan emits `files_per_s > 0` (liveness). Run in CI by the **`bench-sanity` job** (blocking since PR #817) in `web-eval-gates.yml` (`--backend web`, now the only arm). |
| `scripts/bench_thumbnail_latency.py` | **omit** (scripts/*) | web-port cutover gate (T4): (A) route 40 MiB full-res cap → 413; (B) full-res serve of a real ProRAW stays under a peak-RSS budget; (C) warm-cache `size=thumb` p95 ≤ 200 ms. Run in CI by the `bench-sanity` job (blocking since PR #817) in `web-eval-gates.yml` (`--mode all`); (A)+(C) run there, (B) is a **local dev-rig checkpoint** — it needs real ProRAW DNGs in the gitignored `qa/fixtures/raw_local/` (populate via `make_qa_large_source.py --include-large-raw`) and skips when absent (CI has no RAW codecs). **Residual risk:** the `_FULLRES_DECODE_SEM` concurrent-`postprocess` path is *not* exercised — it is unreachable for real ProRAW (40 MiB cap + embedded-thumb fast path serve the file without `postprocess`), so forcing it would test a synthetic regime. The semaphore stands as defense-in-depth for the rare no-embedded-thumb <40 MiB RAW. See the T4 implementation note in `docs/design/web-port-tech-design.md`. |

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
   boundary failure mode that's hard to trigger through the UI
   (e.g. exiftool returning malformed output, send2trash on a locked
   file). Default action: don't write one. Layer 2 is on-demand, not
   an obligation.

3. **User-facing flow** (button, dialog, menu, status bar, manifest
   review)
   → extend an existing `qa/web/scenarios/sNN_*.py` driver, OR add a new
   one and register it in `qa/scenario_ids.py:ALL_SCENARIOS` and
   `qa/web/scenario_map.yml`
   → optionally a layer-1 unit test for any pure logic that backs the
   UI behavior (Python under `app/web/`, vitest under `frontend/src/`)
   → if the change touches a behavior several scenarios already
   exercise (status-bar shape, menu enable lifecycle, destructive
   confirm semantics), reach for `qa/web/_invariants.py` instead
   of duplicating asserts. Each existing driver calls one or two of
   these probes — adding a new probe there benefits every scenario
   for free.

---

## Changing UI labels (and not breaking the QA batch)

User-facing strings live in `translations/<locale>.yml` (the i18n
catalog), not in TSX or Python literals. The drivers mostly avoid
coupling to them at all: locators go through `data-testid`, declared once
in `frontend/src/testids.ts` and mirrored in `qa/web/testid_constants.py`.
Text still leaks into two places — **`qa/web/_invariants.py`'s menu-item
table** (menu labels for the manifest-action invariant) and **inline
strings inside individual scenario files** (status-bar regex, dialog body
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
   qa.web._batch sNN_xyz` — fast iteration vs. the full batch.
5. If you rename or add a **testid**, `scripts/check_testid_parity.py`
   (and [`tests/test_testid_parity.py`](../tests/test_testid_parity.py))
   catches a `testids.ts` ↔ `testid_constants.py` mismatch at PR time.

**What the parity check does NOT catch:**

- Inline strings inside individual `qa/web/scenarios/sNN_*.py` files
  (status-bar regex like `r"Removed N items from list"`, dialog body
  substrings). Those are matched by intent rather than exact text and
  live in arbitrary positions.
- A label that changed in `translations/en.yml` while the driver still
  asserts the old copy — nothing static ties the two together; the
  scenario simply fails on its next run.
- A testid that exists in both files but is attached to the wrong
  element — the check verifies the string, not what it labels.

For comprehensive verification before merge, run the full batch:
`python -m qa.web._batch`. The parity check is the cheap, fast,
CI-runnable subset that catches the most common drift class.

---

## Frontend tests (vitest / jsdom)

The React frontend has its own test layer living inside `frontend/src/`.

| Layer | What | Where it runs | Catches |
|---|---|---|---|
| **vitest / jsdom** | Component render tests, hook logic, pure utility functions | `frontend.yml` CI on every PR that touches `frontend/**` | Testid wiring regressions, broken renders, TypeScript type errors, missing imports |

**Running locally:**

```bash
npm --prefix frontend run test            # single run
npm --prefix frontend run test:coverage   # with v8 coverage
```

**Real-browser interaction behaviour — jsdom is a smoke test, not proof.**
jsdom does not implement a real browser's event/portal/focus machinery, so
a vitest test can pass while the actual behaviour in Chromium is wrong. A
concrete example: a portal-dialog dismissal test asserted the dialog's
`open` state flipped to `false` under jsdom and passed — but a live
Playwright run of the same interaction showed the real dialog never
closed, because jsdom doesn't dispatch Radix's real `pointerdown` /
portal / focus events the component depends on. For these behaviour
classes, the qa-web layer-3 Playwright scenario (real Chromium, driven by
`web-scenario-batch` CI) is the only test that counts as proof; treat any
passing jsdom vitest for them as a structural smoke check only:

- Radix/portal open-dismiss behaviour (dialogs, popovers, menus)
- Outside-click and Escape-key dismissal
- Focus trap / focus restoration
- Drag interactions
- Virtualized-list scroll behaviour

**testid parity** (`tests/test_testid_parity.py`)

`frontend/src/testids.ts` is auto-generated from `qa/web/testid_constants.py`
by `scripts/gen_testid_ts.py`.  The parity test in
`tests/test_testid_parity.py` runs under the existing `tests.yml` pytest job
and fails CI if someone edits `testid_constants.py` without regenerating the
TypeScript mirror.  Regenerate with:

```bash
python scripts/gen_testid_ts.py
```

---

## Authoring new QA scenarios

Read [`qa/web/INSTALL.md`](../qa/web/INSTALL.md) for the one-time
Playwright + Chromium setup, then copy the nearest existing driver in
`qa/web/scenarios/` — they share a fixed shape (`run(page, base_url)`,
`failures: list[str]`, testids from `qa/web/testid_constants.py`, shared
asserts from `qa/web/_invariants.py`).

Register the new driver in **both** places or it never runs:

- `qa/scenario_ids.py:ALL_SCENARIOS` — the ordered id registry;
- `qa/web/scenario_map.yml` — id → module, status, and the `notes` field
  that records what the scenario asserts and what it deliberately omits.

`tests/test_all_scenarios_registered.py` fails loudly on any of the four
ways those two can drift apart from each other or from the files on disk.

---

## Probe inventory

**Static probes** live in two files, both run in the main CI job as pure
Python — no Playwright, no server.

[`tests/test_source_probes.py`](../tests/test_source_probes.py) holds the
sweep-shaped invariants: "no file anywhere under these roots may do X",
which has no single call site to test. It is what survives of the old
`tests/test_ui_probes.py` — the probes whose targets outlived the #646
cutover, with their scan roots narrowed to the remaining trees:

| Probe | Invariant |
|---|---|
| `test_probe_scanner_and_infrastructure_popen_declare_creationflags` | No `subprocess.Popen` under `scanner/` or `infrastructure/` omits `creationflags` (#427 — a visible console window on the `--noconsole` build) |
| `test_probe_production_code_does_not_write_literal_keep_to_user_decision` | Nothing writes the literal `"keep"` to `user_decision` (#425) |
| `test_probe_make_row_per_row_stat_budget` | `scanner/dedup.py::_make_row` stays within its per-row `stat` budget (#474) |
| `test_probe_sse_bus_implements_every_scanprogressbus_method` | `SseScanBus` implements every `ScanProgressBus` method — the Protocol's stub bodies can't be unit-tested (T7) |
| `test_probe_video_tile_video_element_carries_video_testid` | `VideoTile.tsx` still emits the `-video` testid s71 locates |
| `test_probe_zh_tw_translations_are_not_english_passthroughs` + `test_probe_no_legacy_move_wording_in_user_facing_translations` | Translation VALUE sweeps — English passthroughs, and legacy `MOVE` wording in user-facing copy |

[`tests/test_web_dom_probes.py`](../tests/test_web_dom_probes.py) holds
the qa/web scaffold's own invariants:

| Probe class | Invariant |
|---|---|
| `TestTestidConstants` | Every parameterised testid helper (`rowFileTestid`, `rowGroupTestid`, `rowDecisionTestid`, `rowLockTestid`, the all-delete jump) yields the documented kebab shape and stays distinct per group — so a driver can never silently address the wrong row |
| `TestRequiredTestidsParity` | Every name in `REQUIRED_TESTIDS` is defined in `qa/web/testid_constants.py` with the expected kebab value — the static half of the `frontend/src/testids.ts` ↔ driver contract (`scripts/check_testid_parity.py` + `tests/test_testid_parity.py` own the TS half) |
| `TestScenarioMapParity` | `qa/web/scenario_map.yml` and `qa.scenario_ids.ALL_SCENARIOS` agree in count, keys and uniqueness |
| `TestImportIsolation` | `qa/web/_pw.py`, `_batch.py`, `_invariants.py` and `smoke_test.py` import no Playwright at module level — otherwise the whole scaffold becomes unimportable in the unit job |
| `test_shell_testids_present` (`web_probe` mark) | The live half: the shell testids really are in the rendered DOM. Needs a browser + server, so it is opt-in |

**Live soft-probes** are `print("probe_status: …")` blocks inside
`qa/web/scenarios/sNN_*.py` — s06 / s07 / s08 / s09 / s10 / s13 and
others carry them. They report a measurement the scenario deliberately
does not hard-assert (an exiftool-version-dependent date, a symlink
check that only works on privileged hosts) and are the cheapest way to
observe a value across CI runs before deciding whether it can become an
assertion.

When a soft probe's target bug is fixed, convert the `print` to
`failures.append(…)` per the comment block in the scenario. A static
probe that is `xfail(strict=True)` flips to XPASS-strict the moment the
fix lands, and the bug-fix PR removes the marker.

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

Two flavours exist. Pick the first one that reaches the invariant.

**1 — Static probe** (`tests/test_source_probes.py` for source-shape sweeps, `tests/test_web_dom_probes.py` for qa/web scaffold shape)

For source-level invariants you can verify by reading the AST, the TS
source or the YAML. Runs as `pytest` in CI on every commit. Fastest,
most reliable.

When to use: callsite passes the right kwargs, list A ⊆ list B, string absent from file, class method set ⊇ Protocol method set, constant declared with expected members.

**2 — Soft live probe** (extension block inside `qa/web/scenarios/sNN_*.py`)

For runtime state only reachable from the rendered DOM — the live
selection, the options actually in a dropdown, element text after a real
operation. Piggybacks on an existing scenario's setup, so it costs no
extra app boot, and runs in the `web-scenario-batch` CI job.

When to use: the invariant requires the app to be running and a DOM (or
API) query to observe.

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

Inside an existing `qa/web/scenarios/sNN_*.py`, immediately after the setup step whose state you want to inspect:

```python
# ---------- Probe #NNN: <one-line invariant description> ----------
# Currently XFAIL: <what is broken today and why>.
# Promote when fixed: swap the two commented lines below (print → failures.append).
# NOTE: the automatic "Detect probes ready for promotion" step lived in the
# deleted qa-batch workflow (#646) and has no web replacement yet — see
# "Open work" below. Until it is rebuilt, promotion is a review-time duty:
# a PR that fixes bug #NNN promotes this block in the same PR.
print("step: probe_nnn_<invariant_name>")
observed = _some_dom_query(page)
if not _invariant_holds(observed):
    print(f"probe_status: XFAIL_KNOWN_BUG_NNN — {observed!r}")
    # failures.append(f"#NNN invariant violated: {observed!r}")  # ← promote to this
else:
    print(f"probe_status: PASS — {observed!r}")
    # ^ a PASS here means the bug is fixed: promote the probe.
# ---------- end probe #NNN ----------
```

Once promoted (bug fixed, PR merges), collapse the entire block to the single `failures.append(...)` inside the regular assertion block.

---

### Forcing-function design

One mechanism still ensures probes never stagnate silently; the second
was lost in the Phase-4 cutover and is tracked under "Open work".

**Static probes — `xfail(strict=True)`.**
The moment the bug is fixed the probe emits XPASS. With `strict=True`, XPASS is a CI failure. The bug-fix PR cannot merge until the `@pytest.mark.xfail` decorator is removed.

**Soft live probes — currently no forcing function.**
The "Detect probes ready for promotion" step (which grepped the batch log
for `probe_status: PASS` and failed the job) lived in `qa-batch.yml`,
deleted with the desktop client in
[#646](https://github.com/jackal998/photo-manager/issues/646). No
equivalent step exists in `web-eval-gates.yml` yet, so a soft probe that
has started passing will sit there silently until a human notices.

The static path still produces the CI-red moment that forces the probe
lifecycle forward:

```
Active (bug open)  →  Triggered (bug fixed, probe not promoted)  →  Promoted (permanent guard)
   XFAIL / print         CI red (static only, today)                 hard assertion
```

---

### Coverage-cascade warning

Static probes should **not** import the module they inspect unless that module is already in coverage measurement. Importing a module for inspection drags its whole import graph into coverage and can tank the 80% gate — see memory entry `feedback_test_import_cascade`.

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

#### Exempt-list pattern for translation probes

The translation probe (`test_probe_zh_tw_translations_are_not_english_passthroughs`) uses the heuristic: `zh_value == en_value` AND contains Latin letters AND no CJK characters → flag as untranslated. Some strings are legitimately identical in both locales — product names, version format strings.

Pattern: keep a `_TRANSLATION_EXEMPT_KEYS: frozenset[str]` set in `tests/test_source_probes.py`. Any new entry must carry a one-line reason in the PR description (e.g. "brand name", "technical term"). Don't add entries to silence a false positive without verifying the string genuinely doesn't need translation.

---

## Open work

- **Layer 2 is on-demand**, not on the roadmap. Add a spot-test under
  `tests/integration/` (with `@pytest.mark.integration` and a
  `skip-if-binary-missing` guard) the first time a specific boundary
  bug surfaces. Don't pre-build the suite. The boundaries we touch
  (`exiftool` / `send2trash` / `rawpy` / `pillow-heif`) are stable
  enough that proactive coverage would mostly duplicate layer 3.
- **Layer-3 hardening.** [#80](https://github.com/jackal998/photo-manager/issues/80) closed: scenarios for Save Manifest (s12), Execute Action (s13, destructive), Set Action by Field (s14), and right-click context-menu decisions (s15) all merged. Each driver now also calls cross-scenario probes from `qa/web/_invariants.py` (status-bar shape, manifest-actions toggle consistency, destructive-confirm shape) — no maintained extra suite, just lines added inside the existing drivers.
- **CI for layer 3.** [#74](https://github.com/jackal998/photo-manager/issues/74) is done: `web-eval-gates.yml`'s `web-scenario-batch` job runs `qa.web._batch` on every web-touching PR and has been blocking since [PR #817](https://github.com/jackal998/photo-manager/pull/817).
- **Soft-probe promotion forcing function.** The "Detect probes ready for
  promotion" step went with `qa-batch.yml` in
  [#646](https://github.com/jackal998/photo-manager/issues/646) and has no
  `web-eval-gates.yml` replacement. Rebuilding it means capturing the
  `web-scenario-batch` output and grepping it for `probe_status: PASS`.
  Until then a soft probe can pass silently — see "Forcing-function design".

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
