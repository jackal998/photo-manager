# Photo Manager

A Windows tool for **deduplication scanning and review** of large personal photo collections.

Produces `migration_manifest.sqlite` recording each file's dedup classification and review decision. (The legacy `MOVE` action + `dest_path` handshake to the external **[photo-transfer](https://github.com/jackal998/photo-transfer)** tool was removed in #433 — see the classification table below.)

---

## Workflow overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. SCAN (photo-manager)                                                    │
│     GUI: File > Scan Sources…                                               │
│     Walks any number of source folders, hashes every file,                  │
│     writes  migration_manifest.sqlite                                       │
│                                                                             │
│  2. REVIEW (photo-manager)                                                  │
│     GUI: File > Open Manifest…                                              │
│     Inspect every group — col 0 (Similarity) shows match strength           │
│     Set decisions per file or in bulk:                                      │
│       Right-click a file → Set Action → delete / keep                       │
│       Action > Set Action by Field/Regex… → regex batch across any column   │
│     File > Save Manifest Decisions… persists decisions to the manifest      │
│                                                                             │
│  3. EXECUTE (photo-manager)                                                 │
│     Action > Execute Action…  opens a full tree review (same columns as     │
│     the main window).  Right-click rows to change decisions before          │
│     confirming.  If every file in a group is marked delete, a               │
│     confirmation dialog appears before proceeding.  Confirm to:             │
│       • delete → send file to recycle bin                                   │
│       • keep   → mark as executed in the manifest                           │
│                                                                             │
│  4. MIGRATE (photo-transfer) — legacy / defunct                            │
│     The MOVE action + dest_path handshake were removed in #433.             │
│     photo-manager is now a standalone dedup-scan + review tool.             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Download (Windows)

A pre-built standalone bundle is published on every release tag — no Python install required.

