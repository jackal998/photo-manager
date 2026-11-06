# Representative development test set — selection summary (2026-11-06)

Source: `migration_manifest_v6.sqlite` (grouping 6 scan of the full library, 40k rows). Selector:
`select_testset.py <manifest> <outdir>` (read-only, seeded `random.Random(20261106)`, whole groups only).
`selection.csv` beside this file holds the full source paths and is a LOCAL work file (gitignored here).
Nothing has been copied yet; the copy step needs the owner's destination and "yes".

## Totals

| | value |
|---|---:|
| files | 1,381 |
| bytes | 12,890,807,009 (12.0 GiB) |
| local disk (D) | 504 files, 3.15 GB |
| NAS share 1 (H) | 334 files, 3.64 GB |
| NAS share 2 (J) | 543 files, 5.22 GB |

Checks the script asserts: no duplicate `source_path`; every selected `group_id` is complete (all manifest
rows of that group are included); the seven regression fixture basenames (eclipse `MVIMG_20200621_160439.jpg`,
`MVIMG_20200621_160730.jpg`, `IMG_8511.JPG`; torii `IMG_1391/1393/1395/1397.DNG`) are present.

## Per category (categories run in this order; "blocked" = candidates already taken by an earlier category)

| category | quota | pool | blocked | units | files | bytes |
|---|---:|---:|---:|---:|---:|---:|
| fixtures | all | 9 | 0 | 9 | 22 | 260,036,341 |
| burst | all | 11 | 0 | 11 | 124 | 449,635,622 |
| exact_copies | 60 | 4,965 | 7 | 60 | 175 | 4,514,832,319 |
| near_dup_high | 80 | 512 | 14 | 80 | 361 | 1,252,556,198 |
| hint_pairs | 40 | 498 | 1 | 40 | 86 | 828,403,137 |
| isolated_hints | 20 | 108 | 5 | 39 | 58 | 67,594,260 |
| screenshots | 30 | 651 | 31 | 30 | 60 | 92,634,155 |
| live_photo_pairs | 20 | 1,831 | 18 | 20 | 66 | 205,535,555 |
| raw_plus_jpeg | 20 | 20 | 0 | 20 | 87 | 721,955,139 |
| undated | 40 | 2,228 | 23 | 40 | 42 | 29,319,044 |
| quality_gated | 40 | 564 | 22 | 40 | 56 | 211,299,596 |
| tz_shift | 10 | 887 | 20 | 10 | 34 | 117,433,677 |
| negatives | 200 | 14,996 | 64 | 200 | 200 | 3,794,427,321 |
| videos | 10 | 990 | 48 | 10 | 10 | 345,144,645 |

No category fell short of its quota.

## Category definitions

1. **fixtures** — the eclipse (class A near-black frames) and torii (class B, four moments) groups.
2. **burst** — groups whose dated pHash non-low rows span > 2 s with every consecutive gap ≤ 2 s (the
   #1042 rule applied to the v6 manifest, which predates grouping 7): 11 groups.
3. **exact_copies** — groups with ≥ 2 rows sharing a sha256, stratified by extension and drive.
4. **near_dup_high** — REVIEW/high groups stratified by size (2 / 3–5 / 6+) and extension.
5. **hint_pairs** — groups with a `; hint:` leaf; **isolated_hints** — rows whose reason starts
   `hint only (not grouped): ` plus the partner named in the reason (partner groups raise units to 39).
6. **screenshots** — groups whose members are all `.png`.
7. **live_photo_pairs** — an image and a video with the same stem in one group, video < 150 MB preferred.
8. **raw_plus_jpeg** — a RAW and a jpg/heic with the same stem in one group (the library has DNG only;
   no CR2/ARW/NEF/RAF rows exist).
9. **undated** — UNDATED rows, one per folder so a large series contributes one unit.
10. **quality_gated** — `phash_quality` < 16 (whole groups where grouped).
11. **tz_shift** — two dated rows differing by an exact multiple of 1800 s (≤ 14 h).
12. **negatives** — 200 ungrouped rows (action "" or UNDATED) as "unique" controls, stratified by
    extension and drive.
13. **videos** — 10 ungrouped videos < 150 MB.

Size cap: a group containing any file > 400 MiB is skipped whole (counts computed, not printed).

## Proposed NAS layout (preserves drive-relative structure so cross-drive duplicate shapes survive)

```
<ROOT>\D\<original path under D:\>
<ROOT>\H\<original path under H:\>
<ROOT>\J\<original path under J:\>
```

`settings.json` sources for development scans (the three roots, recursive):

```json
"sources": {
  "list": [
    {"path": "<ROOT>\\D", "recursive": true},
    {"path": "<ROOT>\\H", "recursive": true},
    {"path": "<ROOT>\\J", "recursive": true}
  ],
  "output": "<repo>\\migration_manifest_testset.sqlite"
}
```

(this is the schema the app's `settings.json` uses today: `sources.list[]` of `{path, recursive}` plus
`sources.output`; keep the full-library entries somewhere safe to swap back.)

## Copy and verification recipe (text only — not executed)

1. For each row of `selection.csv`, map `source_path` → target as above and copy with a method that
   preserves last-write time (robocopy `/COPY:DAT`, or `shutil.copy2`).
2. Verify every target: size == `file_size_bytes`, SHA-256 == `source_hash` (the scanner's hash).
3. Report mismatches by path; do not retry silently. Expect zero mismatches; a mismatch means the
   source changed since the v6 scan (re-select that group) or the copy is corrupt.
4. First development scan on the test set: compare its manifest against the rows of `selection.csv`
   joined on sha256 — same groups, same actions — as the baseline for grouping 7 and later.

## Baseline scan on the test set (2026-11-06, master e94e859, GROUPING "7")

Scan 16:04:37 → manifest 16:08 (about 4 minutes against 1 h 37 m for the full library; one exiftool wedge
line in the log). `compare_testset.py selection.csv <ROOT> migration_manifest_testset.sqlite`
(the scanner stores the mapped drive as a UNC path, so the mapping matches on the ROOT folder name):

| check | result |
|---|---|
| files scanned / selected | 1,381 / 1,381, none missing, none extra |
| sha256 equal to the v6 manifest | 1,381 / 1,381 |
| grouping partition (member sets per group) | 312 / 312 identical to v6 |
| rows carrying `burst stack (keep all)` | 121 in 12 groups (the probe predicted 11; a REVIEW/low frame made the 12th) |
| byte copies inside burst groups | 13, all still EXACT (their keepers carry the burst reason) |

Action / confidence changes against v6, all explained:
- burst (#1042): 78 REVIEW/high → keeper, 13 REVIEW/low → keeper/high, 12 format-duplicate EXACT (distinct
  sha) → keeper, 18 keepers gained `high` + the burst reason.
- subset artefacts (the partner that decided the v6 label is outside the test set, so the first pHash
  candidate in path order differs): 8 REVIEW/low → unique, 3 REVIEW/low → `unique; hint`, 1 REVIEW/high →
  EXACT format duplicate, 1 REVIEW/low → REVIEW/high, 1 keeper/low → keeper. 14 rows; groups unchanged.

This manifest is the GROUPING 7 baseline for later changes: re-scan the test set (minutes), then run
`compare_testset.py` against `selection.csv` or diff two test-set manifests on sha256.
