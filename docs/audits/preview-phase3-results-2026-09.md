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
