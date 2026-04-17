# Photo Manager

A Windows desktop tool for reviewing and culling large collections of **near-duplicate / similar photos**.

The app does **not** find duplicates itself — that job is delegated to an external tool (see [External Tools](#external-tools-upstream)). Once you have a CSV listing duplicate groups, Photo Manager lets you browse, compare, bulk-select, and safely delete the files you no longer want.

---

## Table of Contents

1. [Background & Motivation](#background--motivation)
2. [Workflow Overview](#workflow-overview)
3. [External Tools (Upstream)](#external-tools-upstream)
   - [What is `.dupproj`?](#what-is-dupproj)
   - [`convert_dupproj_to_csv.py`](#convert_dupproj_to_csvpy)
4. [Scripts at a Glance](#scripts-at-a-glance)
5. [Application Features](#application-features)
6. [CSV Format](#csv-format)
7. [Project Structure](#project-structure)
8. [Getting Started](#getting-started)
9. [Configuration (`settings.json`)](#configuration-settingsjson)
10. [Development](#development)

---

## Background & Motivation

After years of photos synced across iPhones, Google Photos, and local backups, it is common to accumulate thousands of near-identical shots — burst photos, re-downloads, Google Takeout copies, etc. Dedicated duplicate-finder apps can detect these, but their built-in UIs are often limited for bulk human review.

Photo Manager fills that gap:

- Import the duplicate-group data as a CSV.
- Browse groups side-by-side (thumbnails + metadata).
- Apply selection rules automatically (e.g. "keep the largest file per group").
- Manually adjust, then send unwanted files to the Recycle Bin in one go.

---

## Workflow Overview

```
┌─────────────────────────────────┐
│  1. Run a duplicate-finder app  │  (external — see below)
│     e.g. Cisdem Duplicate Finder│
└────────────────┬────────────────┘
                 │  exports  .dupproj  (XML)
                 ▼
┌─────────────────────────────────┐
│  2. convert_dupproj_to_csv.py   │  ← standalone helper script
│     Converts .dupproj → .csv    │
└────────────────┬────────────────┘
                 │  produces  groups.csv
                 ▼
┌─────────────────────────────────┐
│  3. Photo Manager (main.py)     │  ← this application
│     Import CSV, review groups,  │
│     apply rules, delete files   │
└─────────────────────────────────┘
```

---

## External Tools (Upstream)

### What is `.dupproj`?

A `.dupproj` file is the **project/results file exported by [Cisdem Duplicate Finder](https://www.cisdem.com/duplicate-finder-mac.html)** (a macOS/Windows utility).

Internally it is an **XML file** that lists groups of duplicate or similar files found during a scan. Each group (`<GroupItem>`) contains `<Item>` elements, each holding the absolute path of one file in the duplicate set.

Example XML structure:

```xml
<Root>
  <GroupItem>
    <Item>/Users/j/Photos/img_1234.heic_simlar_100</Item>
    <Item>/Users/j/Photos/img_1234_copy.heic</Item>
  </GroupItem>
  <GroupItem>
    <Item>/Users/j/Photos/vacation_01.jpg_simlar_98</Item>
    <Item>/Users/j/Photos/vacation_01_edit.jpg</Item>
  </GroupItem>
</Root>
```

> **Note:** Cisdem appends a `_simlar_{score}` suffix (note the typo — "simlar" not "similar") to indicate the similarity score used for matching. `convert_dupproj_to_csv.py` strips this suffix automatically.

---

### `convert_dupproj_to_csv.py`

A standalone **pre-processing script** — run it once before opening the app.

**What it does:**

1. Parses the `.dupproj` XML file.
2. Assigns sequential `GroupNumber` values (1, 2, 3 …) to each `<GroupItem>`.
3. Strips the `_simlar_{N}` suffix from every file path.
4. Normalises paths to lowercase.
5. Splits each path into `FolderPath` + `FilePath`.
6. Writes a CSV the app can import directly.

**Usage:**

```powershell
python convert_dupproj_to_csv.py <input.dupproj> <output.csv>

# Example:
python convert_dupproj_to_csv.py testdata\20250916.dupproj groups.csv
```

**Output columns produced:**

| Column | Value |
|---|---|
| `GroupNumber` | Sequential integer per duplicate group |
| `IsMark` | `0` (default) |
| `IsLocked` | `0` (default) |
| `FolderPath` | Parent directory with trailing `\` |
| `FilePath` | Full lowercase path |
| `Capture Date` | *(empty — not in XML)* |
| `Modified Date` | *(empty — not in XML)* |
| `FileSize` | *(empty — filled by the app on import)* |

The app will back-fill `FileSize`, `Creation Date`, and `Shot Date` from the actual files when the CSV is imported.

---

## Scripts at a Glance

| Script | Purpose | Run standalone? |
|---|---|---|
| `main.py` | Application entry point — launches the PySide6 GUI | Yes — `python main.py` |
| `convert_dupproj_to_csv.py` | Pre-processing: converts Cisdem `.dupproj` → CSV | Yes — `python convert_dupproj_to_csv.py` |
| `heic_test.py` | Diagnostics: verifies HEIC decoding (Pillow + WIC) on your machine | Yes — `python heic_test.py [file.heic …]` |
| `run_all_linters.py` | Dev utility: runs Black → isort → Ruff → Pylint in sequence | Yes — `python run_all_linters.py` |

### Relationship between scripts

```
convert_dupproj_to_csv.py  ──(CSV)──►  main.py (app)
                                            │
                                            └── uses infrastructure/ (csv_repository, image_service, delete_service)
                                            └── uses core/ (models, services)
                                            └── uses app/ (views, viewmodels)

heic_test.py      ── standalone sanity-check, no dependency on the app's CSV data
run_all_linters.py ── standalone dev helper, analyses app/ core/ infrastructure/
```

`convert_dupproj_to_csv.py` is entirely independent from the app codebase. It only uses Python's standard library (`xml`, `csv`, `pathlib`, `re`).

---

## Application Features

- **Import / Export CSV** — load any conforming CSV; export with all fields refreshed from disk.
- **Grouped tree view** — collapsible groups (`QTreeView`); each group shows photo count.
- **Side-by-side preview** — single-image full preview + group thumbnail grid with LRU cache.
- **HEIC / HEIF support** — via `pillow-heif`; falls back to Windows WIC thumbnails if not installed.
- **Bulk selection rules** — "Select by Field/Regex" dialog; or JSON rule files (e.g. "keep largest per group").
- **Lock** — mark individual records as locked so they are skipped by delete operations.
- **Safe delete** — sends to Recycle Bin via `send2trash`; skips locked items; warns if an entire group would be deleted; writes a CSV delete log.
- **Sort** — multi-key sort (configurable defaults in `settings.json`).
- **Performance** — virtual/lazy loading designed for ~20 000 groups / 50 000 files.

---

## CSV Format

The canonical CSV used by the app (both import and export):

```
GroupNumber, IsMark, IsLocked, FolderPath, FilePath,
Capture Date, Modified Date, Creation Date, Shot Date, FileSize
```

| Column | Type | Notes |
|---|---|---|
| `GroupNumber` | int | Groups files that are duplicates of each other |
| `IsMark` | 0 / 1 | User-managed mark flag |
| `IsLocked` | 0 / 1 | Protected from deletion (app-internal, not a file attribute) |
| `FolderPath` | string | Parent folder with trailing `\` |
| `FilePath` | string | Absolute file path (lowercase) |
| `Capture Date` | datetime / blank | Legacy / source backup date |
| `Modified Date` | datetime / blank | File modified timestamp |
| `Creation Date` | datetime / blank | File system creation time (`os.path.getctime`); filled on import if blank |
| `Shot Date` | datetime / blank | EXIF `DateTimeOriginal`; falls back to `Capture Date` if no EXIF |
| `FileSize` | int (bytes) | Always re-read from disk on import and export |

Human-readable sizes like `1.44MB` in the `FileSize` column are accepted on import and replaced with the actual byte count.

A sample CSV is provided in `samples/sample.csv`.

---

## Project Structure

```
photo-manager/
├── main.py                      # App entry point
├── convert_dupproj_to_csv.py    # Standalone: .dupproj → CSV
├── heic_test.py                 # Standalone: HEIC decoding diagnostics
├── run_all_linters.py           # Dev: run all linters at once
├── settings.json                # Runtime configuration
├── requirements.txt             # Runtime dependencies
├── dev-requirements.txt         # Dev / linting dependencies
├── pyproject.toml               # Tool config (black, isort, ruff, pylint)
├── DESIGN.md                    # Architecture & design decisions (Chinese)
├── LINTING_GUIDE.md             # Linting conventions
│
├── app/
│   ├── views/                   # PySide6 windows, dialogs, widgets
│   │   ├── main_window.py
│   │   ├── preview_pane.py
│   │   ├── image_tasks.py       # Background thumbnail loading
│   │   ├── media_utils.py
│   │   ├── selection_service.py
│   │   ├── tree_model_builder.py
│   │   ├── constants.py
│   │   ├── components/          # Reusable controllers (tree, menu, selection)
│   │   ├── dialogs/             # Select, Delete-confirm, Filters, Rules dialogs
│   │   ├── handlers/            # Event handlers (file ops, dialogs, context menu)
│   │   ├── layout/              # Layout manager
│   │   └── widgets/             # Group media controller, video player
│   └── viewmodels/
│       ├── main_vm.py           # Main ViewModel (groups, sort, selection state)
│       └── photo_vm.py          # Per-photo ViewModel
│
├── core/
│   ├── models.py                # PhotoRecord, PhotoGroup dataclasses
│   └── services/
│       ├── interfaces.py        # IPhotoRepository, IImageService, etc.
│       ├── selection_service.py # Bulk select/unselect logic
│       └── sort_service.py      # Multi-key sort
│
├── infrastructure/
│   ├── csv_repository.py        # Read/write CSV ↔ PhotoRecord
│   ├── image_service.py         # Thumbnail + preview loading, disk/memory cache
│   ├── delete_service.py        # Recycle-bin delete, plan & execute
│   ├── settings.py              # Load/validate settings.json
│   ├── logging.py               # loguru initialisation
│   └── utils.py                 # Shared helpers
│
├── schemas/
│   └── rules.schema.json        # JSON Schema for rule files
│
└── samples/
    ├── sample.csv               # Example CSV for quick testing
    └── sample_rules.json        # Example bulk-selection rule file
```

---

## Getting Started

### Prerequisites

- **Windows 10/11** (the app is Windows-only; WIC thumbnail fallback requires Windows).
- **Python 3.11+**
- *(Optional but recommended)* Microsoft Store → **HEIF Image Extensions** — enables HEIC thumbnails in Windows shell.

### Install

```powershell
# Clone the repo
git clone https://github.com/your-org/photo-manager.git
cd photo-manager

# Create a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install runtime dependencies
pip install -r requirements.txt
```

### Run

```powershell
# Launch the GUI (auto-loads samples/sample.csv if present)
python main.py
```

### Convert a `.dupproj` file

```powershell
# Step 1 — export your Cisdem Duplicate Finder results as a .dupproj file
# Step 2 — convert it:
python convert_dupproj_to_csv.py path\to\results.dupproj path\to\output.csv

# Step 3 — open the app and File > Import the CSV
python main.py
```

### Test HEIC decoding on your machine

```powershell
python heic_test.py "H:\Photos\example.heic"
```

---

## Configuration (`settings.json`)

```jsonc
{
  "thumbnail_size": 512,               // Max thumbnail side in pixels (256 / 512 / 1024)
  "thumbnail_mem_cache": 512,          // In-memory LRU cache size (number of thumbnails)
  "thumbnail_disk_cache_dir": "%LOCALAPPDATA%/PhotoManager/thumbs",
  "delete": {
    "confirm_group_full_delete": true  // Require extra confirmation when deleting all files in a group
  },
  "sorting": {
    "defaults": [
      { "field": "file_size_bytes", "asc": false },  // Largest first
      { "field": "file_path",       "asc": true  }
    ]
  },
  "ui": {
    "locale": "zh-TW"
  }
}
```

---

## Development

### Install dev dependencies

```powershell
pip install -r dev-requirements.txt
```

### Run all linters

```powershell
python run_all_linters.py
```

This runs (in order): **Black** (formatting) → **isort** (import sorting) → **Ruff** (fast lint) → **Pylint** (deep analysis).

Individual tools:

```powershell
python -m black .
python -m isort .
python -m ruff check .
python -m pylint app core infrastructure
```

See `LINTING_GUIDE.md` for project conventions and `pyproject.toml` for tool configuration.

### Branching

- `main` — stable
- `feature/*` — development; merge via PR

Versioning follows [SemVer](https://semver.org/); the first milestone release is `v1.0.0`.
