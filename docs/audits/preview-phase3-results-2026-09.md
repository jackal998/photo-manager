# Preview redesign (#622) Phase 3 — session results, 2026-09-06

Owner-authorised session executed per
[`preview-phase3-runbook.md`](preview-phase3-runbook.md). Both probe runs at
commit `a202282` (`git_dirty: false` in both artifacts). Raw JSONs archived
locally at `~/.claude/handovers/worker-reports/622-phase3-artifacts/` — **not
committed**, they contain the owner's real library paths, per the runbook's
"Where the artifacts land" and the #785 precedent. Every claim below carries
the 4-tuple (probe + SHA `a202282` + args + JSON file).

NAS: `J:` mapped, target tree `J:/圖片/20240601-0712大阪` — the owner's
Osaka-trip iPhone ProRAW library, 445 `.DNG` in a directory of 1544 entries.
Host: 31.8 GiB RAM, Python 3.12.9, PySide6 6.11.0, Qt platform `offscreen`.

## Verdict — **`FAIL`**, on box 2 alone

`summary.verdict.verdict` = `FAIL`, `verdict.failed` = `['box2']`,
`verdict.unmeasured` = `[]` (`phase3_fulldecode.json`).

| Box | Issue's bar | Measured | Verdict |
|---|---|---|---|
| 1 — 100-click steady-state RSS on the J: NAS DNG mix | < 600 MB | **157.946 MB** p50, **171.418 MB** max | **pass** |
| 2 — NAS DNG TTF-paint faster on the embedded-JPEG path | ≥ 5× | **4.242×** median over 89 paired paths | **fail** |
| 3 — double-click opens the full-res viewer with pan/zoom | qualitative | opened, zoomed 1.0 → 1.25, pan wired | **pass** |
| 4 — the viewer's QImage is freed when it closes | qualitative | label valid → invalid after close | **pass** |

Nothing here is a refusal: `placeholder_count` = 0 and `cold_decode_count` =
100 on both runs, so every box was answered from real decodes.

## The two runs

Both from the worktree root, back to back, with nothing else touching the NAS
(every concurrent agent was paused for the pair). `ART` =
`~/.claude/handovers/worker-reports/622-phase3-artifacts`.

```
# run 1 — baseline, embedded-JPEG path      18:09:45 → 18:10:42 (wall_s 56.715)
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --output "$ART/phase3_embedded.json"

# run 2 — forced full raw decode            18:10:54 → 18:14:42 (wall_s 227.9)
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --force-full-decode \
    --compare-json "$ART/phase3_embedded.json" \
    --output "$ART/phase3_fulldecode.json"
```

| | run 1 (embedded) | run 2 (forced full) |
|---|---|---|
| clicks / ok / timeouts | 100 / 100 / 0 | 100 / 100 / 0 |
| placeholders / cold decodes | 0 / 100 | 0 / 100 |
| `decode_path_counts` | `embedded_jpeg` 89, `non_raw_source` 11 | `forced_full_decode` 89, `non_raw_source` 11 |
| `ttfp_ms` p50 / p95 / max | 374.707 / 1000.828 / 1118.308 | 1301.763 / 4876.087 / 4943.267 |
| steady-state RSS p50 / max | 157.946 / 171.418 MB | 141.826 / 151.171 MB |

A rehearsal on `qa/sandbox` preceded both
(`--clicks 20 --steady-window 10`, `phase3_rehearsal.json`) and matched the
runbook's healthy shape exactly: `clicks=20 ok=20 timeouts=0`,
`decode_path_counts={'non_raw_source': 20}`, boxes 1/3/4 `pass=True`, box 2
`not_measured`, `VERDICT: NOT_MEASURED`.

## Box 1 — steady-state memory. **Pass, with room to spare.**

`summary.box1.steady_state_rss_mb` over the last 50 of 100 clicks:
p50 **157.946 MB**, mean 156.324, p95 166.814, max **171.418** — against
`threshold_mb` 600.0. `pass: true` and `pass_on_max: true`, so the bar holds
on the worst single click, not only the median.

The reading is qualified as real by the fields that exist to disqualify it:
`steady_window_used` 50 of 50 requested, `placeholder_paints_in_window` 0,
`cold_decode_count` 100 against `min_cold_decodes` 3, `reason: null`.

