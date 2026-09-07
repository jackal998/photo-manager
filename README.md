# Photo Manager

A Windows tool for **deduplication scanning and review** of large personal photo collections.

Produces `migration_manifest.sqlite` recording each file's dedup classification and review decision. (The legacy `MOVE` action + `dest_path` handshake to the external **[photo-transfer](https://github.com/jackal998/photo-transfer)** tool was removed in #433 — see the classification table below.)

---

## Workflow overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. SCAN (photo-manager)                                                    │
│     File > Scan Sources…                                                    │
│     Walks any number of source folders, hashes every file,                  │
│     writes  migration_manifest.sqlite                                       │
│                                                                             │
│  2. REVIEW (photo-manager)                                                  │
│     File > Open Manifest…                                                   │
│     Inspect every group — col 0 (Similarity) shows match strength           │
│     Set decisions per file or in bulk:                                      │
│       Right-click a file → Set Action → delete / keep                       │
│       Action > Set Action by Field… → pattern batch across any column       │
│     Every decision is persisted to the manifest as you make it              │
│                                                                             │
│  3. EXECUTE (photo-manager)                                                 │
│     Action > Execute Action…  opens a full tree review (same columns as     │
│     the main window).  Right-click rows to change decisions before          │
│     confirming.  If every file in a group is marked delete, a               │
│     confirmation dialog appears before proceeding.  Confirm to:             │
│       • delete → send file to recycle bin                                   │
│       • keep   → left in review; no state written                           │
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

ffmpeg is **not** a prerequisite: the bundle ships its own `ffmpeg.exe` / `ffprobe.exe` plus their libraries in `_internal\ffmpeg\` (a pinned LGPL *shared* build, ~128 MB unpacked / ~54 MB of the download — see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)), which is what lets the web UI play HEVC video on browsers that cannot decode it natively. To use a different build, put a complete set (`ffmpeg.exe`, `ffprobe.exe` and any DLLs they need) in a folder named `ffmpeg` next to `photo-manager.exe` — the app prefers that over the bundled one and over `PATH`.

> **SmartScreen note:** the binary is unsigned, so on first launch Windows shows *"Windows protected your PC"*. Click **More info → Run anyway**. The warning is expected and will disappear once we publish a signed release.

`settings.json` is written next to `photo-manager.exe`, so the extracted folder is portable — copy it to a USB stick and your config travels with it.

---

## Getting started (from source)

### Prerequisites

- Windows 10/11, Python 3.11+, Node 20+ (to build the frontend bundle)
- [exiftool](https://exiftool.org/) on `PATH` (required for EXIF date extraction)
- The **Microsoft Edge WebView2 Runtime** — the app window renders through
  it. Most Windows 10/11 machines already have it (it ships with Windows
  Update / Edge); `launcher.py` checks for it up front and raises a clear
  error naming the install URL rather than opening a blank window. If it's
  missing, install the Evergreen runtime from
  [Microsoft's WebView2 page](https://developer.microsoft.com/microsoft-edge/webview2/).
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

cd frontend
npm ci
npm run build          # writes frontend/dist — the app serves this bundle
cd ..
```

`settings.json` is gitignored. It may contain personal folder paths; edit it to add your sources after copying.

`frontend/dist` is gitignored too, so `npm run build` is a required step on
a fresh checkout and after any change under `frontend/src/`.

### Launch

```powershell
run.bat          # activates .venv and starts launcher.py
# or
.venv\Scripts\python launcher.py
```

