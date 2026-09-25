---
name: photo-scanner-patterns
description: >
  Patterns for Python media-file scanning pipelines. Use this skill whenever
  you're working with: exiftool (slow per-file calls, -stay_open batching),
  PIL EXIF extraction (getexif() returning None, DateTimeOriginal missing after
  convert()), pHash / imagehash (hamming distances wrong after JPEG save, flat
  images all colliding on 8000000000000000), reading photos from NAS (double
  file-open, ThreadPoolExecutor worker count, SMB latency), deduplication logic
  (near-duplicate groups not connecting transitively, union-find for group_id),
  QThread background workers with progress signals and cancellation, SQLite WAL
  mode or additive ALTER TABLE migrations, or writing EXIF into test JPEG files
  without piexif. Covers the full scan pipeline: single-read SHA-256 + pHash +
  EXIF date from one read_bytes(), tiered exiftool only for HEIC/RAW/MOV/MP4,
  and the outer-vs-inner loop placement bug in pairwise near-duplicate scanners.
  Also covers Google Takeout sidecar matching (May-2026+ `.supplemental-metadata.json`
  family with truncation variants, dupe `(N)` disambiguator placement, heavy
  basename truncation requiring title-field fallback) and Windows-specific
  scanner gotchas (trailing-period folder names hidden by Win32, NTFS
  case-collisions, large-video split-out at Takeout archive root).
origin: project-retrospective
---

# Photo Scanner Patterns

Patterns extracted from the photo-manager project: a NAS-aware deduplication scanner
(SHA-256 + pHash + exiftool) with a web review UI (FastAPI + React) backed by SQLite.
The Qt/QThread examples below come from its earlier PySide6 client; the
patterns carry over to any background worker.

## When to Activate

- Building a file-scanning pipeline that reads media from NAS or slow storage
- Working with pHash / perceptual hashing for image similarity
- Extracting EXIF dates at scale
- Designing SQLite schemas that evolve over time without migrations being painful
- Writing Qt background workers (QThread) with cancellation support

---

## 1. File I/O — Single-Read Rule

**Pattern: derive everything from one `path.read_bytes()` call.**

Calling `compute_sha256(path)` then `compute_phash(path)` opens the file twice.
Over a 50 k-file NAS share this doubles network traffic. Instead:

```python
def compute_hashes(path: Path, file_type: str) -> tuple[str, str | None, str | None]:
    """Returns (sha256, phash_or_None, raw_exif_date_or_None). One file read."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    with Image.open(io.BytesIO(data)) as pil_img:
        raw_date = _raw_exif_date(pil_img)   # BEFORE convert() — see §3
        img = pil_img.convert("RGB")
        img.load()

    phash_str = str(imagehash.phash(img))
    return sha, phash_str, raw_date
```

**Videos (mp4/mov):** stream SHA in 64 KB chunks; never load into RAM:

```python
if file_type in ("mp4", "mov", "gif"):
    return compute_sha256(path), None, None   # streaming SHA, no PIL
```

---

## 2. Thread Pool for I/O-Bound Scanning

PIL decode and `hashlib` both release the GIL → `ThreadPoolExecutor` is effective
even for local SSD, and dramatically better on NAS (latency overlap).

```python
cancel_flag = threading.Event()

def _hash_one(idx_record: tuple) -> tuple:
    idx, record = idx_record
    if cancel_flag.is_set():
        return idx, None          # fast exit for cancelled tasks
    sha256, phash, raw_date = compute_hashes(record.path, record.file_type)
    pil_date = parse_exif_date(raw_date) if raw_date else None
    return idx, HashResult(record=record, sha256=sha256, phash=phash, exif_date=pil_date)

hash_results = [None] * len(records)
with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {pool.submit(_hash_one, (i, r)): i for i, r in enumerate(records)}
    for future in as_completed(futures):
        if self.isInterruptionRequested():        # Qt cancellation check
            cancel_flag.set()
            pool.shutdown(wait=False, cancel_futures=True)
            self.failed.emit("Scan cancelled.")
            return
        idx, result = future.result()
        if result is not None:
            hash_results[idx] = result
```

**Worker tuning guidance:**
- NAS (SMB/NFS): 4–8 workers (latency dominates)
- Local SSD: 2–4 workers
- Local HDD: 1–2 workers (seek-limited)

---

## 3. PIL EXIF — Extract Before `convert()`

`pil_img.convert("RGB")` creates a **new** `Image` object with no EXIF.
Always call `getexif()` on the original image object.