Budget context: `host.total_ram_bytes` 34,189,557,760 → `min(256 MB, RAM/32)`
= 256 MB, split `{thumb: 67,108,864, preview: 201,326,592}`. Peak occupancy
`lru_occupancy_bytes.preview_max` = 39,565,490 B (~37.7 MiB) — the preview
tier never came close to its 192 MB budget on a 100-click DNG pass, and the
thumb tier stayed at 0 (this path populates the preview tier only).

```
Probe: scripts/preview_phase3_probe.py
SHA:   a202282190f5757e6cb373d37f2f4db51e85f9d1
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
JSON:  phase3_embedded.json
```

## Box 2 — embedded JPEG vs full raw decode. **Measured, and it fails at 4.242× against 5×.**

`summary.box2`: `status: measured`, `ratio_median` **4.242**, `threshold`
5.0, `pass: false`, over `n_paths` **89** with `dropped_timeouts.total`
**0** (`embedded_arm` 0, `forced_arm` 0).

| Field | Value |
|---|---|
| `embedded_ttfp_ms` min / p50 / mean / p95 / max | 256.823 / **381.124** / 547.53 / 1000.828 / 1118.308 |
| `full_decode_ttfp_ms` min / p50 / mean / p95 / max | 1128.113 / **1342.884** / 2455.722 / 4876.087 / 4943.267 |
| `ratio_min` / `ratio_median` / `ratio_max` | 3.094 / **4.242** / 5.922 |
| `ratio_of_medians` | 3.523 |

**Read the pairing before the ratio.** `dropped_timeouts.total` is 0, so no
file was lost from either arm — the runbook's warning that timeouts bias the
ratio toward fast files does not apply to this session. The 89 is not a loss
either: it is 100 clicks minus the 11 files that took `non_raw_source` in
both arms (below), and the probe pairs only paths where run 1 genuinely took
the embedded route.

Per the runbook's decision row for a measured fail, the honest report is the
distribution, not the one number. The per-path ratio spans **3.094×–5.922×**:
the issue's 5× is met by part of this library and missed by the rest.
`ratio_of_medians` (3.523×) sitting below `ratio_median` (4.242×) says the
slowest full decodes are not the same files as the slowest embedded reads —
a heterogeneous mix, embedded thumb sizes differing per file, which is
exactly the shape the runbook predicts for a wide spread.

**What this does not indict.** The Phase 1 fast path is working: 89 of 100
DNG clicks took `embedded_jpeg`, and the median click paints in 381 ms
instead of 1343 ms — a ~962 ms saving per click on a dedup pass. The gap is
between "markedly faster" as built and the specific 5× the issue wrote down
before anything had been measured. Whether to re-state the bar against the
measured 4.242× or leave box 2 open is the owner's call; this document does
not make it, and the number is not softened to reach it.

```
Probe: scripts/preview_phase3_probe.py
SHA:   a202282190f5757e6cb373d37f2f4db51e85f9d1
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
       --force-full-decode --compare-json "$ART/phase3_embedded.json"
JSON:  phase3_fulldecode.json (summary.box2.compared_with names run 1)
```

## Boxes 3 and 4 — the full-res viewer. **Both pass.**

Opened on `IMG_0867.DNG` (auto-picked from the clicks; `qimage_is_placeholder`
false, so the checks ran against a real 3024 × 4032 image, not the grey
square).

Box 3: `double_click_delivered` true → `request_full_res_emitted` true →
`dialog_opened` true; `zoom_scale_before` 1.0 → `zoom_scale_after` 1.25 with
`zoom_pixmap_grew` true; `pan_wired` true with `pan_scroll_range` 2896.

Box 4: `label_valid_before_close` **true** → `label_valid_after_close`
**false**, `full_qimage_none_after_close` true, `dialog_valid_after_close`
false. The before/after pair is what makes this a measurement rather than an
assertion that would pass having done nothing. RSS around the modal:
184.1 MB before open → 236.4 MB open → 234.0 MB after close.

Two limits, both already documented in the runbook and neither hidden here:
`pan_wired` asserts the wiring (`_ZoomLabel._scroll_area` is the dialog's
scroll area), not that dragging works — `mouseMoveEvent` is never driven, so
a drag-to-pan regression inside that handler would survive this box. And
`modal.dialog_is_modal` is **false**: the shipped viewer is non-modal
(`app/views/dialogs/full_res_viewer.py:90` sets `setModal(False)`) while
issue #622 calls it a "modal viewer". Box 3 is scored on behaviour, not on
the word; reconciling the two is the owner's call.

```
Probe: scripts/preview_phase3_probe.py
SHA:   a202282190f5757e6cb373d37f2f4db51e85f9d1
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
JSON:  phase3_embedded.json (modal block + summary.box3 / summary.box4)
```

