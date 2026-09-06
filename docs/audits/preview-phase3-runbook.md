# Preview redesign (#622) Phase 3 — verification runbook

**Status:** instrument ready for an owner-present session on the real NAS
library. **No real-NAS reading is recorded in this document.** Every number
that appears below is either a threshold copied from issue #622's acceptance
list or a placeholder shape in the results template — never a measurement.
Fill the template in during the session and cite each reading per
[Citation template](#citation-template).

## What this verifies

Issue #622 shipped in two phases: Phase 1 (viewport cap, byte-budget LRU, DNG
embedded-JPEG fast path, versioned disk cache, full-res modal viewer — PRs
#624/#632/#638) and Phase 2 (per-device coordinator, cancellation, 1-ahead
prefetch, mtime TTL — PR #842). Phase 3 is the measurement that decides
whether those landed, and it has four acceptance boxes:

| Box | Issue wording | Where the probe answers it |
|---|---|---|
| 1 | 100-click steady-state RSS below the memory bar on the J: NAS DNG mix | `summary.box1.steady_state_rss_mb` vs `summary.box1.threshold_mb` |
| 2 | NAS DNG time-to-first-paint markedly faster for files with an embedded JPEG at or above the viewport cap | `summary.box2.ratio_median` vs `summary.box2.threshold` — **computed by the probe** from two runs, never typed |
| 3 | Double-click opens the full-res viewer with pan/zoom | `modal.request_full_res_emitted`, `modal.zoom_pixmap_grew`, `modal.pan_wired` → `summary.box3.pass` |
| 4 | The viewer's QImage is freed when it closes | `modal.label_valid_before_close` → `modal.label_valid_after_close` (`shiboken6.isValid`) plus `modal.children_count_before_close` → `summary.box4.pass` |

The thresholds in boxes 1 and 2 are the issue's own acceptance bar. They are
CLI arguments (`--rss-threshold-mb`, `--box2-ratio`), so a session that wants
to record against a different bar changes the flag rather than the reading.

## The instrument

`scripts/preview_phase3_probe.py` — one command, one JSON artifact. Its pure
half (the summary roll-up, the box-2 ratio, the schema check, the artifact
write) lives beside it in `scripts/preview_phase3_analysis.py` and is
unit-tested in `tests/test_preview_phase3_probe.py`; the split is what keeps
the arithmetic behind every citation inside CI's reach.

The probe builds a headless Qt harness (`QT_QPA_PLATFORM=offscreen`) around the real
`ImageService`, the real `ImageTaskRunner` (which owns the real
`PreviewRequestCoordinator`), the real `PreviewPane` and the real
`FullResViewerDialog`. The only substitution is `MainWindow`: the harness
supplies a `QObject` that owns `imageLoaded` and forwards it to
`pane.on_image_loaded`, which is all `MainWindow._on_image_loaded`
(`app/views/main_window.py:855-863`) does on this path. A real `MainWindow`
would demand a loaded manifest, the tree model, menus and dialogs, none of
which is on the preview path.

**Deliberately not driven:** the 1-ahead prefetch. It fires from
`MainWindow._prefetch_next_group` on GROUP selection
(`app/views/main_window.py:836-850`), not from a single-file click, so a
single-click harness cannot exercise it and the probe does not pretend to.

**Which decode route a click took** is observed, not inferred from timing:
`ImageService` publishes no counter, so the probe wraps `_load_from_source`
and `_try_rawpy_embedded_thumb` on its own service instance and records what
they returned. `--force-full-decode` makes the second wrapper return `None`
without calling `extract_thumb`, which is exactly the pre-Phase-1 route into
`raw.postprocess`.

### Read-only posture

The probe never writes to, deletes from or renames anything under `--root`.
The only things it writes are the JSON artifact you name with `--output` and a
disk-cache directory. That cache defaults to a **fresh temp directory** —
never `~/AppData/Local/PhotoManager/thumbs/` — for two reasons: the app's real
cache is left untouched, and the two box-2 runs cannot serve each other's
bytes (both runs derive the same `path|side|mtime` cache key, so a shared
cache would make the second run measure the first one's output). The probe
deletes nothing, including that temp directory; its path is printed at the end
of the run and recorded in `env.disk_cache_dir`, and you can remove it
yourself afterwards.

## Prerequisites

* The repo checked out at the commit you intend to cite, and its virtualenv.
  Record `git rev-parse HEAD` — the probe stamps it into the artifact as
  `git_sha`, and `git_dirty` records whether the tree was clean.
* `J:` mapped and reachable, pointing at a directory containing the DNG mix
  the issue is about. Boxes 1 and 2 are both about that mix; a run over
  JPEG-only files cannot measure box 2 and will say so
  (`summary.box2.status == "not_measured"`).
* Nothing else hammering the NAS during the session (a running scan, a DSM
  index rebuild, another copy of the app).
* Expect the pair of runs to take a while: each click is a real decode, and
  the forced-full-decode run is the slow one by construction.

## Session order

Run the two box-2 invocations back to back on the same tree, the baseline
first. Everything else (boxes 1, 3, 4) comes out of run 1.

### 1. Baseline run — embedded-JPEG path (boxes 1, 3, 4 + the box-2 baseline)

```
.venv\Scripts\python.exe scripts\preview_phase3_probe.py ^
    --root J:\<your DNG library path> ^
    --clicks 100 ^
    --viewport-cap 2048 ^
    --output phase3_embedded.json
```

`--viewport-cap` matters: the probe runs offscreen, and offscreen Qt reports a
small virtual screen, so `_compute_viewport_cap()` would pin the request side
to that virtual width instead of your display's. Pass your real display width
(the production cap is `min(2048, primary screen width)`). The artifact records
both the effective `env.viewport_cap` and `env.viewport_cap_pinned`.

Watch the progress lines — they print the per-click route, so a run that is
silently taking `non_raw_source` (i.e. you pointed it at JPEGs) is visible
within the first few clicks rather than at the end.

### 2. Comparison run — forced full raw decode (box 2)

```
.venv\Scripts\python.exe scripts\preview_phase3_probe.py ^
    --root J:\<the same path as run 1> ^
    --clicks 100 ^
    --viewport-cap 2048 ^
    --force-full-decode ^
    --compare-json phase3_embedded.json ^
    --output phase3_fulldecode.json
```

`--compare-json` is what makes the ratio a computed field: the probe loads
run 1, pairs the two runs **by path**, keeps only paths where run 1 genuinely
took the embedded route and run 2 genuinely took the forced full decode, and
writes `summary.box2` into `phase3_fulldecode.json`. It also records which
artifact it compared against (`summary.box2.compared_with`).

Use the same `--root` and the same `--clicks` for both runs. A different tree
or a shorter run shrinks the pairing, and `summary.box2.n_paths` is the number
to sanity-check before quoting anything from it.

## Where the artifacts land

Both JSONs land wherever `--output` points (the commands above write them into
the current directory). They contain your library's real file paths, so treat
them the way PR #785 treated the NAS read-probe JSONs: archive them outside the
repo (e.g. under `~/.claude/handovers/`) and cite them by name rather than
committing them.

## Reading the artifact

```
.venv\Scripts\python.exe -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(d['probe'], d['git_sha']); print(json.dumps(d['summary'], indent=2)[:2000])" phase3_fulldecode.json
```

Schema, top level:

| Key | What it carries |
|---|---|
| `probe`, `probe_version` | which instrument produced this |
| `git_sha`, `git_dirty` | the commit it ran at, and whether the tree was clean |
| `args`, `argv` | every resolved argument and the exact command line |
| `host` | hostname, Python, platform, PySide6 version, Qt platform plugin, total RAM |
| `timestamps` | start / finish / wall seconds |
| `env` | root, file count, viewport cap (and whether pinned), disk-cache dir, LRU budgets, extension histogram |
| `per_click[]` | one row per click: `path`, `ext`, `device`, `size_bytes`, `ttfp_ms`, `ok`, `timed_out`, `decode_path`, `source_loaded`, `image_w/h`, `rss_bytes`, `private_bytes`, `lru_thumb_bytes`, `lru_preview_bytes` |
| `modal` | the box 3 + 4 fields listed in the table at the top |
| `summary` | `ttfp_ms` stats, `decode_path_counts`, `lru_occupancy_bytes`, and `box1`..`box4` |

`decode_path` on each click is one of `embedded_jpeg`, `raw_full_decode`,
`forced_full_decode`, `non_raw_source`, `cache_hit`, `unknown`.

The probe validates its own payload before writing and prints
`SCHEMA PROBLEMS: …` (exit code 1) if a citation field is missing, so an
artifact that cannot be cited never passes silently as one that can.

## Decisions

Each box's verdict is the probe's `pass` field; the table below says what to do
with it. `pass: null` means *not measured* — never read it as a pass.

### Box 1 — steady-state memory

| Observation | Decision |
|---|---|
| `summary.box1.pass` is `true` | The byte-budget LRU holds under a 100-click pass. Record the reading; box 1 is met. |
| `pass` is `true` but `pass_on_max` is `false` | The median is under the bar and a spike is over it. Look at `per_click[].rss_bytes` around the spike and at `lru_occupancy_bytes`: a spike with flat LRU occupancy is a transient decode peak (`_FULLRES_DECODE_SEM` bounds two at a time), not a leak. |
| `pass` is `false` and RSS climbs monotonically across `per_click` | Retention, not a peak. Re-run with `PHOTO_MANAGER_MEMORY_PROBE=1` per `memory-probe.md` to attribute it before proposing any fix. |
| `pass` is `false` and RSS is flat but high from click 1 | Baseline cost, not preview growth — attribute the baseline (loaded manifest? another window?) before treating it as a #622 regression. |
| `summary.box1.steady_window_used` is well under `steady_window_requested` | The run was shorter than the window; the reading is not a steady state. Re-run with more clicks. |

### Box 2 — embedded JPEG vs full raw decode

| Observation | Decision |
|---|---|
| `status` is `measured` and `pass` is `true` | The fast path delivers at or above the issue's bar on this library. Record `ratio_median`, `n_paths`, and both `ttfp_ms` stat blocks. |
| `status` is `measured` and `pass` is `false` | The fast path works but not at the issue's bar. Compare `ratio_min`/`ratio_max` and `ratio_of_medians` against `ratio_median`: a wide spread means the DNG mix is heterogeneous (embedded thumb sizes differ), and the right follow-up is to report the distribution, not one number. |
| `status` is `not_measured`, reason mentions no qualifying path | Either the tree has no DNG, or every DNG's embedded thumb is below the viewport cap so the baseline run also took the full decode. Check `env.ext_histogram` and `summary.decode_path_counts` in run 1 before concluding anything about the fast path. |
| `n_paths` is small (a handful) | The ratio is a median over very few files. Say so beside the number, or widen the run. |

### Boxes 3 and 4 — the full-res viewer

| Observation | Decision |
|---|---|
| `summary.box3.pass` and `summary.box4.pass` both `true` | Both boxes met. Note that `modal.label_valid_before_close` must be `true` and `label_valid_after_close` `false` — that pair is what makes the check a measurement rather than an assertion that would pass having done nothing. |
| `box3.pass` false with `request_full_res_emitted` false | The double-click never reached `PreviewPane._on_single_label_double_click`. A harness problem before it is a product problem — re-run with `--modal-path` naming a file you know loads. |
| `box3.pass` false with `zoom_pixmap_grew` false | Zoom did not change the rendered pixmap. Check `qimage_loaded` first: no image means nothing to zoom. |
| `modal.pan_scroll_range` is `0` | The image fits the viewer, so there is nothing to pan; `pan_wired` is the wiring assertion in that case. Not a failure. |
| `box4.pass` false | The dialog or its label survived close. `full_qimage_none_after_close`, `dialog_valid_after_close` and `label_valid_after_close` say which of the three conditions failed. |

`modal.dialog_is_modal` records that the shipped viewer is **non-modal**
(`app/views/dialogs/full_res_viewer.py` sets `setModal(False)` deliberately, so
the user can keep working while it is open) even though issue #622 calls it a
"modal viewer". The probe reports the fact rather than deciding it.

## Results template

Fill one row per reading. A row missing any of the four citation columns gets
deleted, not softened — that is the project's perf-claim rule.

| Box | Reading (field → value) | Probe | SHA | Args | JSON |
|---|---|---|---|---|---|
| 1 | `summary.box1.steady_state_rss_mb.p50` = _fill in_ (threshold `…threshold_mb`, `pass` = _fill in_) | `scripts/preview_phase3_probe.py` | _fill in_ | `--root … --clicks 100 --viewport-cap …` | `phase3_embedded.json` |
| 1 | `summary.box1.steady_state_rss_mb.max` = _fill in_ | `scripts/preview_phase3_probe.py` | _fill in_ | _same as above_ | `phase3_embedded.json` |
| 2 | `summary.box2.ratio_median` = _fill in_ over `n_paths` = _fill in_ (threshold `…threshold`, `pass` = _fill in_) | `scripts/preview_phase3_probe.py` | _fill in_ | `--root … --clicks 100 --force-full-decode --compare-json phase3_embedded.json` | `phase3_fulldecode.json` |
| 2 | `summary.box2.embedded_ttfp_ms.p50` / `full_decode_ttfp_ms.p50` = _fill in_ / _fill in_ | `scripts/preview_phase3_probe.py` | _fill in_ | _same as above_ | `phase3_fulldecode.json` |
| 3 | `summary.box3.pass` = _fill in_ (`modal.zoom_pixmap_grew`, `modal.pan_wired`) | `scripts/preview_phase3_probe.py` | _fill in_ | _run 1 args_ | `phase3_embedded.json` |
| 4 | `summary.box4.pass` = _fill in_ (`modal.label_valid_before_close` → `label_valid_after_close`) | `scripts/preview_phase3_probe.py` | _fill in_ | _run 1 args_ | `phase3_embedded.json` |

Session notes to record beside the table, because they change what the numbers
mean: the `--root` tree (file count and `env.ext_histogram`), whether anything
else was touching the NAS, `env.viewport_cap` and whether it was pinned, and
`host.total_ram_bytes` (the LRU budget is `min(256 MB, RAM/32)`, so a different
machine gets a different budget).

When the table is filled, write the session up as its own document alongside
`nas-probe-results-2026-07.md` — that is the #784 → #785 shape: runbook and
instrument in one PR, the session's readings in the next — and close #622 from
there.

## Citation template

Per the project's perf-claim rule (every memory / latency / ratio claim needs
probe + SHA + args + JSON, or it gets deleted rather than hedged), cite every
number pulled from this session like this:

```
Probe: scripts/preview_phase3_probe.py
SHA:   <git rev-parse HEAD at the time of the run — the artifact's git_sha>
Args:  --root J:\<path> --clicks 100 --viewport-cap 2048
JSON:  phase3_embedded.json (attach or link the artifact)
```

Do not report a number from this session without all four fields. A missing
field means the claim gets removed, not softened.