`launcher.py` starts the FastAPI app under uvicorn on loopback
(`127.0.0.1:8765` by default — override with `PHOTO_MANAGER_WEB_PORT`),
waits for it to report healthy, then opens a native
[pywebview](https://pywebview.flowrl.com/) window pointed at it. The result
is a desktop window, not a browser tab, but the UI inside it is the React
app served by the same process.

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

# Layer 3 — full app exercise via /qa-explore. Drives the running web
# shell with Playwright through scripted scenarios to catch UI /
# state-transition / copy regressions AND validates real third-party
# boundaries (exiftool, send2trash) on happy paths. Needs the app
# running; see qa/web/INSTALL.md for the one-time browser install.
.venv\Scripts\python -m qa.web._batch --base-url http://127.0.0.1:8765
```

The frontend has its own unit suite (vitest / jsdom), run from
`frontend/`:

```powershell
npm test
```

**The full strategy — what each layer catches, what it misses, and the
per-module residual risk — lives in [`docs/testing.md`](docs/testing.md).**
Read that before adding tests for a new feature. Short version:

| Layer | Catches | Misses |
|---|---|---|
| 1 — Unit + mocks (CI) | Refactoring bugs, parser logic | Real third-party behavior |
| 2 — Integration (local, on-demand) | Spot-tests for specific boundary bugs already hit (exiftool / send2trash / rawpy edge cases) | UI behaviour; anything you haven't written a spot-test for |
| 3 — `/qa-explore` (local + CI) | Label drift, dialog regressions, state-transition bugs, boundary happy paths | Anything off the scripted path |
| Probes — `tests/test_source_probes.py` + `tests/test_web_dom_probes.py` + sNN soft-probe blocks | Cross-cutting invariants: sweep-shaped source rules, testid drift, translation passthroughs, menu-gating drift ([#243](https://github.com/jackal998/photo-manager/issues/243)) | Anything not framed as a structural invariant |

**No test padding.** A test that exists only to clear a coverage gate
is metric gaming, not engineering — see the testing rules in
[`CLAUDE.md`](CLAUDE.md) for the explicit list of patterns to avoid.

---

## Usage

Launch with `run.bat`; the window that opens is the app. Everything below
happens inside it.

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

**Navigating:** click a group header to toggle expand / collapse;
double-click the preview tile to open the full-resolution viewer
(Escape closes it). Arrow keys move a roving cursor through the rows,
and `d` / `k` set delete / keep on the selection (#709).

If you close the window while a scan is running you get a Leave / Stay
prompt; leaving cancels the scan rather than orphaning the worker (#703).
Decisions themselves are written to SQLite as you make them, so there is
no separate unsaved-work prompt.

### Step 3 — Decisions persist as you make them

Every decision is written straight to the manifest via
`PATCH /api/decision`, so there is no separate save step and nothing to
lose by closing the window. (`POST /api/save` — exporting a snapshot to a
second file — is plumbed on the backend but not surfaced in the menu; see
[`docs/features.md`](docs/features.md).)

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
  - `keep` → left in review; no manifest state written (the `outcome` column records `deleted` / `ignored` only)
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

## Architecture

The app is a **localhost web app** wearing a desktop window: a FastAPI +
SSE backend serving a React + TanStack frontend, opened through a native
[pywebview](https://pywebview.flowrl.com/) shell rather than a browser
tab. The UI drives the same headless `core/` + `scanner/` +
`infrastructure/` engine the CLI scanner uses — none of those packages
imports anything UI-specific, which is what `tests/test_web_qt_free.py`
pins.

See [`docs/design/web-port-tech-design.md`](docs/design/web-port-tech-design.md)
for the architecture, and the `### Web —` entries throughout
[`docs/features.md`](docs/features.md) for the full feature surface —
every dialog, endpoint, and the divergences recorded during the port.

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
- **Sub-second shot time** — `subsec_time_original` and `offset_time_original`
  columns (#820) hold EXIF `SubSecTimeOriginal` / `OffsetTimeOriginal` as text
  (e.g. `"087"`, `"+09:00"`), read on both extraction paths: the exiftool tag
  list for HEIC/RAW/video and the in-memory PIL pass for JPEG. `shot_date`
  keeps its whole-second format on purpose, so these are what separate frames
  of a burst shot inside the same second. Both are `NULL` on manifests written
  before #820 and on files whose camera wrote neither tag — a re-scan fills
  them. No scoring weight reads them yet
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

Manifest loading runs on the FastAPI threadpool (a plain `def` route), so the
event loop — and therefore the UI — stays responsive while the manifest opens.

---

## Project structure

```
photo-manager/
├── run.bat                  # Launch the app (activates .venv automatically)
├── launcher.py              # Entry point — uvicorn + the pywebview window
├── run_all_linters.py       # Runs Black, isort, Ruff, Pylint in sequence
│
├── scanner/                 # Scanner engine (headless — no UI dependency)
│   ├── media.py             # Extensions, magic-byte detection, filename parsing
│   ├── walker.py            # Directory walk + Live Photo pairing
│   ├── hasher.py            # SHA-256 + pHash + mean-color; single file read
│   ├── exif.py              # Batch EXIF date reads + scoring-signal census via exiftool -stay_open
│   ├── media_extract.py     # MediaExtract canonical extraction schema (#187)
│   ├── dedup.py             # Classification: exact → format → near-dup → UNDATED; mean-color gate
│   ├── scoring.py           # Keep-worthiness scorer — two-tier composite (#187)
│   └── manifest.py          # SQLite writer + summary printer
│
├── app/web/                 # FastAPI backend — the only UI server
│   ├── main.py              # App factory; mounts the SPA + every router
│   ├── models.py            # Pydantic request / response bodies
│   ├── registry.py          # In-process scan registry (one live scan at a time)
│   ├── security.py          # Loopback-only + allowed-roots enforcement
│   └── routes/
│       ├── scan.py          # POST /api/scan + the SSE progress stream
│       ├── review.py        # GET /api/manifest, PATCH /api/decision, /api/lock
│       ├── action.py        # Field/regex bulk-decide
│       ├── execute.py       # Execute plan + run, POST /api/save
│       ├── image.py         # Thumbnails and full-res reads
│       ├── media.py         # Range-streamed video + on-the-fly transcode
│       ├── fs.py            # Folder browser for the scan dialog
│       ├── settings.py      # GET / PATCH the settings.json keys the UI owns
│       ├── i18n.py          # Serves the shared YAML catalog to the client
│       └── health.py        # /api/health — what launcher.py polls on boot
│
├── frontend/                # React + TanStack client (built to frontend/dist)
│   ├── src/components/      # ResultTree, PreviewPane, MenuBar, dialogs, execute/
│   ├── src/hooks/           # useScanSSE, useDecisionShortcuts, useOverlayGeometry
│   ├── src/store/           # useAppStore — selection, decisions, manifest state
│   ├── src/lib/             # Pure helpers (column widths, regex escape, prune)
│   ├── src/api/             # Typed client for the routes above
│   └── src/i18n/            # useT + the locale store
│
├── core/                    # Models + UI-agnostic application services
│   ├── models.py            # PhotoRecord (action, user_decision, group_id), PhotoGroup
│   ├── app_service/         # scan_runner, review_service, execute_service, dtos…
│   └── services/
│       ├── interfaces.py         # DeleteResult, DeletePlan, DeletePlanGroupSummary
│       ├── auto_select.py        # Auto-select keepers after a scan (#212)
│       └── sort_service.py       # SortService
│
├── infrastructure/          # I/O: manifest repo, delete service, image cache
│   ├── manifest_repository.py   # load/save/batch_update_decisions; finalize_outcome()
│   ├── delete_service.py         # Recycle-bin deletion + audit CSV logging
│   ├── image_service.py          # Thumbnail loading; disk + memory LRU cache
│   ├── transcode_service.py      # HEVC → H.264 via the bundled ffmpeg
│   ├── device_key.py             # Per-device read budgeting for the scan pipeline
│   ├── i18n.py                   # YAML translator catalog + t() lookup helper
│   ├── logging.py                # loguru configuration and file rotation
│   ├── settings.py               # settings.json loader
│   └── utils.py                  # Shared utilities
│
├── scripts/
│   ├── make_qa_images.py    # Generates controlled near-dup test images for QA
│   └── hooks/               # PreToolUse / CI gate scripts (see Contributing)
│
├── qa/web/                  # Layer-3 Playwright drivers + the scenario map
│
├── translations/            # Locale catalogs — single source of truth for UI strings
│   ├── en.yml
│   ├── zh_TW.yml
│   └── README.md
│
├── settings.json            # User configuration (source paths, thumbnail cache, …)
│
└── tests/                   # Layer 1 — scanner, infra, core services, web routes
    ├── conftest.py              # Shared fixtures
    ├── test_dedup.py
    ├── test_hasher.py
    ├── test_walker.py
    ├── test_manifest_repository.py
    ├── test_manifest_skip_reconcile.py  # #821 recycle-bin rows dismissed on open
    ├── test_settings.py
    ├── test_utils.py
    ├── test_delete_service.py
    ├── test_scanner_exif.py
    ├── test_scanner_manifest.py
    ├── test_scanner_media.py    # magic-byte detection, Takeout filename parsing
    ├── test_scan_pipeline.py    # Reader / compute split + the bounded queue
    ├── test_scan_runner.py      # core.app_service.scan_runner + its no-UI-import guard
    ├── test_review_service.py   # set_decisions, lock gating, outcome column
    ├── test_auto_select.py
    ├── test_sort_service.py
    ├── test_scoring.py          # Keep-worthiness scorer (#187)
    ├── test_transcode_service.py
    ├── test_launcher.py         # Boot path, health poll, WebView2 preflight
    ├── test_web_*.py            # One per router, plus CORS / security / SPA mount
    ├── test_web_qt_free.py      # Pins the headless seam: the web stack imports no UI toolkit
    ├── test_source_probes.py    # Sweep-shaped source invariants (#243)
    ├── test_web_dom_probes.py   # Static probes over the qa/web scaffold + testid parity
    ├── test_i18n.py             # Catalog parity (en ↔ zh_TW), fallback, format-placeholder safety
    ├── test_docs_guard.py       # The three PR gates under scripts/hooks/
    ├── test_qa_scenario_guard.py
    └── test_zombie_check.py
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
    "prune_singletons": "ask",
    "action_dialog": {
      "recent_patterns": []
    }
  }
}
```

Source paths and recursive flags set via **File › Scan Sources…** are saved here
automatically. List order determines dedup priority (index 0 = highest priority).
The Set Action dialog persists a capped list of recently-used patterns under
`ui.action_dialog.recent_patterns`. `settings.json` is read and written through
`GET` / `PATCH /api/settings`, which only exposes the keys the UI owns — the
scanner-tuning keys stay file-only.

Window geometry, column widths and panel splits are per-viewer state and live
in the client's `localStorage`, not in `settings.json` (overlay rects under
`pm.overlay-geometry.*`). `PHOTO_MANAGER_HOME`, when set, relocates
`settings.json` and the manifest so QA scenarios and dev runs stay isolated
from any installed-app state.

---

## Languages

The UI ships in **English** (`en`) and **Traditional Chinese** (`zh_TW`).
Switch via **View › Language**; the UI re-renders in place — no app
restart needed. The chosen locale is persisted in `settings.json` under
`ui.locale`, and the client fetches the catalog from
`GET /api/i18n/{locale}`.

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
| `scripts/hooks/qa_scenario_guard.py` | `gh pr create` | **Blocks** (exit 2) if user-facing files under `frontend/src/` or `app/web/` changed without a `qa/web/scenarios/sNN_*.py` driver. Vitest specs and `frontend/src/test/` are excluded. | `[qa-not-needed: <reason>]` in title/body |
| `scripts/hooks/docs_guard.py` | `gh pr create` | **Blocks** if doc-relevant code (new modules under `app/web/`, `frontend/src/`, `infrastructure/`, `scanner/`, `core/services/`; new tests; qa-scenario changes) lands without a corresponding `README.md` / `docs/*.md` / `CLAUDE.md` / `pyproject.toml` edit. A behaviour-bearing modify under `app/web/routes/` or `frontend/src/components/` needs `docs/features.md` specifically. | `[docs-not-needed: <reason>]` in title/body |
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

A third gate script, `scripts/hooks/news_guard.py`, has no client half
at all: [`.github/workflows/news-gate.yml`](.github/workflows/news-gate.yml)
calls it to require a `news/<PR>.<type>` changelog fragment, and the
fragment's filename needs the PR number, which doesn't exist until
after `gh pr create`. Its bypass is `[skip-news: <reason>]`. All three
tokens share one enforced rule — a blank reason, the literal
`<reason>` placeholder, and an unclosed bracket are rejected; see
[`news/README.md`](news/README.md) § Bypass for the canonical
statement.

---

