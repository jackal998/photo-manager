# NAS read-performance probe runbook (2026-07)

**Status:** toolkit ready for an owner-present measurement session on the
real NAS. No real-NAS numbers are recorded in this document yet — every
number below is a placeholder shape, not a claim. Fill in the actual
readings during the session and cite each one per the template in
[Citation template](#citation-template).

## Scope

Four read-only probes + one corpus generator, testing three hypotheses
about NAS small-file read performance plus the #737 HEVC first-byte gate:

* **H1** — does read throughput keep climbing past the current
  concurrency-depth cap (8, see `scanner/workers.py:_NAS_WORKERS`), or does
  it knee over sooner/later on this NAS?
* **H2** — how much of a real scan's wall time is pure directory
  enumeration (`scanner/walker.py::_iter_tree`) vs. file I/O?
* **H3** — how expensive is the exiftool second-read pass
  (`scanner/exif.py::batch_read_extracts`, #187 scoring extraction)
  relative to the hash-stage read?
* **#737** — how long does a cold HEVC->H.264 transcode make the browser
  wait for first byte, and does the cache actually avoid repeating that
  cost on a second view?

All four probes are **read-only** against the filesystem / running app —
they never write, delete, or modify anything under the scanned root or
the transcode cache. The **only** step that writes to disk (and, if
pointed at a NAS path, to the NAS) is corpus generation
(`make_small_file_corpus.py`) — run and approve that step explicitly
before the read probes; it refuses to run if the target directory is
non-empty so it can never silently overwrite real data.

## Session order

Run in this order so each probe's corpus is ready before the next needs it:

1. Generate the small-file corpus (owner approves the target path).
2. H1 — read knee ladder.
3. H2 — walk timing (same corpus).
4. H3 — exif second-read cost (same corpus).
5. #737 — HEVC first-byte (separate: needs a running app server + real
   HEVC files, not the synthetic corpus).

## 1. Corpus generation (writes to disk — owner approves the target)

```
python scripts/make_small_file_corpus.py --target J:\nas-probe-corpus \
    --count 5000 --mean-size-kb 40 --size-spread 0.5 \
    --files-per-dir 100 --manifest-out J:\nas-probe-corpus-manifest.json
```

Expected runtime: a few minutes for 5000 files (COM-padding is a pure
bytes operation; no per-file re-encoding). Refuses immediately (exit 2,
no writes) if `J:\nas-probe-corpus` already exists and is non-empty.

## 2. H1 — read knee ladder

```
python scripts/probe_read_knee_ladder.py --root J:\nas-probe-corpus \
    --rungs "1,2,4,8,16,32,64" --seconds-per-rung 30 \
    --output nas_probe_h1.json
```

Expected runtime: ~30s x 7 rungs = ~3.5 minutes.

**Decision:** compare `files_per_s` (or `mb_per_s`) at `c=16` against
`c=8`.

| Observation | Decision |
|---|---|
| `files_per_s[c=16] >= 1.15 * files_per_s[c=8]` | Knee is above 8 — candidate change: raise `scanner/workers.py:_NAS_WORKERS` past 8, validate with a real-scan A/B (`bench_web_port.py`) before shipping. |
| `files_per_s[c=16] < 1.15 * files_per_s[c=8]` (diminishing returns, mirrors the `_KNEE_GAIN_THRESHOLD` convention from the #551 autotune) | Knee is at or below 8 — current default is already near-optimal; DO-NOT-BUILD on raising the cap. |
| `p95_latency_ms` climbs steeply (e.g. >3x) between two adjacent rungs with `files_per_s` flat or falling | That rung has saturated the NAS's concurrent-request handling (SMB max mux or similar) — the true knee, regardless of the files/s reading. |

## 3. H2 — walk timing

```
python scripts/probe_walk_timing.py --root J:\nas-probe-corpus --repeat 2 \
    --output nas_probe_h2.json
```

Expected runtime: seconds to low minutes depending on entry count (5000
files across 50 dirs is small; re-run against a REAL large source tree
for a representative number — the synthetic corpus mainly validates the
tool, not the real-world walk share).

**Decision:** compare `passes[0].wall_s` (cold) against a reference full
scan's total wall time (`bench_web_port.py` or a real scan log).

| Observation | Decision |
|---|---|
| Walk share (`wall_s / full_scan_wall_s`) < 5% | Walk is not a bottleneck — DO-NOT-BUILD on any walk-specific optimisation. |
| Walk share >= 5%, and `passes[1].wall_s` (warm) is much lower than `passes[0]` | Cost is dominated by cold directory-metadata I/O (SMB round trips) — a candidate fix is a directory-listing prefetch/cache, not walker logic changes. |
| Walk share >= 5% and warm pass doesn't improve much | NAS-side directory caching isn't helping (or ``_iter_tree``'s own per-entry overhead is the cost) — profile `_iter_tree` itself before optimising I/O. |

Remember the caveat documented in the script itself: the dirs/files
breakdown adds an extra `Path.is_dir()` stat call beyond what the walker
pays internally, so treat `wall_s` as an upper bound and prefer the
cold-vs-warm **delta** over the absolute number.

## 4. H3 — exif second-read cost

```
python scripts/probe_exif_second_read.py --root J:\nas-probe-corpus \
    --max-files 2000 --output nas_probe_h3.json
```

Expected runtime: Phase A (read_bytes) should be fast (seconds); Phase B
(exiftool `-stay_open` batch) is typically the slower phase — expect
low minutes for 2000 files given ~500-file chunking.

**Decision:** read `phase_b_share_of_phase_a` from the JSON.

| Observation | Decision |
|---|---|
| `phase_b_share_of_phase_a` <= ~1.0 (exiftool pass costs about the same as or less than the hash read) | Exiftool second-read is NOT a meaningful tax on this NAS — DO-NOT-BUILD on any exiftool-avoidance optimisation. |
| `phase_b_share_of_phase_a` in ~1-3x | Moderate tax — worth tracking but not urgent; consider only if #187 scoring becomes a user-visible complaint. |
| `phase_b_share_of_phase_a` > 3x | Exiftool second-read roughly doubles (or worse) the total NAS I/O time for a scan — candidate follow-up: investigate `_EXIF_CHUNK` sizing or overlapping the exif pass with the hash pass instead of running them sequentially. |

## 5. #737 — HEVC first-byte gate

Requires the web app server running separately (owner starts it; this
runbook does not start or stop it) and a list of real HEVC-container
files (`.mov` / `.mp4` / `.m4v`) — point `--root` at a real iPhone-HEVC
folder, or curate a `--paths` file.

```
python scripts/probe_hevc_first_byte.py --base-url http://127.0.0.1:8765 \
    --root J:\iphone-hevc --limit 20 --output nas_probe_hevc.json
```

Expected runtime: dominated by ffmpeg transcode time on the FIRST request
per file (the `-preset ultrafast` path from #737); the SECOND request per
file should be near-instant (cache hit). A handful of files at ~5-30s
cold transcode each is plausible for a first pass.

**Decision:** for each file, compare `request1.ttfb_ms` (cold) against
`request2.ttfb_ms` (should be cache-warm).

| Observation | Decision |
|---|---|
| `request1.ttfb_ms` stays under the target SLO (e.g. a few seconds) for typical clip lengths | #737's `ultrafast` preset fix is sufficient — no further follow-up needed. |
| `request1.ttfb_ms` regularly exceeds the SLO, but `request2.ttfb_ms` is fast | Cache works correctly; the fix needed is progressive/streaming transcode (already flagged as the "proper follow-up" in `transcode_service.py`'s #737 comment) rather than a cache bug. |
| `cache_hit_on_request1` is `True` for files the owner believed were cold | The transcode cache already had these keyed entries from a PRIOR session — re-run with genuinely untouched files, or treat the reported `request1` numbers as warm-cache data, not cold-transcode data. |

## Citation template

Per the project's perf-claim rule (every MB/s / N× / latency claim needs
probe + SHA + args + JSON, or it gets deleted rather than softened), cite
every number pulled from this session like this:

```
Probe: scripts/probe_read_knee_ladder.py
SHA:   <git rev-parse HEAD at the time of the run>
Args:  --root J:\nas-probe-corpus --rungs "1,2,4,8,16,32,64" --seconds-per-rung 30
JSON:  nas_probe_h1.json (attach or link the artifact)
```

Do not report a number from this session without all four fields. A
missing field means the claim gets removed, not hedged.
