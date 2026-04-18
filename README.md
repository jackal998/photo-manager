# Photo Manager

A Windows tool for **deduplication scanning and review** of large personal photo collections.

Produces `migration_manifest.sqlite` consumed by **[photo-transfer](https://github.com/jackal998/photo-transfer)** for the actual file migration.

---

## Workflow

```
scan.py   →   migration_manifest.sqlite   →   review.py   →   photo-transfer/migrate.py
  │                                              │
  └── walks sources, hashes files,              └── interactive triage of
      classifies duplicates                          REVIEW_DUPLICATE rows
```

---

## Tools

### `scan.py` — Deduplication scanner

Walks three source directories, computes SHA-256 + pHash for every media file, detects exact and cross-format duplicates, and writes a non-destructive `migration_manifest.sqlite`.

```powershell
# Full scan
python scan.py `
  --source iphone="\\LinXiaoYun\home\Photos\MobileBackup\iPhone" `
  --source takeout="D:\Downloads\Takeout\Google 相簿" `
  --source jdrive="J:\圖片" `
  --output migration_manifest.sqlite

# Dry run — summary only, no file written
python scan.py ... --dry-run

# Tighter near-duplicate threshold (default: 10)
python scan.py ... --similarity-threshold 6
```

**Classification rules:**

| Condition | Action |
|-----------|--------|
| SHA-256 match | `SKIP` (EXACT_DUPLICATE) |
| pHash hamming = 0, both lossy (JPG/HEIC/PNG) | `SKIP` lower format priority (FORMAT_DUPLICATE) |
| pHash hamming = 0, one RAW + one lossy | `MOVE` both (complementary — kept together) |
| pHash hamming 1–threshold | `REVIEW_DUPLICATE` (human review) |
| No EXIF `DateTimeOriginal` | `UNDATED` |
| iPhone source | `KEEP` (stays in place, used as dedup reference) |
| Everything else | `MOVE` |

**Source priority** (for exact duplicates): `iphone > takeout > jdrive`  
**Format priority** (for FORMAT_DUPLICATE): `heic > jpeg > png > others`

### `review.py` — Near-duplicate review CLI

Interactive terminal tool for triaging `REVIEW_DUPLICATE` rows before migration.

```powershell
python review.py --manifest migration_manifest.sqlite

# Re-show already-resolved rows
python review.py --manifest migration_manifest.sqlite --show-all
```

Choices per pair: **[s]** skip candidate · **[k]** keep both · **[d]** defer  
Decisions are persisted immediately — session is resumable.

---

## Scanner features

- **SHA-256** exact duplicate detection across all sources
- **pHash** cross-format duplicate detection (JPEG vs HEIC vs RAW vs PNG)
- **Hamming distance** near-duplicate similarity threshold (configurable)
- **Live Photo pairs** — same-stem HEIC + MOV treated as atomic units
- **RAW + lossy** — always kept together, never marked as duplicates
- **Magic-byte verification** — catches JPEG files saved as `.HEIC`
- **Takeout numbering** — `IMG_9556(1).HEIC` duplicate numbering handled
- **Edited variants** — `-已編輯`, `-edited`, etc. excluded from pairing
- **Batch EXIF** — exiftool `-stay_open` for fast date reads across all formats

---

## Project structure

```
photo-manager/
├── scan.py                  # Deduplication scanner CLI
├── review.py                # REVIEW_DUPLICATE interactive triage
├── main.py                  # PySide6 GUI (legacy review app)
│
├── scanner/                 # Scanner engine (no Qt dependency)
│   ├── media.py             # Extensions, magic-byte detection, filename parsing
│   ├── walker.py            # Directory walk + Live Photo pairing
│   ├── hasher.py            # SHA-256 + pHash (Pillow / pillow-heif / rawpy)
│   ├── exif.py              # Batch EXIF date reads via exiftool stay_open
│   ├── dedup.py             # Classification: exact → format → near-dup → UNDATED
│   └── manifest.py          # SQLite writer + summary printer
│
├── app/                     # PySide6 GUI (legacy)
├── core/                    # Models + services
├── infrastructure/          # CSV repo, image service, delete service
│
└── tests/
    ├── test_hasher.py
    ├── test_walker.py
    ├── test_dedup.py
    └── test_review.py
```

---

## Getting started

### Prerequisites

- Windows 10/11, Python 3.11+
- [exiftool](https://exiftool.org/) on `PATH` (required for EXIF date extraction)
- *(Optional)* [rawpy](https://pypi.org/project/rawpy/) for RAW file support

### Install

```powershell
git clone https://github.com/jackal998/photo-manager.git
cd photo-manager
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run tests

```powershell
.venv\Scripts\python -m pytest
```

---

## Legacy GUI (`main.py`)

The original PySide6 desktop app for reviewing duplicate groups imported from a CSV (previously produced by Cisdem Duplicate Finder) is still available via `python main.py`. The new `scan.py` / `review.py` workflow replaces the Cisdem dependency for new scans.
