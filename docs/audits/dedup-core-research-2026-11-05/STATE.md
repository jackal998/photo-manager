# Dedup-core follow-through — single source of truth (STATE)

Maintained by the working session; any session or agent can resume from this file alone.
Last update: **2026-11-05 23:15** (local). Tracked in git since PR (docs/dedup-state-and-auto-select-prd).
NEVER write personal paths, hostnames or library folder names into this file — the `*-output.txt` probe outputs that name library folders stay local for that reason.
The research report lives on branch `docs/dedup-core-research`; the session's project memory
(`memory/project_dedup_core_research_2026_11_05.md`) points here.

## 1. Goal

Re-measure the owner's real library under the new dedup recipe (hash recipe "5", grouping "5") and
compare against the June manifest, to see what the four research PRs changed:
pHash contrast gate (#1032), shot-time / screenshot demotion (#1030), persisted `match_confidence`
(#1031), append-only `decision_log` (#1034). Report: `../dedup-core-research-2026-11-05.md` (§6 item 8
is this measurement).

## 2. What is DONE (all on master)

| PR | SHA | What |
|---|---|---|
| #1030 #1031 #1032 #1034 | e1e2229 91bb5e1 76fef86 0328b5a | the research stack, merged 2026-11-05 06:00Z |
| #1035 | d7da28a | exif consumer rotates a wedged exiftool, retries once, records exif-missing, never deadlocks; cancel never spawns a replacement |
| #1037 | ef2b250 | exiftool `-execute` in sub-batches with a read budget (first attempt: 25 files, 60 s + 4 s/file — too small) |
| #1038 | ed3665d | budget by content units: image 1 / video 4, ≤25 units per call, 60 s + 12 s/unit; timeout message carries `(N paths, T s since -execute)` |
| issue #1036 | — | follow-up: Cancel cannot interrupt an in-flight multi-GB hash read |

Root causes found tonight (keep for the retro): (a) the harness's low-memory reaper killed the app when
launched as a session background task → the OWNER launches `.\run.bat` from their own terminal, always;
(b) `ExiftoolTimeout` was caught by nobody → consumer died → bounded `exif_queue` filled →
`_route_outcome` spun forever (HASH frozen at 3,500 while workers kept reading); (c) the 60 s "idle"
timer is really a per-`-execute` budget because Perl block-buffers stdout (~8 KB) → every slow NAS batch
was a false "wedge" (measured ~8.6 s/HEIC, ~27 s/multi-GB MOV under scan load).

## 3. Current run (attempt 5)

- Started **2026-11-05 22:58:24**, app launched by the owner, master ed3665d.
- Sources: a local-disk Takeout export, the NAS share's mobile-backup folder, the NAS photo library
  (recursive) = 39,985 files; process pool, local disk=1 worker, NAS=8 workers; 2 exiftool consumers.
- Output: `migration_manifest_v5.sqlite` (written only at the end).
  The June baseline `migration_manifest.sqlite` must NOT be overwritten.
- 23:14:10 **first wedge of this run**: `exiftool stdout idle for 360.0s (16 paths, 360.0s since
  -execute)` — 16 paths = 25 units (≈13 images + 3 videos) silent for the whole budget while the NAS link
  was saturated by the 8 hash workers (NIC rx 11.7 MB/s, exiftool bandwidth-starved). Retry pending at
  23:17. Decision: a fourth fix is in flight — one path per `-execute` (so `{ready}` flushes per file and
  the idle timer is honest) with a 300 s base + 12 s/unit budget; branch `fix/exiftool-per-file-execute`
  (haiku worker), then opus review → PR → merge → rescan. Cancel this run once the owner confirms.
- 23:19:45 second consumer wedged (10 paths / 22 units, 324 s); both retries failed 23:21; owner
  **cancelled 23:21:24** and closed the app. Attempt 5 is over.
- **Windows Defender finding (23:23, measured, owner was right):** `MsMpEng.exe` at 50 % CPU with 36.5 GB
  cumulative reads (more than all hash workers together); `RealTimeProtectionEnabled=True`,
  `DisableScanningNetworkFiles=False`, Tamper Protection ON (an earlier "turned off" was reverted).
  Defender re-reads every NAS file the workers read → ~2× SMB traffic, exiftool starved harder. Task
  Manager's Network column attributing the traffic to Defender is correct. **Owner's ruling: NO Defender
  exclusions, ever** (excluding `python.exe` is a hole for every Python program; a tool for other people
  cannot ask them to weaken AV). Product stance: the scanner must stay correct when AV doubles reads —
  slow is fine, false timeouts are not. Hence the fourth fix below; AV is just a throughput factor.
- Fourth fix: branch `fix/exiftool-per-file-execute`, commit dcb6e28 — one path per `-execute`
  (`{ready}` flushes per file → the idle timer is per file), budget 300 s + 12 s/unit; packing removed;
  254 tests pass; opus review 23:33: code APPROVE, one test-only finding (the slow-file fake waited one
  delay per call, so the regression passed on master) fixed in 21123c1 (verified: master now fails with
  ExiftoolTimeout, HEAD passes). **PR #1039** opened 23:36 (dcb6e28 + 21123c1 + news 2d45371), CI running.
  **#1039 MERGED 23:45 as a9a65af**; local master fast-forwarded (`_EXIFTOOL_READ_TIMEOUT_SECONDS = 300.0`
  present). Watcher re-armed. Owner to run `.\run.bat` → overnight scan (attempt 6, 8–9 h expected).
  One CI hiccup worth knowing: docs_impact_guard requires EVERY touched `docs/features/*` entry heading in
  the `Docs impact:` line — a one-word wording fix in `main-window.md` made `gates` red until the line
  also named `Main window — close-during-scan confirm` (body edit re-triggers the gate).
  Note: other sessions' `git status` polling leaves transient / stale `index.lock` files under
  `.git/worktrees/<agent>/`; a 0-byte lock older than a minute with no long-lived git.exe is stale — move it
  aside (`mv index.lock index.lock.stale-HHMM`), never delete blindly.
- Real ETA, from June byte totals: NAS = 135 GB + 123 GB (two NAS shares) ≈ 258 GB at the link's ~11.7 MB/s ≈ 6 h of
  hashing (the local disk, 169 GB, is fast) → HASH ≈ 6–7 h, then the EXIF tail for 7,684 exiftool-routed NAS
  files (HEIC/DNG/MOV, 230 GB) once hashing stops starving exiftool. Whole scan ≈ 8–9 h. The earlier
  "02:00" estimate was wrong (it extrapolated the D:-heavy first minutes).
- `Hashed N` can stall for minutes with no error: that is backpressure (bounded `exif_queue`, maxsize
  2000, HEIC-heavy segments fill it because exiftool is 10–15× slower than hashing during HASH). Not a bug.
- EXIF stage progress is only on the UI stage bar / SSE, NOT in the log — known gap.
- Previous attempts: 14:38 (reaped), 16:06 (deadlock, cancelled 18:21), 20:12 (false wedges, cancelled
  20:28), 22:18 (160 s budget too small, cancelled ~22:33).