```python
def _raw_exif_date(img: Image.Image) -> str | None:
    """Return raw 'YYYY:MM:DD HH:MM:SS' from PIL EXIF, or None."""
    try:
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)          # ExifIFD sub-IFD
        raw = exif_ifd.get(36867) or exif.get(36867) or exif.get(306)
        return str(raw) if raw else None
    except Exception:
        return None

# CORRECT order:
with Image.open(io.BytesIO(data)) as pil_img:
    raw_date = _raw_exif_date(pil_img)   # ← before convert
    img = pil_img.convert("RGB")
```

**Writing EXIF in tests — use PIL, not piexif:**

```python
# No external dependency — PIL can write EXIF natively:
img = Image.new("RGB", (64, 64), (128, 64, 32))
exif = img.getexif()
exif.get_ifd(0x8769)[36867] = "2024:06:15 10:30:00"   # DateTimeOriginal
img.save(str(path), "JPEG", exif=exif.tobytes())
```

---

## 4. exiftool — Persistent Process, JSON Output, `-fast` Flag

Spawning `exiftool` per-file is 10–100× slower than a persistent `-stay_open` process.
The `-fast` flag stops reading after the first metadata block — safe for camera files
because `DateTimeOriginal` is always in the JPEG APP1 segment at the file start.

### Use `-j -G` (JSON), NOT `-s3` (line-positional)

This is the most important rule in this section. `-s3` looks simpler — one tag value
per line, just count `i * tags_per_file` and you're done. **Don't.** Line-positional
parsing of `-stay_open` output has shipped a silent multi-file alignment bug to
production in at least two repos (`google-album-metadata`, `photo-manager`) — see
the Detours table at the bottom of this skill. The bug is invisible to mocked unit
tests because the mocks hand-build clean output; only real `-stay_open` runs include
the `======== <path>` per-file headers and `    N image files read` trailer that
break the indexing.

`-j -G` produces self-identifying JSON records:

```json
[{"SourceFile": "/tmp/a.jpg",
  "EXIF:DateTimeOriginal": "2024:01:01 12:00:00",
  "XMP:DateTimeOriginal":  "2024:01:01 12:00:00"},
 {"SourceFile": "/tmp/b.heic",
  "EXIF:DateTimeOriginal": "2024:06:12 11:17:37",
  "EXIF:OffsetTimeOriginal": "+09:00"}]
```

Each record carries its own `SourceFile`. Bind values to paths by identity, not
position. Reordered records, missing records, extra records, and inserted status
messages all stop being parser hazards. The single-file vs multi-file framing
distinction goes away. Missing tags are simply absent from the record (no `"-"`
sentinel to filter — drop `-f` too).

```python
import json
from pathlib import Path

# Group-prefixed JSON keys. Centralise to prevent typos from silently dropping
# a signal. PNG/GIF/WebP carry the date in XMP (galbum doesn't write EXIF for
# those), JPEG/HEIC/RAW in EXIF, video in QuickTime.
_KEYS_PROCESSED = (
    "EXIF:DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "QuickTime:CreateDate",
)

def _parse_exiftool_json(output: str) -> list:
    """Slice the bracket-bounded JSON blob.

    `ExiftoolProcess` merges stderr into stdout, so exiftool status messages
    (e.g. `    3 image files read`) appear interleaved. Find the outermost
    `[ ... ]` and parse only that.
    """
    start = output.find("[")
    end = output.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(output[start:end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def batch_read(paths: list[Path], et) -> dict[Path, dict]:
    args = ["-j", "-G",
            "-DateTimeOriginal", "-CreateDate", "-QuickTime:CreateDate",
            "-fast"]
    args += [str(p) for p in paths]
    records = _parse_exiftool_json(et.execute(args))
    # pathlib normalises / vs \ on Windows, so dict equality just works.
    by_path = {Path(r["SourceFile"]): r
               for r in records if isinstance(r.get("SourceFile"), str)}
    return {p: by_path.get(Path(str(p))) or {} for p in paths}
```

**Mandatory test pattern** — canned outputs must include the status-message
prefix exiftool actually emits, AND a `test_records_returned_in_different_order_still_match`
case that swaps record order and asserts each path still gets its own data.
With positional parsing this would silently corrupt every file's data; with
JSON it's structurally guaranteed to bind correctly. That single test is the
proof that the parser is bug-class-proof, not just bug-instance-proof.