## Session deviations

One, decided before any run and disclosed in the runbook itself.

**`--ext .dng` was added to both NAS runs.** The runbook's example command
passes no `--ext`, so the probe's default still-image extension set applies.
Measured read-only before touching the probe: the root holds 1353 files
matching that default set — 821 `.heic`, 445 `.dng`, 62 `.png`, 25 `.jpg`
(plus 190 videos the probe excludes by design) — and `discover_images` sorts
case-insensitively by full path, so the first 100 clicks would have been
**97 `.heic` + 1 `.jpg` + 2 `.png` and zero DNG**. That run would have
reported `box2: not_measured` and a box-1 curve describing a HEIC pass, while
issue #622's boxes 1 and 2 both say "on the J: NAS DNG mix" and the runbook's
own prerequisite asks for "a directory containing the DNG mix the issue is
about".

`--ext` is a documented argument (`preview_phase3_probe.py:680`); restricting
it to `.dng` makes the click pool the 445 ProRAW files the issue names.
`--root`, `--clicks`, `--viewport-cap` and `--ext` were identical across run
1 and run 2, which is the constraint the runbook places on the pair. The
runbook now carries a note that `--ext` is load-bearing whenever `--root` is
a mixed library.

## Observations recorded, not acted on

* **11 of the 100 clicked `.DNG` files are not readable by rawpy** —
  `IMG_0989, 1002, 1007, 1009, 1015, 1025, 1043, 1053, 1055, 1134, 1164`.
  The same 11 in both runs, so deterministic rather than a flake; run 2's log
  shows `rawpy exception … Unsupported file format or not RAW file` and the
  service falls through to the Pillow / Shell-WIC route, which paints them
  correctly at the 2048 cap. They are 0.8–12.8 MB against 16–75 MB for the
  ProRAW files beside them, so they are very likely not ProRAW at all
  (exported or edited DNGs). No user-visible defect: they load, and they are
  excluded from the box-2 pairing by construction. Worth knowing before
  anyone reads `n_paths: 89` as a loss.
* **The `offscreen` platform is the measurement environment**, not the
  shipped one. `env.viewport_cap_pinned` is true precisely because offscreen
  Qt reports a small virtual screen (the rehearsal, unpinned, recorded
  `viewport_cap: 800`). The pin makes the probe request the size production
  requests; it does not make the probe production.
* **Box 1's headroom is large enough to be worth re-reading if the LRU
  budget changes.** 158 MB against a 600 MB bar on a 31.8 GiB machine, with
  the preview tier peaking at 37.7 MiB of its 192 MB. A smaller-RAM machine
  gets a smaller budget and a different curve, so this box is a pass *for
  this host*, not a universal one.

---

# 2026-09 re-measurement after #865

