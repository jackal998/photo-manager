# NAS read-probe session results — 2026-07-21

Owner-present session executed per `nas-probe-runbook-2026-07.md`. All probes
at commit `6420dd9` (PR #784). Raw JSONs archived locally at
`~/.claude/handovers/2026-07-21-probe-results/` (not committed — they contain
personal library paths); every claim below carries the 4-tuple
(probe + SHA `6420dd9` + args + JSON file). NAS: `\\LINXIAOYUN` mapped `J:`,
target trees `J:/圖片` (28,741 files / 266 dirs) and a generated
5000×~40KB corpus (seed 42, deleted after the session as authorized).

## H1 — is the NAS read-concurrency knee above 8? **YES for small files (≈16); NO for the real workload.**

`probe_read_knee_ladder.py --rungs 1,2,4,8,16,32,64 --files-per-rung 600`
on the small-file corpus (`ladder-smallfile.json`):

| c | files/s | MB/s | p50 ms | p95 ms |
|---|---|---|---|---|
| 1 | 95.5 | 3.8 | — | — |
| 2 | 180.0 | 7.1 | — | — |
| 4 | 314.9 | 12.7 | — | — |
| 8 | 494.4 | 19.9 | 15.3 | 23.0 |
| **16** | **686.7** | **26.9** | 22.9 | 31.0 |
| 32 | 495.8 | 19.8 | 42.5 | 292.2 |
| 64 | 601.6 | 24.4 | 89.0 | 184.4 |

c=16 / c=8 = **1.39×** — clears the runbook's ≥1.15× threshold. c=32
regresses (p95 ×12.7 vs c=16): the small-file knee sits at ≈16.

Same ladder on the real tree (`--root J:/圖片 --files-per-rung 150
--shuffle-seed 7`, `ladder-real.json`): files/s flat at 13–16 from c=2
upward; MB/s peaks at c=8 (78.9) and FALLS to 47.4 at c=32 while p50
latency climbs 78ms→1.7s. The real mixed workload is bandwidth-bound by
c≈4–8; deeper concurrency only buys latency.

**Note on absolute rates:** this session's real-tree peak (78.9 MB/s) is
well below the 137 MB/s mean recorded in
`scan-nas-starvation-2026-06-06.md`. Sample composition (150-file slices
including small files) and possible NAS contention differ from that
audit's full-scan measurement — treat the SHAPE (where the knee is) as
the finding, not the absolute rates.

**Decision:** a blanket `_NAS_WORKERS` 8→16 bump is NOT supported (real
workload gains nothing and pays latency). **Candidate for owner review:
size-aware read concurrency** — deeper pool only when the pending
work-set is dominated by small files (e.g. sidecars, thumbnails, JSON
metadata). Listed only; not implemented (session mandate: report-only).

## H2 — is directory enumeration worth parallelizing? **NO — do-not-build.**

`probe_walk_timing.py --root J:/圖片 --repeat 2` (`walk.json`): cold walk
30.5 s (951 entries/s), warm 29.1 s (no meaningful SMB dir-cache effect),
266 dirs / 28,741 files. Against this tree's own full-scan time at the
measured 13–16 files/s hash rate (~32–37 min), the walk is **~1.4 % of
scan wall time** — far under the runbook's 5 % build-threshold.
FastCopy-style parallel enumeration is a **do-not-build** for this
library shape (few, large directories).

## H3 — what does the exiftool second read cost? **The metadata pass is 1.9×–8× the hash-read pass.**

`probe_exif_second_read.py` (`exif-smallfile.json`, `exif-real.json`):

| Corpus | Phase A (hash-shaped read) | Phase B (real exiftool batch) | B/A |
|---|---|---|---|
| small-file (2000 × ~40KB) | 4.0 s (501 files/s) | 31.8 s (62.8 files/s) | **7.98×** |
| real dir (1000 files, 20110709_大陸十日) | — | — (32.2 files/s) | **1.93×** |

The "single-read" design is real for hashing, but every non-skip file is
re-opened by exiftool (`scan_worker.py:972` → `exif.py:487`), and that
second pass costs 2–8× the first. **Candidate for owner review:
in-memory EXIF extraction for JPEG-class files** (parse GPS/XMP/rating
from the bytes already read for hashing; keep exiftool for
HEIC/RAW/video). Respects the standing do-not-build on exiftool
*parallelism* (this removes reads; it does not add workers). Listed
only; not implemented.

## #737 — HEVC first-byte latency (gate measurement)

`probe_hevc_first_byte.py` against a live isolated-home server, 5 real
iPhone HEVC files from `J:/圖片/20240601-0712大阪` (`hevc.json`):

| Source | Cold TTFB | Warm TTFB |
|---|---|---|
| 1.7 MB MP4 | 0.52 s | 13 ms |
| 5.6 MB MOV | 0.62 s | 14 ms |
| 34 MB MOV | 5.22 s | 14 ms |
| 98 MB MOV | 14.43 s | 17 ms |
| 231 MB MOV | 16.85 s | 28 ms |

Cold first view blocks on the FULL synchronous transcode (TTFB scales
with source size, 5–17 s for large clips); the mtime-keyed cache then
makes every later view <30 ms. This is the quantified gate input #737
asked for: the minimum-bar mitigation (cache) fully solves repeat views;
only progressive/fragmented streaming would fix the 5–17 s FIRST view.
Owner decides at the cutover gate whether first-view latency of that
magnitude is acceptable for the transition period.

Anomaly logged: one 98 MB HEVC source transcoded to a 415 MB h264 output
(≈4× inflation) — bitrate ceiling on the transcode profile may be worth
a look if disk-cache growth ever matters (observation only).

## Cross-validation run — 2026-07-21, second tree (owner-requested)

Full re-run on `\\LinXiaoYun\home\Photos\MobileBackup\iPhone\2024` — same
NAS box, different share (`home` vs `J`), very different composition:
2,988 files / 12 dirs / 81.3 GB, HEIC-dominant (1,563) + 541 MOV +
422 DNG, p50 2.4 MB but mean 27 MB (video-skewed). JSONs:
`ladder-iphone2024.json`, `exif-iphone2024.json`, `hevc-iphone2024.json`.

- **H1 REPLICATED + absolute-rate discrepancy RESOLVED.** MB/s saturates
  at c=4-8 again (137→149 MB/s peak, plateau ~127-133 above; p50 latency
  98ms→6.9s climbing c=1→64). The 149 MB/s ceiling matches the 2026-06-06
  audit's 137 MB/s link rate — proving the first run's 78.9 MB/s peak on
  `J:/圖片` was FILE-COMPOSITION drag (small files pay per-op round-trip
  tax per byte), not NAS slowdown or contention. Conclusion unchanged and
  now二-share confirmed: real workloads are bandwidth-bound by c≈4-8;
  only genuinely small-file-heavy sets have a deeper knee (≈16).
- **H2 REPLICATED**: cold walk 4.2 s / 2,988 entries (710/s) ≈ <1 % of
  this tree's full-scan time → parallel enumeration stays do-not-build.
- **H3 COMPLETED INTO A CURVE**: B/A here = **0.24** (phase A whole-file
  reads 249 s @146 MB/s dominate; exiftool pass 60.5 s). Three-point
  picture: 40KB corpus **7.98×** → ~3MB photos **1.93×** → 27MB-mean
  video/DNG tree **0.24×**. The exiftool second-read tax is inversely
  proportional to file size: the in-memory-EXIF candidate pays off on
  small-file-heavy libraries and is negligible for video-heavy ones.
- **#737 STEEPER THAN FIRST SAMPLE**: cold TTFB 3.2MB→0.65s,
  59.7MB→12.8s, 137MB→24.3s, 220MB→41.6s, 334MB→**46.1s** (warm all
  <30 ms). The `圖片` sample's 231MB→16.9s vs this tree's 220MB→41.6s
  shows transcode cost follows DURATION×resolution, not bytes — long
  clips stall the first view for 40+ s. This strengthens the case that
  the gate decision should assume worst-case ~45-60 s first views on
  real libraries, cache notwithstanding.

## Session deviations (contingency defaults applied, none required a question)

- First HEVC sample all resolved under `J:/圖片/$RECYCLE.BIN/…` —
  excluded per the never-touch list; re-sampled from live folders.
- Scan-completion poll initially watched the wrong signal (server access
  log); switched to the scan SSE stream (`event: finished`).
- Corpus deleted from `J:` and deletion verified (listing count 0);
  manifest for regeneration kept locally.
