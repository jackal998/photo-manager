# features.md history archive (2026-10-14)

What this is: the history text removed from the entries of `docs/features.md` by PR-F1
of the docs source-of-truth program (tracker #958, owner decision D-3). It is a verbatim
archive, not current behaviour. Every removed line appears below unchanged, under the
entry it came from, in the original order.

Source: `docs/features.md` at commit `d7687b5` (base of the PR that adds this file).
Removed: whole `- **Last verified:**` bullets and `**History` bullets only. Mid-sentence
history ("Previously", "Before #NNN", "Originally", "retired desktop client") was left in place
for the F2 split and is not archived here.
Do not add to this file. Current history belongs in git, `news/` fragments and CHANGELOG.

## Removed history, by entry

### Bulk regex — remove from list (deferred decision)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Context menu — Lock / Unlock

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-22 (web-docs-drift backfill — added the missing web pointer)

### Context menu — Open Folder

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-16 (web audit remediation — `MAIN_EXECUTE_ERROR` footer surface for failed reveal)

### Context menu — Set Action (per-file)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Empty area context menu

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client; the previous text described a global tree menu that the app never had)

### Empty-state action buttons

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Execute Action — base flow

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-04 (web parity #742)

### Execute Action — complete-group delete confirm

  - **History.** Before #733 the web gate fired only when the whole scope was 100% Delete. A mixed manifest — one complete-delete group plus one group with keeps — then executed with **no blocking confirm** at all.
- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (web parity #733); 2026-09-19 (#917 confirm body)

### Execute Action — complete-group warning banner with jump-to

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Execute Action — dialog geometry persistence

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Execute Action — lock-confirm dialog

  - **History (#733).** Before #733 the web wrote decisions to locked rows **silently**. The only guard was the single-row dropdown's client-side `disabled`, which the context menus and shortcuts bypassed. The same change made bulk-decide's `force_locked` also **unlock** the forced rows (`batch_update_decisions_and_lock`). Before that, the decision landed but the row stayed locked, so the next Execute tripped the gate again.
- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (web parity #733); 2026-07-05 (web cancel-parity #721 + Set Action twin #764)

### Execute Action — preview pane

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Execute Action — partial execution via "Execute selected"

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Execute Action — filter by action type (#502)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-30 (#940 — Execute selected intersects the highlight with the visible rows); 2026-06-23 (web parity #676 — ExecuteDialog filter-scoped commit + HiddenDestructiveBanner; web s60 scenario added + live-verified)

### Execute Action — scope to selected groups (only-selected entry, #410, #430, #429)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Exit dirty-flag prompt

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Singleton-prune offer after destructive ops (#426)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-30 (#941 — execute skips singletons the review hides); 2026-06-24 (#686 — web port)

### Keep-worthiness scoring

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (#738 — the Live Photo MOV-passenger rule now recognises JPG/JPEG stills, not just HEIC/HEIF); 2026-09-05 (#680 — per-dimension signals added to the web manifest API, verified live against `GET /api/manifest` and by the web s42 driver)

### Language switch

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client; `web.*` key sets re-counted, en 362 / zh_TW 362, no asymmetry); 2026-09-14 (copy audit batch 1 — second coverage sweep, s22 extended with a bilingual copy probe); 2026-06-22 (web lang toggle functional, scenarios s22+s58 ported)

### List menu — Remove from List

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-29 (#909 undo toast — store/Toast/service unit tests; s20 branch D drives Undo, s21 reads the List-menu toast, both in CI `web-scenario-batch`); 2026-07-11 (web menu entry, #678-D)

### Log menu

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client; log locations and CSV contents code-read, not live-run)

### Main window — close-during-scan confirm

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client; backend cancel and reaping paths code-read)

### Main window — column order/width persistence

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Main window — geometry + splitter persistence

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Web — preview panel resizable + persistence

- **Last verified:** 2026-09-06 (#851 — off-window release ends the drag; the new vitest fails on the pre-fix build with the pane widening 388px → 614px after a `blur` + no-button move, and s39 fails its `blur` control there too); 2026-07-03 (#739 initial implementation); 2026-07-04 (#743 — full-res zoom clamp narrowed to 5%–800%)

### Web — overlay window geometry (movable / resizable / persisted)

- **Last verified:** 2026-09-06 (#852 — live-read `getComputedStyle(...).animationName` = `enter`, duration 0.2s on the dialog content and 0.15s on both backdrops, against `none`/`0s` on the pre-change build; restored-near-edge dialog settles at top 288 as it did before the animation); 2026-09-05 (#739 — move persists position only; scrollable dialog bodies + 320×280 floor; live-run via s39/s48)

### Main window — keyboard navigation

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-18 (layout slice T focus ring — live s74: cursor row `outline 2px solid rgb(168, 90, 44)`, offset `-2px`, caret `▸` displayed); 2026-09-06 (#709 web roving arrow cursor — live s26 branch D)

### Main window — results tree double-click

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Main window — row selection (no horizontal auto-scroll)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Main window — sort persistence within session

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Main window — status bar baseline

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-22 (web-docs-drift backfill — web footer states + `MAIN_STATUS_ERROR`/`MAIN_EXECUTE_ERROR` documented here, consolidated from the one-off mention under Context menu — Open Folder)

### Open Manifest — base flow

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-22 (web-docs-drift backfill — toolbar input/Open button + `FsBrowser` file-picker route documented); 2026-09-03 (#821 — skip-directory reconcile on open, measured on the author's 40,774-row manifest)

### Save Manifest Decisions — base flow

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Scan dialog — auto-select after scan

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Scan dialog — auto-select aggressive ("delete all others")

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-06-03 (#536 Ref-tier passenger exclusion; #517 confidence gate — backend eligibility rule, unchanged since)

### Scan dialog — stage / throughput / ETA progress (#424)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Scan dialog — collapse Advanced Settings

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-07 (#823 — pHash/dHash floor raised to 2; live web s17 PASS)

### Scan dialog — folder list (no priority arrows)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (#736 — web alphabetized source list; s17 extended)

### Scan dialog — exiftool workers (setting-only)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (#652 — web read-back)

### Scan dialog — read-knee autotune opt-out

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (#652 — web read-back)

### Scan dialog — hash pool mode

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-03 (#652 — web read-back)

### Scan dialog — hash workers (NAS-aware auto, no UI control)

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-03 (#822 EXIF Orientation before pHash/dHash + #826 RAW single LibRaw open and reduced preview — one shared `HASH_RECIPE_VERSION` bump `"3"` → `"4"`; A/B on 200 real NAS files: 105/105 Orientation-1 parity, 50/50 valid-orientation files now hash oriented, RAW compute 429 ms → 133 ms median)

### Scan dialog — multi-source scan

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Scan flow — manifest summary in progress log

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Scan walk — skip-on-error traversal

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-06-01 (#509 — walker traversal, unchanged since)

### Scan walk — recycle-bin / system folders are never walked

- **Last verified:** 2026-09-30 (#888 — checked against the shipped web client; no change); 2026-09-03 (#821 — `#recycle` added; previously undocumented, entry written with the change)

### Scan flow — rescan confirm

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-07-22 (web-docs-drift backfill — `RescanConfirmDialog` documented)

### Scan flow — visual selection of KEEP rows after scan

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Scan grouping — complementary RAW + lossy pair

- **Last verified:** 2026-09-04 (#824)

### Set Action dialog — dual-section Simple + Regex view

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Set Action dialog — live preview + validation

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Set Action dialog — numeric comparison panel

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Set Action dialog — Score / Lock / Resolution fields

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Set Action dialog — geometry persistence

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-05 (#739 — live-run via s48, per the overlay-geometry entry)

### Web — Action dialog (Set Action by Field)

- **Last verified:** 2026-06-22 (Phase 2 web port — s31 scenario added; backend `POST /api/action/bulk-decide` already shipped); 2026-06-23 (web lock-confirm parity #674 — `skip_locked` + 3rd verdict + s32, live-run PASS); 2026-07-03 (#735 — reachable from the file-row and group-row right-click, with column pre-fill; s15 extended); 2026-07-04 (#741 — Apply no longer disabled on an empty pattern, Recent-patterns dropdown, pattern-aware delete-confirm summary; s31 extended)

### Web — main result tree multi-selection

- **Last verified:** 2026-06-24 (PR-D1 — main-tree multi-selection + selection-aware context menu; #694 — result-tree "Remove from list" now finalizes `outcome='ignored'`, s20 restructured to assert the finalize + live-run PASS); 2026-07-03 (#735 — By-Field + Execute-selected added to the file-row menu, new reduced group-row menu; s15 extended).

### Web — Context menu: Apply best-copy decisions to this group

- **Last verified:** 2026-09-18 (layout slice G — s75 live-run on `qa/sandbox/near-duplicates/`: the header button left the locked `EXACT` duplicate at `user_decision=''` + `is_locked=True` while the other three went to `delete` and the keeper took `''` + lock; toast read `Group 1 · 3 files marked for deletion · 1 locked file unchanged · Undo` and 「群組 1 · 已標記 3 個檔案待刪除 · 1 個鎖定的檔案未變更 · 復原」; Undo restored the group byte-identically, the keeper's lock included. s72 green on the same build); 2026-07-11 (#744 — initial implementation, reintroducing the action PR #224 had removed)

### Web — Context menu: stays inside the window (#897)

- **Last verified:** 2026-09-30 (#897 — unit tests; s15 step (g) in CI `web-scenario-batch`).

### Web — Execute (only selected)

- **Last verified:** 2026-06-24 (PR-D2 — Execute (only selected) menu entry + group-pull scope; s44 live-run PASS; s54/s60/s64/s20 regressions PASS); 2026-07-03 (#735 — file-row + group-row context-menu parity: By-Field pre-fill, context-menu Execute-selected entry, reduced group-row menu; s15 extended).

### Web — ScanDialog source/output persistence

- **Last verified:** 2026-06-24 (cluster E — ScanDialog source persistence; s23a/s23b live-run PASS via batch; full 50-scenario batch PASS confirming per-scenario reset isolation); 2026-07-03 (#736 — added grouping-sensitivity threshold inputs + display-only alphabetized source list; s17 extended); 2026-09-07 (#823 — `min="2"` + clamp-to-2 on the wire + parity note; s17 live-run PASS).

### Web — scan dialog: subfolders by default, empty result stays open

- **Last verified:** 2026-09-30 (#896)

### Web — app shell never scrolls; the tree does (#894)

- **Last verified:** 2026-09-12 (#894 — live Playwright measurement before and after on the same fixture; s73 passes on the fixed build).

### Web — result-tree column sort and resize persistence

- **Last verified:** 2026-09-05 (#699 — `scrollMargin` from the measured header height; s47 live-run on the pre-fix build FAILS with a 25.00px row-coordinate offset at scrollTop 0, mid-scroll and back at top, and PASSES with 0.00px after; s01/s15/s25/s26/s40/s45/s47/s49/s68 batch 9/9 PASS. Earlier: 2026-06-24 #685 — result-tree column model; full 52-scenario batch PASS confirming the default-order isolation held across the 21 row-reading scenarios; 279 vitest).

### Web — sort by decision (Action header)

- **Last verified:** 2026-09-30 (#923 — vitest; s79 in CI's `web-scenario-batch`).

### Web — manifest decision persistence

- **Last verified:** 2026-06-28 (s12 web port — live-run PASS; decision round-trips through reload + reopen and is committed on disk).

### Web — scan dialog invalid source path

- **Last verified:** 2026-06-29 (s38 web port — live-run PASS).

### Web — scan progress throughput / ETA / localized stages (#740)

- **Last verified:** 2026-07-03 (#740 — initial web port of throughput/ETA/localized stages); 2026-07-22 (web-docs-drift backfill — Cancel button documented, previously only a one-clause mention under Main window — close-during-scan confirm)

### Web — scan SSE connection-drop watchdog (#661)

- **Last verified:** 2026-07-05 (#661 — named ping + client connection-drop watchdog).

### Web — scan progress, skips and errors in the app log

- **Last verified:** 2026-09-29 (#886)

### Web — desktop shell (pywebview launcher)

- **Last verified:** 2026-07-11 (#772 — `tests/test_launcher.py` + `tests/test_web_spa_mount.py` PASS locally; the packaged-exe path is proven by the release workflow's bundle checks + web smoke on the next test tag; the native WebView2 window remains a manual Windows smoke).

### Web — settings dialog

- **Last verified:** 2026-07-22 (web-docs-drift backfill — initial documentation of this surface; no prior features.md entry existed)

### Web — filesystem browse dialog

- **Last verified:** 2026-07-22 (web-docs-drift backfill — initial documentation of this surface; no prior features.md entry existed)

### Web — Daylight theme and results-tree framing

- **Last verified:** 2026-09-19 (layout slice TB, round-2 review — s76 live-run: bulk label across a filter `'Set 5 selected:' → 'Set 1 selected:'`; keep-best while filtered `disabled=True title='Clear the filter to use Keep best'` and `disabled=False` after clearing; at a 1000px viewport `manifest_input=0 open=0`, CTA after scroll `left 849 · right 991 · inner 1000 · hits True`, both controls back at 1280 — and the same reproduced under a FORCED wide fallback (the stack CI has, with no Segoe UI): 113px over, CTA after scroll `left 841 · right 1000 · inner 1000 · hits True`. s74 green on the same build); 2026-09-18 (layout slice TB — s76 live-run on `qa/sandbox/near-duplicates/` at 1280×800: toolbar computed `height 52px` on `rgb(250, 246, 238)` with `accents: ['main-scan-button']` (exactly one warm fill) and no horizontal overflow; status strip `height 30px`, `font-size 11.5px`, footer surface `rgb(243, 237, 227)`; menu-bar summary `5 files · 1 group · near-duplicates` at `white-space: nowrap` inside a 33px bar with exactly ONE chrome bar above the toolbar; filter `q95` left `['row-file-1-neardup_00_q95.jpg']` and a no-match query left 0 rows AND 0 group headers, with the manifest unchanged and a clear restoring all five; ctrl-selecting two rows gave `Set 2 selected:` and the bulk Delete verb wrote `delete` to both (`q88`, `q80`) and nothing else, toast `2 files set to Delete · Undo`, Undo restoring all five to `''`; status bar then read `⊗ 2 marked to delete` / `↺ reclaims 48.7 KB` and the CTA `⊗ Delete 2 files…`, which opened the Execute dialog; density Comfortable→Compact took the row `72px → 52px` AND the virtualiser's total size `410 → 308`, with a scroll to the last row still landing on it, and the selected segment computed `rgb(168, 90, 44)`; zh_TW read `⊗ 刪除 0 個檔案…` / `⊗ 0 個待刪除` / `篩選照片…` / `緊湊` with no English leaking. s74 extended with the same chrome probe (`stripFontSize 11.5px`, `menuHeight 33px`, `titlebarCount 1`, `summaryInsideMenu True`); s74 / s22 / s73 green on the same build); 2026-09-18 (layout slice G — s74 live-run: group header computed `height 44px` at BOTH densities, padding `12px` / `16px`, title `14px`/`700`, the derived total rendering `· 126.3 KB` and the folder suffix `· near-duplicates`, the Q5 button `28px` at radius `8px` on `rgb(255, 253, 249)`, and a measured caret-to-button gap of 791.6px against the 16px Q5 requires; s75 live-run for the button's behaviour); 2026-09-18 (layout slice R — s74 live-run at both densities. Comfortable: row `72px` with `align-items: center` and a `rgb(244, 238, 227)` rule, the group's last row `78px`, thumbnail `48×48` radius `8px` with a `1px rgb(231, 221, 205)` border, decision track `32px` on `rgb(243, 237, 227)` radius `9px` with the selected Delete segment `rgb(196, 80, 63)`, padlock hit box `28×28`, score track `6px` with a SOLID `rgb(184, 148, 106)` fill and `background-image: none`, `keep` label present, folder sub-line `direction: rtl`, Size cell in `"Cascadia Code", …`. Compact, re-hydrated from the `density` localStorage key: row `52px`, last row `56px`, thumbnail `36px`/`6px`, control `28px`, padlock `24px`, score cell `72px` with no label. s26 / s73 / s47 / s15 / s35 / s13 green on the same build — the two qa row helpers were re-aimed in the same PR: centring the cells put the decision control under the row box's centre point, which is where Playwright aims `click()`, so `click_row` now clicks the filename cell and `right_click_row` the thumbnail); 2026-09-18 (layout slice C — s74 live-run: header computed `height 28px`, `background-color rgb(250, 246, 238)`, `font-size 11px`, `text-transform uppercase`, `letter-spacing 0.66px`, bottom rule `rgb(228, 214, 189)`, 8px resize hit area, `Action` over the decision control and zero classification heads/cells; Size cell `text-align right` in `"Cascadia Code", …`; row separator `rgb(244, 238, 227)`; measured table width 986px at a 1280×800 viewport with the preview open — Resolution shed, Shot Date kept, File Name filling to 248px against a 160px stored width with a 17px gutter — and 606px at 900×800 with both shed and File Name at its floor; s47 drag `stored 160 → 280` / `rendered 250 → 374` / both restored after reload; a +200px drag on the Shot Date handle commits `date=202` with the column still rendered, unchanged across a reload. s45 / s15 green on the same build); 2026-09-18 (layout slice T — s74 live-run: `body` font-family `"Segoe UI", system-ui, -apple-system, "Noto Sans TC", sans-serif`, `font-mono` → `"Cascadia Code", …`, group strip + primary button + focus ring all `rgb(168, 90, 44)`); 2026-09-13 (#878 slice (e) — s74 live-run against a real server; computed preview-pane background `rgb(255, 253, 249)`, status-bar strip `rgb(243, 237, 227)`, row context menu `rgb(255, 253, 249)` with a hovered item at `rgb(247, 235, 218)`, Scan dialog `rgb(255, 253, 249)`). 2026-09-13 (#878 slice (b) — s74 live-run against a real server; active Delete chip computed `rgb(196, 80, 63)` on white ink, untouched row's None chip `rgb(231, 243, 236)` on `rgb(47, 138, 90)`, inactive segments `rgba(0, 0, 0, 0)`, padlock `rgb(168, 159, 143)` → `rgb(189, 107, 57)` with `aria-pressed` false → true, score fill 25.5 px in a 48 px cell drawn with the `scoreFill` gradient. Slice (a): computed app-root `rgb(245, 239, 230)`, group band + 4 px accent strip, delete wash + `line-through`)

### Similarity column

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client); 2026-09-14 (copy audit R6 — near-dup label `~dup` restored and translated); 2026-09-13 (#878 slice (a) — web 5-state badge, live-run s74); 2026-09-04 (#824 — complementary RAW+lossy pairs join the passenger class)

### Preview pane — byte-budget LRU cache

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client: request sizes, tier routing and the log-only cache-wipe notice read from code); 2026-07-16 (web audit remediation — source-mtime cache-key freshness, Correction #11); 2026-09-07 (#865 draft-mode embedded decode, re-measured on the J: NAS through the retired desktop preview stack per `docs/audits/preview-phase3-results-2026-09.md`); 2026-09-07 (#874 — the web lifespan passes settings to both media services; verified against a live server on port 8782 with both cache dirs pointed at temp directories: the thumbnail and the H.264 transcode landed there, and `%LOCALAPPDATA%/PhotoManager/{thumbs,transcodes}` gained 0 files)

### Preview pane — per-device request coordinator + 1-ahead prefetch

- **Last verified:** 2026-09-30 (#888 — rewritten as removed history; `git grep -i "device_key\|coordinator\|prefetch" -- app/web frontend/src infrastructure/image_service.py` finds nothing)

### Preview pane — full-resolution viewer

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Preview pane — no-autoplay video default

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client)

### Web preview pane — video transcode fallback (V2 H.264)

- **Last verified:** 2026-06-29 (V2 transcode fallback); 2026-07-03 (#737 — ffmpeg preset `fast`→`ultrafast` to cut the first-view transcode stall; FullResViewer now shows the same "Preparing video…" indicator as PreviewPane); 2026-09-05 (#787 — HEVC capability pre-check chooses the initial `src`; measured in headless Chromium: `hvc1`/`hev1` report `supported: false`, VP9 `supported: true`); 2026-09-06 (#853 — ffprobe codec passthrough; exercised through the real server on port 8772: an H.264+AAC `.mov` returned 200 `video/mp4` whose video stream MD5 matched the source's exactly, an HEVC `.mov` still came back re-encoded to `h264`, and an H.264 `.avi` came back as a real MP4; a 10-bit H.264 High 10 `.mov` took the ENCODE path and came back `h264` / `yuv420p`); 2026-09-07 (#854 — release bundles a pinned LGPL **shared** ffmpeg in `_internal\ffmpeg\`, verified through a real local PyInstaller build: SHA-256 matches the publisher's `checksums.sha256`; the built bundle carries the 10-file set once (no loose duplicates) and `ffmpeg -version` / `-encoders` / a throwaway `libopenh264` encode all pass from `dist\`; the frozen exe's own web smoke resolved `dist\photo-manager\_internal\ffmpeg\ffmpeg.exe` with a chocolatey ffmpeg still on PATH and logged `H.264 encoder for transcodes: libopenh264`; `GET /api/media?path=…&transcode=h264` against that bundle returned 200 `video/mp4`, 496 491 bytes in 0.49 s, probing as `h264` / `yuv420p` 1920x1080 + `aac`, from a 2 s 1080p HEVC source; the dev-checkout path still resolves the PATH ffmpeg and selects `libx264`. Sizes measured on that build: the staged set is 127.9 MiB (the static variant was 218.0 MiB), which is exactly the `dist\` delta between two builds differing only in the staging dir (292.2 → 420.1 MiB); the finished bundle is 426.4 MiB and its release zip 172.4 MiB, of which 53.9 MiB is ffmpeg — the same zip without `_internal\ffmpeg\` is 118.5 MiB); 2026-09-07 (#862 — first-audio-only verdict + shared ffprobe cap, exercised through the real server on port 8779: a `.mov` carrying H.264/`yuv420p` + AAC **+ a second PCM track** logged `Remuxed (no re-encode) dual_audio.mov` and came back 200 `video/mp4` in 0.15 s with exactly ONE audio stream (`aac`) — before #862 the same file logged `Transcoded`; an HEVC control still took the encode. For the cap, 6 concurrent cold requests for 6 distinct H.264 clips were served through a deliberately slow `ffprobe` shim (`ping -n 3`, then the real binary with the same argv) while a `tasklist` sampler counted live probes: peak 2, and the responses landed in three pairs at 2.22 s / 4.43 s / 6.67 s — the same six shim invocations fired with no semaphore in the path peak at 6 and finish together in 2.16 s); 2026-09-30 (#869 — native-decode notice; vitest drives the real #787 probe through a fake `decodingInfo` for all three verdicts, and s70 forces `supported` / `unsupported` + `platform: "Windows"` in headless Chromium through an init script. Not observed on a real Windows machine WITHOUT the extension — the owner's machine reports `supported`, so the `unsupported` path is proven only through the forced state)

### Web preview pane — video playback (V1 streaming)

- **Last verified:** 2026-09-30 (#888 — desktop-client references removed); 2026-06-29 (V1 streaming slice)

### Web preview pane — group grid view (multi-tile)

- **Last verified:** 2026-09-30 (#888 — desktop-client references removed; ResultTree path corrected); 2026-07-03 (#734 — video tiles now show a real Shell/WIC poster frame behind the ▶ glyph, with dark fallback on load error; s71 web scenario extended to assert poster resolution pre-click)

### Web preview pane — title, image frame, metadata table, decision block

- **Last verified:** 2026-09-19 (slice PV — s77 green locally, screenshot at 1280×800 with a row selected)

### Web preview pane — group sync controller (GroupMediaController)

- **Last verified:** 2026-09-30 (#888 — desktop-client references removed; mute default corrected to the 0..1 value); s71 web scenario (grid-multi-tile-video feature)

### Developer tool — memory probe

- **Last verified:** 2026-09-30 (#888 — rewritten against the shipped web client: no production call site, `git grep memory_probe` hits only `scripts/` and `tests/`)
