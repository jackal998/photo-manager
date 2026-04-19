# Photo Manager

A Windows tool for **deduplication scanning and review** of large personal photo collections.

Produces `migration_manifest.sqlite` consumed by **[photo-transfer](https://github.com/jackal998/photo-transfer)** for the actual file migration.

---

## Workflow overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. SCAN (photo-manager)                                                    │
│     GUI: File > Scan Sources…  —or—  CLI: python scan.py …                │
│     Walks iphone / takeout / jdrive, hashes every file,                    │
│     writes  migration_manifest.sqlite                                       │
│                                                                             │
│  2. REVIEW (photo-manager)                                                  │
│     GUI: File > Open Manifest…                                              │
│     Inspect every group — col 0 shows match type (exact / similar / empty) │
│     Mark files with Sel checkboxes, then use                               │
│       File > Set Action > delete / keep  to record your decision           │
│     Right-click a single file → Set Action → delete / keep (per-file)     │
│     File > Save Manifest Decisions… persists decisions to the manifest     │
│                                                                             │
│     CLI alternative: python review.py … for REVIEW_DUPLICATE triage       │
│                                                                             │
│  3. EXECUTE (photo-manager)                                                 │
│     File > Execute Action…  opens a review dialog listing all planned      │
│     operations.  Confirm to:                                                │
│       • delete → send file to recycle bin                                  │
│       • keep   → mark as executed in the manifest                          │
│                                                                             │
│  4. MIGRATE (photo-transfer)                                                │
│     python migrate.py --manifest migration_manifest.sqlite --dest-root … │
│     Copies every MOVE row to the destination tree                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Getting started

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
```

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

---

## Usage — GUI

The PySide6 desktop app is the primary interface. Launch it with `run.bat`.

### Step 1 — Scan sources

**File › Scan Sources…** opens the scan dialog.

1. Fill in (or Browse to) each source folder:
   - **iphone** — iPhone backup folder (NAS or local)
   - **takeout** — Google Takeout export folder
   - **jdrive** — J:\圖片 archive (or any third source)
   - **output** — path for `migration_manifest.sqlite`
2. Click **Start Scan**. Progress is streamed to the log pane.
3. When the scan finishes, click **Close & Load** — the manifest loads
   directly into the review tree.

Source paths are remembered in `settings.json` for the next session.

### Step 2 — Review groups

The tree shows all files loaded from the manifest.

| Column | Meaning |
|--------|---------|
| **Match** (col 0) | Scanner-assigned match type: `exact` / `similar` / *(empty for unmatched)* |
| **Sel** | Checkbox — select files for batch actions |
| **Action** (col 8) | Your decision: `delete` / `keep` / *(empty = undecided)* |

**Setting decisions:**

- *Per file*: right-click a file → **Set Action → delete** or **keep**.
- *Batch*: tick **Sel** on the files you want to act on, then
  **File › Set Action › delete** (or **keep**) applies to all checked files.

### Step 3 — Save decisions

**File › Save Manifest Decisions…** writes `delete` / `keep` decisions back
to the SQLite manifest.  Decisions persist across sessions.

### Step 4 — Execute actions

**File › Execute Action…** opens a confirmation dialog listing every planned
operation.  Click **Execute** to carry out:

- `delete` → file is sent to the recycle bin (`send2trash`)
- `keep` → marked as executed in the manifest (no file operation)

After execution, the manifest reflects which files were processed.

---

## Usage — CLI

### `scan.py` — Deduplication scanner

```powershell
# Full scan
python scan.py `
  --source iphone="\\NAS\Photos\MobileBackup\iPhone" `
  --source takeout="D:\Downloads\Takeout\Google 相簿" `
  --source jdrive="J:\圖片" `
  --output migration_manifest.sqlite

# Bounded debug run — stops after 200 files per source
python scan.py ... --limit 200

# Dry run — prints summary, does not write a manifest
python scan.py ... --dry-run

# Tighter near-duplicate threshold (default: 10 Hamming bits)
python scan.py ... --similarity-threshold 6
```

### `review.py` — Near-duplicate review CLI

Interactive terminal triage for `REVIEW_DUPLICATE` rows.

```powershell
python review.py --manifest migration_manifest.sqlite

# Include rows already resolved in a previous session
python review.py --manifest migration_manifest.sqlite --show-all
```

Per-pair choices: **[s]** skip candidate · **[k]** keep both · **[d]** defer  
Decisions persist immediately — the session is resumable at any time.

---

## Classification rules

| Condition | Action |
|-----------|--------|
| SHA-256 match | `EXACT` (exact duplicate — lower-priority copy) |
| pHash hamming = 0, both lossy (JPG / HEIC / PNG) | `EXACT` lower-priority format (format duplicate) |
| pHash hamming = 0, one RAW + one lossy | `MOVE` both (complementary — always kept together) |
| pHash hamming 1–threshold | `REVIEW_DUPLICATE` — needs human triage |
| No EXIF `DateTimeOriginal` | `UNDATED` |
| iPhone source | `KEEP` (reference copy, stays in place) |
| Everything else | `MOVE` |

**Source priority** (exact duplicates): `iphone > takeout > jdrive`  
**Format priority** (FORMAT_DUPLICATE): `heic > jpeg > png > others`

---

## Scanner features

- **SHA-256** exact duplicate detection across all three sources
- **pHash** (imagehash) cross-format detection — JPEG vs HEIC vs RAW vs PNG
- **Hamming distance** configurable near-duplicate threshold
- **Live Photo pairs** — same-stem HEIC + MOV treated as an atomic unit
- **RAW + lossy** — DNG/ARW/CR3 always kept alongside their JPEG/HEIC partner
- **Magic-byte verification** — catches JPEG files saved with a `.HEIC` extension
- **Google Takeout numbering** — `IMG_9556(1).HEIC` handled correctly
- **Edited variants** — `-已編輯`, `-edited`, etc. excluded from pair matching
- **Batch EXIF** — exiftool `-stay_open` chunked at 500 files/call for speed

---

## Project structure

```
photo-manager/
├── run.bat                  # Launch GUI (activates .venv automatically)
├── main.py                  # PySide6 GUI entry point
├── scan.py                  # Deduplication scanner CLI
├── review.py                # REVIEW_DUPLICATE triage CLI
│
├── scanner/                 # Scanner engine (no Qt dependency)
│   ├── media.py             # Extensions, magic-byte detection, filename parsing
│   ├── walker.py            # Directory walk + Live Photo pairing
│   ├── hasher.py            # SHA-256 + pHash (Pillow / pillow-heif / rawpy)
│   ├── exif.py              # Batch EXIF date reads via exiftool -stay_open
│   ├── dedup.py             # Classification: exact → format → near-dup → UNDATED
│   └── manifest.py          # SQLite writer + summary printer
│
├── app/                     # PySide6 GUI
│   ├── views/
│   │   ├── main_window.py   # Main window — wires all components
│   │   ├── tree_model_builder.py  # Builds QStandardItemModel from groups
│   │   ├── constants.py     # Column indices and header labels
│   │   ├── components/
│   │   │   ├── menu_controller.py     # Menu creation + "Set Action" submenu
│   │   │   ├── tree_controller.py     # Tree view interactions
│   │   │   └── selection_controller.py
│   │   ├── handlers/
│   │   │   ├── file_operations.py     # set_decision, batch_set_decision, execute_action
│   │   │   └── context_menu.py        # Right-click Set Action routing
│   │   ├── dialogs/
│   │   │   ├── scan_dialog.py         # Scan Sources dialog
│   │   │   └── execute_action_dialog.py  # Review + execute delete/keep operations
│   │   └── workers/
│   │       └── scan_worker.py         # Background QThread for scan pipeline
│   └── viewmodels/
│       └── main_vm.py       # Groups/marks logic; loads CSV or manifest
│
├── core/                    # Models + service interfaces
│   └── models.py            # PhotoRecord (action, user_decision), PhotoGroup
├── infrastructure/          # I/O: CSV repo, manifest repo, delete service, settings
│   └── manifest_repository.py  # load/save user_decision; update_decision(); mark_executed()
│
├── settings.json            # User configuration (source paths, thumbnail cache, …)
│
└── tests/                   # 190+ tests — scanner, infra, viewmodel, GUI handlers
    ├── conftest.py              # Shared fixtures (qapp)
    ├── test_dedup.py
    ├── test_hasher.py
    ├── test_walker.py
    ├── test_review.py
    ├── test_manifest_repository.py
    ├── test_settings.py
    ├── test_utils.py
    ├── test_csv_repository.py
    ├── test_delete_service.py
    ├── test_scanner_exif.py
    ├── test_scanner_manifest.py
    ├── test_main_vm.py
    ├── test_file_operations.py  # set_decision, batch_set_decision
    ├── test_execute_action_dialog.py
    └── test_context_menu.py
```

---

## Configuration (`settings.json`)

```json
{
  "sources": {
    "iphone":  "",
    "takeout": "",
    "jdrive":  "",
    "output":  "migration_manifest.sqlite"
  },
  "thumbnail_size": 512,
  "sorting": {
    "defaults": [
      { "field": "file_size_bytes", "asc": false },
      { "field": "file_path",       "asc": true  }
    ]
  }
}
```

Source paths set via **File › Scan Sources…** are saved here automatically.

---