### Attempt 6 (2026-11-06, master a9a65af) — RUNNING, owner launched ~00:07
- 00:20:37 Hashed 3,500/39,985 (log `app_20261106.log`), then the counter stalled (backpressure).
- **00:31:02 one wedge:** `exiftool stdout idle for 312.0s (1 paths, 312.0s since -execute)` — a SINGLE
  image file silent for the full per-file budget. Retry on a fresh process pending at 00:32. If the retry
  also fails, that 500-file batch is recorded exif-missing (pre-existing whole-batch cost). Open
  question for the morning: which file? (the timeout message does not carry the path — add it; and the
  "resume from the failed path instead of re-running 500" follow-up from the #1037/#1039 reviews).
  Decide from the log how many `retry on a fresh process also failed` lines accumulated overnight: a
  handful = analyse anyway and note the gap; many = the per-file budget is still not the right model
  under Defender + 8-worker starvation (consider pausing exiftool while HASH runs, i.e. EXIF strictly
  after HASH, or lowering NAS hash workers during EXIF).
- 00:59:43 the retry ALSO failed on that one file (312 s again, after re-running the rest of the batch
  for ~28 min) → that 500-file batch is exif-missing. Same file twice = content-specific (a file exiftool
  genuinely chokes on, or a huge DNG under starvation), not a transient. 00:59:31 Hashed 4,000 — HASH is
  now exiftool-bound through the HEIC-heavy `H:` segment (backpressure), expect several hours there.
- Session usage limit hit at 00:33; the watcher expires ~00:51 and will not be re-armed until the
  session resumes. The scan itself is unaffected (it runs in the owner's own app process).

### Attempt 6 — DONE 2026-11-06 01:56 (scan 00:07→01:56, ≈1 h 50 m, not 8–9 h: the byte-based ETA was
far too pessimistic — the NAS link ran well above 11.7 MB/s once Defender/hash contention eased)
- Log summary: indexed 39,888 (97 unreadable); EXIF done 39,888 files, 34,691 dates, 14,881 GPS;
  exact 11,917 / review 2,117 / undated 2,466; groups 7,953 (24,804 files), isolated 15,084.
- Exactly ONE exiftool wedge all night (00:31, single file, retry also 312 s → one 500-file batch
  exif-missing); net date loss vs June = **39 files** (23 MOV, 13 HEIC, 3 MP4). Acceptable; noted.
- Output: `migration_manifest_v5.sqlite` (20.9 MB). Analysis outputs saved next to this file:
  `main-v5-measure-output.txt`, `main-v5-followup-output.txt`, `main-v5-followup2-output.txt`.

### Results (June `migration_manifest.sqlite` vs v5) — headline numbers
| metric | June | v5 |
|---|---|---|
| rows | 40,774 | 39,888 (the NAS recycle bin's 3,952 rows gone; 556 June deletes gone) |
| groups / grouped rows | 8,365 / 24,531 | 7,953 / 24,804 |
| pHash-0-only groups / near-dup groups | 7,228 / 1,137 | 7,161 / **792** |
| EXACT / REVIEW / UNDATED | 13,870 / 2,296 / 2,479 | 11,917 / 2,117 / 2,466 |
| match_confidence | — | high 12,777 / **low 1,257** (REVIEW: 860 high, 1,257 low) |
| phash_quality | — | p1 13.7, p50 51.2; **564 files (1.6 %) gated at Q<16** (331 jpg, 100 png, 65 heic, 43 jpeg, 25 dng); 207 of them were in a June group |
| class A (eclipse/lights, Q 5.67 / 5.63 / 15.36) | one EXACT/high group | **fixed**: no cross-file group; only sha256-identical copies pair up |
| class B (torii DNGs, Q≈52.7) | one group | still ONE group of 15 (4 moments 1391/1393/1395/1397, 8 sha256, 2 pHash); cross-moment rows now **REVIEW/low**, byte copies EXACT/high — grouping unchanged by design (T2 split is the report's next tier) |
| class C (PNG-only groups) | 619 (92 near-dup) | 568 (46 near-dup); near-dup PNG groups are all `low`; 188 pHash-0 PNG groups still `high` = all single-sha256 byte copies (correct) |
| decision_log | absent | table + 2 append-only triggers present, 0 rows (fresh scan) |

**Finding to investigate (not caused by the scan): 1,555 JPGs under `<NAS library>\20181212_<undated USB-stick JPEG series>\…`
were EXACT in June and are UNDATED now.** They are byte-identical copies (1,535 share a sha256 with ≥2
rows, 1,538 are grouped), had no shot_date in EITHER run, and June's reason was "exact duplicate of
$R….jpg" — their June partner was a recycle-bin file. New reason: "no EXIF DateTimeOriginal". Either
the UNDATED rule now outranks the sha256-identical rule, or June's EXACT came through the recycle-bin
partner. Byte-identical copies should be EXACT/T0 regardless of date — check `scanner/dedup.py`
classify precedence before the next PR.

Other observations: low-confidence reasons are dominated by "same pHash as X but shot N h apart" (time
gate) and near-dup hamming=10 with disagreeing time — exactly the classes the gates were built for.
The 556 June deletes are all gone from disk (expected).

### Network finding (2026-11-06 morning, owner's observation + measurement)
- Owner: during HASH, Task Manager showed Defender holding the network and the scan "looked stopped";
  this last run they did not intervene except **turning Wi-Fi on**, after which it flowed better.
- Measured: the wired NIC (Realtek PCIe **2.5GbE**) is negotiated at **100 Mbps** → the ~11.7 MB/s
  ceiling seen in every probe is the link, not the NAS. Wi-Fi 6 AX201 is up at 1.9 Gbps and SMB
  multichannel is enabled → turning Wi-Fi on gave SMB a second, ~10× faster path; that is why attempt 6
  finished in 1 h 50 m instead of the 6+ h the 100 Mbps arithmetic predicted. Defender's real-time
  re-read of every network file halves whatever the link gives, so on 100 Mbps it looks like a stall.
- Owner-side lever (not the product's): the Ethernet cable / switch port / NIC driver is negotiating
  100 Mbps — a 2.5GbE NIC on a gigabit switch should show 1 Gbps. Check `Get-NetAdapter` LinkSpeed
  after swapping the cable/port. Product-side idea (later): warn when a NAS source's sustained read
  rate is ≈ 11 MB/s on a NIC that reports 100 Mbps.

### Next (core-task items chosen from the measurement, 2026-11-06)
1. ~~Byte-identical copies must be EXACT regardless of date~~ — **RESOLVED, not a bug.** `classify()`
   Pass 1 marks every sha256 duplicate EXACT and leaves the KEEPER unlabelled; Pass 3 then labels an
   undated keeper UNDATED. In June the keeper of each of these groups was the recycle-bin copy (in the NAS library's recycle bin, `…`)
   (`$` sorts before `2`, so it won lex-min), which made the real files "EXACT duplicates of a
   recycle-bin file" — the broken state. #821 (after June) made the walker skip the recycle bin /
   `#recycle`, so now the real file is the keeper and, being undated, shows as UNDATED with its
   siblings EXACT. The 1,555 rows are keepers, one per group. Nothing to fix; the June numbers for
   this folder were the wrong ones.
2. **Low-confidence edges are hints, never chains** (report §5.1 T3) — IN REVIEW 2026-11-06, branch
   `feat/hint-edges-never-merge` (adfa3f8 + aa27cf3, worktree agent-a5a393120f79b7e59). Rule: high edges
   union as before; a low edge may pair two lone files or attach one lone file to a HIGH-CORED group;
   it never merges two groups and never extends a low-only pair. A row whose hint was skipped and that
   ends up isolated has action reset to ""/UNDATED, keeps `match_confidence=low`, reason prefixed
   `hint only (not grouped): …`. `GROUPING_STRATEGY_VERSION` "6". **Design trade-off taken (owner may
   veto before merge):** this changes #538's "transitive closure over every pHash link" — three #538
   tests rewritten to "no SILENT orphan" (hint survives in reason). CORRECTION (review-i): an undated
   pHash match with dHash present carries NO demotion note and stays HIGH — only time-DISAGREE,
   screenshot pairs and dHash-unavailable matches are low — so undated series (USB-stick JPEGs) keep
   chaining as before; the three #538 fixtures broke only because they had no dHash. Opus review round
   1: NEEDS-FIX — H1 hints could still chain through a leaf (rule looked at the component's core, not
   the node; fix = attach only to a node that itself has a high edge), M1 #824 complementary edges
   ignored the time vote (torii at Hamming 0 would still merge), docs newline/wording, function length.
   Round 2 (ac6b8aa): H1/M2/M3/L1–L3 fixed (attach only to a node that itself has a high edge; six
   screenshots + one byte copy → [2,2,3]); NEW regression N1 — the #824 complementary star anchored on
   the bucket's lex-min file dropped ALL peers when the anchor disagreed in time (x.cr2 + its DNG
   conversion orphaned). Round 3 (70390f3): N1 FIXED — cluster each exact-pHash bucket by time agreement, HIGH
   edge within a cluster, LOW edge between cluster anchors; gate parity with master on 346 random buckets, deterministic over 41 orders. NEW F1: an UNDATED member sorting first became the anchor and re-merged every moment HIGH ([6] vs [2,2,2] by folder name). Round 4 (7ae605e): members visited by (undated, path) so undated files attach but never anchor (z bucket → [2,2,2] regardless of folder name); taken low leaves now carry low confidence + "hint: low-confidence match with <partner>". 651 tests pass; round-4 review APPROVE. **PR #1040 opened** (adfa3f8 → df4de44 incl. news fragment), CI green; **MERGED 2026-11-06 as 974228a** (owner's yes); local master fast-forwarded. Attempt 7 prepared: `settings.json` output → `migration_manifest_v6.sqlite` (keep v5 for the v5→v6 comparison), watcher re-armed on v6. Owner to run `.\run.bat` → Start Scan; then run `main-v5-measure.py migration_manifest_v5.sqlite migration_manifest_v6.sqlite` plus a torii/PNG check. Offline validation on the v5 manifest is NOT faithful (manifest lacks dhash/mean colour/
   dimensions) — the real-library effect needs a rescan (~1 h 50 m with Wi-Fi on).

## 4. How to monitor / resume

- App log: `%LOCALAPPDATA%\PhotoManager\logs\app_YYYYMMDD.log` — progress lines `Hashed N/39,985`;
  `exiftool wedged` = rotation happened; `retry on a fresh process also failed` = 500 files lost EXIF;
  `Scan cancelled by user`; manifest write at the end.
- Health: `GET http://127.0.0.1:8765/api/health` → 200 while the app runs.
- Watcher script (session scratchpad, may be gone in a new session — trivial to rewrite):
  `lead-scan-watch.sh` (in the session scratchpad)
  (polls health / free RAM / log / output file every 60 s).
- Do NOT start the app from a Claude background task (reaper). Do NOT `git stash` (shared across worktrees).

## 5. Next steps (in order)

1. When `migration_manifest_v5.sqlite` exists and the UI says done: run
   `python docs/audits/dedup-core-research-2026-11-05/main-v5-measure.py migration_manifest.sqlite migration_manifest_v5.sqlite`
   (copies of the measure / watch / exiftool-timing scripts sit next to this file as `main-v5-measure.py`, `main-scan-watch.sh`, `main-exiftool-timing.py`; the measure script covers: §1 phash_quality distribution + per-ext,
   §2 files with Q<16 and whether they were grouped in June, §3 groups/action/confidence before vs after,
   §4 action transitions joined on sha256 + where the 556 June deletes landed, §5 class A/B/C fixtures —
   eclipse `MVIMG_20200621_160439/160730.jpg` + `IMG_8511.JPG` (old pHash `cccc3333cccc3333`), torii
   `IMG_1391/1393/1395.DNG`, PNG-only near-dup groups — §6 decision_log table/triggers/rows).
   If the script is gone, rewrite from that list; `phash_quality` and `match_confidence` are columns of
   `migration_manifest` in the v5 file.
2. Count recycle-bin rows in v5 (the June scan walked the NAS library's recycle bin); decide on an exclusion.
3. Append §8 of the research report with the before/after numbers (report lives on local branch
   `docs/dedup-core-research` in worktree `.claude/worktrees/docs-dedup-research`; its push was denied
   by the classifier — the owner pushes or keeps it local).
4. Owner decisions outstanding: push the docs branch; pip-audit bump PR (black 25.1.0 / pylint 3.3.8,
   dev-only CVEs); T3 hint-only, decision-log UI, S-A/S-B from the report.
5. Small follow-ups worth filing: EXIF-stage progress line in the app log; keep completed sub-batch
   records on a mid-batch timeout (reviewer note on #1037); `]` in trailing stderr can mis-slice the
   JSON (`_parse_exiftool_json`), unverified live.

## 6. Working agreements observed this session

- Owner approves merges with an explicit "yes" per PR (given for #1035, #1037, #1038); CI must be green
  and a fresh opus review APPROVE first; haiku implements in a worktree, opus reviews, lead opens the PR
  with `[qa-not-needed: …]`, `Docs impact: Scan dialog — exiftool workers (setting-only)`, and a
  `news/<PR>.bugfix` fragment.
- Bash rule in this harness: `< /dev/null` on the FIRST command of a pipeline; no heredoc to interpreters.

### Attempt 7 (2026-11-06, master 974228a, grouping 6) — RUNNING
- Scan started 03:57 (owner launched); zero exiftool wedges so far; Hashed 38,000 at 05:12; Classifying from 05:22; manifest write expected ~05:35 to `migration_manifest_v6.sqlite`.
- Session usage limit hit 05:23. On resume: run `main-v5-measure.py migration_manifest_v5.sqlite migration_manifest_v6.sqlite` (the script compares any two manifests) + the follow-up scripts; check torii group count (expect 4 groups), PNG-only near-dup groups (expect pairs/hints), hint rows (reason contains 'hint'), total groups; update §Results and memory.
- **DONE 05:34** (03:57→05:34, 1 h 37 m, zero exiftool wedges). Output `migration_manifest_v6.sqlite`.

### Results v5 (grouping 5) → v6 (grouping 6, #1040) — outputs `main-v6-*-output.txt` beside this file
| metric | v5 | v6 |
|---|---|---|
| groups / grouped rows | 7,953 / 24,804 | 8,337 / 24,690 (more, smaller) |
| group sizes 2 / 3 / 4 / 10+ | 4,125 / 651 / 2,654 / 56 | 4,615 / 657 / 2,611 / 35 |
| largest groups | 286, 38, 30, 29, 27 | **30, 26, 21, 19, 18** — the 286-row `20170111_101` JPEG chain (271 REVIEW/low) is now 120 groups (6,5,4,2,2…) + 37 isolated hints; the 38-row HEIC chain → 16 groups |
| EXACT / REVIEW / UNDATED | 11,917 / 2,117 / 2,466 | 11,917 / 2,016 / 2,233 |
| match_confidence low | 1,257 | 1,774 (REVIEW low 1,162; 497 grouped hint leaves; 108 isolated `hint only (not grouped)` rows, all action "") |
| torii (IMG_1391/1393/1395/1397) | ONE group of 15 | **four groups (4 / 3 / 4 / …)**, one per moment; byte copies EXACT/high, the H: copy REVIEW/low leaf |
| PNG-only groups / near-dup | 568 / 46 | 651 / 63 (smaller: 39 of the 63 are a single low pair) |
| groups whose HIGH rows disagree in shot time (> 2 s, not a tz offset) | 351 | **242** (−31 %); the rest are bursts/timelapses chained by agreeing ≤2 s edges (GoPro series, 30 rows) — same-moment, T2 territory, not "unrelated" |
| groups whose ANY rows disagree | 557 | 649 — expected: hint leaves (low) are allowed to disagree and are now counted as groups of their own |

Reading: the owner's criterion (unrelated photos must not share a group) moved in the right direction on every targeted shape — the mega-chains dissolved, torii split by moment, screenshot chains became pairs/hints — without touching EXACT (11,917 unchanged). Residue = same-moment bursts, which the report assigns to T2 (stack with cover, keep-all); next lever if wanted.

## 7. Round 3 — owner instructions 2026-11-06 (after the v5→v6 report)

Owner's decisions, verbatim intent:
0. **"Strictly on-device" is lifted, as an OPT-IN.** Media may leave the machine (1024 px thumbnails,
   EXIF stripped) only when the user has switched it on under the Scan page settings after being told
   plainly that media leaves the machine; default OFF; without the switch the feature is not used at
   all (policy: privacy/safety). Build the ENTRY (switch + disclosure text, setting-only) before the
   real backend. Not urgent; it is an acceptance criterion of auto-select.
1. Start now, in parallel: (a) T2 burst stack layer; (b) research report §9/§10 on the docs branch;
   (c) the three small scan improvements (timeout message names the file; resume from the failed
   file instead of re-running the whole batch; EXIF-stage progress in the app log). The 100 Mbps NIC
   is handled by the owner in another session — NOT ours.
2. When (a)–(c) have PRs opened and reviewed, and budget remains: write an auto-select feasibility
   analysis and save it as a PRD (start from `docs/audits/visual-autoselect-feasibility.md` on
   `docs/web-port-feasibility` @ b14b59f and this study's §5; shape = metadata rule layer + VLM judge
   opt-in; pixel ranker stays DO-NOT-BUILD; acceptance includes the opt-in switch of item 0).
3. Keep this file current.

Plan (same harness rules as §6): haiku implements in a worktree, opus reviews, lead opens the PR, the
owner says "yes" per merge.
| stream | branch / place | status |
|---|---|---|
| (a) T2 burst stack | design `main-t2-burst-design.md` (opus planner) + probe `main-t2-probe.py` → `main-t2-probe-output.txt`; haiku implementing on `feat/burst-stack` | **PR #1042 MERGED 2026-11-06 as 55f4948** (owner's yes; CI 11/11 green; local master fast-forwarded; e006759 + master merge fab642a + news 495fcca); opus review APPROVE (7/11 tests fail with the pass disabled, 200-shuffle determinism identical; the full-suite stop at the #1035 rotation test was judged independent of dedup — re-run after merging #1041: full unit suite exit 0, zero failures — the earlier stop was a load flake, two full suites were running at once); CI running; needs the owner's "yes". **Probe correction:** of the "242 high-disagree groups", 213 disagree only through a Live-Photo MOV/MP4 peer (UTC QuickTime date, no pHash) and 203 have their photos within 2 s — so the real burst residue is 11–15 groups (GoPro 30, CIMG 21/16, GoPro 14, DNG trio), 74 REVIEW + 9 format-dup EXACT rows → keep-all; the crossmoment metric must ignore video rows (redo the v5→v6 row). Rule: dated pHash frames span > 2 s and every consecutive gap ≤ 2 s → every non-byte-copy frame becomes action ""/high with reason `burst stack (keep all): N frames over S s`; GROUPING 6→7; one source file. Open questions for the owner in the design file §Open questions (UI shows no burst label yet; fast bursts ≤ 2 s unchanged; no span cap). |
| (b) report §9/§10 | worktree `.claude/worktrees/docs-dedup-research`, branch `docs/dedup-core-research` | DONE — commit 8e1ec4f (§8 rows for #1035–#1040, §9 first rescan, §10 second rescan; +123 lines, no personal paths in the new text). NOT pushed: the branch still has pre-existing library paths at report lines 193/199 (§2) and 440 (§7) — owner decides scrub-then-push or keep local. Correction recorded in §10.1: #1035–#1039 were already merged before the v5 scan, so the only code change between v5 and v6 is #1040. §8 intro ("Four PRs, one stack") is stale. |
| (c) scan improvements | `fix/exif-resume-from-failed-file` → **PR #1041** (f8cb904 + aaf9cc6 + news ad0d43c) | **MERGED 2026-11-06 as e418823** (owner's yes; opus review round 1 APPROVE + MEDIUM fixed: dead whole-batch retry removed, 5 old tests moved to the live path, new replacement-failure test; round 2 APPROVE + stale docstring fixed afe1aa8; CI 11/11 green). Local master fast-forwarded. |
| (0) opt-in switch entry | `feat/cloud-analysis-opt-in` (key `scan.cloud_analysis_opt_in`, default false, Advanced-section checkbox + disclosure en/zh_TW, docs entry `Scan dialog — cloud analysis opt-in (setting-only)`) | **PR #1043 OPEN** 2026-11-06 (16c27f0 + review fix 9bb892e: touched-ref so a late settings load never unticks a user click + testid docstring; master merge b85fc6f; news ce63223). Opus review APPROVE. vitest/tsc NOT run locally (no node_modules in the worktree, junction denied by the classifier) → the CI `frontend` workflow (lint/typecheck/build/vitest) is the gate; CI 12/12 green (incl. frontend lint/typecheck/build/vitest); **MERGED 2026-11-06 as b78113d** (owner's yes); local master fast-forwarded. |
| (2) auto-select PRD | `docs/audits/auto-select-prd-2026-11.md` | DRAFT WRITTEN 2026-11-06 (opus; 398 lines; decision: switch entry → append-only verdict table → metadata rule layer R1/R2/R4 → cloud judge for undecided groups only, abstain + swap check + sha256 cache; pixel ranker / auto-delete / preference model before about 200 verdicts NOT built; gate = OFF ⇒ zero non-loopback egress by test). Finding: only 99/150 proxy groups and 22/30 human groups of the Sept labels are still on disk → second labelling round planned. Fresh opus document review round 1 NEEDS-FIX (undecided groups 792→1,020 so costs ×1.28; PR-1 = the already-open #1043; protected set must include rule-layer picks; citations rebased to 55f4948; AC-6 tied to Q3) — all applied by the author; round 2 ACCEPT (cost row, anchors on 55f4948, skill quote re-verified). Probes preserved in `docs/audits/auto-select-prd-2026-11/` (prd-labels.py, prd-reach*.py). Owner to read the file: decision summary + 10 open questions. |

Anchors for stream (0), the opt-in "cloud analysis" switch (scouted 2026-11-06, master 974228a): copy the
autotune checkbox pattern — `frontend/src/components/ScanDialog.tsx:719-745` (Advanced `<details>`, checkbox
at :730), testids `frontend/src/testids.ts:77-80`, strings `translations/en.yml:144-160,785-789` +
`translations/zh_TW.yml:110-126,644-646`, settings allowlist `app/web/routes/settings.py:35`
(`_WEB_SETTINGS_KEYS`), request field `app/web/models.py:279` + resolver :317-323, DTO
`core/app_service/dtos.py:66`, settings-keys reference `docs/reference/settings-keys.md`, docs entry
pattern `docs/features/scan.md:61-67`, qa scenarios for the Advanced section `s17_scan_dialog_widgets.py`,
`s49_scan_auto_select.py`, `s66_autotune_read_knee.py`; tests `tests/test_web_settings_routes.py:165-180`
(round trip) + `tests/test_web_scan_routes.py:432-613`. Key proposal: `scan.cloud_analysis_opt_in`
(bool, default False), label + a disclosure sentence that says media (1024 px EXIF-stripped thumbnails)
leaves this machine; no backend consumer yet.

## 8. Phase close 2026-11-06 — hand-off for a zero-context session

Master is `b78113d` (#1043). Everything below §7 is DONE except the items marked "owner". A new session
resumes by reading, in order: (1) this file §1, §6, §7, §8; (2) `docs/audits/auto-select-prd-2026-11.md`
§1 and §11; (3) `main-t2-burst-design.md` §Open questions. The owner was handed an artifact page (https://claude.ai/artifact/JMdHdpH1x2eBnf7gf14xUr)
("Dedup Core 階段收尾") summarising this and the decisions; the discussion continues from its answers.

Owner decisions outstanding (none blocks anything merged):
1. Docs branch `docs/dedup-core-research` (8e1ec4f, worktree `.claude/worktrees/docs-dedup-research`):
   push as-is (report §2 lines 193/199 and §7 line 440 contain library paths) or scrub first; also whether
   this audit folder, the PRD and its probes (all UNTRACKED in the main checkout) get a docs PR — the PRD
   and probes contain no personal paths, STATE.md does.
2. PRD §11: ten questions; the three that shape the next PR: judge auto-run vs button (Q1); stop
   aggressive mode proposing deletes of distinct captures until a people/pet signal exists (Q3); people
   signal via the cloud judge or a 423 MB on-device runtime (Q10).
3. T2 follow-ups (design file §Open questions): UI "burst" badge needs an API field (reason is not
   serialized); fast bursts with span ≤ 2 s keep REVIEW; no span cap.
4. The 100 Mbps Ethernet link (owner, other session).

Suggested next PRs (from the PRD §10): PR-1b finish #1043 (`scan.cloud_analysis_disclosure_version`, full
disclosure text once retention is verified); PR-2 append-only verdict table (decision log cannot hold a
suggestion, R-LOG-1); PR-3 rule layer R1/R2/R4; measurement M-1 (people metadata prevalence) and M-2
(thumbnail size) before any judge call; second human labelling round (only 99/150 proxy and 22/30 human
groups of the Sept labels are still on disk). Working agreements §6 still apply (haiku implements in a
worktree, opus reviews, owner says "yes" per merge; the owner launches the app; no AV exclusions ever).

### Owner answers 2026-11-06 evening (to the PRD §11 questions) + two new asks
- Q1 (judge auto-run vs button) — owner did not understand the question; clarified: it is about the cloud
  VLM judge over the "undecided" groups, not about grouping. Still open after the clarification.
- Q3 — owner said the auto-select design is too aggressive; the only unit that matters to the owner is
  per-group picture quality plus the human's decision. Read: the people/pet rule and the aggressive-mode concern are not the owner's framing; the
  unit is per-group picture quality plus the human's decision. To be discussed later; the PRD's R1 and
  AC-6 are PROVISIONAL.
- Q10 — research first; quality and effect over cost/footprint.
- New ask A: docs PR with the PRD + its probes + STATE.md (scrubbed) → branch
  `docs/dedup-state-and-auto-select-prd` (haiku prep, opus review, PR).
- New ask B: build a REPRESENTATIVE TEST SET from the real library (every classifier shape, whole groups,
  stratified by drive), copy it to the NAS, and run all future development scans on it instead of the
  40k-row library. Step 1 = selection script + summary (read-only, `docs/audits/testset-2026-11/`);
  step 2 = owner picks the NAS destination and says yes (writing outside the repo is gated); step 3 =
  copy + sha256 verification; step 4 = `settings.json` sources → the test set.
- Test set step 1 DONE 2026-11-06: `docs/audits/testset-2026-11/select_testset.py` + `selection.csv`
  (local, gitignored, full paths) + `summary.md`. 1,381 files / 12.0 GiB across the local disk (504 files)
  and the two NAS shares (334 / 543); 14 categories, whole groups, all quotas met, fixtures + 11 bursts
  included; layout `<ROOT>\D|H|J\<original relative path>`.
- Docs PR **#1044 MERGED 2026-11-06 as e94e859** (owner's yes; opus privacy review round 1 NEEDS-FIX — four
  probes hardcoded the testdata labels path → argv/env, a CR artefact, owner quote → paraphrase — fixed;
  independent positive-controlled blob scan clean except the generic `%LOCALAPPDATA%`). This file is TRACKED
  from here on: the path-bearing untracked original was backed up to the session scratchpad
  (`pre-1044-backup/`) and removed; every later note goes here without personal paths. Updates to this file
  are committed through the next docs PR, so the working tree may carry uncommitted lines between PRs.
- Test set steps 2–3 STARTED 2026-11-06: the owner named the NAS QA folder as `<ROOT>` (kept locally in
  `docs/audits/testset-2026-11/ROOT.local.txt`, gitignored) and approved the copy. `copy_testset.py
  selection.csv <ROOT> copy-report.csv` runs in the background: `shutil.copy2` (keeps mtime), skip when the
  target already matches, verify size + sha256 against the manifest, one report row per file
  (ok / skipped / mismatch / missing-source / error; `copy-report.csv` gitignored). Step 4 after a clean
  report: point `settings.json` `sources.list` at `<ROOT>\D`, `<ROOT>\H`, `<ROOT>\J` (recursive) and
  `sources.output` at `migration_manifest_testset.sqlite`; keep the three full-library entries for swapping back.
- Test set steps 2–4 DONE 2026-11-06: copy finished in 495 s, report `1,381 ok / 0 skipped / 0 mismatch /
  0 missing / 0 error` (size + sha256 verified against the v6 manifest). `settings.json` now points
  `sources.list` at `<ROOT>/D`, `<ROOT>/H`, `<ROOT>/J` (recursive) and `sources.output` at
  `migration_manifest_testset.sqlite`; the full-library sources are saved in
  `docs/audits/testset-2026-11/settings.full-library.local.json` (gitignored) and the test-set block in
  `settings.testset.local.json` — swap by copying either block back into `settings.json`. NEXT: the owner
  runs `.\run.bat` → Start Scan once on the test set (minutes, not hours) to produce the baseline
  `migration_manifest_testset.sqlite` under GROUPING "7"; then compare it against `selection.csv` joined on
  sha256 (same groups / actions as v6 except the 11 burst groups, which must now read `burst stack`).
  Tooling to commit in the next docs PR: `select_testset.py`, `copy_testset.py`, `summary.md`, `.gitignore`.
- Test set BASELINE DONE 2026-11-06 16:08 (owner scanned; about 4 min vs 1 h 37 m): 1,381/1,381 files,
  sha256 all equal to v6, grouping partition identical (312 groups), 121 `burst stack` rows in 12 groups,
  13 byte copies inside bursts still EXACT; 14 label changes are subset artefacts (first pHash candidate
  differs when the deciding partner is outside the set). Details + method: `docs/audits/testset-2026-11/
  summary.md` §Baseline; `compare_testset.py` added (UNC-aware path mapping). From here, grouping changes
  are measured on the test set first; the full library only for a final confirmation.
- Docs PR **#1045 MERGED 2026-11-06 as 4fc1dcc** (owner's yes; opus privacy review PUBLISH-OK, two should-fix
  items folded in: the selector no longer overwrites the folder's `.gitignore`, summary.md dates the copy).
  Test-set tooling and this STATE round are now on master. Next-step plan handed to the owner (A collect:
  PRD R1/AC-6 → "per-group picture quality + human decision", PR-2 verdict table, PR-1b disclosure_version,
  T2 UI badge via an API field; B measure on the test set: second human labelling round using the 312
  test-set groups via decision log, Haiku judge pilot with swap check + abstain, thumbnail-size M-2;
  C wire: R2 needs Q8, R4 = burst stack, judge behind the switch with delete-set ⊆ invariant).
  Owner items still open: research-report docs branch 8e1ec4f (push or scrub), Q1/Q8, the 100 Mbps NIC.
- 2026-11-06 night, owner asks: (1) a product-proxy review of the PRD direction (opus, memo →
  `docs/audits/auto-select-prd-2026-11/direction-review.md`, then the hand-off artifact is updated) —
  DONE (see the bullet below); (2) preview performance research → **issue #1046** filed (first view of each scanned file
  re-reads the original from the NAS and decodes it although the hash step already decoded a draft;
  the route's disk cache only helps repeat views; option A = persist a 512 px thumbnail at scan time under
  the same cache key; measure first on the test set; related #887/#790/#865/#826). The owner takes #1046
  in another session.
- Direction review DONE 2026-11-06 night (`docs/audits/auto-select-prd-2026-11/direction-review.md`, 153 lines,
  untracked until the next docs PR; artifact §4b updated): auto-select = one visible reasoned suggested keeper
  per group, judged on picture quality, accepted by the owner. CUT: people/pet rule (PRD R3), AC-6, the
  aggressive-mode change (the owner's Sept policies hold by construction — every row defaults to Keep, no
  new delete path). DEFER: original-vs-edit rule until the owner answers. First three PRs: (1) reason +
  match_confidence into the review payload + "keep all" badge on burst headers (reason is read from the DB
  but never reaches PhotoRecord/UI today); (2) "Reviewed" control → append-only verdict table recording the
  keep set incl. keep-all (the decision log cannot record keep-all); (3) "Suggested" marker = the Keep best
  keeper with reason "best metadata (score)", aligned with the Ref label (today chosen from different row
  sets). Judge UX: the #1043 switch IS the consent; ON → runs after each scan on uncached groups, fills the
  same Suggested marker with a one-line reason / keep all / can't tell; OFF → metadata pick only. Measure
  first on the test set: metadata-keeper agreement with the owner's labels, keep-all share, Ref≠keeper
  rate, per-candidate judge precision/abstain/swap/cost; provider retention terms + egress test before any
  backend PR. Owner questions left (one word each): edit-vs-original policy; one-off thumbnail send for the
  judge measurement; a bulk "accept all" button once precision is met.
- 2026-11-06 night — ALIGNMENT RESET. The owner rejected the direction review's three questions as jargon
  ("labels", "suggestion source", an "accept all" button) and corrected three product facts: (1) no hacky
  rules such as original-vs-edit — the engine's general gate decides and whoever it picks is the pick;
  (2) cloud use is simply allowed once the user opted in, but WHICH model, why not local, and whether the
  user can pick the model are open — the PRD must not hardcode a provider; (3) the flow has no new step:
  settings → Scan → the view already shows actions (as aggressive mode does today). The owner asked for a
  full questionnaire to capture their expectations first. Published: **Auto-select 需求問卷**
  https://claude.ai/artifact/Xs5SMpGfoSVCURQeS3MrXQ (36 questions in 8 sections A–H; answers autosave to the
  artifact's `db` collection `answers/<qid>` = {choices, text, updatedAt, question}; read them with the
  ArtifactData tool, action "list", collection "answers"). The PRD §5/§9/§10 and direction-review.md are
  PROVISIONAL until the answers are in; the next PRD revision is written FROM the answers.
- 2026-11-06 late — QUESTIONNAIRE ANSWERED (35/36; E4 API key open). Saved verbatim:
  `docs/audits/auto-select-prd-2026-11/requirements-answers-2026-11-06.md` (untracked until the next docs
  PR). Product model it fixes: three scan modes (default = groups only, no actions; auto-select = most
  actions + a list of items to confirm; aggressive = all actions); nothing happens until the user presses
  Execute; "needs a human" = a CONFIDENCE threshold, never a ratio; the engine judges picture quality only,
  preferences are the user's (B-section rules become plugin-like toggles in Advanced settings with our
  defaults; never hardcoded); keep-both on quality-vs-metadata conflicts and RAW+JPEG; bursts keep all;
  Live-Photo MOV follows its photo; screenshots like photos; people/pets deletable only when an exact dup;
  PURE LOCAL MODE FIRST with usable quality (G1), cloud only if local cannot, user picks provider/model in
  settings, cloud budget < $1 per full-library scan, scan time irrelevant; uncertain → REVIEW; engine runs
  inside the scan; view shows action + one-line reason + confidence; no auto-lock; decision log required;
  validation on all 312 test-set groups, threshold after seeing numbers, cloud thumbnails allowed during
  validation. NEW CORE-TASK FINDING (A4): colour-graded edits are being labelled EXACT ("100% match") —
  they must be near-duplicates, never EXACT → measure on the test set / v6 (pHash-0, different sha, colour
  differs), then fix the format-duplicate EXACT rule. The PRD will be rewritten FROM these answers.
- A4 PROBE DONE 2026-11-06 (`docs/audits/testset-2026-11/probe_exact_colour.py`, output gitignored): v6 has
  11,917 EXACT rows = 9,169 byte copies + **2,748 "format duplicates"** (pHash 0, different sha; heic|heic
  1,974, jpg|jpg 663; 266 pairs differ in dimensions; 407 pairs differ > 20 % in size). On the test set 65 of
  85 format pairs decoded: 50 identical-ish, 6 re-encodes, **9 colour/pixel-different** — and the graded
  examples are CONSECUTIVE shots (IMG_3788 vs IMG_3789, P1011080 vs P1011083): distinct captures labelled
  EXACT because pHash 0 + shot times within 2 s. Today's rule: `_classify_format_group` dedup.py:597 → EXACT
  at ~:683; gates = mean colour (#462, :707) + dHash vote (#524, :724); no pixel-identity check. Gate sweep
  (same dims AND 32×32 RGB mean-abs-diff ≤ T): T=6 keeps 40/65 EXACT, demotes 8/9 graded + 1/6 re-encodes;
  T=3 demotes 9/9 graded + 5/6 re-encodes. DECISION (core task, owner's A4 "EXACT must be identical"): PR
  `fix/exact-requires-pixel-identity` — hasher stores a 32×32 RGB signature on HashResult (scan-time only,
  no manifest column); format duplicates stay EXACT only when dims are equal AND diff ≤ 6, else
  REVIEW_DUPLICATE/high "same pHash, pixels differ (edited/resized/other shot)"; byte copies untouched;
  GROUPING "7" → "8"; measured on the test set after the owner's 4-minute rescan.
- EXACT pixel gate IMPLEMENTED 2026-11-06 (717286d on `fix/exact-requires-pixel-identity`, worktree, not
  pushed): `rgb32` on HashResult (32×32 BOX of the oriented working image, scan-time only),
  `FORMAT_DUP_MAX_RGB_DIFF = 6.0`, `_pixels_identical`, gate last before EXACT in `_classify_format_group`,
  GROUPING "8", 10 new dedup tests + 4 hasher tests, targeted 189 green, full suite green by log (93 %
  coverage). Implementer caveats under review: the signature is taken from the DRAFT-decoded image while the
  probe measured full decodes (threshold drift possible → reviewer re-measures the 65 pairs with the branch
  code); a single-channel +10 shift (mean 3.3) still passes — a per-channel mean-shift ≤ 4 second condition
  is being evaluated; `_classify_format_group` was already > 50 lines. PRD v2 draft exists
  (`docs/audits/auto-select-prd-2026-11-v2.md`, 419 lines, every owner requirement cites an answer id);
  fresh review round 1 NEEDS-FIX (B7 contradicted, C3 condition ignored, videos undesigned, two open
  questions already answered by A1/C5/B8, branch over-claim) — fixes being applied.
- PRD v2 ACCEPTED 2026-11-06 night (`docs/audits/auto-select-prd-2026-11-v2.md`, 417 lines, untracked until
  the next docs PR): rewritten from the owner's answers; fresh review round 1 NEEDS-FIX (13 findings: B7
  contradicted → screenshot demotion is a toggle default OFF; C3 gating of cloud work on local failing; G2
  videos line; branch over-claim; B3/B8 attribution; two open questions already answered) → round 2 ACCEPT
  + three nits applied by the lead. Only open question: E4 (API key storage). Phased plan PR-1 (reason +
  confidence in the view) → PR-2 (EXACT pixel gate, in review) → PR-3 (three modes + threshold, no lock)
  → PR-4 rule toggles → PR-5 traceability → M-1 owner labels 312 groups → PR-6 local veto → M-2 local models
  → PR-7 engine picker → M-3/PR-8/PR-9 cloud only if local is not usable. Artifact §4 updated to v2.
- EXACT gate REVIEW round 1 (2026-11-06 night): NEEDS-FIX, calibration CONFIRMED on the branch's draft decodes
  (|draft − full| MAD ≤ 0.42; 85 pairs all decodable incl. 18 .DNG-named JPEGs; T=6 keeps 60/85, demotes 8/9
  pixel-different; IMG_0405/0406 passes with per-channel shift 5.72). Fixes requested: F1 dimensions are read
  before `exif_transpose` while the signature is after → accept rotation-swapped dims; F2 add max per-channel
  mean shift ≤ 4 (`FORMAT_DUP_MAX_CHANNEL_SHIFT`) — demotes only IMG_0406 on the sample, catches the +10
  single-channel case; F3 confidence from the dHash vote, not hardcoded high; docs → new entry `### Scan
  grouping — EXACT requires pixel identity`. INFO for a follow-up issue: a REVIEW/high row at Hamming 0 is
  still aggressive-delete eligible (`auto_select.py:291`) and still shows the 100 % "exact" badge
  (`review_view.py:85`, `similarityBadge.ts:141`) — the gate relabels without protecting until PRD PR-1/PR-3.
- **PR #1048 OPEN** 2026-11-06 night (`fix/exact-requires-pixel-identity`: 717286d + a891bfc + news 6605e32).
  Review round 2 APPROVE; calibration on the branch's own decode over all 85 test-set pairs: 59/85 stay
  EXACT, 9/9 pixel-different demoted (incl. IMG_0405/0406 via the channel-shift gate), the other 17 are
  resized copies + one dimension-changed re-encode, no honest same-size re-encode demoted. Gate = equal or
  90°-swapped dims AND 32×32 MAD ≤ 6 AND max channel shift ≤ 4; confidence from the dHash vote; GROUPING "8";
  docs entry `Scan grouping — EXACT requires pixel identity`. Follow-up #1047 (REVIEW/high at Hamming 0 still
  aggressive-eligible + 100 % badge). CI running; needs the owner's "yes". AFTER MERGE: owner rescans the
  test set (≈ 4 min) → `compare_testset.py` → expect the 26 EXACT→REVIEW moves (9 + 17) and no group change.
- **PR #1048 MERGED 2026-11-06 night as b47c63c** (owner's yes; CI 11/11; opus review round 2 APPROVE). Local
  master fast-forwarded; GROUPING "8" is live. NEXT (owner): `.\run.bat` → Start Scan on the test set
  (≈ 4 min; the previous `migration_manifest_testset.sqlite` is overwritten — copy it to
  `migration_manifest_testset_g7.sqlite` first if a GROUPING 7 keepsake is wanted); then lead runs
  `compare_testset.py selection.csv <ROOT> migration_manifest_testset.sqlite` and expects 26 EXACT→REVIEW
  moves (9 pixel-different + 17 resized/dimension-changed), partition unchanged (312 groups).
- Test set re-scanned under GROUPING 8 (owner, 2026-11-06 20:07). `compare_manifests.py` (new, diffs two
  scans of the same file set) g7 → g8: 1,381/1,381 rows, sha256 unchanged, partition identical (312/312),
  exactly **26 EXACT/high → REVIEW_DUPLICATE/high "same pHash, pixels differ"** (IMG_3789, P1011083 …), no
  other label change. #1048 behaves as calibrated. GROUPING 7 keepsake: `migration_manifest_testset_g7.sqlite`.
- NEW OWNER FINDING 2026-11-06 night: groups consisting ONLY of a Live-Photo image + its own video. Measured:
  test set 16/312 groups; **v6 777/8,337 groups (9.3 %)** are exactly {image, video, same stem}, both rows
  action "" "unique"; 1,060 groups mix images and videos. Side artefact: in v6 some pair-only groups carry a
  HEIC labelled REVIEW_DUPLICATE "near-duplicate of IMG_4359" whose partner sits in ANOTHER pair-only group
  (low edge between two multi-member groups is never merged per #1040, but the label still points across).
  Opus planner dispatched: design "a pair-only component is not a group" (keep the MOV-follows-photo link
  outside group membership; GROUPING "9"), with the hint-pass and R-LOCK interplay. Core-task item.
- 2026-11-07: opus design received and saved as `main-live-pair-design.md` (option (a)): a third Edge kind
  "pair" for image↔video same-stem edges (image↔image stays "high"); `_assign_group_ids` gains pass 3
  `_pair_pass` — pair clusters attach WHOLE only when a member is already joined, otherwise nobody gets a
  group_id; GROUPING "9". Expected on the test set: 16 pair-only groups → NULL (32 rows), 312 → ≈296 minus
  hint-merges. Contradictions the design surfaced: no "MOV follows photo" behaviour exists (#88 removed it;
  `scoring.py:423-425` docstring is false — B6 needs its own PR); pairing is filename-only; the IMG_4361
  cross-group label is the pair-as-high side effect and disappears for free. Scope calls taken by the lead
  without the owner (flag at report): video↔video same stem stays grouped; image↔image same-stem pairs out
  of scope; qa `s11_video_live.py` is edited but NOT run locally (owner's OK needed; CI runs the batch).
  Dispatched haiku implementer in a worktree (`fix/lone-live-pair-not-a-group`).
- 2026-11-07 prediction (`main-v6-livepair-predict.py`, label-based: a group is pair-only when it has no EXACT,
  no in-group REVIEW target, no accepted hint, no burst row, and holds a video): test set 312 → 16 dissolve
  (32 rows NULL) + 1 group keeps its image pair and loses only its video → **296** predicted (matches the
  design). Full library v6 8,337 → 783 dissolve (1,575 rows; the 777 "both unique" + 6 REVIEW-cross-pointing)
  + 3 partial → **7,554** predicted, upper bound before hint-merges. To confirm after the owner rescans.
- 2026-11-07: haiku implemented GROUPING "9" (worktree commit 21a8c07, 9 files, 15 new tests, full suite
  2,272 passed; mutation check: forcing `_pair_kind` to "high" fails 5 tests). Fresh opus review:
  NEEDS-FIX — core passes (order independence, hint interplay, scope, docs, privacy), but qa `s11` scanned
  non-recursively so the `dupe/` copy was never walked (would fail CI), the 6-order cluster test could not
  fail (video↔video high edge pre-joins the cluster; Windows path filter never matched), the
  "attaches to high member" test duplicated the near-dup test, a stale comment, a wording nit. Sent back
  as a second commit. Implementer flagged: a lone HEIC+MOV+MP4 triple still groups (video↔video high) —
  owner question, documented in the new scan.md entry.
- 2026-11-07: fixes committed (e03da09: s11 `recursive=True`; 6-order cluster test rebuilt as HEIC + gated
  JPG byte-copied elsewhere + MOV — real pass 6/6, naive edge-by-edge mutant fails 6/6; low-hint-to-high-
  member test now really low; comment + wording). Re-review **APPROVE**. **PR #1049** opened
  (`fix/lone-live-pair-not-a-group`, GROUPING "9", news/1049.bugfix, 10 files). Awaiting CI, then the owner's
  "yes", then test-set rescan + `compare_manifests.py` g8 → g9 (expect 296 groups, 32 rows → NULL).
- 2026-11-07: PR #1049 CI green, all 11 checks incl. `web-scenario-batch` (s11 with the `dupe/` copy ran in
  CI and passed) and `pytest`. Waiting for the owner's merge "yes".
- 2026-11-07: **#1049 merged** (squash 9c92012), local master fast-forwarded. GROUPING "9" is on master.
  Test-set manifest from GROUPING 8 kept as `migration_manifest_testset_g8.sqlite` (gitignored); owner to
  rescan the test set, then `compare_manifests.py migration_manifest_testset_g8.sqlite
  migration_manifest_testset.sqlite` (expect 296 groups, 32 rows → NULL, no label change outside them).
  Open owner questions: HEIC+MOV+MP4 triple (video↔video edge stays high); RAW+JPG pairs untouched.
- 2026-11-07 12:41: test set rescanned under GROUPING 9 (owner). `compare_manifests.py` g8 → g9: 1,381/1,381
  rows, sha256 unchanged, **312 → 296 groups, 296 identical member sets, 16 only-before, 0 only-after, zero
  label changes**. Row level: exactly 32 rows grouped → NULL, 0 the other way; the 16 dissolved groups are all
  one-stem image+video pairs (13 HEIC+MP4, 2 JPG+MP4, 1 HEIC+MOV), every row action "". Matches the
  prediction exactly. Probe correction: a RAW+JPG(+video) same-stem group is unchanged (the image↔image high
  edge joins the still, so the video attaches) — `main-v6-livepair-predict.py` fixed; v6 prediction stays
  8,337 → 7,554. Keepsake: `migration_manifest_testset_g8.sqlite`.
- OWNER STANDING INSTRUCTION 2026-11-07: update the test set whenever needed and deliberately hunt odd
  combinations into it. Next: shape census of v6 groups vs the test set (`extend_testset_shapes.py`) → extend
  `select_testset.py` with the missing shapes → copy → rescan → new baseline.
- 2026-11-07 13:00 HAND-OFF (session shell wedged — Bash cwd stuck in a subfolder, project hooks resolve
  `scripts/hooks/*.py` relative to cwd and block every call, subagents inherit it; lesson in memory
  `never-end-bash-in-subdir`). Written but NOT yet run: `docs/audits/testset-2026-11/extend_testset_shapes.py`
  (shape census full-library vs test set; writes `selection-shapes.csv` + `shape-census-output.local.txt`, both
  gitignored). Next session, from the repo root:
  `python -I docs/audits/testset-2026-11/extend_testset_shapes.py migration_manifest_v6.sqlite
  migration_manifest_testset_g8.sqlite docs/audits/testset-2026-11/selection.csv docs/audits/testset-2026-11 2`
  → read the shape table → `copy_testset.py selection-shapes.csv <ROOT> copy-report-shapes.csv` (ROOT in
  `ROOT.local.txt`) → owner rescans → `compare_manifests.py` against the g9 keepsake → new baseline in
  `summary.md`. Uncommitted docs for the next docs PR: STATE.md tail, `main-live-pair-design.md`,
  `main-v6-livepair-predict.py`, `extend_testset_shapes.py`, testset `.gitignore` (+3 lines),
  `compare_manifests.py`, `probe_exact_colour.py`, PRD v2, `direction-review.md`, requirements answers.
- 2026-11-07 13:20: shell unwedged (owner typed `! cd <repo root>` with forward slashes — recorded in memory).
  Shape census ran (`extend_testset_shapes.py`, v6 vs test set g8): 198 shapes in the full library, 84 in
  the test set, **132 missing covering 1,682 groups**; biggest absentees are cross-folder same-stem HEIC/JPG
  format duplicates (580 + 108 + 106 groups), screenshot PNG pairs with a cross-group label, Live triples
  (HEIC+MOV+MP4), video-only same-stem sets, RAW pairs. Proposed extension `selection-shapes.csv`: 2 small
  groups per missing shape = 213 groups / 662 files / 2.6 GB (5 shapes got 0 picks: size cap). Awaiting the
  owner's yes before copying to the NAS test-set root.
- 2026-11-07 13:35: owner approved the 2.6 GB extension copy. g9 keepsake saved (`migration_manifest_testset_g9.sqlite`).
  First copy attempt did nothing: `testset-2026-11/ROOT.local.txt` held a literal TAB where the `\t` of the
  drive path should be (written earlier through a printf FORMAT string) → WinError 123, 0 report rows. Fixed
  with a literal write (never put a Windows path into a printf format string). Copy re-launched.
- 2026-11-07 13:45: extension copy DONE — 662/662 ok (size + sha256 verified), 2.79 GB in 179 s, report
  `copy-report-shapes.csv` (gitignored). Test set is now 1,381 + 662 = 2,043 files. Owner to rescan; then
  `compare_manifests.py migration_manifest_testset_g9.sqlite migration_manifest_testset.sqlite` (the 1,381 old
  rows must keep partition + labels; the 662 new rows appear as only-after) and a new baseline in `summary.md`.
- 2026-11-07 14:07: extended test set rescanned (owner). `compare_manifests.py` g9-keepsake → new: 1,381 old
  rows sha unchanged, **296/296 old groups identical**, 206 new groups, 1 old-row label change (a unique JPEG
  gained a skipped low hint from a new file — expected). `analyze_extension.py` (new): of the 213 added
  groups 205 intact, 6 dissolved (all Live-pair shapes incl. HEIC+MOV+PNG where the HEIC↔PNG edge is
  content-gated) and 2 merged with their cross-group label target — exactly the GROUPING 9 behaviour.
  **New baseline: 2,043 files, 502 groups, 154 burst rows / 20 groups, 94 groups with video, 18 video-only
  same-stem sets, 0 pair-only groups** — written to `testset-2026-11/summary.md` §Extension 1. The 18
  video-only same-stem groups are the owner's pending decision (video↔video edge stays high).
- 2026-11-07 evidence for the owner's video↔video question: test set — all 18 video-only groups are
  byte-identical EXACT copies (sha edge, not the stem edge). Full library v6 — 454 same-stem video sets with
  DISTINCT bytes, every one next to a still (MOV+MP4 376, MOV+MP4×3 48 …); 449 are anchored by an EXACT
  elsewhere in the group, **5 are pair-only** (HEIC+MOV+MP4, nothing EXACT): under GROUPING 9 these 5 stay a
  3-member undecided group via the video↔video high edge. Sizes in the examples (MOV 55 MB vs MP4 4.5 MB, same
  stem) say the two videos are NOT transcodes of each other — same-stem videos are not duplicates by name.
  Decision pending: treat video↔video same-stem as "pair" too (5 groups hidden; the 449 unaffected).
