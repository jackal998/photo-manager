# Preview redesign (#622) Phase 3 — verification runbook

**Status:** run on the owner's J: NAS on **2026-09-06** at SHA
`a202282190f5757e6cb373d37f2f4db51e85f9d1`. The
[Results](#results--2026-09-06-session) table below now carries real
readings, each with its probe + SHA + args + JSON citation. Everything above
that table is still procedure, and every threshold in it is copied from issue
#622's acceptance list, never measured.

**Session verdict: `FAIL`** — boxes 1, 3 and 4 pass; **box 2 fails**
(`ratio_median` 4.242× against the issue's 5× bar). The box-2 reading is a
measurement, not a refusal: `status: measured`, `n_paths: 89`,
`dropped_timeouts.total: 0`.

**Re-measured 2026-09-06 at SHA `75a976d` after #865** (draft-mode decode of
the embedded JPEG). Box 2 **still fails, at `ratio_median` 3.85×**; boxes 1, 3
and 4 still pass. See
[Results — 2026-09-06 re-measurement after #865](#results--2026-09-06-re-measurement-after-865)
below. The embedded arm itself got faster (paired p95 1003.1 → 546.9 ms over
the same 89 files) — box 2 is a two-arm ratio, so read that section before
reading the drop from 4.242 to 3.85 as a regression.

**Re-measured again 2026-09-07 at SHA `5fdee78`** (that draft may now land up
to 2 % under the cap, so a near-miss takes the next DCT step). **Session
verdict: `PASS` — all four boxes.** Box 2 `ratio_median` **6.958×**,
`ratio_of_medians` **5.184×**, `n_paths` 89, `dropped_timeouts.total` 0; box 1
157.340 p50 / 167.309 max against 600 MB. See
[Results — 2026-09-07 re-measurement after the draft undershoot](#results--2026-09-07-re-measurement-after-the-draft-undershoot).
This is the reading #622's box-2 acceptance names.

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

Rehearse once on the local sandbox, then run the two box-2 invocations back to
back on the same NAS tree, the baseline first. Everything else (boxes 1, 3, 4)
comes out of run 1.

### 0. Rehearsal on the local sandbox (about a minute, no NAS)

Do this first. It proves the harness starts, the artifact writes, and your
`gh`/venv/Qt setup works, before you commit to a ~100-click NAS session:

```
.venv/Scripts/python.exe scripts/preview_phase3_probe.py \
    --root qa/sandbox \
    --clicks 20 \
    --steady-window 10 \
    --output phase3_rehearsal.json
```

What a healthy rehearsal looks like: `clicks=20 ok=20 timeouts=0`,
`decode_path_counts` dominated by `non_raw_source`, `box1 … pass=True`,
`box3 pass=True`, `box4 pass=True`, and — **expected, not a failure** —
`box2 status=not_measured`, because `qa/sandbox` has no DNG at all and box 2
compares two DNG decode routes. The final `VERDICT:` line will read
`NOT_MEASURED` for exactly that reason.

To see the refusal machinery work, point it at the deliberately-undecodable
corpus: `--root qa/sandbox/corrupted --clicks 12 --steady-window 6` gives every
click the service's 64×64 grey placeholder, and all four boxes come back
`pass=None` with a reason rather than a fabricated pass.

### 1. Baseline run — embedded-JPEG path (boxes 1, 3, 4 + the box-2 baseline)

```
.venv/Scripts/python.exe scripts/preview_phase3_probe.py \
    --root "J:/<your DNG library path>" \
    --clicks 100 \
    --viewport-cap 2048 \
    --output phase3_embedded.json
```

`--viewport-cap` matters, and the value is not your monitor's width.
`image_tasks._compute_viewport_cap` returns a pinned value **verbatim** — it
applies no clamp of its own (`app/views/image_tasks.py:41-43`) — while
production computes `min(2048, primary screen logical width)`. So pass
**`min(2048, your display's logical width)`**: on this owner's 3840 × 2160 at
175 % that is a 2194 px logical width, so the value is `2048`. Typing `3840`
would measure a request size production never asks for, and every DNG would
then land on `raw_full_decode` (its embedded thumb is smaller than 3840) —
box 2 would report `not_measured` with no obvious clue why. The artifact
records the effective `env.viewport_cap` and `env.viewport_cap_pinned`; check
them before quoting anything.

The pin is needed at all because the probe runs offscreen and offscreen Qt
reports a small virtual screen, which would otherwise become the cap.

Watch the progress lines — they print the per-click route and whether the click
was answered with the placeholder, so a run that is silently taking
`non_raw_source` (you pointed it at JPEGs) or `placeholder=True` (the files are
not decodable) is visible within the first few clicks rather than at the end.

### 2. Comparison run — forced full raw decode (box 2)

```
.venv/Scripts/python.exe scripts/preview_phase3_probe.py \
    --root "J:/<the same path as run 1>" \
    --clicks 100 \
    --viewport-cap 2048 \
    --force-full-decode \
    --compare-json phase3_embedded.json \
    --output phase3_fulldecode.json
```

`--compare-json` is what makes the ratio a computed field: the probe loads
run 1, pairs the two runs **by path**, keeps only paths where run 1 genuinely
took the embedded route and run 2 genuinely took the forced full decode, and
writes `summary.box2` into `phase3_fulldecode.json`. It also records which
artifact it compared against (`summary.box2.compared_with`).

Use the same `--root`, the same `--clicks` and the same `--viewport-cap` for
both runs. A different tree or a shorter run shrinks the pairing;
`summary.box2.n_paths` and `summary.box2.dropped_timeouts` are the two numbers
to sanity-check before quoting anything from it.

## Where the artifacts land

Both JSONs land wherever `--output` points (the commands above write them into
the current directory). They contain your library's real file paths, so treat
them the way PR #785 treated the NAS read-probe JSONs: archive them outside the
repo (e.g. under `~/.claude/handovers/`) and cite them by name rather than
committing them.

## Reading the artifact

```
.venv/Scripts/python.exe -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(d['probe'], d['git_sha']); print(json.dumps(d['summary'], indent=2)[:2000])" phase3_fulldecode.json
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
| `per_click[]` | one row per click: `path`, `ext`, `device`, `size_bytes`, `ttfp_ms`, `ok`, `timed_out`, `decode_path`, `source_loaded`, `is_placeholder`, `image_w/h`, `rss_bytes`, `private_bytes`, `lru_thumb_bytes`, `lru_preview_bytes` |
| `modal` | the box 3 + 4 fields listed in the table at the top, plus `qimage_is_placeholder` |
| `summary` | `ttfp_ms` stats, `decode_path_counts`, `placeholder_count`, `cold_decode_count`, `lru_occupancy_bytes`, `box1`..`box4`, and `verdict` |

`decode_path` on each click is one of `embedded_jpeg`, `raw_full_decode`,
`forced_full_decode`, `non_raw_source`, `cache_hit`, `unknown`.

The probe validates its own payload before writing and prints
`SCHEMA PROBLEMS: …` (exit code 1) if a citation field is missing, so an
artifact that cannot be cited never passes silently as one that can.

## Decisions

Each box's verdict is the probe's `pass` field; the table below says what to do
with it. `pass: null` means *not measured* and carries a `reason` — never read
it as a pass.

**Read `summary.verdict.verdict` first.** It folds the four boxes into one
word and the run prints it as a `VERDICT:` line: `PASS` only when all four are
true, `FAIL` if any box failed, and `NOT_MEASURED` if any box could not be
answered — `NOT_MEASURED` outranks `PASS` precisely so a run with three passes
and one unanswerable box is never quoted as a verification.
`summary.verdict.reasons` lists why, per box.

A box refuses rather than guesses because `ImageService` answers an
undecodable file with a 64×64 grey placeholder instead of raising, so a run
over files it cannot read completes with `ok=True` on every click and a
perfectly plausible RSS curve. `summary.placeholder_count` and
`summary.cold_decode_count` are the two run-level numbers that say whether the
run decoded anything real.

### Box 1 — steady-state memory

| Observation | Decision |
|---|---|
| `summary.box1.pass` is `true` | The byte-budget LRU holds under a 100-click pass. Record the reading; box 1 is met. |
| `pass` is `true` but `pass_on_max` is `false` | The median is under the bar and a spike is over it. Look at `per_click[].rss_bytes` around the spike and at `lru_occupancy_bytes`: a spike with flat LRU occupancy is a transient decode peak (`_FULLRES_DECODE_SEM` bounds two at a time), not a leak. |
| `pass` is `false` and RSS climbs monotonically across `per_click` | Retention, not a peak. Re-run with `PHOTO_MANAGER_MEMORY_PROBE=1` per `memory-probe.md` to attribute it before proposing any fix. |
| `pass` is `false` and RSS is flat but high from click 1 | Baseline cost, not preview growth — attribute the baseline (loaded manifest? another window?) before treating it as a #622 regression. |
| `summary.box1.steady_window_used` is well under `steady_window_requested` | The run was shorter than the window; the reading is not a steady state. Re-run with more clicks. |
| `pass` is `null` with a reason naming the placeholder | Every click in the steady window was the grey placeholder (or timed out): the RSS curve is real but describes a run that decoded nothing. Check `summary.placeholder_count` and the `--root` you gave — this is what pointing the probe at unreadable files looks like. |
| `pass` is `null` with a reason naming cold decodes | The run has fewer than `box1.min_cold_decodes` genuine cold source decodes. Either the library is tiny (so nearly every click is a cache hit) or most files failed to decode; widen `--root` or raise `--clicks`. |

### Box 2 — embedded JPEG vs full raw decode

| Observation | Decision |
|---|---|
| `status` is `measured` and `pass` is `true` | The fast path delivers at or above the issue's bar on this library. Record `ratio_median`, `n_paths`, and both `ttfp_ms` stat blocks. |
| `status` is `measured` and `pass` is `false` | The fast path works but not at the issue's bar. Compare `ratio_min`/`ratio_max` and `ratio_of_medians` against `ratio_median`: a wide spread means the DNG mix is heterogeneous (embedded thumb sizes differ), and the right follow-up is to report the distribution, not one number. |
| `status` is `not_measured`, reason mentions no qualifying path | Either the tree has no DNG, or every DNG's embedded thumb is below the viewport cap so the baseline run also took the full decode. Check `env.ext_histogram` and `summary.decode_path_counts` in run 1 before concluding anything about the fast path. |
| `n_paths` is small (a handful) | The ratio is a median over very few files. Say so beside the number, or widen the run. |
| `dropped_timeouts.total` is non-zero | That many files appeared in both runs but a timeout in one arm kept them out of the pairing, so `n_paths` is smaller than the run size suggests. **Read this before quoting the ratio:** on a NAS the file that times out is the slowest one, so the losses bias the ratio toward the fast files. `embedded_arm` / `forced_arm` say which side lost them; raise `--timeout-s` and re-run if the count is more than incidental. |

### Boxes 3 and 4 — the full-res viewer

| Observation | Decision |
|---|---|
| `summary.box3.pass` and `summary.box4.pass` both `true` | Both boxes met. Note that `modal.label_valid_before_close` must be `true` and `label_valid_after_close` `false` — that pair is what makes the check a measurement rather than an assertion that would pass having done nothing. |
| `box3.pass` false with `request_full_res_emitted` false | The double-click never reached `PreviewPane._on_single_label_double_click`. A harness problem before it is a product problem — re-run with `--modal-path` naming a file you know loads. |
| `box3.pass` false with `zoom_pixmap_grew` false | Zoom did not change the rendered pixmap. Check `qimage_loaded` first: no image means nothing to zoom. |
| `modal.pan_scroll_range` is `0` | The image fits the viewer, so there is nothing to pan. Not a failure. |
| `box4.pass` false | The dialog or its label survived close. `full_qimage_none_after_close`, `dialog_valid_after_close` and `label_valid_after_close` say which of the three conditions failed. |
| `box3.pass` / `box4.pass` are `null` with a reason | The viewer was opened on a file that decoded to the placeholder (or to nothing). Both boxes refuse: the zoom would have been applied to the grey square, and `FullResViewerDialog.closeEvent` nulls `_full_qimage` unconditionally, so "freed" would be true for an image that never existed. Give `--modal-path` a file you know decodes, or fix the `--root`. |

**What box 3's pan check does and does not cover.** The zoom half is exercised
for real — `_apply_zoom(1.25)` runs and the rendered pixmap is measured before
and after. The pan half asserts only the **wiring** (`_ZoomLabel._scroll_area`
is the dialog's scroll area); `mouseMoveEvent` is never driven, so a
drag-to-pan regression inside that handler would not be caught here. Treat
`pan_wired` as "pan is connected", not "pan works", and confirm panning by hand
in the real app if that is what you need to sign off.

`modal.dialog_is_modal` records that the shipped viewer is **non-modal**:
`app/views/dialogs/full_res_viewer.py:90` sets `setModal(False)` and the class
docstring reads "Non-modal full-resolution viewer with pan/zoom", while issue
#622 calls it a "modal viewer". The probe reports the fact; whether the issue
text or the code should move is the owner's call.

## Results — 2026-09-06 session

One row per reading. A row missing any of the four citation columns gets
deleted, not softened — that is the project's perf-claim rule.

The two commands the rows below cite, verbatim as run from the worktree root
(`ART` = `~/.claude/handovers/worker-reports/622-phase3-artifacts`, where the
artifacts are archived per [Where the artifacts land](#where-the-artifacts-land)):

```
# run 1 args
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --output "$ART/phase3_embedded.json"

# run 2 args
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --force-full-decode \
    --compare-json "$ART/phase3_embedded.json" \
    --output "$ART/phase3_fulldecode.json"
```

`SHA` in every row below is `a202282190f5757e6cb373d37f2f4db51e85f9d1`, the
artifacts' own `git_sha`, with `git_dirty: false` on both.

| Box | Reading (field → value) | Probe | SHA | Args | JSON |
|---|---|---|---|---|---|
| 1 | `summary.box1.steady_state_rss_mb.p50` = **157.946 MB** (threshold `threshold_mb` = 600.0, `pass` = **true**) | `scripts/preview_phase3_probe.py` | `a202282` | _run 1 args_ | `phase3_embedded.json` |
| 1 | `summary.box1.steady_state_rss_mb.max` = **171.418 MB** (`pass_on_max` = **true**; `steady_window_used` = 50 of 50 requested, `placeholder_paints_in_window` = 0, `cold_decode_count` = 100) | `scripts/preview_phase3_probe.py` | `a202282` | _run 1 args_ | `phase3_embedded.json` |
| 2 | `summary.box2.ratio_median` = **4.242×** over `n_paths` = **89**, `dropped_timeouts.total` = **0** (threshold `threshold` = 5.0, `pass` = **false**) | `scripts/preview_phase3_probe.py` | `a202282` | _run 2 args_ | `phase3_fulldecode.json` |
| 2 | `summary.box2.embedded_ttfp_ms.p50` / `full_decode_ttfp_ms.p50` = **381.124 ms** / **1342.884 ms** | `scripts/preview_phase3_probe.py` | `a202282` | _run 2 args_ | `phase3_fulldecode.json` |
| 2 | distribution, per the box-2 decision row for a measured fail: `ratio_min` = **3.094**, `ratio_max` = **5.922**, `ratio_of_medians` = **3.523** | `scripts/preview_phase3_probe.py` | `a202282` | _run 2 args_ | `phase3_fulldecode.json` |
| 3 | `summary.box3.pass` = **true** (`modal.zoom_pixmap_grew` = true, scale 1.0 → 1.25; `modal.pan_wired` = true, `pan_scroll_range` = 2896; `request_full_res_emitted` = true) | `scripts/preview_phase3_probe.py` | `a202282` | _run 1 args_ | `phase3_embedded.json` |
| 4 | `summary.box4.pass` = **true** (`modal.label_valid_before_close` = true → `label_valid_after_close` = false; `full_qimage_none_after_close` = true, `dialog_valid_after_close` = false) | `scripts/preview_phase3_probe.py` | `a202282` | _run 1 args_ | `phase3_embedded.json` |
| — | `summary.verdict.verdict` = **`FAIL`**, `verdict.failed` = `['box2']`, `verdict.unmeasured` = `[]` | `scripts/preview_phase3_probe.py` | `a202282` | _run 2 args_ | `phase3_fulldecode.json` |

Run 1 read alone reports `verdict: NOT_MEASURED` with `unmeasured: ['box2']`
— that is the single-run state the probe describes ("re-run with
`--force-full-decode --compare-json`"), not a second opinion about box 2. The
session verdict is run 2's, which is where the pairing lives.

### Session notes

These change what the numbers mean, so they are recorded beside the table:
the `--root` tree (file count and `env.ext_histogram`), whether anything else
was touching the NAS, `env.viewport_cap` and whether it was pinned, and
`host.total_ram_bytes` (the LRU budget is `min(256 MB, RAM/32)`, so a different
machine gets a different budget).

* **`--root` tree:** `J:/圖片/20240601-0712大阪`, the owner's Osaka-trip
  iPhone ProRAW library. `env.file_count` = 445, `env.ext_histogram` =
  `{".dng": 445}` (under the `--ext` restriction below).
* **`--ext .dng` was passed, and it is load-bearing on this root.** The
  directory holds 1353 files matching the probe's default extension set (821
  `.heic`, 445 `.dng`, 62 `.png`, 25 `.jpg`; plus 190 video files the probe
  excludes by design). `discover_images` sorts case-insensitively by full
  path, so the *default* extension set would have spent the first 100 clicks
  on 97 `.heic` + 1 `.jpg` + 2 `.png` and **zero DNG** — box 2 would have
  reported `not_measured` and box 1 would have described a HEIC run, while
  issue #622's boxes 1 and 2 both say "on the J: NAS DNG mix". The
  restriction was decided from a directory listing *before* the first run,
  and both NAS runs used identical `--root`, `--clicks`, `--viewport-cap` and
  `--ext`. **If your `--root` is a mixed library rather than a DNG-only tree,
  pass `--ext` —** the prerequisite above ("pointing at a directory
  containing the DNG mix") is not satisfied by a directory that merely
  *contains* DNGs.
* **Nothing else was touching the NAS** during either run: no scan, no second
  copy of the app, no other agent — every concurrent worker was paused for the
  pair, and the two runs went back to back (run 1 18:09:45–18:10:42, run 2
  18:10:54–18:14:42 local; `timestamps.wall_s` 56.7 and 227.9).
* **`env.viewport_cap` = 2048, `env.viewport_cap_pinned` = true** on both
  runs. The rehearsal, which pins nothing, recorded `viewport_cap: 800` from
  offscreen Qt's virtual screen — the concrete reason the pin exists.
* **`host.total_ram_bytes` = 34,189,557,760** (31.8 GiB), so the budget
  resolved to `min(256 MB, RAM/32)` = 256 MB, split
  `cache_budget_bytes: {thumb: 67,108,864, preview: 201,326,592}`. Observed
  peak `lru_occupancy_bytes.preview_max` = 39,565,490 B (~37.7 MiB), well
  inside the 192 MB preview tier. A machine with less RAM gets a smaller
  budget and a different box-1 curve.
* **11 of the 100 clicked `.DNG` files took `non_raw_source` in *both* runs**
  — the same 11 paths, so this is deterministic, not a flake. rawpy refuses
  them (`Unsupported file format or not RAW file` in the run-2 log) and the
  service falls through to the Pillow / Shell-WIC route, which still paints
  them at the 2048 cap. They are 0.8–12.8 MB against 16–75 MB for the ProRAW
  files around them, so they are very likely not ProRAW at all. The probe
  excludes them from the box-2 pairing by construction (run 1 must genuinely
  have taken the embedded route), which is why `n_paths` is 89 and not 100.
  Recorded as an observation; nothing was changed for it.
* **`modal.dialog_is_modal` = false**, exactly as the section above predicts.
  Box 3 is scored on the behaviour (opens, zooms, pan wired), not on the word
  "modal"; whether the issue text or the code should move is still the
  owner's call.

### What box 2's fail does and does not say

`status: measured` with `dropped_timeouts.total: 0` over `n_paths: 89` means
the pairing lost nothing: every file that took the embedded route in run 1
took the forced full decode in run 2, and both arms completed. So the 4.242×
median is a real reading of this library, not an artefact of a thin pairing.

Per the box-2 decision row for a measured fail, the follow-up is the
distribution rather than the one number: the per-path ratio spans
3.094×–5.922×, and `ratio_of_medians` (3.523×) sits below `ratio_median`
(4.242×) because the slowest full decodes are not the same files as the
slowest embedded reads. That spread is what a heterogeneous DNG mix looks
like — embedded thumb sizes differ per file — and it means the issue's "5×"
is met for part of this library and not for the rest.

What it does **not** say: nothing here indicts the Phase 1 fast path's
existence. 89 of 100 DNG clicks took `embedded_jpeg`, the median click paints
in 381 ms instead of 1343 ms, and box 1 lands at 158 MB against a 600 MB bar.
The gap is between "markedly faster" as built and the specific 5× number the
issue wrote down before anything had been measured. Closing #622 on this
session means either re-stating that bar against the measured 4.242× or
leaving box 2 open — an owner decision, not one this document makes.

The session write-up alongside `nas-probe-results-2026-07.md` — the #784 →
#785 shape, runbook and instrument in one PR, the session's readings in the
next — is
[`preview-phase3-results-2026-09.md`](preview-phase3-results-2026-09.md).
#622 closes from there, on the owner's call about box 2.

## Results — 2026-09-06 re-measurement after #865

Second session, same root, same arguments, at SHA
`75a976d97a05fa33bd3807b009c6f9e66c44fbb3` (`git_dirty: false` on both
artifacts), after #865 added draft-mode decode to the embedded-JPEG branch.
`ART` is now `~/.claude/handovers/worker-reports/865-artifacts`; the run 1 /
run 2 argument blocks above are otherwise unchanged, so `_run 1 args_` and
`_run 2 args_` below mean the same flags against that directory.

| Box | Reading (field → value) | Probe | SHA | Args | JSON |
|---|---|---|---|---|---|
| 1 | `summary.box1.steady_state_rss_mb.p50` = **156.834 MB** (threshold 600.0, `pass` = **true**, `pass_on_max` = **true**) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 1 args_ | `phase3_embedded.json` |
| 1 | `summary.box1.steady_state_rss_mb.max` = **169.955 MB** (`steady_window_used` = 50 of 50, `placeholder_paints_in_window` = 0, `cold_decode_count` = 100) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 1 args_ | `phase3_embedded.json` |
| 2 | `summary.box2.ratio_median` = **3.85×** over `n_paths` = **89**, `dropped_timeouts.total` = **0** (threshold 5.0, `pass` = **false**) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 2 args_ | `phase3_fulldecode.json` |
| 2 | `summary.box2.embedded_ttfp_ms` p50 / **p95** / **max** = **380.887** / **521.997** / **623.486 ms** (pre-#865: 381.124 / 1000.828 / 1118.308) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 2 args_ | `phase3_fulldecode.json` |
| 2 | `summary.box2.full_decode_ttfp_ms.p50` = **1291.335 ms** — the control arm, which #865 does not touch, moved 51.5 ms on its own (pre-#865: 1342.884) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 2 args_ | `phase3_fulldecode.json` |
| 2 | distribution: `ratio_min` = **2.835**, `ratio_max` = **12.436**, `ratio_of_medians` = **3.390** | `scripts/preview_phase3_probe.py` | `75a976d` | _run 2 args_ | `phase3_fulldecode.json` |
| 3 | `summary.box3.pass` = **true** (zoom 1.0 → 1.25, `pan_scroll_range` = 2896) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 1 args_ | `phase3_embedded.json` |
| 4 | `summary.box4.pass` = **true** (`label_valid_before_close` true → `label_valid_after_close` false) | `scripts/preview_phase3_probe.py` | `75a976d` | _run 1 args_ | `phase3_embedded.json` |
| — | `summary.verdict.verdict` = **`FAIL`**, `verdict.failed` = `['box2']`, `verdict.unmeasured` = `[]` | `scripts/preview_phase3_probe.py` | `75a976d` | _run 2 args_ | `phase3_fulldecode.json` |

**Read the two-arm caveat before quoting the drop from 4.242 to 3.85.** Box 2
divides one arm by the other and #865 changed only the embedded arm. Paired by
path across the two sessions, that arm improved on every summary statistic
except the median (mean 547.5 → 402.3 ms, p95 1003.1 → 546.9, max 1118.3 →
623.5, faster on 46 of 89 files), while the untouched control arm ran 51.5 ms
faster at the median. The full analysis, including the measured reason the
median did not move — this library's embedded JPEGs are bimodal, 4032 × 3024
for most files and 8064 × 6048 for 23 of them, and `draft` correctly declines
to reduce the first group below the 2048 cap — is in
[`preview-phase3-results-2026-09.md`](preview-phase3-results-2026-09.md).

## Results — 2026-09-07 re-measurement after the draft undershoot

Third session, same root and arguments, at SHA
`5fdee788af2a0386c63bcb5b7f46a8413f5ab391` (`git_dirty: false` on both
artifacts), after `_DRAFT_UNDERSHOOT = 0.02` let a near-miss take the next DCT
step. `ART` is `~/.claude/handovers/worker-reports/865-artifacts`, artifacts
`r2_`-prefixed; the run 1 / run 2 argument blocks are otherwise unchanged.

| Box | Reading (field → value) | Probe | SHA | Args | JSON |
|---|---|---|---|---|---|
| 1 | `summary.box1.steady_state_rss_mb.p50` = **157.340 MB** (threshold 600.0, `pass` = **true**, `pass_on_max` = **true**) | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 1 args_ | `r2_phase3_embedded.json` |
| 1 | `summary.box1.steady_state_rss_mb.max` = **167.309 MB** (`steady_window_used` 50 of 50, `placeholder_paints_in_window` 0, `cold_decode_count` 100) | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 1 args_ | `r2_phase3_embedded.json` |
| 2 | `summary.box2.ratio_median` = **6.958×** over `n_paths` = **89**, `dropped_timeouts.total` = **0** (threshold 5.0, `pass` = **true**) | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 2 args_ | `r2_phase3_fulldecode.json` |
| 2 | `summary.box2.ratio_of_medians` = **5.184×** — also clears the bar, so the pass does not rest on the per-path median alone | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 2 args_ | `r2_phase3_fulldecode.json` |
| 2 | `summary.box2.embedded_ttfp_ms` p50 / p95 / max = **241.228 / 449.702 / 544.030 ms** (pre-#865: 381.124 / 1000.828 / 1118.308) | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 2 args_ | `r2_phase3_fulldecode.json` |
| 2 | `summary.box2.full_decode_ttfp_ms.p50` = **1250.554 ms** — control arm, untouched by every commit, drifted 1342.9 → 1291.3 → 1250.6 across the three sessions | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 2 args_ | `r2_phase3_fulldecode.json` |
| 2 | distribution: `ratio_min` = **2.582**, `ratio_max` = **20.020** | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 2 args_ | `r2_phase3_fulldecode.json` |
| — | output long edge, `per_click[].image_w/h` over the 89 embedded paints = **2016 px × 83, 2048 px × 6** — the measured cost of the tolerance | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 1 args_ | `r2_phase3_embedded.json` |
| 3 | `summary.box3.pass` = **true** (zoom 1.0 → 1.25, `pan_scroll_range` 2896) | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 1 args_ | `r2_phase3_embedded.json` |
| 4 | `summary.box4.pass` = **true** (`label_valid_before_close` true → `label_valid_after_close` false) | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 1 args_ | `r2_phase3_embedded.json` |
| — | `summary.verdict.verdict` = **`PASS`**, `verdict.failed` = `[]`, `verdict.unmeasured` = `[]` | `scripts/preview_phase3_probe.py` | `5fdee78` | _run 2 args_ | `r2_phase3_fulldecode.json` |

**Two things to read beside the pass.** The control arm got 6.9 % faster across
the three sessions with no code change, and since it is the ratio's numerator
that works *against* the pass — recomputed against the pre-#865 control median
the ratio of medians would be 5.57×, higher. And the tolerance has a measured
cost: 83 of 89 previews now paint at 2016 px rather than 2048 (1.56 %, one DCT
step), with the other 6 still exactly at the cap. Detail in
[`preview-phase3-results-2026-09.md`](preview-phase3-results-2026-09.md).

## Citation template

Per the project's perf-claim rule (every memory / latency / ratio claim needs
probe + SHA + args + JSON, or it gets deleted rather than hedged), cite every
number pulled from this session like this:

```
Probe: scripts/preview_phase3_probe.py
SHA:   <git rev-parse HEAD at the time of the run — the artifact's git_sha>
Args:  --root "J:/<path>" --clicks 100 --viewport-cap 2048
JSON:  phase3_embedded.json (attach or link the artifact)
```

Do not report a number from this session without all four fields. A missing
field means the claim gets removed, not softened.
