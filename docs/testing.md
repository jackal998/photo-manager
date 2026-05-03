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
| `scanner/exif.py` | 100% (all mocks) | spot-add only | s01, s04, s06, s08 (real exiftool, happy path) | exiftool protocol drift between versions; subtle output-format changes our mock doesn't anticipate. Add a layer-2 spot-test if exiftool ships a known-breaking change. |
| `scanner/hasher.py` | 73% | spot-add only | s06, s07, s11 (real fixtures, happy path) | uncovered tail (~27%) is rawpy / HEIC fallback paths only reachable with real raw files. Layer 3 covers the formats we ship fixtures for; spot-add a layer-2 test only if a real-world RAW format misbehaves. |
| `scanner/dedup.py` | 93% | — | s01, s07, s10 | low — pure logic, well-covered |
| `scanner/walker.py` | 95% | — | s09 | very low — symlink + flat-mode branches well-covered |
| `scanner/media.py` | 95% | — | s06, s11 | very low — file-type detection covered for all listed formats |
| `scanner/manifest.py` | 95% | — | every scenario writes a manifest | low |

### `core/`

| Module | Layer 1 | Notes |
|---|---|---|
| `core/models.py` | 100% | dataclasses |
| `core/services/sort_service.py` | 100% | pure logic |
| `core/services/interfaces.py` | 100% | dataclasses + protocols |

### `infrastructure/`

| Module | Layer 1 | Layer 2 | Layer 3 | Residual risk |
|---|---|---|---|---|
| `infrastructure/manifest_repository.py` | 99% | — | every scenario | very low |
| `infrastructure/settings.py` | 100% | — | every scenario | none |
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
| `app/views/tree_model_builder.py` | 76% | s01, s06, s07, s10 | uncovered 24% is `setData()` `except: pass` defensive wrappers — only triggered if Qt's setData raises, which doesn't happen in practice |
| `app/views/workers/manifest_load_worker.py` | 100% | every load | none |
| `app/views/workers/scan_worker.py` | 91% | every scan scenario | minor — cancellation timing branch hard to test deterministically |
| `app/views/handlers/file_operations.py` | 81% | s01 + every scenario that loads a manifest, plus s12 for Save Manifest Decisions end-to-end | uncovered 19% is QFileDialog interaction (file picker for open manifest) — Save Manifest is now driven by s12, with the WAL-checkpoint branch (#91) covered by both layer-1 unit test and the s12 layer-3 driver |
| `app/views/handlers/context_menu.py` | 88% | s01 (menu probes) | low — `_open_folder` Windows + non-Windows + fallback paths covered; remaining 12% is Protocol stub bodies |
| `app/views/dialogs/scan_dialog.py` | 84% | every scenario opens it | uncovered 16% is QFileDialog browse interaction + a few worker-signal branches |
| `app/views/dialogs/execute_action_dialog.py` | 83% | s13 (planned, will exercise real send2trash through the GUI) | uncovered 17% is `_on_tree_context_menu` + the actual destructive `_on_execute` flow — qa-explore s13 will cover the happy path; spot-add a layer-2 test only if a destructive-flow bug surfaces that's hard to reproduce via the GUI |
| `app/views/dialogs/select_dialog.py` | 94% | s01 (action menu) | low |

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