Second owner-authorised session, **2026-09-06**, same runbook, same root, same
arguments, at commit `75a976d` (`git_dirty: false` in both artifacts) — the
commit that adds draft-mode decode to the embedded-JPEG branch
(`infrastructure/image_service.py`, issue #865). Artifacts archived at
`~/.claude/handovers/worker-reports/865-artifacts/`, **not committed** (real
library paths), same as the first session. Rehearsal on `qa/sandbox` preceded
both runs and matched the runbook's healthy shape (`clicks=20 ok=20
timeouts=0`, `decode_path_counts={'non_raw_source': 20}`, boxes 1/3/4
`pass=True`, box 2 `not_measured`, `VERDICT: NOT_MEASURED`).

## Verdict — still **`FAIL`** on box 2 alone, now at **3.85×**

`summary.verdict.verdict` = `FAIL`, `verdict.failed` = `['box2']`,
`verdict.unmeasured` = `[]` (`phase3_fulldecode.json`).

| Box | Bar | Pre-#865 (`a202282`) | Post-#865 (`75a976d`) | Verdict |
|---|---|---|---|---|
| 1 — steady-state RSS, 100 clicks | < 600 MB | 157.946 p50 / 171.418 max | **156.834** p50 / **169.955** max | **pass** (`pass_on_max` true) |
| 2 — embedded vs full-decode TTFP | ≥ 5× | 4.242× over 89 paths | **3.85×** over 89 paths | **fail** |
| 3 — full-res viewer opens with pan/zoom | qualitative | pass | pass (zoom 1.0 → 1.25, `pan_scroll_range` 2896) | **pass** |
| 4 — the viewer's QImage is freed on close | qualitative | pass | pass (label valid → invalid) | **pass** |

`placeholder_count` = 0 and `cold_decode_count` = 100 on both runs, so every
box was answered from real decodes. `dropped_timeouts.total` = 0 on both arms.

**Box 2 misses the bar, so the acceptance for #622 is not met and this
document does not declare it met.** The rest of this section is the honest
account of what the change did, because "the ratio went down" and "the code
got slower" are different claims and only the first one is true.

## The two runs

`ART` = `~/.claude/handovers/worker-reports/865-artifacts`. Back to back on a
quiet machine (every concurrent agent held for the pair, released by a flag
file the moment run 2 finished).

```
# run 1 — baseline, embedded-JPEG path   17:48:38 → 17:49:22 UTC (wall_s 43.811)
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --output "$ART/phase3_embedded.json"

# run 2 — forced full raw decode          17:49:41 → 17:53:23 UTC (wall_s 222.182)
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --force-full-decode \
    --compare-json "$ART/phase3_embedded.json" \
    --output "$ART/phase3_fulldecode.json"
```

| | run 1 (embedded) | run 2 (forced full) |
|---|---|---|
| clicks / ok / timeouts | 100 / 100 / 0 | 100 / 100 / 0 |
| placeholders / cold decodes | 0 / 100 | 0 / 100 |
| `decode_path_counts` | `embedded_jpeg` 89, `non_raw_source` 11 | `forced_full_decode` 89, `non_raw_source` 11 |
| `ttfp_ms` p50 / p95 / max | 379.005 / **564.750** / **803.414** | 1261.491 / 4754.820 / 5020.624 |
| steady-state RSS p50 / max | 156.834 / 169.955 MB | 142.217 / 151.089 MB |

The run-level `p95` and `max` are the first sign of what changed: 1000.828 →
564.750 and 1118.308 → 803.414 ms, while `p50` sat still (374.707 → 379.005).
Run 1's wall clock fell 56.715 → 43.811 s for the same 100 clicks.

## Box 1 — steady-state memory. **Still passes.**

`summary.box1.steady_state_rss_mb` over the last 50 of 100 clicks: p50
**156.834 MB**, mean 155.093, p95 165.999, max **169.955**, against
`threshold_mb` 600.0. `pass: true`, `pass_on_max: true`, `reason: null`,
`steady_window_used` 50 of 50, `placeholder_paints_in_window` 0,
`cold_decode_count` 100 against `min_cold_decodes` 3.

Essentially unchanged from the pre-#865 session (157.946 / 171.418), which is
the expected result: draft mode reduces the size of a transient decode buffer,
not what the LRU retains. Peak `lru_occupancy_bytes.preview_max` = 56,586,706 B
(~54.0 MiB) against the 192 MB preview tier, up from 39,565,490 B — the cache
holds output JPEGs at the cap, whose size is unchanged, so this is ordinary
between-run variation in which files were clicked, not a #865 effect.

```
Probe: scripts/preview_phase3_probe.py
SHA:   75a976d97a05fa33bd3807b009c6f9e66c44fbb3
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
JSON:  phase3_embedded.json
```

## Box 2 — **3.85×, down from 4.242×, and not because the code got slower**

`summary.box2`: `status: measured`, `ratio_median` **3.85**, `threshold` 5.0,
`pass: false`, `n_paths` **89**, `dropped_timeouts.total` **0**.

| Field | Pre-#865 (`a202282`) | Post-#865 (`75a976d`) |
|---|---|---|
| `embedded_ttfp_ms` min / p50 / mean / p95 / max | 256.823 / 381.124 / 547.530 / 1000.828 / 1118.308 | 282.570 / **380.887** / **402.331** / **521.997** / **623.486** |
| `full_decode_ttfp_ms` min / p50 / mean / p95 / max | 1128.113 / 1342.884 / 2455.722 / 4876.087 / 4943.267 | 1078.904 / **1291.335** / 2394.564 / 4754.820 / 5020.624 |
| `ratio_min` / `ratio_median` / `ratio_max` | 3.094 / **4.242** / 5.922 | 2.835 / **3.85** / **12.436** |
| `ratio_of_medians` | 3.523 | 3.390 |

Box 2 is a **two-arm** quantity and #865 touched only one arm. Reading the
headline ratio alone would attribute a control-arm movement to the code
change, so the arms were paired **by path across the two sessions** — the same
89 files, the same route, `a202282` against `75a976d`:

| Embedded arm, same 89 paths | p50 | mean | p90 | p95 | max |
|---|---|---|---|---|---|
| pre-#865 `a202282` | 381.1 | 547.5 | 981.4 | 1003.1 | 1118.3 |
| post-#865 `75a976d` | 380.9 | **402.3** | **516.9** | **546.9** | **623.5** |

Split at the pre-#865 p75 (895.8 ms), the change is not spread evenly — it is
entirely in the tail:

| Group | n | p50 pre → post | mean pre → post |
|---|---|---|---|
| slow tail (≥ p75) | 23 | 964.9 → **505.1 ms** (−459.8) | 972.9 → 497.1 |
| body (< p75) | 66 | 341.2 → 367.1 ms (+25.9) | 399.3 → 369.3 |

And the control arm, which #865 cannot touch (`--force-full-decode`
short-circuits `_try_rawpy_embedded_thumb` before `extract_thumb`,
`scripts/preview_phase3_probe.py:198-201`), moved on its own between the two
sessions: paired p50 **1342.9 → 1291.3 ms**, 51.5 ms faster with no code
change on that path.

So the ratio fell because its **denominator files** — the body, where draft
does nothing — sat against a control arm that happened to run ~4 % faster this
session, while the files draft does help moved out to `ratio_max` 12.436 (from
5.922). The embedded arm strictly improved on the arm's own terms: mean
−145.2 ms, p95 −456.2 ms, max −494.8 ms, faster on 46 of 89 files.

### Why most of the 89 files were unaffected — measured, not inferred

`draft` reduces only when the source is at least 2× the requested size in
**both** axes, and it never undershoots. A bounded read-only probe over 8 of
the 89 files (the 4 fastest and the 4 slowest of the pre-#865 embedded arm)
opened each DNG's embedded JPEG and read its header:

| Group | files sampled | embedded JPEG | MP | `draft("RGB", (2048, 2048))` → |
|---|---|---|---|---|
| small | IMG_1432, 1445, 1449, 1450 | **4032 × 3024** | 12.2 | 4032 × 3024 — **no reduction** |
| large | IMG_1230, 1235, 1277, 1278 | **8064 × 6048** | 48.8 | 4032 × 3024 — **halved** |

**How many of each — counted, not extrapolated.** The 8-file probe establishes
the two shapes; the split across all 89 comes from the artifacts, by **source
DNG size**, which is the cleanest available proxy (a 48 MP full-sensor ProRAW
file cannot be small) and needs no further NAS reads. `per_click[].size_bytes`
over the 89 paired paths splits with a 15.3 MB gap and nothing inside it:
**32 large** (47.5–124.8 MB) and **57 small** (10.7–32.2 MB).

Two independent signals in the same artifacts agree: `full_decode_ttfp_ms`
above 2500 ms selects **exactly the same 32 files, 0 disagreements**, and on
the `75a976d` artifact a per-path `ratio ≥ 5` selects 33 (one more), the
distribution there having a clean gap between 4.198 and 6.231. (That third
signal stops discriminating at `5fdee78`, where the undershoot lifts 79 of 89
past 5×, so it is quoted from the round-1 data only.)

**Do not read the 23 / 66 in the table above as this count.** Those are the
p75 *latency* quartile — by construction ~25 % of 89 — and a quartile cannot
count a group that is 36 % of the library. All 23 of those latency-tail files
are inside the 32, which is why the split looked plausible.

The library is bimodal, and the median file — the 45th of 89, comfortably
inside the 57 — is already at the floor: a 4032 × 3024 thumb against a 2048
cap is 1.97× the cap, so the next libjpeg step (1/2 → 2016 × 1512) would land
**below** the cap and lose output resolution. `draft` declines, correctly. The
48.8 MP frames are the full-sensor ProRAW preview #826 identified, and those
are exactly the ~460 ms each that #865 removed.

The saving matches an independent bench off the NAS: the same pipeline on a
synthetic 8064 × 6048 JPEG runs 661 ms → 180 ms median (5 iterations) with
byte-identical output geometry (2048 × 1536). Two instruments, one number.

### What this means for #622's box 2

The embedded path is now doing the least decode work the 2048 cap permits, on
every file in this library. **Box 2 cannot be lifted to 5× by decoding less**
— there is nothing left to remove for the 57 small-frame files, and the 32
large-frame ones already improved by ~48 %. The remaining TTFP on a small-frame
click is the SMB read of a 10.7–32.2 MB DNG plus a 12.2 MP decode that the cap
requires.

Levers that would move it, none of them built here and none of them inside
#865's scope:

* **Lower the viewport cap.** At 1024, a 4032 × 3024 thumb drafts to
  2016 × 1512 and the body files halve too. This changes preview fidelity —
  a product decision, and #622 explicitly put the cap out of scope.
* **Let `draft` undershoot by a small tolerance.** Accepting 2016 × 1512
  instead of 2048 × 1536 (1.6 % on the long edge) would halve the body decode.
  **Built after this session**, on the owner's call, as
  `_DRAFT_UNDERSHOOT = 0.02` — see the third re-measurement below for whether
  it moved box 2. It does change output geometry, by up to 2 % of the long
  edge, which is why it was raised here as an owner decision rather than
  folded into the first change.
* **Read less over SMB.** `rawpy.imread` pulls the whole DNG before
  `extract_thumb` can run. Untested, larger, and a different issue.
* **Re-state the bar** against the measured distribution.

Per the runbook's decision row for a measured fail, the report is the
distribution and not the one number: per-path ratios span **2.835×–12.436×**,
splitting cleanly along the same size boundary — the **32** large-frame files
at **6.414×–12.436×** and the **57** small-frame ones at **2.835×–6.231×**.
**The 5× bar is met by the part of the library that carries a 48 MP embedded
frame and missed by the part that carries a 12 MP one.**
Whether to re-state the bar, lower the cap, or leave box 2 open is the
owner's call; this document does not make it, and the 3.85 is not softened to
reach it.

```
Probe: scripts/preview_phase3_probe.py
SHA:   75a976d97a05fa33bd3807b009c6f9e66c44fbb3
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
       --force-full-decode --compare-json "$ART/phase3_embedded.json"
JSON:  phase3_fulldecode.json (summary.box2.compared_with names run 1)
```

## Boxes 3 and 4 — unchanged, both still pass

Opened on `IMG_0867.DNG` (`qimage_is_placeholder` false). Box 3:
`request_full_res_emitted` true, `zoom_scale_before` 1.0 → `zoom_scale_after`
1.25 with `zoom_pixmap_grew` true, `pan_wired` true, `pan_scroll_range` 2896.
Box 4: `label_valid_before_close` **true** → `label_valid_after_close`
**false**, `full_qimage_none_after_close` true, `dialog_valid_after_close`
false.

The two limits recorded in the first session still hold and are not re-argued
here: `pan_wired` asserts wiring rather than a driven drag, and
`modal.dialog_is_modal` is still false against #622's "modal viewer" wording.

```
Probe: scripts/preview_phase3_probe.py
SHA:   75a976d97a05fa33bd3807b009c6f9e66c44fbb3
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
JSON:  phase3_embedded.json (modal block + summary.box3 / summary.box4)
```

## Session notes

Same host and same deviations as the first session — `--ext .dng` passed to
both runs for the reason recorded above, `env.viewport_cap` 2048 with
`viewport_cap_pinned` true, `env.file_count` 445, `env.ext_histogram`
`{".dng": 445}`, `host.total_ram_bytes` 34,189,557,760, Python 3.12.9,
PySide6 6.11.0, Qt platform `offscreen`, budget `min(256 MB, RAM/32)` = 256 MB
split `{thumb: 67,108,864, preview: 201,326,592}`.

The same 11 `.DNG` files took `non_raw_source` in both runs again, the same
paths as the first session — deterministic, already explained there, and still
excluded from the box-2 pairing by construction (hence `n_paths` 89).

One thing this session did **not** do: re-run to fish for a better number.
Both runs completed on the first attempt with zero timeouts; the 3.85 is the
first and only reading taken at `75a976d`.

---

# 2026-09 second re-measurement, after the draft undershoot

Third owner-authorised session, **2026-09-07**, same runbook, same root, same
arguments, at commit `5fdee78` (`git_dirty: false` on both artifacts) — the
commit that adds `_DRAFT_UNDERSHOOT = 0.02`, the lever the previous section
raised and the owner chose. Artifacts at
`~/.claude/handovers/worker-reports/865-artifacts/r2_phase3_*.json`, **not
committed**. Rehearsal on `qa/sandbox` first, matching the runbook's healthy
shape (`clicks=20 ok=20 timeouts=0`, `{'non_raw_source': 20}`, boxes 1/3/4
`pass=True`, box 2 `not_measured`, `VERDICT: NOT_MEASURED`).

## Verdict — **`PASS`**. All four boxes.

`summary.verdict.verdict` = **`PASS`**, `verdict.failed` = `[]`,
`verdict.unmeasured` = `[]` (`r2_phase3_fulldecode.json`).

| Box | Bar | `a202282` pre-#865 | `75a976d` draft | `5fdee78` + undershoot | Verdict |
|---|---|---|---|---|---|
| 1 RSS p50 / max | < 600 MB | 157.946 / 171.418 | 156.834 / 169.955 | **157.340 / 167.309** | **pass** |
| 2 `ratio_median` | ≥ 5.0 | 4.242 | 3.850 | **6.958** | **pass** |
| 2 `ratio_of_medians` | — | 3.523 | 3.390 | **5.184** | — |
| 3 viewer pan/zoom | — | pass | pass | pass | **pass** |
| 4 QImage freed | — | pass | pass | pass | **pass** |

`placeholder_count` 0, `cold_decode_count` 100, `n_paths` **89**,
`dropped_timeouts.total` **0** (`embedded_arm` 0, `forced_arm` 0) — the pairing
lost nothing, so the ratio is a real reading of this library.

**#622's box-2 acceptance is met on this library and this host.** Both the
per-path median (6.958×) and the ratio of the two medians (5.184×) clear 5.0.

## The two runs

```
# run 1 — baseline, embedded-JPEG path   01:05:30 → 01:06:02 UTC (wall_s 31.763)
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --output "$ART/r2_phase3_embedded.json"

# run 2 — forced full raw decode          01:06:11 → 01:09:46 UTC (wall_s 215.533)
python.exe scripts/preview_phase3_probe.py \
    --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 \
    --ext .dng --force-full-decode \
    --compare-json "$ART/r2_phase3_embedded.json" \
    --output "$ART/r2_phase3_fulldecode.json"
```

Run 1's wall clock across the three sessions: 56.715 → 43.811 → **31.763 s**
for the same 100 clicks.

## Box 2 — **6.958×, and the median moved this time**

| Field | `a202282` | `75a976d` | `5fdee78` |
|---|---|---|---|
| `embedded_ttfp_ms` min / p50 / mean / p95 / max | 256.823 / 381.124 / 547.530 / 1000.828 / 1118.308 | 282.570 / 380.887 / 402.331 / 521.997 / 623.486 | **117.309 / 241.228 / 261.920 / 449.702 / 544.030** |
| `full_decode_ttfp_ms` p50 / mean / max | 1342.884 / 2455.722 / 4943.267 | 1291.335 / 2394.564 / 5020.624 | 1250.554 / 2321.339 / 4762.057 |
| `ratio_min` / `ratio_median` / `ratio_max` | 3.094 / 4.242 / 5.922 | 2.835 / 3.850 / 12.436 | **2.582 / 6.958 / 20.020** |

Paired **by path** across all three sessions (89 files common to all three,
embedded arm):

| Session | p50 | mean | p90 | p95 | max |
|---|---|---|---|---|---|
| `a202282` pre-#865 | 381.1 | 547.5 | 981.4 | 1003.1 | 1118.3 |
| `75a976d` draft | 380.9 | 402.3 | 516.9 | 546.9 | 623.5 |
| `5fdee78` + undershoot | **241.2** | **261.9** | **422.5** | **464.5** | **544.0** |

The first change took the tail (48 MP frames, half scale); this one takes the
**body** as well, which is why the median finally moves — 381.1 → 241.2 ms,
−36.7 % against pre-#865, with the mean down 52 % and p95 down 54 %.

### The control arm drifted, and it drifted *against* this result

The forced-full-decode arm is untouched by every commit in this arc
(`--force-full-decode` short-circuits `_try_rawpy_embedded_thumb` before
`extract_thumb`), yet its paired median has fallen each session: **1342.9 →
1291.3 → 1250.6 ms**, −6.9 % overall. That is NAS/host variation, not code.

It matters for reading the pass, and it cuts the safe way: a faster control arm
makes the ratio **smaller**, because it is the numerator. Recomputing
`ratio_of_medians` against the pre-#865 control median instead of this
session's gives 1342.9 / 241.2 = **5.57×** — higher, not lower. So the pass is
not an artefact of a lucky control run; the drift was working against it.

### What the undershoot cost, measured

The declared trade-off is an output long edge up to 2 % under the cap. From
`per_click[].image_w/h`, the long edge of the 89 embedded-arm paints:

| Session | Output long edge |
|---|---|
| `a202282` pre-#865 | 2048 px × 89 |
| `75a976d` draft | 2048 px × 89 |
| `5fdee78` + undershoot | **2016 px × 83**, 2048 px × 6 |

83 of 89 previews now render at 2016 px instead of 2048 — **32 px, 1.56 %**,
exactly the half-scale step the tolerance was sized to buy and comfortably
inside the declared 2 %. The remaining 6 are the files whose half-scale would
have fallen further under the cap than the tolerance allows, and they still
land exactly on 2048. No file fell below 2016, so the tolerance never
compounded.

Whether 32 px on the long edge is acceptable for dedup previews is a product
judgement, not a measurement — it is ~1.6 % of linear resolution, below one
logical pixel on this owner's 4K display at 175 %, and #622's own premise is
that this pane exists for near-duplicate discrimination rather than
pixel-peeping. The full-res viewer (`viewport_cap == 0`) never drafts and is
unaffected.

**`PREVIEW_RECIPE_VERSION` was deliberately left at `"1"`** rather than bumped
for this change, which means a preview cached before it (2048 px) can sit
beside a freshly decoded one (2016 px) for different files. That is a stated
decision, not an oversight: a bump wipes and rebuilds every cached preview, and
paying that for a ≤ 2 % long-edge difference that is inside the declared
tolerance is exactly the waste the bump exists to justify. Nothing served is
stale either way — the cache key still carries path, size and mtime, so an
entry is only ever the same file at a marginally larger edge.

```
Probe: scripts/preview_phase3_probe.py
SHA:   5fdee788af2a0386c63bcb5b7f46a8413f5ab391
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
       --force-full-decode --compare-json "$ART/r2_phase3_embedded.json"
JSON:  r2_phase3_fulldecode.json (summary.box2.compared_with names run 1)
```

## Box 1 — **still passes**, unchanged

`steady_state_rss_mb` over the last 50 of 100 clicks: p50 **157.340 MB**, mean
154.842, p95 164.221, max **167.309**, against `threshold_mb` 600.0.
`pass: true`, `pass_on_max: true`, `reason: null`, `steady_window_used` 50 of
50, `placeholder_paints_in_window` 0, `cold_decode_count` 100.

Flat across all three sessions (157.9 → 156.8 → 157.3 p50), which is the
expected result: draft mode shrinks a transient decode buffer, not what the LRU
retains. Peak `lru_occupancy_bytes.preview_max` 54,317,897 B (~51.8 MiB) of the
192 MB preview tier. Host unchanged: 34,189,557,760 B RAM, budget 256 MB split
`{thumb: 67,108,864, preview: 201,326,592}`.

```
Probe: scripts/preview_phase3_probe.py
SHA:   5fdee788af2a0386c63bcb5b7f46a8413f5ab391
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
JSON:  r2_phase3_embedded.json
```

## Boxes 3 and 4 — unchanged, both pass

Opened on `IMG_0867.DNG`, `qimage_is_placeholder` false.
`request_full_res_emitted` true, zoom 1.0 → 1.25 with `zoom_pixmap_grew` true,
`pan_wired` true, `pan_scroll_range` 2896; `label_valid_before_close` **true** →
`label_valid_after_close` **false**, `full_qimage_none_after_close` true,
`dialog_valid_after_close` false. The two standing limits (pan is asserted as
wired, not driven; the viewer is non-modal against #622's wording) are recorded
in the first session and unchanged.

```
Probe: scripts/preview_phase3_probe.py
SHA:   5fdee788af2a0386c63bcb5b7f46a8413f5ab391
Args:  --root "J:/圖片/20240601-0712大阪" --clicks 100 --viewport-cap 2048 --ext .dng
JSON:  r2_phase3_embedded.json (modal block + summary.box3 / summary.box4)
```

## Session notes

Same host, same root, same `--ext .dng` deviation and the same reason, same
`env.viewport_cap` 2048 with `viewport_cap_pinned` true, `env.file_count` 445,
`env.ext_histogram` `{".dng": 445}`. The same 11 `.DNG` files took
`non_raw_source` in both runs, as in both earlier sessions.

Neither run was repeated. 6.958 is the first and only reading taken at
`5fdee78`.

**What this does and does not settle.** Box 2 passes on *this library, this
host, this cap*. The ratio is sensitive to the mix — a library of 12 MP-embedded
files with no 48 MP frames would land lower, and a smaller-RAM machine gets a
different box-1 budget. The reading is what the runbook asks for and #622's
acceptance names; it is not a claim about every library.