1. Grab the latest zip from **[Releases → latest](https://github.com/jackal998/photo-manager/releases/latest)**: look for `photo-manager-<version>-windows-x64.zip`.
2. Extract anywhere — the folder is self-contained.
3. Run `photo-manager.exe`.

You still need [exiftool](https://exiftool.org/) on `PATH` for EXIF date extraction (same prerequisite as the source install).

> **SmartScreen note:** the binary is unsigned, so on first launch Windows shows *"Windows protected your PC"*. Click **More info → Run anyway**. The warning is expected and will disappear once we publish a signed release.

Settings (`settings.json`, `window_state.ini`) are written next to `photo-manager.exe`, so the extracted folder is portable — copy it to a USB stick and your config travels with it.

---

## Getting started (from source)

### Prerequisites

- Windows 10/11, Python 3.11+
- [exiftool](https://exiftool.org/) on `PATH` (required for EXIF date extraction)
- Dependencies installed in a venv (see Install below)

### Install

```powershell
git clone https://github.com/jackal998/photo-manager.git
cd photo-manager
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r dev-requirements.txt        # pytest, black, ruff, pylint
copy settings.json.example settings.json   # local config — never committed
```

`settings.json` is gitignored. It may contain personal folder paths; edit it to add your sources after copying.

### Launch (GUI)

```powershell
run.bat          # activates .venv and starts main.py
# or
.venv\Scripts\python main.py
```

### Run tests

```powershell
.venv\Scripts\python -m pytest
```

This runs **layer 1** (unit + mock-based). Coverage is configured in
`pyproject.toml`; the build fails if global branch coverage drops below
80% **or** any single tracked module drops below 70% (per-file gate
enforced by `scripts/check_coverage_per_file.py`, run as a CI step
right after `pytest`). CI runs this on every push and pull request to
`master` via `.github/workflows/tests.yml`.

A separate monthly job (`.github/workflows/pip-audit.yml`) runs
`pip-audit` against `requirements.txt` + `dev-requirements.txt` on the
1st of each month plus on-demand via `workflow_dispatch`; failure = a
known CVE was found against a pinned dependency.

Two more test layers exist locally:

```powershell
# Layer 2 — real binaries (exiftool, send2trash, rawpy / pillow-heif).
# On-demand: tests/integration/ is created only when a specific
# boundary bug needs a regression guard. Not maintained as a suite,
# because layer 3 already covers the boundary happy paths via real
# fixtures. Run when present:
.venv\Scripts\python -m pytest -m integration

# Layer 3 — full GUI exercise via /qa-explore. Drives main.py through
# scripted scenarios to catch UI / state-transition / copy regressions
# AND validates real third-party boundaries (exiftool, send2trash) on
# happy paths.
.venv\Scripts\python -m qa.scenarios._batch
```

**The full strategy — what each layer catches, what it misses, and the
per-module residual risk — lives in [`docs/testing.md`](docs/testing.md).**
Read that before adding tests for a new feature. Short version:

| Layer | Catches | Misses |
|---|---|---|
| 1 — Unit + mocks (CI) | Refactoring bugs, parser logic | Real third-party behavior |
| 2 — Integration (local, on-demand) | Spot-tests for specific boundary bugs already hit (exiftool / send2trash / rawpy edge cases) | GUI behavior; anything you haven't written a spot-test for |
| 3 — `/qa-explore` (local) | Label drift, dialog regressions, state-transition bugs, boundary happy paths | Anything off the scripted path |
| Probes — `tests/test_ui_probes.py` + sNN soft-probe blocks (CI) + `qa/probes/` (local) | Cross-cutting invariants: dropdown drift, missing kwargs, label uniqueness, translation passthroughs, menu-gating drift, bridge-pattern holes ([#243](https://github.com/jackal998/photo-manager/issues/243)) | Anything not framed as a structural invariant |

**No test padding.** A test that exists only to clear a coverage gate
is metric gaming, not engineering — see the testing rules in
[`CLAUDE.md`](CLAUDE.md) for the explicit list of patterns to avoid.

---

## Usage — GUI

The PySide6 desktop app is the primary interface. Launch it with `run.bat`.

### Step 1 — Scan sources

**File › Scan Sources…** opens the scan dialog.

1. Browse the embedded folder tree to find source directories.
   - Double-click or press **+ Add Selected Folder** to add a folder to the list.
   - The source list is displayed alphabetically by path. Scan order
     (and therefore dedup priority for exact duplicates) is inferred
     from the underlying insertion order, not the displayed row order.
   - Tick or untick the **Recursive** checkbox per source — recursive scans all
     subdirectories; unticked scans only the immediate folder.
   - Use **×** to remove a source; **Remove All** to clear the list.
2. Set the **Save manifest to** path (defaults to `migration_manifest.sqlite`).
3. *(Optional)* Expand **Advanced settings** and tick **Auto select after
   scan** to have the highest-scoring file in each duplicate group marked
   `action="KEEP"` automatically when the scan finishes (#212). Other
   duplicates stay un-decided so deletions still require your explicit
   confirmation. The setting persists across sessions.
4. Click **Start Scan**. Progress is streamed to the log pane.
5. When the scan finishes, click **Close & Load** — the manifest loads
   directly into the review tree.

Source paths are persisted to `settings.json` (`sources.list`) between sessions.

> **Scanning a Google Takeout export?** On export, Google strips the
> original capture date and GPS from the media files themselves — that
> metadata lives only in the per-file `.json` sidecars, which this scanner
> does not read. Run [`galbum`](https://github.com/jackal998/google-album-metadata)
> first (`galbum sync "…/Takeout/Google Photos"`) to write the sidecar
> metadata back into the files, then scan the result. Photo Manager reads
> the now-embedded EXIF dates normally, so Takeout photos date and dedupe
> correctly instead of landing in the `UNDATED` bucket.

### Step 2 — Review groups

The tree shows all files loaded from the manifest.

| Column | Meaning |
|--------|---------|
| **Similarity** | Scanner-assigned match type: `exact` / `similar` / *(empty for unmatched)*. How groups are formed (and why): [`docs/grouping-topology.md`](docs/grouping-topology.md). |
| **Action** | Your decision: `delete` / `keep` / *(empty = undecided)* |
| **Score** | Keep-worthiness ranking in `[0.0, 1.0]` (#187). Within-group rows sort by this descending — best copy at the top. Empty for Live Photo MOV passengers. |
| **Lock** | 🔒 if the row is locked against bulk operations (#182), empty otherwise. Sortable; searchable via the regex dialog as `Locked` / `""`. |
| **File Name** | File name |
| **Folder** | Containing directory |
| **Size (Bytes)** | File size |
| **Group Count** | Number of files in the duplicate group |
| **Creation Date** | File creation date |
| **Shot Date** | EXIF `DateTimeOriginal` |
| **Resolution** | Pixel dimensions (e.g. `4032×3024`) |

**Setting decisions:**

- *Per file*: right-click a file → **Set Action → delete** / **keep** /
  **remove from list**.
- *Multiple files*: select rows (Ctrl/Shift-click), then right-click
  any of them → **Set Action** opens the same submenu and applies the
  chosen decision to every selected row.
- *In bulk*: **Action › Set Action by Field/Regex…** — pick a column,
  describe what to match, choose an action (`delete`, `keep`, or
  `remove from list`). The dialog defaults to **Simple** mode (pick
  contains / starts with / ends with / exactly matches and type plain
  text) and toggles to **Regex** for power users; both modes share a
  live preview pane so you see the matched filenames update as you
  type. The "remove from list" action is a deferred decision: matched
  rows are flagged and dropped on save, no files are moved or deleted.
  Right-clicking a row in the main tree (single or multi-select) and
  in the Execute Action dialog also opens the same dialog.

**Navigating:** double-click a file row to open it in the OS default
viewer (#143); double-click a group header to toggle expand / collapse.

If you close the app with unsaved decisions a prompt appears with
**Save & leave** / **Leave** / **Back**, so you don't lose work
accidentally.

### Step 3 — Save decisions

**File › Save Manifest Decisions…** opens a file picker. Choose the same
path to save in-place or a new path to export a copy. Decisions are written
to the chosen file, and subsequent saves default to that location.

### Step 4 — Execute actions

**Action › Execute Action…** opens a full tree view (same columns as the main
window) showing all groups for final review.

- Right-click any file row → **Set Action** → change its decision before executing.
- If every file in a group is marked `delete`, an amber warning banner appears
  in the dialog. Clicking **Execute** shows a confirmation prompt before proceeding.
- Click **Execute**. With no rows highlighted, every decided row is
  processed. Highlight one or more rows first (Ctrl/Shift-click) to
  scope execution to just those — the button label changes to
  **Execute Action (highlighted)** when in scope (#211).
- The chosen rows are then carried out:
  - `delete` → file sent to the recycle bin (`send2trash`)
  - `keep` → marked as executed in the manifest (no file operation)
  - Files that no longer exist on disk are skipped and listed in a warning dialog.

All decision changes are batch-persisted to SQLite in a single transaction
immediately before execution.

For the full Execute Action feature surface — lock-confirm dialog,
preview pane, dialog geometry persistence, all-delete jump-to banner,
scope-to-highlighted-rows — and for every other user-visible flow in
the app, see [`docs/features.md`](docs/features.md). This Step 1-4
walkthrough is the onboarding path; `docs/features.md` is the
canonical catalogue.

---

## Classification rules

| Condition | Action |
|-----------|--------|
| SHA-256 match | `EXACT` (exact duplicate — lower-priority copy) |
| pHash hamming = 0, both lossy (JPG / HEIC / PNG) | `EXACT` lower-priority format (format duplicate) |
| pHash hamming = 0, one RAW + one lossy | `""` both (complementary — undecided, kept for review) |
| pHash hamming 1–threshold | `REVIEW_DUPLICATE` — needs human triage |
| No EXIF `DateTimeOriginal` | `UNDATED` |
| Everything else | `""` (undecided non-duplicate file) |

> **#433 — `MOVE` action + `dest_path` column removed.** These were the
> handshake to the now-defunct external photo-transfer tool. Unique, dated,
> non-duplicate files now carry the empty action (`""`) — the canonical
> "undecided" state the review UI already renders as a Ref-tier row. Opening
> a pre-#433 manifest auto-migrates: the `dest_path` column is dropped and any
> `action='MOVE'` rows are rewritten to `""`, preserving every row.

**Source priority** (exact duplicates): positional — order in the scan dialog (top = highest priority) or `--source` CLI flag order. No source receives a hardcoded `KEEP`.  
**Format priority** (FORMAT_DUPLICATE): `heic > jpeg > png > others`

---

## Similarity detection — what it catches and what it misses

The scanner uses two signals in sequence:

1. **pHash** (perceptual hash) — a 64-bit fingerprint of the image's macro brightness structure (DCT coefficients). Two images are candidates if their Hamming distance ≤ threshold (default 10 bits out of 64).
2. **Mean-color gate** — computes the average RGB of each image and rejects the pair when the colors differ by more than ~30 units (L2). This prevents images that share a similar composition but are clearly different colors from being flagged.

Neither signal reads faces, object identity, or text.

### What WILL be grouped as `REVIEW_DUPLICATE`

| Scenario | Why |
|----------|-----|
| Same photo saved as both JPEG and HEIC | Identical pHash, similar mean color |
| Same photo re-exported at different quality | pHash changes by ≤ a few bits |
| Burst shots of a static scene | Near-identical DCT structure |
| Minor brightness / contrast edits | DCT coefficients shift only slightly |
| Light crop or small rotation of a photo | pHash remains close when the main subject is unchanged |
| Photos of a uniformly white/black background | Very similar DCT → may group unrelated screenshots if mean color also matches |

### What will NOT be grouped (false negatives)

| Scenario | Why |
|----------|-----|
| Eyes open vs eyes closed | Pupil/eyelid change many DCT coefficients — Hamming distance grows beyond threshold |
| Standing vs sitting / different pose | Body position changes the spatial frequency content significantly |
| Hand-drawn annotation or sticker overlaid on a photo | The added lines/color shift both pHash and mean color |
| Heavy filter (sepia, high-contrast B&W) | Mean-color gate rejects the pair even when pHash is close |
| Major crop that removes the primary subject | pHash diverges once the dominant structure changes |
| Screenshot of a chat → same app, different content | Usually different pHash; but a uniform-background chat UI may slip through if content area is small |

### Tuning the threshold

Lower `--similarity-threshold` (e.g. 6) → fewer false positives, more false negatives.  
Higher threshold (e.g. 14) → more pairs flagged, including pose/blink variants — but also more noise.

The default of 10 is calibrated for a personal photo library where the main risk is missing a true duplicate. All flagged pairs land in `REVIEW_DUPLICATE` for human triage — nothing is deleted automatically.

---

## Keep-worthiness scoring (#187)

Within each duplicate group, every file gets a **composite score** in
`[0.0, 1.0]` measuring how "keep-worthy" it is. The score column sits
at the right of the result tree and within-group rows sort by score
descending — the best copy lands at the top of every group.

Right-click a group header and pick **"Apply best-copy decisions to
this group"** to mark the top scorer `keep` and the rest `delete` in
one batch. Locked rows are silently protected. Live Photo MOV
passengers (the `.mov` that pairs with a `.heic` of the same stem)
inherit their HEIC's decision and are not ranked.

### Algorithm — two tiers

The scorer is a pure function of file attributes (no user-intent
signals). Inspired by Apple Photos' "highest detail + most metadata"
framing and py-image-dedup's open-source multi-factor approach, then
evolved into a two-tier architecture:

**Tier 1 — Categorical penalties** (absolute deductions):
- Format: `RAW=0.00`  `TIFF=0.05`  `HEIC=0.10`  `PNG=0.12`
  `WebP=0.18`  `JPEG/MOV/MP4=0.20`  `GIF=0.35`
- `xmpMM:DerivedFrom` present → `−0.30` (file is a Photoshop/
  Lightroom-exported derivative)

**Tier 2 — Weighted composite** (eight continuous signals, configurable):

| Dimension | Default weight | Signal |
|-----------|---------------:|--------|
| Resolution | 0.25 | Within-group normalised pixel count |
| EXIF completeness | 0.20 | Census tag count vs format baseline (image=16, video=9) |
| Date provenance | 0.15 | DateTimeOriginal vs `shot_date == mtime` (suspicious) |
| Filename | 0.12 | Penalise `copy`, `(N)`, `edited`, `thumb`, `screenshot` |
| GPS | 0.08 | Binary — `GPSLatitude` present |
| Path | 0.08 | Penalise `Downloads/`, `WhatsApp/`, `temp/` segments |
| Live Photo | 0.07 | HEIC with MOV peer > orphan HEIC |
| File size | 0.05 | Low — correlated with resolution same-format |

```
Final = max(0.0, min(1.0, Tier2 − format_penalty − derived_penalty))
```

Live Photo MOV passengers get `score = NULL` and are skipped by
ranking — they inherit the HEIC's decision via pair-cluster logic.

### Re-scoring without re-scanning

Changing weights doesn't require a full re-scan.
`ManifestRepository.rescore(weights)` recomputes scores from cached
raw signals (`pixel_width`, `file_size_bytes`, `exif_tag_count`,
`gps_present`, `xmp_derived`, `shot_date`, `mtime`) in one batched
SQL update — ~1–3 seconds for 100k rows, zero file I/O.

---

## Scanner features

- **SHA-256** exact duplicate detection across all source folders
- **pHash** (imagehash) cross-format detection — JPEG vs HEIC vs RAW vs PNG
- **Hamming distance** configurable near-duplicate threshold
- **Live Photo pairs** — same-stem HEIC + MOV treated as an atomic unit
- **RAW + lossy** — DNG/ARW/CR3 always kept alongside their JPEG/HEIC partner
- **Magic-byte verification** — catches JPEG files saved with a `.HEIC` extension
- **Google Takeout numbering** — `IMG_9556(1).HEIC` handled correctly
- **Edited variants** — `-已編輯`, `-edited`, etc. excluded from pair matching
- **Batch EXIF** — exiftool `-stay_open` chunked at 500 files/call for speed
- **Cached metadata** — `file_size_bytes`, `shot_date`, `creation_date`, `mtime` written
  to the manifest at scan time; load reads from SQLite with zero filesystem round-trips
- **Keep-worthiness scoring** — composite score in `[0.0, 1.0]` per file (#187);
  highest-scoring copy lands at the top of each group, "Apply best-copy"
  right-click action marks it `keep` and the rest `delete` in one batch

---

## Performance

| Scenario | Load time |
|----------|-----------|
| Old manifest (no cached columns) | 10+ min on NAS (filesystem stat per row) |
| New manifest (cached columns) | **< 1 second** (pure SQLite read) |

**How it works:** The scanner stores `file_size_bytes`, `shot_date`, `creation_date`, and
`mtime` in the manifest at scan time (when files are local). On subsequent opens,
`ManifestRepository.load()` reads these from SQLite — no `os.stat()` or Pillow EXIF
calls per row. Old manifests without these columns auto-migrate and fall back to the
original filesystem reads transparently (re-scan once to get the speed benefit).

Manifest loading runs in a **background `QThread`** (`ManifestLoadWorker`) so the UI
stays responsive while the manifest opens.

---

## Project structure

```
photo-manager/
├── run.bat                  # Launch GUI (activates .venv automatically)
├── main.py                  # PySide6 GUI entry point
├── run_all_linters.py       # Runs Black, isort, Ruff, Pylint in sequence
│
├── scanner/                 # Scanner engine (no Qt dependency)
│   ├── media.py             # Extensions, magic-byte detection, filename parsing
│   ├── walker.py            # Directory walk + Live Photo pairing
│   ├── hasher.py            # SHA-256 + pHash + mean-color; single file read
│   ├── exif.py              # Batch EXIF date reads + scoring-signal census via exiftool -stay_open
│   ├── media_extract.py     # MediaExtract canonical extraction schema (#187)
│   ├── dedup.py             # Classification: exact → format → near-dup → UNDATED; mean-color gate
│   ├── scoring.py           # Keep-worthiness scorer — two-tier composite (#187)
│   └── manifest.py          # SQLite writer + summary printer
│
├── app/                     # PySide6 GUI
│   ├── views/
│   │   ├── main_window.py             # Main window — wires all components
│   │   ├── main_window_helpers.py     # Pure-logic helpers extracted from main_window (layer-1 testable)
│   │   ├── tree_model_builder.py      # Builds QStandardItemModel from groups
│   │   ├── constants.py               # Column indices and header labels
│   │   ├── preview_pane.py            # Image/video preview; grid + single-file modes
│   │   ├── preview_pane_helpers.py    # Pure-logic helpers extracted from preview_pane (layer-1 testable)
│   │   ├── image_tasks.py             # Background image loading tasks
│   │   ├── image_tasks_helpers.py     # Pure-logic token-format helpers extracted from image_tasks (layer-1 testable)
│   │   ├── media_utils.py             # Media type helpers for the views layer
│   │   ├── window_state.py            # Shared geometry persistence (MainWindow + dialogs)
│   │   ├── components/
│   │   │   ├── menu_controller.py      # Menu creation + "Set Action" submenu
│   │   │   ├── status_messages.py      # Centralized status-bar copy formatter
│   │   │   └── tree_controller.py      # Tree view interactions
│   │   ├── handlers/
│   │   │   ├── file_operations.py      # set_decision, execute_action
│   │   │   ├── context_menu.py         # Right-click Set Action routing
│   │   │   └── dialog_handler.py       # Dialog lifecycle coordination
│   │   ├── layout/
│   │   │   └── layout_manager.py       # Window layout initialisation
│   │   ├── widgets/
│   │   │   ├── group_media_controller.py  # Grid thumbnail controller per group
│   │   │   └── video_player.py            # Embedded video player widget
│   │   ├── dialogs/
│   │   │   ├── scan_dialog.py              # Scan Sources dialog
│   │   │   ├── execute_action_dialog.py    # Tree review + execute delete/keep
│   │   │   ├── locked_rows_confirm_dialog.py  # Unified "Unlock to proceed?" confirm
│   │   │   └── select_dialog.py            # Set Action by Field/Regex dialog
│   │   └── workers/
│   │       ├── scan_worker.py              # Background QThread for scan pipeline
│   │       └── manifest_load_worker.py     # Background QThread for manifest load
│   └── viewmodels/
│       └── main_vm.py       # Groups/marks logic; loads manifest
│
├── core/                    # Models + service interfaces
│   ├── models.py            # PhotoRecord (action, user_decision, group_id), PhotoGroup
│   └── services/
│       ├── interfaces.py         # DeleteResult, DeletePlan, DeletePlanGroupSummary
│       └── sort_service.py       # SortService
│
├── infrastructure/          # I/O: manifest repo, delete service, image cache
│   ├── manifest_repository.py   # load/save/batch_update_decisions; mark_executed()
│   ├── delete_service.py         # Recycle-bin deletion + audit CSV logging
│   ├── image_service.py          # Thumbnail loading; disk + memory LRU cache
│   ├── i18n.py                   # YAML translator catalog + t() lookup helper
│   ├── logging.py                # loguru configuration and file rotation
│   ├── settings.py               # settings.json loader
│   └── utils.py                  # Shared utilities
│
├── scripts/
│   └── make_qa_images.py    # Generates controlled near-dup test images for QA
│
├── translations/            # Locale catalogs — single source of truth for UI strings
│   ├── en.yml
│   ├── zh_TW.yml
│   └── README.md
│
├── settings.json            # User configuration (source paths, thumbnail cache, …)
│
└── tests/                   # Scanner, infra, viewmodel, GUI handlers
    ├── conftest.py              # Shared fixtures (qapp)
    ├── test_dedup.py
    ├── test_hasher.py
    ├── test_walker.py
    ├── test_manifest_repository.py
    ├── test_settings.py
    ├── test_utils.py
    ├── test_delete_service.py
    ├── test_scanner_exif.py
    ├── test_scanner_manifest.py
    ├── test_scanner_media.py    # magic-byte detection, Takeout filename parsing
    ├── test_main_vm.py
    ├── test_file_operations.py  # set_decision, execute_action, regex remove-from-list
    ├── test_sort_service.py
    ├── test_execute_action_dialog.py
    ├── test_locked_rows_confirm_dialog.py  # LockedRowsConfirmDialog body / verdicts / button states (#182)
    ├── test_context_menu.py
    ├── test_manifest_load_worker.py
    ├── test_scan_dialog.py      # _auto_label, _SourceListWidget, ScanDialog settings
    ├── test_scan_worker.py
    ├── test_select_dialog.py    # initial_field, Set Action signal, settable decision options
    ├── test_status_messages.py  # Pins status-bar copy so qa-explore regexes stay coherent
    ├── test_status_bar_baseline.py  # Persistent baseline widget (#138, #140) — survives temp messages + menu hover
    ├── test_media_utils.py
    ├── test_tree_model_builder.py
    ├── test_menu_controller_manifest_actions.py  # Language picker exclusivity, action toggle lifecycle
    ├── test_i18n.py             # Catalog parity (en ↔ zh_TW), fallback, format-placeholder safety
    └── test_uia_label_coupling.py  # Lint: every _uia.py constant exists in app/*.py or translations/*.yml
```

---

## Configuration (`settings.json`)

```json
{
  "sources": {
    "list": [
      { "path": "D:\\Archive",           "recursive": true  },
      { "path": "\\\\NAS\\MobileBackup", "recursive": true  },
      { "path": "D:\\Inbox",             "recursive": false }
    ],
    "output": "migration_manifest.sqlite"
  },
  "thumbnail_size": 512,
  "sorting": {
    "defaults": [
      { "field": "file_size_bytes", "asc": false },
      { "field": "file_path",       "asc": true  }
    ]
  },
  "ui": {
    "locale": "en",
    "action_dialog": {
      "recent_patterns": [],
      "window_modality": "application"
    }
  }
}
```

Source paths and recursive flags set via **File › Scan Sources…** are saved here
automatically. List order determines dedup priority (index 0 = highest priority).
The regex dialog persists a capped list of recently-used regex patterns under
`ui.action_dialog.recent_patterns`. The optional
`ui.action_dialog.window_modality` key (default `"application"`) accepts
`"window"` to switch the Set Action dialog to `Qt.WindowModal` so the user can
interact with other top-level windows while it's open — note that on Windows
this does NOT set `WS_DISABLED` on the parent the way `ApplicationModal` does
(PR #151), so the main window's menu bar stays clickable when this opt-in is
on. Any unrecognised value falls back to the `ApplicationModal` default.

The main window's position, size, and splitter ratio are persisted across
launches (#141) in a separate `window_state.ini` (Qt `QSettings` INI format)
alongside `settings.json`, under the keys `geometry/main_window` and
`geometry/main_splitter`. Stored under `PHOTO_MANAGER_HOME` when set, so QA
scenarios and dev runs stay isolated from any installed-app state. The
splitter also enforces a 200 px floor on each pane and disables collapse
(#136), preventing the preview pane from being squeezed to invisibility at
the minimum window width.

---

## Languages

The UI ships in **English** (`en`) and **Traditional Chinese** (`zh_TW`).
Switch via **View › Language**; after a Yes/No confirmation the main
window rebuilds in place — no app restart needed. The chosen locale is
persisted in `settings.json` under `ui.locale`.

To add another language, copy `translations/en.yml` to
`translations/<code>.yml`, translate the values, and restart once —
the new locale then appears automatically in the picker for the rest
of the session and on every later launch. Full translator workflow in
[`docs/i18n.md`](docs/i18n.md).

---

## Contributing

New here? Start with [`CONTRIBUTING.md`](CONTRIBUTING.md) — it covers
the bits that aren't obvious from the code (especially: every
user-facing string lives in `translations/*.yml`, not in a Python
literal). Deeper references in [`docs/i18n.md`](docs/i18n.md) and
[`docs/testing.md`](docs/testing.md).

### Claude Code hooks (`.claude/settings.json`)

Three `PreToolUse` hooks fire on `Bash` calls to keep PRs honest:

| Hook | Fires on | Behaviour | Bypass |
|---|---|---|---|
| `scripts/hooks/qa_scenario_guard.py` | `gh pr create` | **Blocks** (exit 2) if user-facing files under `app/views/{handlers,dialogs,components,workers}/` changed without a `qa/scenarios/sNN_*.py` driver. | `[qa-not-needed: <reason>]` in title/body |
| `scripts/hooks/docs_guard.py` | `gh pr create` | **Blocks** if doc-relevant code (new modules under `app/`, `infrastructure/`, `scanner/`, `core/services/`; new tests; qa-scenario changes) lands without a corresponding `README.md` / `docs/*.md` / `CLAUDE.md` / `pyproject.toml` edit. | `[docs-not-needed: <reason>]` in title/body |
| `scripts/hooks/zombie_check.py` | `git commit` | **Warns** (non-blocking) when a QA-relevant commit is about to land and stale Photo Manager / pytest python processes are still running. Lists PIDs + a `taskkill` command. Windows-only. | n/a (warn only) |

Setup: `.claude/settings.json` is gitignored. Copy
`.claude/settings.json.example` to `.claude/settings.json` on a fresh
checkout to install all three.

Server-side mirror: `qa_scenario_guard` and `docs_guard` also run in
CI via [`.github/workflows/pr-gates.yml`](.github/workflows/pr-gates.yml)
(#273), so the same gate decision applies to PRs opened from the web
UI, a fork, mobile, or a machine without `.claude/settings.json`
configured. The bypass tokens work identically server-side (CI parses
PR title + body). `zombie_check` stays local-only — it inspects host
processes and has no CI analogue.

---