**Why the explicit group enumeration in `_KEYS_PROCESSED`:** under `-G`, JSON
keys are `EXIF:DateTimeOriginal`, `XMP:DateTimeOriginal`, etc. — distinct keys
per source group. PNG/GIF/WebP only carry `XMP:DateTimeOriginal` (some pipelines
intentionally don't write EXIF for these formats). If you only check the EXIF
key, force=False re-runs will re-process every PNG every time. Always enumerate
all groups your write path can produce.

**Edge case:** MOV/MP4 with moov atom at file end may lose `QuickTime:CreateDate`
under `-fast`. Fall back to `CreateDate`.

### NEVER `stderr=subprocess.STDOUT` for a long-running stay_open process

The seemingly-harmless line in the `ExiftoolProcess` wrapper:

```python
self.proc = subprocess.Popen(
    ["exiftool", "-stay_open", "True", "-@", "-"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,   # ← TIME BOMB
    encoding="utf-8", errors="replace",
)
```

is a time bomb that fires once your batch is large enough to fill the OS pipe
buffer (~64 KB). When that happens, the kernel flushes stdout in chunks, and
exiftool's stderr (its progress message, any warnings) can be spliced **into**
stdout at the byte level — including into the middle of a string value. We
observed in production:

```
"EXIF:DateTimeOriginal": "2 3360 image files read\n024:04:08 19:56:15"
```

That's the `3360 image files read` progress line spliced between bytes `2`
and `024:...` of a date string. With JSON parsing this aborts the entire
batch silently — `json.loads` returns nothing and the caller gets empty maps
for thousands of files. Mocked unit tests cannot reproduce this; only real
runs against folders with >~300 files trip the buffer.

**Always separate stderr** with a draining thread, then append stderr text
*after* stdout in your wrapper's `execute()` return value:

```python
import threading

self.proc = subprocess.Popen(
    [...],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,   # ← separate
    ...
)
self._stderr_buf = []
self._stderr_lock = threading.Lock()
threading.Thread(target=self._drain_stderr, daemon=True).start()

def _drain_stderr(self):
    while True:
        line = self.proc.stderr.readline()
        if not line:
            break
        with self._stderr_lock:
            self._stderr_buf.append(line)

def execute(self, args):
    # ... existing stdout-until-{ready} loop ...
    with self._stderr_lock:
        err = "".join(self._stderr_buf)
        self._stderr_buf.clear()
    return stdout_text + ("\n" + err.rstrip("\n") if err else "")
```

The daemon drains stderr continuously so exiftool never blocks on a full
stderr pipe. Appending stderr at the end keeps backward-compat for callers
that grep for "error"/"warning" in the result string, while making stream
interleaving structurally impossible.

**Verification protocol — small batches don't catch this.** Run the full
pipeline on a real folder of >1000 files and assert no parser-side data is
empty. A unit-test mock cannot reach the failure mode because it never
fills an OS pipe buffer. This is the kind of bug only an end-to-end audit
finds.

### Writing EXIF dates — set OffsetTimeOriginal alongside DateTimeOriginal

Whenever you write `DateTimeOriginal` to a JPEG/HEIC/RAW file, also write
`OffsetTimeOriginal` (and ideally `OffsetTime` / `OffsetTimeDigitized` for
their respective dates). Otherwise a stale offset from a prior writer
(camera firmware, GooglePhotoScan, an earlier sync) survives, and any
reader that prioritises OTO over DTO's embedded suffix will compute UTC
8–12 hours wrong.

The hazard pattern after a tier-4 (UTC fallback) write that didn't reset OTO:

```
EXIF:DateTimeOriginal:    "2020:09:13 04:21:42+00:00"   ← galbum's UTC write
EXIF:OffsetTimeOriginal:  "+08:00"                       ← stale, from camera
```

A reader that does `combine_local_and_offset(DTO, OTO)` will produce
`2020:09:13 04:21:42` interpreted as `+08:00` local → `2020:09:12 20:21:42 UTC`.
That's an 8-hour timezone shift. Most viewers trust DTO's own suffix, but
not all — Lightroom imports and some metadata libraries prefer OTO.

**The minimal write fix:** extract the offset from the date string you're
writing and append it as a separate arg.

```python
# galbum's dt_str format is always 'YYYY:MM:DD HH:MM:SS+HH:MM' (25 chars).
if len(dt) >= 25 and dt[19] in ("+", "-"):
    args.append(f"-OffsetTimeOriginal={dt[19:25]}")
```

PNG/GIF/WebP write XMP only; XMP encodes offset directly in the date
string (`<xmp:DateTimeOriginal>2020-09-13T04:21:42+00:00</xmp:DateTimeOriginal>`)
so no separate offset tag applies. Videos use `Keys:CreationDate` (with
offset) and UTC `QuickTime:CreateDate` — no OTO concept in the QT spec.

**Mandatory regression test:** simulate a file that already has OTO=+08:00
from a prior writer, run your pipeline with a tier-4 input (no GPS, no
existing-offset signal), and assert the resulting OTO is `+00:00` (not the
stale `+08:00`). This is the exact bug class.

**Verification protocol — measure prevalence on real data.** Sample
the post-write library, parse DTO and OTO independently per file, and
count files where the two disagree. If any non-zero count, you have stale
OTO drift; the data is correct (per DTO's own suffix) but inconsistent.

**Tiered EXIF strategy** (saves ~70% of exiftool calls):

```python
_EXIFTOOL_TYPES = frozenset(("heic", "raw", "mov", "mp4"))

# Pass 1: PIL for JPEG/PNG (free — already read for pHash)
# Pass 2: exiftool only for the subset that PIL can't handle
et_records = [r for r in hash_results
              if r.exif_date is None and r.record.file_type in _EXIFTOOL_TYPES]
```

---

## 5. pHash Stability — JPEG vs PNG

**JPEG compression destroys high-frequency sinusoidal patterns** in ways that make
pHash distances unpredictable. If you need stable pHash for test images or
transitive-chain scenarios, save as PNG (lossless).

```python
# JPEG: pHash of sinusoidal image BEFORE save ≠ pHash AFTER save
# PNG:  pHash is always deterministic — round-trip safe

# For test images where hamming distance must be precise:
img.save(str(path))  # PNG by default if path.suffix == ".png"
```

**Degenerate pHash `8000000000000000`:** uniform/flat images (solid color) all
produce the same pHash because all DCT coefficients are below the mean.
Use **structured content** (gradients, sinusoids, patterns) for any test image
where pHash uniqueness matters.

```python
# BAD: produces degenerate hash — all flat images collide
img = Image.new("RGB", (64, 64), (128, 128, 128))

# GOOD: structured gradient — produces unique hash
arr = np.array([[[int((r + c * 2) * 1.2) % 220, int(r * 2.2) % 200, int(c * 1.8) % 180]
                 for c in range(100)] for r in range(100)], dtype=np.uint8)
img = Image.fromarray(arr)
```

---

## 6. Union-Find for Transitive Group IDs

Pairwise classification produces directed edges (A→B, B→C). If you skip the
transitive closure step, A and C end up in separate groups even though they are
connected through B. Union-find solves this in O(n·α(n)) ≈ O(n).

```python
def _assign_group_ids(rows: dict[Path, ManifestRow]) -> None:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while x in parent:
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    # Build edges from duplicate_of links set during classification
    for row in rows.values():
        if row.duplicate_of:
            union(row.source_path, row.duplicate_of)

    # Collect all paths that have at least one edge
    has_edge: set[str] = set()
    for row in rows.values():
        if row.duplicate_of:
            has_edge.add(row.source_path)
            has_edge.add(row.duplicate_of)

    for row in rows.values():
        if row.source_path in has_edge:
            row.group_id = find(row.source_path)
        # else: isolated file → group_id stays None
```

**Key insight:** run union-find *after* all classification passes, not during.
The `duplicate_of` field is a transient edge — it is not persisted to the DB.
Only `group_id` (the canonical root path) is written.

---

## 7. SQLite — Performance Pragmas + Schema Migration

**Connection pragmas** (WAL mode persists in the DB file after first write):

```python
conn = sqlite3.connect(path)
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA synchronous = NORMAL")
conn.execute("PRAGMA cache_size = -32000")   # 32 MB page cache
conn.execute("PRAGMA temp_store = MEMORY")
```

**WAL+copy gotcha — `shutil.copy2` of an active WAL DB silently produces an
empty target.** When the writer (e.g. the scanner) has the manifest open in
WAL mode, recently-committed data lives in the `.sqlite-wal` sibling until a
checkpoint runs. `shutil.copy2` only copies the main `.sqlite` file, so the
destination ends up as just the 4096-byte header — schema and rows missing.
Subsequent `sqlite3.connect(dst)` + `UPDATE` then fails with
`no such table: <name>`, which is the visible symptom.

Force a checkpoint via a second connection BEFORE copying:

```python
ckpt = sqlite3.connect(src_path)
try:
    ckpt.execute("PRAGMA wal_checkpoint(FULL)")  # safe with active writer
finally:
    ckpt.close()
shutil.copy2(src_path, dst_path)
```

`FULL` (not `TRUNCATE`) because TRUNCATE returns SQLITE_BUSY when other
connections are active. FULL transfers WAL pages to main without truncating
the WAL file — exactly what we need for the copy. Alternatively, use
SQLite's [Online Backup API](https://www.sqlite.org/backup.html)
(`src_conn.backup(dst_conn)`), which is WAL-aware by design.

**Bug surface:** any code that uses `shutil.copy2` / `shutil.copyfile` /
filesystem-level copy on a SQLite DB that may have a live writer. Reading
the source after the writer's process has exited works fine because SQLite
checkpoints WAL→main on connection close — so the bug is invisible to
standalone scripts that test the flow without the live writer in the picture.

**Backward-compatible column migration** — try/except ALTER TABLE, safe to replay:

```python
_MIGRATIONS = [
    ("user_decision",   "TEXT    NOT NULL DEFAULT ''"),
    ("file_size_bytes", "INTEGER"),
    ("shot_date",       "TEXT"),
    ("creation_date",   "TEXT"),
    ("mtime",           "TEXT"),
    ("group_id",        "TEXT"),
]

for col, ddl in _MIGRATIONS:
    try:
        conn.execute(f"ALTER TABLE migration_manifest ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception:
        pass  # column already exists — silently continue
```

**DB-first metadata caching pattern:** write file_size, EXIF shot_date, ctime, mtime
at scan time → zero filesystem stat calls when loading the review UI (critical for NAS
where each stat is a network round-trip).

```python
# At load time — no filesystem reads needed:
if db_file_size is not None:
    size = db_file_size
else:
    size = int(os.path.getsize(source_path))   # fallback for old manifests
```

---

## 8. pytest — `importorskip` Placement

**`pytest.importorskip` must precede any import of the guarded module**, or Python
raises `ModuleNotFoundError` before pytest can intercept it.

```python
# WRONG — hard ModuleNotFoundError if piexif not installed:
import piexif
pytest.importorskip("piexif")

# CORRECT — clean skip when module is absent:
pytest.importorskip("piexif")
import piexif
```

**Prefer PIL's built-in EXIF writing over piexif in tests** — one fewer dependency:

```python
# Uses only Pillow (already required by the project):
exif = img.getexif()
exif.get_ifd(0x8769)[36867] = "2024:06:15 10:30:00"
img.save(str(path), "JPEG", exif=exif.tobytes())
```

---

## 9. Renaming Public/Private Symbols

When making a `_private` function public (removing the underscore), immediately
grep all test files and call sites — test imports commonly reference the old name
and will silently become `ImportError` rather than `AttributeError`.

```bash
# Before renaming _parse_exif_date → parse_exif_date, check all imports:
grep -r "_parse_exif_date" tests/
```

---

## 10. Near-Duplicate Classifier — Outer-Loop vs Inner-Loop Skip

The pairwise near-duplicate scan has a subtle but critical placement rule for the
"already classified" guard. **Skip the inner target, never the outer comparator.**

```python
# WRONG — skipping hr_a on the outer loop breaks transitive chains:
#   A classified → skip A → B~C never compared → C lands in a separate group
for i, (hr_a, hash_a) in enumerate(hashes):
    if hr_a.record.path in rows:   # ← moves the skip to the outer loop — BUG
        continue
    for hr_b, hash_b in hashes[i + 1:]:
        ...

# CORRECT — hr_a stays as comparator even after it's classified:
for i, (hr_a, hash_a) in enumerate(hashes):
    # hr_a may already be classified (REVIEW_DUPLICATE of an earlier file).
    # Do NOT skip it — it must still serve as a comparator so B~C is found
    # when B is similar to already-classified A which is similar to C.
    for hr_b, hash_b in hashes[i + 1:]:
        if hr_b.record.path in rows:   # ← skip the target, not the comparator
            continue
        distance = hash_a - hash_b
        if 0 < distance <= threshold:
            ...
```

**Why:** Pairwise comparison only detects direct edges (A~B). Union-find then closes
the transitive chain (A~B + B~C → same group). But if B is skipped as a comparator,
the B~C edge is never discovered and C gets classified independently.

**The invariant:** an already-classified file must remain available as a comparator
(`hr_a`). Only unclassified files should be skipped as targets (`hr_b`).

---

## 11. Fix All Reading Sites, Not Just the Write Path

When a displayed value is wrong (e.g. "DNG resolution shows thumbnail size"),
the instinct is to find where it is **written** and fix that. That is necessary
but not sufficient when multiple code paths independently read the same attribute.

**Mandatory audit before marking a data-quality bug fixed:**

```bash
# Find every place the attribute is computed or displayed:
grep -rn "pixel_width\|pixel_height\|resolution\|img\.size\|raw\.sizes" \
  app/ infrastructure/ scanner/ --include="*.py" | grep -v __pycache__
```

Then categorise results into:

| Site | Source of truth | Needs fix? |
|---|---|---|
| Scanner (write path) | rawpy / PIL at scan time | Yes — fix once |
| DB cache (read at load) | manifest_repository.py | If SQL filters wrong |
| Tree column | `PhotoRecord.pixel_*` from DB | Fixed by DB fix |
| Preview info panel | live file read (`_read_resolution`) | Independent — fix separately |
| Grid view | live file read (`_image_dims`) | Independent — fix separately |

**Rule:** a bug is fixed only when every row in the table above is accounted for.
Live-file-read paths are completely independent of the DB cache — fixing the
scanner does not fix the preview pane.

**DNG-specific pattern:** PIL's TIFF reader opens the first IFD, which may be
a low-res embedded thumbnail. Always use `rawpy.imread(path).sizes.width/height`
for true sensor dimensions in RAW/DNG files. Provide `_raw_sensor_dims()` as a
shared helper so QImageReader → PIL → rawpy is the consistent fallback chain.

```python
_RAW_EXTENSIONS = frozenset((".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2"))

def _raw_sensor_dims(path: str) -> tuple[int, int]:
    try:
        import rawpy
        with rawpy.imread(path) as raw:
            w, h = raw.sizes.width, raw.sizes.height
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 0, 0

def _image_dims(path: str) -> tuple[int, int]:
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if f".{ext}" in _RAW_EXTENSIONS:      # rawpy first for RAW/DNG
        w, h = _raw_sensor_dims(path)
        if w > 0 and h > 0:
            return w, h
    # ... QImageReader → PIL fallbacks ...
```

---

## 12. Google Takeout Sidecar Matching (May-2026+ Format)

Google Takeout exports created on/after May 2026 use
`<media>.<ext>.supplemental-metadata.json` as the sidecar suffix (was: plain
`.json`). Two non-obvious behaviours.

### Suffix truncation at Windows MAX_PATH

When the full path would exceed 260 chars, Google truncates the suffix from
the right rather than the basename. Five variants observed in production
(frequencies from one ~882-sidecar real-world sample):

| Suffix | Frequency |
|---|---|
| `.supplemental-metadata.json` | 836 (94%) |
| `.supplemental-metada.json`   | 26 (3%) |
| `.supplemental-metadat.json`  | 19 (2%) |
| `.supplemental-metad.json`    | 1 |
| `.supplemental-meta.json`     | 0 (theoretical) |

Match all five with a single anchored regex, not a priority list:

```python
_SUPPL_FAMILY_RE = re.compile(
    r"\.supplemental-meta(?:data|dat|da|d)?(\(\d+\))?\.json$"
)
```

The capture group on `(\d+)` handles the next gotcha.

### Dupe `(N)` lives INSIDE the suffix

When the same media has multiple uploads, Google places the `(N)`
disambiguator **between** the suffix and `.json` — NOT at the basename:

```
IMG_X.HEIC.supplemental-metadata(1).json   ← real Google output
IMG_X.HEIC(1).supplemental-metadata.json   ← what you might intuitively expect
```

Naive suffix-stripping leaves `IMG_X.HEIC.supplemental-metadata(1)` — a string
that matches no real media file. The fix: capture `(N)` in the regex and
re-attach it to the head, so `IMG_X.HEIC.supplemental-metadata(1).json` →
`IMG_X.HEIC(1)` (the form your matcher's "duplicate-reorder" step expects).

### Index by implied-media-name, not by sidecar-name

A single normalised key serves all suffix variants — both legacy `.json` and
all five new-format truncations collapse to the same lookup:

```python
def _strip_json_suffix(name: str) -> str | None:
    m = _SUPPL_FAMILY_RE.search(name)
    if m:
        head = name[:m.start()]
        dupe = m.group(1) or ""
        return head + dupe   # IMG_X.HEIC.supplemental-metadata(1).json → IMG_X.HEIC(1)
    if name.endswith(".json"):
        return name[:-len(".json")]   # legacy form
    return None
```

### Title-field fallback for heavy truncation

When the basename itself was trimmed (path was *really* long), the suffix-strip
can't recover the original. Read each JSON's `"title"` field at index time —
that's the canonical original media filename. Match by 80% prefix similarity
when the filename-key lookup misses. This handles UUIDs and very-long names
that no amount of suffix logic will save.

```python
# Title-match step in the matcher's algorithm:
if len(mf.path.stem) > 10:
    for title, jp in index.by_title.items():
        short = min(len(media_name), len(title))
        threshold = int(short * 0.8)
        if threshold > 0 and media_name[:threshold] == title[:threshold]:
            return MatchResult(jp, "title_match")
```

### Test fixture pitfall — synthetic vs real placement

Synthetic test fixtures must mirror Google's *actual* placement, not what
you'd intuitively design. We shipped three PRs (matcher, fixtures, opt-in
parity tests) that all passed against `IMG_X.HEIC(1).supplemental-metadata.json`
before a real-data dry-run revealed Google emits
`IMG_X.HEIC.supplemental-metadata(1).json` — 66 silent orphans in one folder.
Whenever you fixture a third-party export format, either copy a real example
verbatim, or include both placements in the fixture.

---

## 13. Windows-specific Path Gotchas When Scanning User Folders

Three landmines that don't surface in unit tests but bite on real user data.

### Trailing-period folder names hide from Win32 GUI

NTFS preserves folders ending in `.` or whitespace (e.g. `E.J.`), but the
Win32 GUI layer hides those characters. Explorer, Open/Save dialogs, and most
third-party tools see `E.J`. Python's `pathlib` (NT API) sees the real name.

Behaviour diverges between (a) drag-and-drop / dialog `+ Add` (sees `E.J`,
no match) and (b) recursive walking from a parent (sees `E.J.`, scans
normally). Same library, two different observed file sets depending on entry
point.

**Detection:** during walk, check `path.name` for trailing `.` or whitespace.
**Action:** warn explicitly — "this folder is hidden from Win32 GUI tools,
rename to fix" — don't try to handle silently. Auto-rename only with consent.

### NTFS case-collisions in same directory

On case-insensitive NTFS (default), `IMG_X.MOV` and `IMG_X.mov` cannot coexist
in the same directory. Google Takeout does occasionally produce both — the
same iPhone counter mapped to two genuinely-distinct video files in different
albums, then merged at extraction time. We hit 6 such collisions in one
May-2026 export.

**Symptom:** `Path.hardlink_to` raises `WinError 183`; `shutil.copy` silently
overwrites; second extraction silently loses the loser.
**Action:** when scanning, detect two paths in the same directory differing
only by case → log, surface to UI as a manual-decision group, never silently
merge. Quarantine to a sibling `_collisions/` dir is a workable fallback.
Per-directory case sensitivity (`fsutil setCaseSensitiveInfo enable`)
requires admin and is rarely the right answer for an automated tool.

### Large videos split out of zip segments

For videos that don't fit in a ~10 GB compressed zip segment, Google extracts
them as standalone files at the archive root, named `<basename>-<NNN>.MOV`
where `<NNN>` is the zip index they would have lived in. The matching JSON
sidecar is in some other zip and lives under
`Takeout/Google 相簿/<album>/` post-extraction. Without a placement step,
every such video becomes an orphan.

When the same basename has multiple loose copies (e.g. three `IMG_2063` files
with different zip indices), they're DISTINCT videos that happen to share an
iPhone counter. Place each one independently against a separate sidecar.

```python
# Loose-MOV pattern at archive root:
LOOSE_RE = re.compile(r"^(?P<base>.+?)-(?P<idx>\d{3})\.(?P<ext>MOV|MP4|mov|mp4)$")
```

If the count of loose files exceeds the count of matching sidecars for a
given basename, leave the extras at the archive root for manual triage —
they may genuinely be a different photo someone else took.

---

## Detours Reference (What Cost the Most Time)

| Detour | Root Cause | Fix |
|--------|-----------|-----|
| QA pHash distances wrong after JPEG save | JPEG quantization destroys sinusoidal frequencies | Save transitive-chain test images as PNG |
| Flat QA images all got pHash `8000000000000000` | Uniform images → degenerate DCT | Use structured gradients/patterns |
| `ModuleNotFoundError: No module named 'piexif'` in tests | `import piexif` before `pytest.importorskip` | Move importorskip to first line; or use PIL EXIF write directly |
| `ImportError` after rename `_parse_exif_date → parse_exif_date` | Test import not updated | Grep all usages before renaming |
| PIL EXIF was None even for JPEG with known dates | `getexif()` called on `convert("RGB")` result | Extract EXIF before `convert()` |
| NAS scan was 2× slower than needed | SHA and pHash opened file separately | Combine into `compute_hashes()` single-read 3-tuple |
| Transitive near-duplicates landed in separate groups | Outer-loop skip for already-classified hr_a broke B~C edge detection | Move "in rows" guard to inner loop; let hr_a remain as comparator |
| DNG resolution still wrong in preview after scanner fix | Fixed write path (DB) but not read paths (preview pane reads live from file, independent of DB) | Grep all display sites; `_read_resolution` and `_image_dims` need rawpy branch too |
| Scanner's "Shot Date" column showed wrong dates pulled from other files | `i * 3` indexing into `output.splitlines()` ignored exiftool's `======== <path>` per-file header in stay_open mode → drift across the batch | Switch to `-j -G` JSON output; bind records to paths by `SourceFile` not position. Metaline filtering is a band-aid; positional parsing of any line-stream format is the actual smell |
| Re-runs without --force re-processed every PNG | Under `-G`, the JSON key for PNG's date is `XMP:DateTimeOriginal`, not `EXIF:DateTimeOriginal`. Parser only checked the EXIF key → PNG always looked unprocessed | Enumerate every source group your write path can produce. PNG/GIF/WebP write XMP; JPEG/HEIC/RAW write EXIF; video writes QuickTime |
| 3360-file Takeout audit returned empty maps for every file | `stderr=subprocess.STDOUT` merges streams; once OS pipe buffer (~64 KB) fills, stderr bytes splice INTO stdout mid-string, producing invalid JSON | Separate stderr to its own pipe; drain it on a daemon thread; append captured stderr text at end of `execute()` for backward-compat. Verification needs a real ≥1000-file run — mocked unit tests cannot fill a pipe buffer |
| Every sidecar in May-2026 Takeout silently missed | New format uses `<media>.<ext>.supplemental-metadata.json` (was plain `.json`); old matcher constructed candidates by appending `.json` to media filenames | Index by *implied media filename* (suffix stripped) instead of by sidecar filename; one anchored regex covers all 5 truncation variants and the `(N)` capture; legacy `.json` and modern suffixes collapse to the same key |
| 66 dupe-uploaded photos became silent orphans even after the suffix-family fix | Google places `(N)` *inside* the new suffix (`IMG_X.HEIC.supplemental-metadata(1).json`), not on the basename — three PRs of unit tests, e2e fixtures, and parity tests all assumed the intuitive `IMG_X.HEIC(1).supplemental-metadata.json` placement | Capture `(\d+)` in the suffix-family regex and reattach it to the head; fixture authors must mirror the *real* third-party placement, not what they'd intuitively design |
| `E.J./` album invisible to file-picker dialog yet found by recursive walk | Win32 GUI hides trailing `.` and whitespace in folder names; NTFS preserves them; Python's pathlib (NT API) sees the real name | Detect `path.name` ending in `.` or whitespace during walk; warn the user and offer rename — do NOT silently coerce |
| 6 IMG_2063 case-collisions silently lost on extraction | `IMG_X.MOV` and `IMG_X.mov` cannot coexist in the same dir on case-insensitive NTFS; `Path.hardlink_to` raises `WinError 183`, `shutil.copy` silently overwrites | Detect same-dir paths differing only by case; quarantine to a sibling `_collisions/` dir for manual decision; never silently merge |
| Loose `<basename>-NNN.MOV` videos at archive root were orphaned post-extraction | Google extracts videos exceeding ~10 GB compressed zip segment as standalone files at the archive root; their JSON sidecars live in some other zip and end up under the album tree | Run a placement step pre-scan that walks loose files and finds matching sidecars; treat multiple loose copies sharing a basename as DISTINCT videos (they're different photos with the same iPhone counter) |
| ~7.6% of post-sync photos had stale OTO disagreeing with DTO's embedded offset (TZ-shift hazard for OTO-prioritising readers) | Tier-4 UTC fallback wrote `DateTimeOriginal=...+00:00` but never touched the pre-existing `OffsetTimeOriginal=+08:00` from camera/GooglePhotoScan; two redundant tags drifted out of sync | When writing DTO, always also write `-OffsetTimeOriginal=<offset>` extracted from the dt string. Found by stratified-sample verification of production output (15% sample of 13,678 files) — the kind of cross-tag inconsistency that PASS/FAIL date checks miss; needs an explicit consistency counter |
