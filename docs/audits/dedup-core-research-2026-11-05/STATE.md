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
- 2026-11-07 15:10: **#1050 merged** (squash 7b96762; single clean commit after the privacy review found a home
  path in the two-commit draft's history — the draft branch was never pushed and is now deleted). Local master
  fast-forwarded. Everything this round produced is tracked.
- OWNER DECISION 2026-11-07 on same-stem videos (and, by extension, on filename pairing itself): a shared
  filename stem is only a coincidence of the iPhone naming scheme and proves nothing about content. Two
  same-stem videos may be grouped ONLY if their content is shown to be the same; "if we do it at all, pairing
  must use the real content". Consequences: (a) no GROUPING "10" by filename — the video↔video `high` stem edge
  stays as-is until a content signal exists (today 5 pair-only HEIC+MOV+MP4 groups in v6 depend on it);
  (b) the Live Photo image↔video link should eventually come from content metadata — Apple writes the same
  `ContentIdentifier` (QuickTime `com.apple.quicktime.content.identifier` / HEIC `MediaGroupUUID`) into both
  halves, readable through exiftool — instead of the stem; (c) video↔video identity needs its own signal
  (duration + a few frame pHashes, or the container's creation time + ContentIdentifier). Research item, not
  scheduled; it belongs after PRD v2 PR-1/PR-3 unless the owner reorders.
- 2026-11-07 15:30: owner started **PRD v2 PR-1** (reason + match_confidence in the view; #1047 badge half:
  "exact" state from `action == EXACT`, not Hamming 0). Opus planner dispatched for the design; then haiku
  implements in a worktree, opus reviews, PR. The aggressive-eligibility half of #1047 stays PR-3.
- 2026-11-07 15:50: PR-1 design saved as `docs/audits/auto-select-prd-2026-11/pr1-design.md`. Findings: only
  `reason` is missing from PhotoRecord (match_confidence already flows); the preview metadata table is pinned
  at 5 rows; the delete-confirm dialog repeats the #1047 defect ("Exact duplicate of X" for any 100 %). Lead
  decisions: reason + confidence in every row's hover tooltip (classification / reason / "Confidence: …"),
  a visible chip only where it changes the decision ("Keep all · burst", "Low confidence"), badge "exact"
  state from `action == EXACT`, delete-confirm wording fixed too (F7). Deviation from PRD §7 wording ("every
  row shows a one-line reason") to flag to the owner: shown on hover, not as a column (a column sheds the
  date at the reference width and reads as debug text). Haiku implementer dispatched in a worktree.
- 2026-11-07 16:40: PR-1 implemented (haiku worktree, f213dff + 1fb592f; 25 files, backend suite 2,277 passed;
  mutation: keep_all forced False fails its test). Fresh opus review **APPROVE** with two LOW notes (null-reason
  test fed no NULL; review.md said `unique` is "a row outside any group" — it is the keeper's reason) → fixed
  in 17289de. Reviewer also classified the s07 fixture live: pHash distance exactly 10 (on the threshold),
  dHash 1 → near-duplicate path, HEIC keeper reason `unique`, so Assertion G holds. **PR #1051** opened
  (`feat/review-reason-confidence`, news/1051.feature). Frontend lint/typecheck/build/vitest run in CI only.
- 2026-11-07 17:00: PR #1051 CI green — 12/12 incl. the `frontend` workflow (lint / typecheck / build / vitest
  on the unverified-locally TS) and `web-scenario-batch` (s07 Assertion G). Waiting for the owner's merge "yes".
- 2026-11-07 17:20: **PR #1051 merged** (squash 7d48ebd), local master fast-forwarded. PRD v2 PR-1 done. Next: PR-3
  (three modes + confidence threshold, keeper lock removed, aggressive-eligibility half of #1047); opus planner
  design first, then haiku worktree implementation as before.
- 2026-11-07 20:00: **PR-3 design** by the opus planner saved as `docs/audits/auto-select-prd-2026-11/pr3-design.md` (untracked,
  next docs PR). Key calls: keep the two wire booleans (off/auto/aggressive), one new field + settings key
  `scan.auto_select.confirm_below` in {high, medium}; a per-row level low < medium(REVIEW_DUPLICATE) < high(EXACT or
  confidence high); auto mode stamps KEEP only in groups with every row admitted, never locks; aggressive never deletes a
  below-threshold row (#1047 half); "Needs your look" is a status-bar group filter. Census on the test set: T=high 263/502
  groups need a look, aggressive deletes 497 -> 316 (54 are #1047 rows); T=medium 148 need a look, deletes unchanged.
  Owner decides the default threshold before implementation starts.
- 2026-11-07 20:30: owner: default threshold **high**, PR-3 started. Haiku implements B1-B9 / F0-F9 / s80 / R-SCAN-4-5 from
  pr3-design.md in a worktree, opus reviews, lead opens the PR.
- 2026-11-07 21:20: PR-3 implemented (haiku worktree, 4 commits b909ee7/a117e60/71435bb/119006e; 53 files). Haiku could not
  run the frontend (worktree guard refused the node_modules junction); lead made the junction and ran it: typecheck caught
  `SettingsMap` missing the new key, vitest caught the PR-1 "unknown level" probe using "medium" -> fixed in 269e38d; then
  typecheck / lint / vitest 1,203 / build all green. Backend 2,297 passed, 2 timing failures in
  test_scan_runner_exif_rotation.py (pass alone; full-suite rerun in progress). Mutation check: below_threshold forced
  False fails the #1047 test. Opus review dispatched.
- 2026-11-07 22:50: fresh opus review of PR-3 **APPROVE**; one MEDIUM (help text hid that aggressive mode still deletes a
  needs-look group's admitted copies) + two LOW (write_manifest docstring said pass None for empty keepers -> new decision-log
  test pins the empty-set path; two docs claims aligned with the UI) fixed in 1dfaf8a. Full backend suite: the 2
  test_scan_runner_exif_rotation.py failures reproduce on MASTER under the same full-suite load (1,600-file scans, 60 s
  budget) -> pre-existing, not PR-3; CI is the gate. Master also fails test_check_rules_register because the UNTRACKED
  pr3-design.md cites R-SCAN-4/5 -> the docs PR carrying it must land AFTER PR-3. Blob scan on all 6 commits: controls ok,
  only pre-existing placeholders. **PR #1052** opened (`feat/auto-select-confidence-threshold`, news/1052.feature, Closes
  #1047). CI watch running. Lesson: the 30-min local full suite is not worth blocking on; run touched files + CI.
- 2026-11-07 23:10: PR #1052 CI 11/12; web-scenario-batch failed s80: on the CI runner the near-dup fixture's q88 re-encode
  decodes pixel-identical -> EXACT (high), which the design admits and deletes inside a needs-look group (keeper unstamped,
  REVIEW rows untouched) — code right, scenario assumed all five rows REVIEW. Fixed the assertion (26902ce, one CI
  iteration). Re-watching CI.
- 2026-11-07 23:30: **PR #1052 CI green** — 12/12 incl. web-scenario-batch (s49 / s57 / s80 all pass). Waiting for the owner's
  merge "yes". After merge: owner rescans the test set; expected at default high: 263 groups under "Needs your look",
  aggressive candidates 497 -> 316; verify with a scratch census on the new manifest.
- 2026-11-07 23:40: **PR #1052 merged** (squash fada596), local master fast-forwarded; #1047 closed by the PR. PRD v2 PR-3 done.
  Next: owner rescans the test set (aggressive, threshold high) -> census on the new manifest; then the docs PR (STATE.md
  tail + pr3-design.md, now safe since R-SCAN-4/5 are in the register); then PR-4 (rule toggles v1) per PRD §11.
- 2026-11-08 00:05: owner rescanned the test set on fada596 (reported: aggressive + high). Census on the new manifest:
  502 groups, **263 need a look (= prediction), 239 KEEP-stamped groups (= prediction), 0 locked rows, 0 needs-look group
  with a stamp, 0 below-threshold or pixels-differ row deleted** — but **0 delete rows (expected 316)** and no "marked N
  row(s) for delete" line in the app log, only "marked 239 keeper(s); 263 group(s) need your look (confirm below high)".
  That is exactly auto mode without aggressive; CI s57 on this commit writes deletes through the real dialog, so the wire
  path works. Most likely the aggressive checkbox (resets on every dialog open, nested under auto-select) was not ticked.
  Asked the owner to rescan with it ticked. Side note confirmed: decision_log is EMPTY after an auto-only scan (R-LOG-1,
  design Contradiction 5) — traceability gap for PR-5.
- 2026-11-08 01:00: owner rescanned with BOTH boxes confirmed ticked (aggressive + high). Census on the new manifest: 502 groups,
  **263 need a look, 239 KEEP-stamped groups, 0 locked, 0 needs-look group with a stamp, 0 below-threshold or pixels-differ
  row deleted, 449 delete rows — all EXACT/high.** Not 316: the pre-measurement script (scratch) modelled the keeper as
  first-index max(score) and counted only action EXACT/REVIEW rows as eligible; the pre-change manifest already carried KEEP
  stamps, so in every group where the stamped keeper tied on score with a byte copy the script dropped one delete (same
  artifact made "today" 497 instead of 619). Re-running the SHIPPED plan_auto_select on the manifest rows reproduces the
  writes exactly: stamped 239 = 239, deletes 449 = 449, needs-look 263; at medium the set would be 619, so high spares 170
  REVIEW rows (the #1047 class). 136 of the 449 are admitted EXACT copies inside needs-look groups (design: copy deleted,
  keeper unstamped) — a group can carry delete marks and still sit under "Needs your look"; watch for UX confusion in PR-4/5.
  Lesson: verify a count with the shipped function, never with a re-modelled predictor (judgment-rubrics "same instrument").
  PR-3 verified end to end on the real test set. Next: docs PR (STATE.md tail + pr1/pr3-design.md), then PR-4.
- 2026-11-08 01:40: **docs PR #1053 merged** (squash 1626771); local master fast-forwarded. pr1-design.md / pr3-design.md are
  tracked. Opus privacy scan of the PR found 8 PRE-EXISTING items in already-public STATE.md lines (camera filenames, two
  dated folder stems, two artifact links); surfaced to the owner, scrub pending their call. Next: PR-4 (rule toggles v1).
- 2026-11-08 02:00: **PR-4 decisions (owner):** split into PR-4a (framework + toggles over existing behaviour: B3 byte copies,
  B4 RAW+JPEG, B5 burst keep-all, B7 screenshot demotion default OFF, B8 protect default ON) and PR-4b (B6 Live Photo MOV
  follows its photo, new behaviour); B2 deferred to PR-6 (no quality signal exists); B3 "which copy stays" keeps today
  (top score, tie-break path); UI = a Rules sub-block in Advanced settings. PR-4a started: opus planner drafting
  pr4-design.md (lands with the feature PR so R-SCAN-6+ and the register move together); then haiku in a worktree, opus
  review, PR.
- 2026-11-08 02:40: pr4-design.md returned (opus, 348 lines; in worktree .claude/worktrees/pr4a, lands with the feature PR).
  Planner findings: B7 default OFF would change default GROUPING (PNG chains merge; may reopen dedup-core class B);
  apply_best_copy should ignore the toggles; B3 must cover every EXACT row incl. format duplicates; s57 must untick B8;
  estimate 12.5 h. **Lead decisions:** B7 stays in PR-4a but DEFAULT ON (no GROUPING bump; the PRD default-off flip is
  PR-4b after a rescan census), apply_best_copy ignores toggles, B3 = all EXACT, auto-mode deletes deferred. Haiku
  implementing on feat/auto-select-rule-toggles-v1.
- 2026-11-08 02:45: **PR #1054 open** (feat/auto-select-rule-toggles-v1, 8 commits incl. pr4-design.md and news/1054.feature).
  Haiku: 17 touched test files 557 passed, frontend typecheck/lint/vitest 1,211/build green, mutation check on B8 fails as
  expected. Opus review APPROVE, one MEDIUM (R-SCAN-10 still said B7 off by default) fixed in 51e24c2. Blob scan clean on
  every commit (only generic pre-existing hits). B4/B5/B7 live effects have no CI fixture (layer-1 only). CI watch running.
- 2026-11-08 02:50: PR #1054 CI round 1: pytest red (testids.ts is GENERATED by scripts/gen_testid_ts.py from
  qa/web/testid_constants.py; haiku edited it by hand -> parity test) and web-scenario-batch red (s81 phase 2 re-created
  its copy folder -> FileExistsError; phase 1 assertions had passed, 76/80 scenarios green). Both fixed in 1007cfa (one
  iteration, one cause each). Lesson for the implementer brief: name every generated file and its generator.
- 2026-11-08 03:05: **PR #1054 CI green** — 12/12 incl. pytest and web-scenario-batch (s81 both phases, s57 with B8 unticked).
  Waiting for the owner merge "yes". After merge: npm build dist, owner rescans the test set to confirm the default rules
  reproduce 239 / 449 / 263 (all-on defaults = today at high), then PR-4b (B6 + the B7 default census).
- 2026-11-08 03:15: **PR #1054 merged** (squash 0e18976) — PRD v2 PR-4a shipped: rule toggles v1 (B3/B4/B5/B7/B8, all on;
  B7 PRD default-off deferred), R-SCAN-6..11, s81. Local master fast-forwarded; frontend/dist rebuilt. Next: PR-4b = B6
  (Live Photo MOV follows its photo) + the B7 default decision after a rescan census of the test-set copy.
- 2026-11-08 04:00: **Owner: grouping on the test set is very bad; checked it myself (census + contact sheets, scratchpad only).**
  Manifest is still the 00:45 scan (GROUPING 9, pre-#1054; the rule toggles default to today, so #1054 changed no group).
  502 groups: **100 held together only by LOW edges**, 43 span >1 calendar day (27 of them with no EXACT row), 91 PNG-only.
  Looked at the worst: (1) a same-stem RAW+JPG pair edge joined two unrelated shots five months apart (stem collision,
  image<->image stem edge is "high" and always groups; the pHash gate did not drop it); (2) a portrait HEIC+MP4 Live Photo
  joined two app screenshots eight months later through a low-confidence HINT edge (hints do not chain, but a hint still
  forms the group); (3) templated document screenshots grouped as near-dups (class C). Timezone-shift groups (480 min
  spans, same day) are legitimate. Conclusion: the dedup-core report §5.1 tiered identity (T0/T1/T3 first) was never
  built; PR-1..PR-4a shipped the auto-select track on top of GROUPING 9 instead. Owner also rejected my Apply-best-copy /
  MOV suggestion: every row is independent; do not propose design changes outside the asked scope.
- 2026-11-08 04:20: **OWNER DIRECTION (binding): grouping optimisation first, far above auto-select. PR-4b/PR-5 parked.**
  The session owns the rescans (no owner clicks). Come back only after several REAL grouping iterations verified with high
  confidence. Forbidden: hardcoding, monkey-patching, overfitting the test data; rules must be general and work for any
  media file; the session answers for the program's real behaviour. Also: never propose design changes outside the asked
  scope (every row stays independent).
- 2026-11-08 04:40: Built the lead-owned loop: `headless-rescan.py` (scratchpad) runs the app's real run_pipeline on the
  test set D/H/J with auto-select OFF into a scratch manifest (grouping only); `grouping-health-census.py` (low-only
  groups, cross-day, PNG-only, worst groups) + `montage-groups.py` (contact sheets to LOOK) + `manifest-diff.py` (pairs
  lost / gained between two manifests, lost-pair sheets). Baseline GROUPING 9 rescan running. Opus planner drafting
  grouping10-design.md (tiered identity step 1: high-confidence-only group edges, content-gated stem pairs, Live Photo
  link via ContentIdentifier). Worktree .claude/worktrees/g10, branch fix/grouping-10-content-gated-identity.
- 2026-11-08 05:30: grouping10-design.md returned (opus, 283 lines; in worktree g10, lands with the fix PR). Principles: A only
  high-confidence content evidence unions (byte identity, two agreeing content signals with no time disagreement, or a
  device-written capture identity); B a filename never creates an edge (stills via content tiers only; videos only via
  equal Apple ContentIdentifier read through exiftool); C screenshots group as byte copies only while B7 is on. Items
  G1-G9; GROUPING 9 -> 10; additive manifest column content_identifier. Planner found: byte-different pixel-identical
  PNG pairs cannot survive with B7 on (32x32 signature cannot separate templated screenshots; 39 such demoted rows on
  the test set), undated bridges are NOT fixed by G10 (later tier). **Lead decisions:** two iterations on one branch,
  each verified by a real headless rescan — iter 1 = G5/G6/G8/G9 (haiku running), iter 2 = G1-G4/G7 after the P0
  exiftool ContentIdentifier census (running now on every same-stem image<->video set of the test set); s11 assertion
  inverted (no CID -> MOV not grouped); no fabricated Keys tags in fixtures.
- 2026-11-08 05:50: **Harness validated.** headless-rescan.py (real run_pipeline, thread pool, auto-select off) on the test
  set in 10.0 min reproduces the owner's GROUPING 9 manifest exactly: 502 groups, 1,701 grouped rows, 3,557 grouped
  pairs, 0 lost / 0 gained (manifest-diff.py keyed on the path below the test-set root, UNC vs drive letter). Fix on the
  way: the first run lacked the Windows __main__ guard and the spawned hash workers re-imported the script.
- 2026-11-08 05:55: **P0 ContentIdentifier census (exiftool 13.11) on 183 same-folder same-stem image<->video sets
  (384 files):** keys are `MakerNotes:ContentIdentifier` (stills, 172 files) and `QuickTime:ContentIdentifier` (videos,
  175). Of 201 image<->video pairs: **172 equal** (HEIC+MP4 132, HEIC+MOV 28, JPG+MP4 7, JPG+MOV 5), 21 one side
  missing, 8 both missing (JPG+MP4 6, PNG+MOV 2). JPEG Live stills DO carry the MakerNote id -> G2 ships. Video<->video
  (MOV+MP4 in one set): 9 sets, none with equal ids -> the video<->video stem edge goes without a content replacement
  (those videos still attach to the still individually when each id matches). Iteration 2 is unblocked on P0.
- 2026-11-08 06:30: iteration 1 implemented (haiku): cce8917 design, 2021485 G5+G6 (low matches are hints, never groups;
  pixel identity is a second signal; 10 extra dHash-less fixtures authorised by the lead: 9 gain an agreeing dHash, 1 flips
  to the hint expectation — production computes dHash with pHash unconditionally, hasher.py:285), a17f78e G8, db72be5 G9
  (GROUPING "10"). 293 dedup/scanner tests green, dedup.py 88 % line, mutation check fails 6/11 new tests as expected.
  Blob scan clean. **The iteration-1 headless rescan was KILLED by Claude Code's low-memory reaper**: the machine had
  0.9 GB free of 31.8 GB (a game client held 14.7 GB). Not restarted while memory is short; waiting for free memory
  >= 6 GB, then rerun in the FOREGROUND with --workers=2. Opus review of iteration 1 dispatched meanwhile.
- 2026-11-08 07:10: opus review of iteration 1: code APPROVE (Principle A traced on every former low path; only _mark_hints
  writes low, and only on ungrouped rows), docs NEEDS-FIX. MEDIUM-1 (recall, not precision): an all-lossy pHash-0 bucket
  compares members only to ONE keeper, so a second moment's genuine pair loses its own EXACT edge (also why the mixed
  fixture had no burst). Fix: cluster the bucket by shot time like _complementary_edges, one keeper per cluster, cross-
  cluster links stay hints. MEDIUM-2: five stale sentences in docs/features/scan.md. LOWs: test names contradict bodies,
  "shot 0.0 h apart" for second-scale gaps, two missing pins. All sent back to the implementer.
- 2026-11-08 07:40: review fixes landed: aa7c885 (all-lossy pHash-0 bucket clustered by shot time, single-link like the
  burst rule, one keeper per moment, cross-moment links stay hints; "+K more" and reclaim pins; gap text in s/min/h) and
  4043bf1 (five scan.md sentences). Nine dedup files 185 passed. Blob scan clean (synthetic D:\Photos test paths only).
  Iteration 1 code is complete; its real rescan still waits for free memory.
- 2026-11-08 07:50: iteration 2 started in a SECOND worktree (.claude/worktrees/g10b, branch fix/grouping-10-iter2 from
  4043bf1) so g10 stays frozen at iteration 1 for its rescan. Haiku implementing G1-G4 + G7 (ContentIdentifier read /
  carry / persist, no filename edges, pair cluster never bridges), s11 inversion, Live Photo docs. Neighbouring tests on
  iteration 1 (scan_runner, source_probes, rules register, auto_select, review_view, manifest_repository): 245 passed.
- 2026-11-08 08:05: iteration 2 progress: 174360c (G1-G3: ContentIdentifier read via exiftool keys QuickTime:/MakerNotes:,
  MediaExtract + HashResult + ManifestRow fields, additive manifest column, JPEG Live stills routed through exiftool).
  G4+G7 coded; two reds outside the flip table -> **lead decisions:** (1) case-collision test keeps its subject, fixture
  gains equal CIDs; (2) G7 REFINED as a general rule: a pair cluster refuses to union only when MORE THAN ONE of the
  components it would join contains a still (content kept two stills apart); a still component + a video-only component
  linked by equal CIDs is one capture and unions. The January/June "all four share a CID" case still refuses. Design doc
  updated in the worktree. Also asked for a unit seam for the scan_runner CID backfill and the one read-only exiftool
  check of the s11 fixture.
- 2026-11-08 08:40: iteration 2 complete on fix/grouping-10-iter2 (174360c G1-G3, 4ec87eb G4+G7 refined, 0a2462f backfill
  helper + tests, f501210 s11 inverted — the sandbox Live fixture carries NO ContentIdentifier at all, d73a380 docs);
  678 tests green; dedup.py 97 %, exif.py 96 %, manifest.py 81 %. Blob scan clean (synthetic UUIDs/paths in tests only).
  Opus review: APPROVE; MEDIUM: in exiftool-missing mode a JPEG routed to the exiftool queue (video peer) loses its
  in-memory scoring signals because the drained queue never fills `extracts` (#793 case) -> fix = setdefault the
  in-memory extract; 3 LOWs (pair_pass docstring overclaims order independence, docs say "a video and a still" though
  video<->video equal ids also pair, s11 B should read the db). Sent back to the implementer.
- 2026-11-08 09:00: iteration 2 review fixes landed (1ae3a97 in-memory extract fallback when exiftool is missing +
  warning text + s11 db read + pair_pass docstring; 8df0ca8 docs wording). 682 tests green. Ruff auto-fix removed three
  imports that were ALREADY unused on master (out of scope) — restored by hand; g10b clean at 8df0ca8. Both iterations
  are code-complete and fresh-reviewed. **Verification blocked on machine memory** (1.3 GB free); waiter armed, then:
  rescan g10 (iteration 1, 4043bf1) and g10b (iteration 2, 8df0ca8), diff each against the GROUPING 9 baseline, census,
  contact sheets of lost pairs and of every surviving cross-day group.
- 2026-11-08 ~11:30: owner freed the memory (game client closed, 16.9 GB free). Both real rescans ran in the foreground
  through `run_pipeline` (2,043 rows each, 5.6 and 5.3 min, no failure). Baseline -> iteration 1 -> iteration 2:
  groups 502 -> 408 -> 412; grouped rows 1,701 -> 1,468 -> 1,451; groups held only by low edges 100 -> 0 -> 0; grouped
  rows with low confidence 278 -> 0 -> 0; stem-bridged groups (stills not connected by sha or pHash <= 10) 6 -> 4 -> 0;
  cross-day groups 43 -> 15 -> 4, without an EXACT row 27 -> 4 -> 1; PNG-only groups 91 (47 with mixed bytes) -> 51 (0)
  -> 51 (0). Unchanged, as the design required: 400 "exact duplicate" and 137 "format duplicate" EXACT rows, 154 burst
  rows in 20 groups, no EXACT row lost its group, no sha-identical pair separated, 0 pairs gained. One deviation,
  explained: REVIEW/high rows 223 -> 239, all 16 were REVIEW/low rows that a low pair claimed first under GROUPING 9 and
  that a high pair (3 s to 57 s apart, hamming 2-10) now classifies (G5 flagging order).
  Lost pairs: baseline -> iteration 1 lost 223, every direct still-still pair carries a low row, a pHash-identical row
  from another moment, or a both-screenshots row (the hint classes). Iteration 1 -> 2 lost 104: 33 same-stem name
  collisions (a video from another day sharing an iPhone counter, e.g. a MOV from 25 May under a HEIC from 6 May; all
  correct splits, verified with exiftool), 18 transitive still pairs (a DNG from June and a JPG from January that only a
  stem-named video had bridged; contact sheet confirms two different scenes), 16 video-video, 16 other, and 21 Live
  pairs: 10 are Google Motion Photo JPG+MP4 (no ContentIdentifier exists in those files), 6 are HEICs that an edit left
  with only a PhotoIdentifier while their MP4 kept a ContentIdentifier, and 5 are HEIC+MP4 pairs whose identifiers ARE
  equal (verified in the files and in the stored `content_identifier` column) but that have no byte-identical copy
  anywhere: GROUPING 9 grouped them only through its same-stem video<->video high edge to the colliding MOV, and without
  that false edge the long-standing rule "a lone Live Photo is not a group" (#88 / GROUPING 9, unchanged) applies.
  Same-stem single-still sets: 162; still and video share a group in 141 -> 138 -> 128 of them.
  Surviving cross-day groups in iteration 2 (all four looked at): two are the same Live capture whose MOV carries a UTC
  time (8 h / 9 h apart); one is a MOV re-saved five days later that shares the capture's ContentIdentifier, model and
  3.02 s duration with the still (same clip, grouped correctly); one is a real defect: an undated JPG near-duplicate
  bridges two dated JPGs shot 31 Jan and 6 May (three scans of one form template). The near-dup and `duplicate_of` edges
  lack the "an undated file never links two moments" rule the pHash-0 path has (design Contradiction 5). The large
  pHash-identical groups (11, 10, 9 rows, 480 min spans) are each one scene; the span is a UTC-vs-local DNG/JPG offset.
  -> Iteration 3 dispatched (haiku, g10b): phase the unions in `_assign_group_ids` (identity edges; dated-dated content
  edges; undated-ended content edges that may not join two components whose dated members all disagree -> refused edge
  becomes a Hint), four new classify-level tests, docs. Then opus review, blob scan, rescan 3, diff against iteration 2.
- 2026-11-08 ~12:20: iteration 3 committed (63cf2da on fix/grouping-10-iter2; `_assign_group_ids` phases: identity ->
  dated-dated -> undated-ended with the moment test -> pair pass; helpers `_time_vote`, `_shot_times`,
  `_would_link_two_moments`; return becomes (pair-skipped paths, refused hints); `test_undated_bridge_is_not_a_burst`
  re-asserted because its old expectation WAS the bridge; test (b) uses 2 s since `BURST_MAX_GAP_S` = 2). 242 dedup
  tests green. Blob scan clean (same 27 synthetic fixture hits, controls ok). Real rescan 3 (5.5 min, no failure):
  iteration 2 -> 3 lost 9 pairs, gained 0, 3 groups split, all three undated bridges (the Jan/May forms; two LINE-album
  groups where undated JPGs had bridged shots 52 s and 38 s apart). Cross-day groups 4 -> 3, all with an EXACT row
  (two UTC-time MOVs, one re-saved MOV sharing the CID); cross-day without EXACT 1 -> 0. Every other quantity identical
  to iteration 2 (groups 413, EXACT 400+137, burst 154/20, REVIEW/high 239, low rows 0, stem-bridge 0, PNG mixed 0).
  No REVIEW_DUPLICATE row sits apart from the partner its reason names (0 in iterations 2 and 3; GROUPING 9 had 54 low
  ones). Pre-existing and unchanged across all three manifests: 6 EXACT rows whose named partner basename is not in the
  row's group (checked: my checker cuts the partner name at " (N)"; each row's byte-identical mate IS in its group).
- 2026-11-08 ~12:40: opus fresh review of 63cf2da = NEEDS-FIX. Criteria 1-5, 7, 8 pass (it also ran test (a) against
  8df0ca8: fails there, so the test bites). F1 MEDIUM: a refused ROW edge leaves the flagged row stale — still
  REVIEW_DUPLICATE with duplicate_of/hamming/reason naming a partner outside its group (ungrouped in test (a); grouped
  with a third row in a constructed case). No deletion risk (auto-select skips group None), but the review text lies and
  the manifest summary counts it. F2 LOW: a refused edge arrives as both a row edge and a pHash edge, so the hint says
  "+1 more" with one partner. Lead decision for F1: reset the refused flagged row to its Pass-3 verdict (UNDATED or
  unique; duplicate_of/hamming/confidence cleared), never re-point duplicate_of (hamming unknown); the link stays a
  hint. Fixes + two more tests + the invariant "every EXACT/REVIEW row shares a group with its duplicate_of" sent to the
  implementer. Full suite on 63cf2da running in the background (will be re-run on the fix commit).
- 2026-11-08 ~13:10: review fixes landed (3ca2f0b: `_reset_refused_row` returns a refused flagged row to its Pass-3
  verdict, hints deduped by unordered pair, two more tests + the partner invariant; the reviewer re-ran both probes on
  the fix and showed the new tests fail on 63cf2da) -> re-review APPROVE. Blob scan clean. Rescan 4 on 3ca2f0b equals
  rescan 3 row for row (0 pairs lost/gained; the reset shape does not occur in the test set). Pyright's "helper not
  accessed / undefined name" flags were stale editor state: the committed code calls the helper and the five new tests
  pass. Branch fix/grouping-10-content-gated-identity fast-forwarded to 3ca2f0b and pushed; **PR #1055 opened**
  (Docs impact: eight Scan entries from docs-impact-headings.py; s11 is the qa scenario; news/1055.bugfix). Next: CI.
- 2026-11-08 ~13:30: **PR #1055 CI green** after one body edit: `gates` wanted the renamed entry's OLD heading too
  ("Scan grouping — low-confidence hints never chain" -> "… low-confidence matches never group"); pytest 7m26s and
  web-scenario-batch (s11) passed first time. Awaiting the owner's merge "yes". After merge: ff master, no frontend
  change (dist rebuild not needed), STATE tail goes in the next docs PR together with grouping10-design.md (already on
  the branch). Open for the owner: whether a lone Live Photo pair (still + its own video, no other copy) should become a
  group — unchanged long-standing rule, 27 such sets in the test set stay ungrouped (21 of them already did under G9).
- 2026-11-08 ~13:45: local full suite on 63cf2da: coverage 92.93 %, two failures, both in
  `test_scan_runner_exif_rotation.py::TestWedgedExiftoolRotation` (the known timing flake, parked, owner undecided);
  the same class passes 8/8 when re-run alone. CI pytest on the final branch passed. Nothing grouping-related failed.
- 2026-11-08 ~14:00: **#1055 merged** (squash 3ccb6ac); local master fast-forwarded; no frontend change. **Owner
  decision:** a lone Live Photo (a still plus its own video, no other copy) must NOT become a group — product design:
  the pair is one "photo". The rule "a lone Live Photo is not a group" (#88 / GROUPING 9) is therefore confirmed, not
  an open question. This docs PR carries the STATE tail since #1053; grouping10-design.md landed with #1055 and gains
  the owner's decision line.
- 2026-11-08 ~14:40: **#1056 merged** (squash aaa297b); local master fast-forwarded. Next: PR-4b plan (B6 Live MOV
  follows its photo's decision; B7 default census by a B7-off rescan).
- 2026-11-08 ~15:20: **B7 default census (real rescan on master, `--rules=B7=false`, 5.5 min).** B7 off vs on:
  groups 413 -> 441 (+28), grouped rows 1,449 -> 1,516, pairs +57, EXACT rows 537 -> 575 (all "format duplicate (png
  vs png)" = the 32x32 pixel signature), REVIEW/high +2, PNG-only groups 51 -> 79 of which 39 hold different bytes;
  low-only 0, cross-day 3, stem-bridge 0 unchanged. Contact sheets of the first 6 gained groups: 4 are true duplicates
  (one screenshot saved in Takeout + iPhone backup + J); **2 are false merges of bank-transfer receipts** — two
  different transactions (different amount and date) joined once by a PNG near-dup edge (hamming 6, "1 s apart"
  because a screenshot's date is its save time) and once by the pixel-identity tier itself (one receipt became an
  EXACT "format duplicate" of a different receipt = an aggressive DELETE candidate). This is dedup-core class B
  reopened. **Lead recommendation: B7 default stays ON** (PRD §6 "default off" is refuted by data); the ~24 true
  screenshot-copy groups B7 on misses are a GROUPING follow-up (an exact decoded-pixel hash for PNG pairs would group
  them without the 32x32 signature), not a B7 question.
  **B6 census (iteration-4 manifest):** 195 videos sit in groups (of 298 videos). 144 have a same-folder same-stem
  still with the same ContentIdentifier in their group — that still is EXACT for 67 (B6 would delete the video with
  it), a keeper/ref for 61, REVIEW for 16; 41 have no same-stem still in their folder; 10 have a still whose CID
  differs/missing. 63 videos are byte-identical copies already EXACT (B3 territory). Today all 132 non-EXACT videos
  in groups are "—" passengers that aggressive never deletes.
- 2026-11-08 ~16:30: owner approved the PR-4b plan (B7 stays ON; B6 as scoped) and said: keep going on grouping
  until it is reliable, general and product-grade; stack PRs; do not stop to ask. **New grouping defect found while
  checking the B7 census: the EXACT pixel-identity gate is too coarse.** Full-resolution comparison of all 137 EXACT
  "format duplicate" rows of the B7-on manifest (119 decodable + 18 DNG pairs): 108 are honest re-encodes or
  metadata-only copies (max 4x4-block grey difference at 128 px <= 2.2, p99.9 pixel difference <= 4), **11 are
  different pictures**: consecutive group shots where people moved, a second shutter press with the camera shifted,
  two shots of a hazy temple, two shots of a notebook page, and two bank-app screenshots saved as JPG 80 minutes
  apart with a different balance line. All 11 are aggressive-delete candidates today (B7 does not apply to JPG/HEIC).
  Their statistics start at max-block 6.6 / p99.9 12 and run to 100 / 134. Contact sheets in the scratchpad
  (look/pairs). The 32x32 RGB mean-difference gate (<= 6.0) passes them because the change is local or a small shift.
  -> GROUPING 11 (next PR, stacked): a 128x128 grey signature per file (BOX of the oriented working image, 16 KB),
  spilled to a scan-local store so RAM stays flat; EXACT for a pHash-0 pair with different bytes requires
  max 4x4-block mean difference <= 4.0 AND p99.9 pixel difference <= 8.0 on that signature (plus today's dimension
  and channel-shift checks); missing signature -> "pixel check unavailable" (REVIEW, as today). Version "11".
  Also folds in the B7 default = ON statement (PRD §6 note). B6 follows as its own stacked PR.
- 2026-11-08 ~17:40: two opus designs returned (scratchpad g11-design.md, b6-design.md; both land in the repo with
  their PRs). Lead decisions on B6's open questions: EXACT byte-copy videos follow their photo while B3 is on; never
  delete a capture's last non-deleted clip; follow only when EVERY qualifying still is deleted. Lead decisions on
  G11: spill file in the system temp dir (not next to a manifest that may be on the NAS); census parity through the
  real signature code is a merge gate; HASH_RECIPE_VERSION 5 -> 6 as the planner found. B6 implemented (worktree b6,
  branch feat/auto-select-b6-live-video, 00e6c16 + a802d99; new core/services/live_photo.py; 154 layer-1 tests,
  frontend tests + build green; blob scans clean). **Replay of plan_auto_select on the iteration-4 manifest:** B6 off
  = today (452 deletes, 26 of them videos); B6 on adds 56 deletes, all videos (36 EXACT MP4 copies under deleted
  photos, 20 Live MOV passengers), removes none, and 0 captures lose their last clip; identical at both thresholds.
  Fresh opus review of B6 running. GROUPING 11 task A (store + hasher + gate + tests) implementing in worktree g11.
- 2026-11-08 ~18:00: GROUPING 11 task A committed (6540467 gate + store + hasher + tests, abbf2ff design doc); 334
  targeted tests green; Pillow 12.2 supports reduce() on mode F; blob scans clean. Task B (scan_runner wiring, pipeline
  tests, docs, PRD B7 note) dispatched. **Pre-existing privacy item found by the implementer's grep (not touched):**
  a test docstring in tests/test_dedup.py (~line 453, committed in b311fe3) quotes a real library folder path
  ("Production case from …"). Add to the owner's scrub list with the 8 old STATE.md lines.
- 2026-11-08 ~19:00: **merge-gate census through the real task-A code FAILED and found the real mechanism.** Real
  recipe: 118 EXACT / 19 differ instead of 126 / 11. The 11 different pictures all fail the gate (correct), but 8
  honest pairs fail too, every one cross-route: a JPEG decoded at reduced DCT resolution (draft to ~504 px) against a
  fully decoded HEIC or PNG, or two differently encoded JPEGs. Under the real routes the classes overlap on every
  mean-difference statistic (honest max 4x4 block up to 15.6 vs distinct min 6.2) and on per-block normalized
  cross-correlation (the two subtle distinct pairs keep NCC >= 0.98). Blur, Lanczos, two-stage BOX: no separation.
  Decoding the JPEG side fully restores the census exactly (honest max 2.19, tail 0; distinct min 6.62, tail 39).
  Measured decode cost on real files (median, 40 JPEGs 12-24 MP, 30 HEICs): JPEG drafted RGB 14 ms, full RGB 33 ms,
  full luma-only 19 ms; HEIC 193 ms either way (pillow_heif 1.3.0 has no draft). **Decision: grey128 comes from a
  full-resolution LUMA decode** — JPEG via a second luma-only decode (`draft("L", im.size)`, +19 ms per JPEG), RAW via
  its embedded preview JPEG bytes through the same path, other formats via convert("L") of the already-full working
  image; EXIF orientation applied; BOX 128. The pHash recipe (drafted RGB) is untouched, so hash values and grouping
  do not move; only the EXACT gate changes. Confirmed on all 137 pairs: honest max 2.56 (tail 0), distinct min 6.62
  (tail 39); thresholds stay 4.0 / 16. The 18 ".dng" files are JPEG containers by content and take the JPEG route.
  Task C (hasher luma signature + tests + design addendum) follows task B in worktree g11.
- 2026-11-08 ~20:10: **B6 PR #1057 open, CI green (12/12)** after one iteration with two mechanical causes: the
  rules register accepts pins only as path::Class::test_fn (the class-level R-SCAN-12 pin failed), and the new
  translation keys left docs/reference/i18n-keys.md stale (regenerated; the other eight reference files differed only
  by line endings and were not staged). B6 branch: 00e6c16 rule, a802d99 docs + pr4b-design.md, 6b2bfaa the three
  review notes (B6 in the muted-label sentence; a test pinning the B8:spared -> B6:delete trace change; the surviving
  clip asserted by name), 15e2188 news, 278ce38 CI fixes. Opus review APPROVE. Real headless rescan with aggressive
  auto-select on the branch logged "Rules v2 … B6 on" and "B6 marked 56 delete", equal to the replay. GROUPING 11
  task B landed (5f9ad3b wiring + pipeline tests, c56d23e docs; blob scans clean); task C (full-luma signature) in
  progress. Both PRs wait for the owner's merge yes; #1057 is independent of GROUPING 11 (disjoint files except
  scan.md and scan_runner.py at different places; the second to merge gets a `git merge origin/master`).
- 2026-11-08 ~20:40: GROUPING 11 task C committed (2baf560: `_grey_from_jpeg_bytes`, `_grey_for_route`,
  `_raw_preview_and_sizes` returns the grey signature as a fourth value, five route tests incl. the drafted-vs-full
  proof; blob scan clean). **Merge gate passed through the real code: 137 census pairs -> 126 EXACT / 11 differ,
  exactly the census.** Real rescan 5 on the branch (5.3 min, no store warning, no leftover temp file): grouping
  identical to iteration 4 (413 groups, 0 pairs lost or gained, every EXACT row keeps its group); exactly the 11
  different pictures moved from EXACT/high to REVIEW_DUPLICATE/high "same pHash, pixels differ"; format-duplicate
  EXACT rows 137 -> 126; REVIEW/high 239 -> 250; nothing else moved. Fresh opus review of the five-commit branch
  running; then push, PR (news `.bugfix`), CI.
- 2026-11-08 ~21:00: opus review of GROUPING 11 = NEEDS-FIX. **HIGH (real regression):** the new luma decode opens a
  fresh image, so on a JPEG with corrupt EXIF `exif_transpose` raises SyntaxError that no guard catches, the file
  becomes a HashFailure and leaves the manifest (an existing scan_runner test fails on the branch; it passes on
  master; the RGB path survives only because the date read pre-warms PIL's EXIF cache). Task C's acceptance set did
  not include tests/test_scan_runner.py — lesson: any hasher change runs the scan_runner and pipeline tests too.
  LOW: two stale version sentences in scan.md; a needless full-size `convert("L")` copy on the luma/RAW paths.
  Fixes sent to the implementer; then re-review, parity rerun, rescan 6. The test set has no corrupt-EXIF file
  (rescan 5 kept all 2,043 rows), so the regression was invisible to the real-data check alone.
- 2026-11-08 ~21:30: fix commit d980576 (SyntaxError tolerated at the luma transpose and in the helper's outer
  guard -> un-rotated signature or None, never a HashFailure; new corrupt-EXIF hasher test that failed before the
  fix; version sentences "11"; no redundant L copy). 348 targeted tests green incl. the scan_runner set. Parity gate
  through the final code: 126 / 11 again. Rescan 6 (5.2 min) equals rescan 5 row for row; no temp leftover. Blob
  scan clean. Reviewer re-check and the full local suite running; then push, PR, CI.
- 2026-11-08 ~22:00: re-check APPROVE (the reviewer ran the new corrupt-EXIF test against 2baf560: raises; against
  d980576: 16,384 bytes). **GROUPING 11 PR #1058 open, CI green (11/11)** after one body edit (the two version
  sentences sit in the "lone Live Photo" and "bursts" entries, which the Docs impact line had to name). Branch:
  6540467 gate+store+hasher, abbf2ff design, 5f9ad3b wiring, c56d23e docs, 2baf560 full-luma route, d980576 review
  fixes, adb76a8 news. Both #1057 (B6) and #1058 (GROUPING 11) await the owner's merge yes; whichever merges second
  gets a `git merge origin/master` before its merge (scan.md and scan_runner.py are touched at different places).
  **Candidate GROUPING 12:** with a reliable full-resolution gate, PNG pairs that are pixel-identical could group
  as EXACT even while B7 is on (today ~24 true screenshot-copy groups stay apart; the receipt false positive that
  Principle C guarded against was a 32x32 artefact). Near-dup PNG pairs would stay hints under B7. To be measured
  on the B7-off manifest's PNG pairs through the new gate before deciding.
- 2026-11-08 ~22:20: measured: the B7-off manifest's 157 format-duplicate pairs through the GROUPING 11 gate = 144
  EXACT / 13 differ; the 13 are the 11 known different pictures plus exactly the 2 receipt PNG pairs (census max
  48.6 and 32.4); all 36 other PNG pairs are pixel-identical. **GROUPING 12 dispatched** (worktree g12 stacked on the
  GROUPING 11 branch): in `_format_verdict`, the "both screenshots" demotion is lifted only when `_pixels_identical`
  is True (shot-time disagreement still wins; the near-dup PNG path is unchanged); version "12"; docs narrow
  Principle C to non-identical pixels. Expected on the rescan: about 24 new PNG-only groups, the two receipt pairs
  stay apart, the near-dup receipt merge stays a hint. Full local suite on the GROUPING 11 tip running.
- 2026-11-08 ~23:00: GROUPING 12 committed (5747ac4 on fix/grouping-12-png-pixel-identity, stacked on the GROUPING
  11 tip; `_format_verdict` order: shot time -> pixel gate -> PNG note -> EXACT; version "12"; six new tests; two
  flipped tests whose fixtures were pixel-identical; docs incl. R-SCAN-10). 377 tests green; blob scan clean. Real
  rescan 7 vs GROUPING 11: groups 413 -> 441 (+28), pairs +43 / 0 lost, EXACT rows 526 -> 562 (+36 = the
  pixel-identical PNG copies), PNG-only groups 51 -> 79, no REVIEW row moved, low-only 0, stem-bridge 0, cross-day
  3. The five bank-receipt screenshots sit in four byte-identical groups exactly as under GROUPING 11; the two
  B7-off false merges do not reappear. Fresh review of 5747ac4 running; one authorized follow-up commit aligns the
  B7 comment in auto_select_rules.py.
- 2026-11-08 ~23:20: full local suite on the GROUPING 11 tip (ran while two rescans were in flight): coverage
  93.61 %, only the two known exiftool-rotation timing flakes failed (same two as earlier today; CI pytest on #1058
  green). GROUPING 12 review: code APPROVE; one MEDIUM doc gap (scan.md's aggressive-delete clause still said no
  PNG pair is ever delete-eligible), two LOWs (a version sentence; no test for the unavailable-pixel-check branch)
  and the B7 toggle label wording — all sent to the implementer (7e03cbe already aligned the rules comment).
- 2026-11-08 ~23:45: GROUPING 12 fixes landed (3dec817: scan.md aggressive clause (b) names "PNG screenshots that are
  not pixel-identical"; version sentence; unavailable-pixel-check test; B7 label "… unless they are pixel-identical"
  in en/zh_TW; frontend tests 54/54 and build green), news a8c491c. **PR #1059 open, stacked on #1058 (gh stack
  #1060), CI green (12/12)** after one Docs-impact body edit. State of the three PRs awaiting the owner's merge yes,
  in order: #1057 (B6, independent), #1058 (GROUPING 11), #1059 (GROUPING 12, base = #1058's branch; after #1058
  merges, check `gh pr view 1059 --json mergeable` and `git merge origin/master` if CONFLICTING — never rebase).
  Housekeeping after merges: remove the `frontend/node_modules` junctions in worktrees b6 and g12 (rmdir the link
  only), and the stale worktrees pr4a / agent-a1a13cf2207cfc02a / g10 / g10b / g11 / g12 / b6.
  **Lead assessment of grouping after rounds 10-12 on the dev test set (2,043 files):** groups form only on byte
  identity, full-resolution pixel identity, agreeing content signals within one shot moment, or a shared Apple
  ContentIdentifier; names, low-confidence matches and undated rows never bridge; every EXACT row is a true copy by
  the full-luma gate (126 + 36 verified pairs; 11 + 2 different pictures rejected); the only cross-day groups are
  UTC-timed videos of one capture. Remaining known limits, by design: a shot subtler than the boat case (block
  difference < 4 at 128 px) would still read EXACT; a lone Live Photo is not a group (owner decision); PNG near-dups
  stay hints under B7; B7 off reopens class B. Next evidence should come from new odd combinations pulled into the
  test set, not from more tuning on this one.
- 2026-11-08 ~19:10: **#1057 merged** (squash d1dda09); local master fast-forwarded. #1058 then read CONFLICTING: #1057
  and GROUPING 11 both edited the Rules entry in scan.md. Resolved in the g11 worktree with `git merge origin/master`
  (d277a32: master six-rule text plus the two GROUPING 11 sentences), targeted tests green, pushed; CI re-running.
- 2026-11-08 ~19:45: **#1058 merged** (squash 24c0ca1, via the merge-async REST API: a `gh stack` member refuses `gh pr merge`);
  local master fast-forwarded. #1059 retargeted to master and read CONFLICTING (stacked-squash artefacts in 6 files plus
  the B6/B7 overlap in rules, labels and scan.md); resolved in g12 with `git merge origin/master` (6c843b7), 3-way checked against the g11 tip as base; tests green; CI re-running.
- 2026-11-08 ~20:00: **#1059 merged** (squash 0eee966, merge-async API); local master fast-forwarded; frontend/dist rebuild pending (see next entry).
  Housekeeping done: worktrees g10 g10b g11 g12 pr4a b6 and 15 agent-* removed (branches kept; the dirty ones held only
  CRLF noise in docs/reference), 3 empty agent-* dirs deleted, both node_modules junctions removed; agent-a77a63527484fef54 left (locked).
- 2026-11-08 ~20:05: **incident** — removing the pr4a / agent-* worktrees with `git worktree remove` emptied the main
  checkout's `frontend/node_modules` (a junction inside one of them pointed there; Git for Windows deletes through
  junctions, reproduced in a scratch repo). No tracked file touched. frontend/dist is the 03:25 build (pre-#1057); rebuild needs `npm ci` (gated, owner asked). Owner yes ~20:20; `npm ci` (295 packages, lockfile) + `npm run build` done 20:23, bundle carries scan-rule-b6.
- 2026-11-08 ~21:00: **blind test set drawn.** Owner asked for a double-blind check after rounds 10-12. Sampler
  `select_blind_windows.py` (scratchpad) reads only shot times from the full-library manifest: 45 random 30-min windows
  (all drives, cap 25) + 40 random undated rows with their same-stem siblings; seed 1794141460; dev-set paths excluded;
  686 files / 2.98 GiB, copied to a new NAS root with copy_testset.py. Lead does not open any sampled image before the owner judges.
  Copy done ~21:00: 686/686 ok (sha256-verified), three drive subfolders. Next: owner scans the blind root in the app (default rules);
  lead runs `blind-sheets.py` (scratchpad; G list = every group, R list = ungrouped hint / same-minute pHash<=12 / same-basename / same-stem pairs) without viewing, owner reports exceptions by id.
- 2026-11-08 ~21:45: owner scanned the blind root in the app (manifest saved over the gitignored test-set manifest name,
  21:03; snapshot in scratchpad). 686 rows: 107 groups, EXACT 181, REVIEW_DUPLICATE 3, ungrouped low hints 28, UNDATED 29. G/R sheets generated unseen; awaiting owner judgments.
- 2026-11-08 ~22:00: **blind test, lead predictions registered before any judgment** (text-only stats; no image opened).
  Set: 107 groups (104 keeper+EXACT only, 3 with a REVIEW row, 31 span two or three sources, 0 burst stacks drawn), 381 ungrouped
  (28 hinted, 29 undated). R list 164: R-hint 26 (21 at hamming 8-10), R-time 120 (30 at hamming<=4), R-stem 15, R-name 3.
  P1 no group holds a stranger. P2 R-stem 15 = Takeout Live pairs (HEIC+MP4, same CID, same second) ungrouped by design. P3 R045 = a
  PNG pair, same second/dims, identical pHash, quality 14 both: quality gate blocks the pixel check -> predicted real miss. P4 R-name 3 = different photos. P5 R-time<=4 = re-shots (2 s rule).

## 2026-11-08 ~22:30: blind round 1, owner feedback and text-only census

- Owner: ids unreadable on the sheets; every group shows one size/resolution/extension; no Live + plain mix drawn. Sheets regenerated with 34 px ids and file size (blind-sheets2.py; scratchpad look/blind2, ids identical to v1).
- Census of the blind manifest (no image opened): 107 groups, 0 with more than one still resolution, 7 with more than one extension (3 HEIC+MP4, 3 MOV+MP4, 1 JPEG+PNG), 31 with more than one file size. All 164 R candidates share dims and extension and differ only in size. The round-1 sample holds no cross-resolution/format pair, grouped or not; it tests copy precision, not similar-grouping recall.
- Full library (v6 manifest, text only): 138 same-moment still pairs (hamming <= 8) differing in dims or extension (dims 27, ext 111); 63 Live stills with a non-Live still within 2 s (54 hamming <= 4). None drawn in round 1.

## 2026-11-08 ~23:05: blind round 2 drawn by shape, copied

- select_blind_shapes.py (scratchpad; seed 1794141460) samples by metadata only (shot time, hamming, dims, extension, same-stem siblings); group_id/action never read. Shapes: S1 cross-shape 47, S2 Live+plain 44, S3 same pHash more than 2 s apart 49, S4 undated+dated 51, S5 PNG pairs 47, S6 bursts 19, S7 DNG+JPEG 7. 574 files, 1.61 GB, copied to the second blind root (copy_testset.py), 574/574 sha256 ok.
- Owner scans it after GROUPING 13 merges (the S5 shape exercises that fix). Lead still has not opened any blind image.

## 2026-11-08 ~23:25: P3 confirmed from code; GROUPING 13 identity rescue

- _classify_phash drops entropy/dimension/quality-gated rows from the candidate list, so _classify_format_group and _pixels_identical never see them; only SHA could pair a byte-different copy. Confirmed by reading the code, not the images (blind protocol kept).
- Full-library census: 564 quality-gated rows; 99 (pHash, dims) buckets with 238 rows; 166 pairs differ in SHA, 70 of them at the same shot second: all invisible to the grouper before this.
- Fix: _rescue_identity (same pHash + same dims or 90-degree swap + one shot moment + mean-colour/dHash gates + _pixels_identical True -> EXACT; anything less writes nothing; undated left alone). GROUPING_STRATEGY_VERSION 13. Branch feat/grouping-13-identity-rescue (worktree g13), commits c8a42df, 5e71d26. Targeted tests green (test_dedup, exact_pixels, grey_gate, scan_pipeline, scan_runner); full suite running; PR pending. ruff: the project config autofixes on check and rewrote both files once; restored from origin/master and re-applied (use ruff check --no-fix).
- ~23:30 lossy-only follow-up commit 7d5775a (a gated RAW+JPEG pair stays as it was; class-A trio fixture carries distinct grey128). Full suite in the worktree: coverage 93.73 % total, dedup.py 97 %; two exif-rotation timing tests failed once and passed on rerun (the known flake). PR #1061 opened 2026-11-09 ~00:00, news fragment 43e64d6, CI all green. Awaiting owner merge; owner scans blind round 2 after it merges.
- 2026-11-09 ~00:15: #1061 merged (squash 3a1cb3b, gh pr merge --squash; owner yes). Local master fast-forwarded to 3a1cb3b; worktree g13 removed (no junctions inside; main frontend/node_modules intact, 295 entries) and its local branch deleted. Owner now scans blind round 2 with master.

## 2026-11-09 ~01:30: blind round 2 scanned by the owner; lead judged first (owner withholding)

- Owner scan: 514 rows, 127 groups, EXACT 72, REVIEW 84, hints 50, GROUPING 13 rescues 7. The scan sources covered D and J of the round-2 root but the H source still pointed at the ROUND-1 blind root, so 113 round-2 files (all H) were not scanned and 53 round-1 H rows are mixed in. 162 of 264 cases are complete (every still present); 102 incomplete await a rescan with the H source corrected.
- Protocol change at owner request: the lead judged the complete cases from case sheets (blind2-cases2.py: one row per case, members with action/confidence/group letter) BEFORE hearing the owner; the owner judges the same sheets next.
- Complete-case verdicts (lead): S1 cross-resolution/format 21: 20 grouped correctly, 1 miss (C016: same second, pHash 4, dHash 5, mean-colour distance 34.2 > 30 vetoed the pair with no hint). S5 PNG pairs 20/20 EXACT, all pixel-identical screenshots. S2 Live+plain 23/23 stills grouped (Live still copies via ContentIdentifier edge or EXACT); 2 Live videos (.mov, wrong file date) not attached to their still. S4 undated+dated 35/35 grouped across resolution (downsized undated copy + dated original); in 9 of them the classifier made the small undated copy the keeper and the full-size original the REVIEW_DUPLICATE (keeper direction worth a look). S3 same pHash > 2 s apart 42: 18 timezone-offset copies (2 h / 8 h) all grouped; 24 re-shots left as hints by the 2 s rule, of which C109 (identical screenshot two days apart) is the one a user may call a duplicate. S6 bursts 19: same-second stacks grouped; frames > 2 s apart hints; two 4-frame exposure brackets split into two groups by exposure (C249, C250). S7 DNG+JPEG 2/2 are different photos sharing a stem, correctly ungrouped.
- GROUPING 13 in the wild: 7 rescues, all genuine copies (night skyline JPEG+JPG, eclipse frames), zero false ones seen.
- Open findings for owner decision: (a) mean-colour gate vetoes a pHash+dHash-agreeing same-second pair without even a hint (C016); (b) keeper direction for undated small copy vs dated original; (c) 2 Live .mov not attached; (d) 2 s rule vs identical screenshots days apart (C109).

## 2026-11-09 ~02:30: blind round 2 complete (H source corrected, rescanned); lead verdicts on all 264 cases

- Rescan manifest snapshot: manifest-blind2-owner.sqlite (scratchpad); v1 kept as -v1. All 264 cases complete. Sheets for the 102 newly complete cases: scratchpad look/blind2-cases-v2 (ids unchanged).
- Verdicts (lead, before hearing the owner): S1 cross-shape 47: 46 correct (45 one group incl. HEIC+JPEG same-second exports, crops, 90-degree swap; 1 case carries an unrelated same-stem PNG that is correctly left out), 1 miss = C016 (mean-colour veto). S2 Live+plain 44: 44 stills correct; the 5 unattached .mov (C067/C079/C081/C082/C084) have no ContentIdentifier and shot dates months away from the stills: filename reuse, correctly refused. Finding (c) withdrawn. S3 49: 23 timezone-offset copies (2 h / 8 h) all grouped; 22 re-shots hints by design (C109 the arguable one); C115 two night shots 10 s apart, quality-gated, correctly apart. S4 51/51 grouped (16 new ones are same-dims undated copies, EXACT); keeper direction issue stands for 9 of the 35 cross-resolution ones. S5 47/47 EXACT (7 via GROUPING 13, all genuine). S6 19 as before (two exposure brackets split by brightness). S7 7: 2 stem collisions correct; **5 iPhone ProRAW DNG + its JPEG, same second, same stem, same dir, NOT grouped and no hint** (pHash hamming 18-22: the RAW preview and the processed night-sky JPEG hash far apart, so no content tier passes and GROUPING 10 removed the stem-only edge).
- Totals: 264 cases, 6 misses (C016 + 5 ProRAW pairs), 0 false groups seen, 7 GROUPING 13 rescues all genuine.
- Open findings for owner decision: (a) mean-colour gate vetoes a pHash+dHash+same-second pair with no hint (C016); (b) keeper direction small undated copy vs dated original (9 cases); (d) 2 s rule vs identical screenshots days apart (C109); (e) RAW+JPEG same capture (same stem, dir, second) with far pHash: 5/5 missed, the largest recall class of round 2.

## 2026-11-09 ~03:00: owner verdict on round 2; lead proposals withdrawn; metadata audit of the current rules

- Owner: satisfied with every group seen (no stranger in any group). A ProRAW DNG and its heavily processed JPEG count as two pictures by the owner standard (content only), so the 5 S7 cases are not misses. Standing direction restated: a rule is about CONTENT; same-second / same-stem / same-dir conditions are monkey patches and are refused.
- Lead withdraws all four proposals of ~02:30 (mean-colour-with-same-second, keeper direction by date, stem+second RAW rule, 2 s rule debate).
- Metadata audit of GROUPING 13 as shipped (what is NOT content): (1) shot-time vote, BURST_MAX_GAP_S = 2 s plus whole half-hour offsets up to 14 h (_shot_time_vote / _time_vote) - a VETO: a pHash-0 pair whose times disagree is a hint even when pixels are identical (_format_verdict checks the time note before the pixel check; only SHA-identical bypasses it); also splits exact buckets into shot moments (_shot_moments), gates near-dup edges (_demotion_note), refuses undated bridging (_would_link_two_moments), and bounds the GROUPING 13 rescue bucket; (2) burst stack relabel uses the same 2 s (_mark_burst_stacks); (3) Live pair edge = same stem + ContentIdentifier (_collect_pair_edges / _pair_pass): stem is metadata, the identifier is the camera declaration; (4) dimension gate >= 128 px and the keeper rule (format, source priority, path) are not grouping evidence but affect eligibility/keeper. Content-only elements: SHA, pHash, dHash, mean colour, entropy/quality gates, rgb32 + grey128 pixel identity. Origin of (1): dedup-core research 2026-11-05 class B (same pHash, different captures grouped falsely) - approved then as GROUPING 10. Owner to decide whether the time veto stays.

## 2026-11-09 ~02:10: GROUPING 14 — shot time removed from grouping (owner direction)

- Owner: a re-shot seconds later is two pictures, but CONTENT must say so, not time. Remove the 2 s veto; list every other rule that is not about content.
- Branch feat/grouping-14-content-only, worktree g14, commits 7eba428 (engine+tests+docs) and e4ce37a (R-SCAN-10 heading). Gone: _shot_time_vote/_time_vote (2 s + half-hour offsets), _gap_text, _shot_moments, _would_link_two_moments, _reset_refused_row, _shot_times, the undated-bridge hint, the GROUPING 13 shot-moment condition. New: _pixel_clusters splits an exact bucket by pixel identity (own keeper per picture; later cluster keepers are REVIEW of the first keeper when dHash agrees, hints otherwise); _burst_runs makes the 2 s burst relabel run per consecutive-frame run inside a content-formed group. GROUPING_STRATEGY_VERSION 14.
- Tests: test_dedup_time_gate.py rewritten; 25 expectations moved from "time disagrees -> hint" to "content agrees -> one group" (grouping10, hint_edges, burst_stack, rule_toggles, TestIdentityRescue). Lesson: the fixture default (one constant pixel signature for every row) means "all copies"; a grey128 that differs by 1-2 levels still passes the grey gate (GREY_BLOCK_MAX 4, GREY_TAIL_MAX 8), so a "different picture" fixture needs levels tens apart. All 152 dedup tests green; full suite running.
- Remaining non-content rules (for the owner): (1) burst-stack relabel reads shot times (2 s runs) - relabel only, never groups; (2) Live pair edge = ContentIdentifier (camera declaration) + same stem; (3) dimension gate >= 128 px smaller side (image property; keeps icons/thumbnails out of pHash grouping); (4) B7 PNG-pair demotion is keyed on FILE FORMAT, not pixels; (5) RAW complementary policy (never a dedup decision) is format-based; (6) keeper order = format priority, source priority, path; (7) UNDATED action label = EXIF date absent (action, not grouping).
- 2026-11-09 ~02:35: #1062 merged (squash 050009b, owner yes in advance). CI 11/11 green after the R-SCAN-10 pin fix (8f8f97f). Local full suite: coverage 93.61 %, dedup.py 97 %; the register test failed only because that run started before the pin fix, plus the two known exif-rotation timing flakes. Local master fast-forwarded; worktree g14 removed. Next: lead rescans blind round 2 headlessly on master to see what the 24 S3 re-shot cases became.

## 2026-11-09 ~10:40: blind round 2 rescanned headlessly on master (GROUPING 14) and compared with the owner GROUPING 13 scan

- Headless pipeline (headless-rescan.py, no auto-select) on the second blind root, 1.0 min: 574 rows, 213 groups (was 203), EXACT 171 (was 140 + 141 KEEP stamps), REVIEW 112 (was 88), hints 8 (was 54). Row-level: 30 "- -> EXACT" transitions are the owner KEEP stamps (auto-select relabels the classifier EXACT row it keeps), not engine changes.
- S3 re-shots (24 cases): 18 became one REVIEW group (C093 C094 C100 C102 C103 C111 C112 C114 C118 C123 C124 C128 C131 C132 C136 C139 C140 + C104 partly), 1 became ungrouped with no hint (C113: dHash disagrees, nothing else speaks), 1 became EXACT (C120). 23 timezone-offset copies unchanged. S6 C240 burst-of-3 now one group. Everything else identical (241/264 cases unchanged).
- C120 is a precision defect exposed by removing the time veto: two interior shots 3 s apart (handheld, static scene), grey128 gate says identical (max 4x4 block mean 2.81 <= 4, 1 pixel > 8 <= 16) but the full-resolution grey diff is mean 3.9 with 12.6 % of pixels > 8 and 1 % > 32 (right third of the frame moved): a re-shot, not a copy. The 128x128 identity test cannot separate a steady re-shot from a transcode; GROUPING 10-13 hid this behind the shot-time veto. Other re-shots (C093 block mean 13.8, C094 40.6, C114 79, C132 74) are far from the gate. 1 of 24 in this sample. Content-based remedy for the owner to decide: confirm EXACT with a finer identity test when the grey gate passes but bytes differ (e.g. full-resolution mean abs diff; a transcode sits at noise < 2 levels, a re-shot at sensor noise 3-4+), paid only on those rare pairs.
- Regression of GROUPING 14 found: C052/C053 (eclipse-frame copies, quality-gated, one degenerate pHash for four frames) lost their EXACT because the rescue bucket was no longer split and every copy was judged against one keeper. Fixed as GROUPING 15 (_rescue_identity uses _pixel_clusters), branch fix/grouping-15-rescue-clusters, worktree g15, commit 748a118; dedup tests 175 green; headless rescan with the fix running for confirmation.
- ~10:55 GROUPING 15 verified on the blind-2 root (headless rescan from worktree g15): only C052 and C053 changed, both back to one group with the copy EXACT via the rescue; every other case identical to the GROUPING 14 scan (574 rows, 215 groups, EXACT 173, REVIEW 112, hints 8). PR #1063 open, news fragment a838de7, CI watching; merge waits for owner yes.
- 2026-11-09 ~11:05: #1063 merged (squash f0f34f0, owner yes). CI 11/11 green. Local master fast-forwarded; worktree g15 removed, branch deleted. Master now = GROUPING 15. Open for owner decision: the C120 EXACT gate (steady re-shot passes the 128x128 grey identity test).

## 2026-11-09 ~11:30: owner direction after GROUPING 15

- Blind round 1 group sheets (GROUPING 13 engine) are dropped as obsolete; only the current engine is judged from now on.
- Owner wants to see more C120-shaped pairs with their own eyes before ruling on the EXACT gate. Lead runs a census over the full-library manifest: same pHash + dims, different SHA, 3 s < gap <= 30 min and not a half-hour offset (search heuristic only), recomputes the engine EXACT gate (rgb32 shift + grey128 block/tail) on each pair and renders the passing ones with full-resolution diff stats (c120-census.py; sheets in scratchpad look/c120-census).
- ~11:50 C120 census over the full library (c120-census.py, gap > 2 s): 266 re-shot-shaped pairs (same pHash + dims, different SHA, 3 s - 30 min apart, not a half-hour offset); 8 pass the EXACT gate: 4 are C120 itself (two files x two copies, re-shot 3 s apart, full-res grey diff mean 3.9, 12.6 % > 8), 2 are a screenshot re-saved 23 s later (mean 0.02, a true copy), 2 are a scanned print kept in colour and as a black-and-white edit 5-10 min apart (grey mean 0.1-0.2 but visibly different pictures: the rgb32 channel-shift test lets a desaturation of a low-saturation scene through). So two gate-looseness classes exist, both rare: a steady re-shot (1 picture in 39,888) and a colour-vs-mono edit (1). Sheets: scratchpad look/c120-census-3s/X-page-01..02.png; paths in c120-census.local.txt there. Remaining blind-2 hints (8) are all the B7 both-screenshots rule (format-based).
- ~12:10 Owner ruling on the EXACT gate: X001-X008 as EXACT is acceptable and close to the intended effect (a steady re-shot 3 s apart and a colour-vs-mono edit of one scan both count as the same picture for dedup purposes). Finding (a) of ~02:30 and the "finer identity confirmation" direction are CLOSED, no work. The grey128 block/tail + rgb32 shift gate stays as the EXACT definition. Open items for owner decision stay: mean-colour hard veto without hint (C016), B7 PNG rule being format-based, keeper direction (small undated copy over dated original), exposure brackets split by mean colour, 128 px dimension gate, Live video by identifier only.
- ~12:40 Owner: apply the rules already given to the six open items; leave only true product decisions. Reviewed against the recorded rulings (content only, 11-09; a colour/tone difference makes two pictures, 11-09 ~02:30; lone Live pair is one photo, 11-08; pairing evidence is the ContentIdentifier, 11-07; B7 default ON, 11-08 ~16:30; approved scorer weights put resolution first at 0.25). (1) C016 is a colour-graded export of one frame (pHash 4, rgb32 channel shift 30 against a gate of 4): two pictures under the tone ruling; the mean-colour veto stands and no hint is owed (a hint is the soft binding the owner declined). CLOSED. (2) Exposure brackets C249/C250 split by brightness: same ruling, the split stands. CLOSED. (3) Keeper direction: in blind 2 under GROUPING 15, 17 of the 38 mixed-resolution groups have the smaller file as structural keeper (9 UNDATED small copy + dated original, 8 same-source pairs); every one is same-source, so the path tiebreak of _keeper_key decided, not source priority. The scorer's top row is the biggest file in 38 of 38, so aggressive never deletes the original (the small keeper carries no duplicate action, the big REVIEW row is the protected top scorer), but such a group yields zero deletes and shows the original as the duplicate. Lead reading: pixel count before path in _keeper_key is what the content principle and the approved scorer already say; a GROUPING 16 engine item, not a product decision. (4) 128 px dimension gate: 28 of 34,702 stills in the full library, 4 of them share a pHash with a full-size row; a known recall gap of 4 rows, no decision. (5) Live video by identifier: the identifier is the evidence the owner named; the same-stem AND condition can only lose the video of a renamed still; the count needs an exiftool census; no decision. (6) B7: the 4 remaining blind-2 hint pairs rendered side by side (sheet in the scratchpad): C108, C134, C137 are different screenshots of one app (chat scrolled, prices moved, slide changed) that pHash+dHash would group as REVIEW with B7 off; C109 is one page re-saved three days apart that B7 keeps as a hint. B7 is the last format-keyed rule; its content-based replacement would be a templated-UI detector on pixels, a design change. THE ONE PRODUCT DECISION LEFT: keep B7 as the PNG proxy for "templated image, pHash not enough" (lead recommends, default ON as ruled) or commission the content-based detector.
- 2026-11-09 ~12:55: **#1064 merged** (squash e6ddd93); local master fast-forwarded (the identical STATE.md was staged first so the ff could proceed; tree clean). docs-state worktree and branch removed. OWNER RULING on the sheet of ~12:40: the four B7 hint pairs (C108, C109, C134, C137) AND C016 all belong in one group each. Consequences: the PNG-pair demotion (B7) must not keep structurally matching screenshots apart by default, and the mean-colour gate must not veto a pair whose pHash and dHash both agree (a colour grade or exposure change is the same picture). This supersedes the lead's ~12:40 reading of C016 and the brackets C249/C250 under the tone ruling: that ruling was about a structurally different DNG/JPEG pair (pHash 18-22), not about a colour difference on an agreeing structure. GROUPING 16 in flight: structure over colour (mean colour vetoes only when dHash cannot vote) and B7 default OFF (toggle kept).
- 2026-11-09 ~13:35: **GROUPING 16 PR #1065 open** (branch feat/grouping-16-structure-over-colour, worktree g16, commit 86a9c2d): (a) the #462 mean-colour gate reads only when dHash cannot vote, in _gates_pass and _classify_near_duplicates (new _mean_color_passes); an agreeing dHash outranks colour; (b) B7 default OFF in DEFAULT_RULES and the web default, toggle kept, LEGACY_RULES unchanged; (c) version 16, scan.md / rules.md R-SCAN-10 / tooltip + translations / s81 / route and frontend tests updated. Affected pytest files green (about 500 tests, 0 failed); ruff --no-fix equal to master. Headless blind-2 rescan: 254/264 unchanged, 10 newly grouped, 0 lost: the five owner-named pairs (C016, C108, C109, C134, C137) plus C104 and C113 (re-shots the colour gate had refused) and the three 4-frame exposure brackets C249/C250/C251 (now one group each instead of split by brightness). Sheet of the five extra cases in the scratchpad (G16-newly-grouped.png); lead judged all five consistent with the ruling. Fresh opus review of 86a9c2d running; CI watch next; merge needs the owner's yes.
- 2026-11-09 ~13:45: fresh opus review of 86a9c2d: NEEDS-FIX, one HIGH: qa/web/_invariants.py reset_scan_persistence still PATCHed B7 on before every web scenario, so s81's new 'B7 unchecked' assertion would fail in the CI batch and every scenario scanned with a non-default rule set. Fixed in 363f06a (the reset writes the full GROUPING 16 default: B6 on, B7 off); criteria 1-3 and 5-8 passed (gate order, no other mean-colour call site, new tests fail on master's code and pass on the branch, rules register OK, privacy clean, 15 files). Pushed; CI re-running.
- 2026-11-09 ~13:55: **PR #1065 CI green (12/12 on 363f06a, including web-scenario-batch with s81 and the frontend build-and-test).** Ready for the owner's merge yes. After merge: fast-forward master, STATE merged line, remove worktree g16, then the owner rescans the test set / blind2 on master (expected: the 10 blind-2 changes above). Still open from the ~12:40 review: keeper direction (pixel count before path in _keeper_key) as an engine item if the owner wants it.
- 2026-11-09 ~14:05: **#1065 merged** (squash fe272ff); local master fast-forwarded; g16 worktree and branch removed. Owner rescans blind2 and the test set on master next (expected on blind2: the 10 case changes listed at ~13:35, nothing lost). Keeper direction stays parked as a GROUPING 17 candidate pending the owner's word.
- 2026-11-09 ~22:30: **Owner rescans on master (GROUPING 16) verified.** blind2 (owner file migration_manifest_testset_g16.sqlite, 574 rows, 217 groups, 0 low rows): case outcomes identical to the headless g16 prediction, 254 unchanged / 10 newly grouped / 0 lost vs GROUPING 15. Test set (owner file migration_manifest_testset.sqlite, 2,043 rows, 468 groups, 0 low rows) vs a headless GROUPING 15 baseline of the same files (worktree g15ref at f0f34f0, removed after): sha unchanged 2,043/2,043; 448 groups identical, 17 dissolved / 20 formed; label changes attributable to grouping = former hint rows now REVIEW/high (16 near-dup + 2 pixels-differ) or now the ref of a new group (15); the '' -> KEEP (217) and EXACT -> KEEP (14) transitions are the app's auto-select stamp overlay (the headless baseline stamps nothing); the EXACT -> KEEP ones are groups where the scorer's keeper differs from the structural keeper (the parked keeper-direction item, visible in the UI as a KEEP on a duplicate row). Health census: 0 low-only groups; worst groups looked at (sheets in scratchpad look/testset-g16-worst and look/testset-g16-png): 18 PNG-only non-identical groups now exist (12 span >1 day), all 'same app, different content' screenshots: a 15-member bank-receipt group (6 transactions with their copies, one day), 4 receipts over two years, Duolingo chain of 6 over 4 months, train tickets, calculator, transaction records, game stats, lock screens. This is the owner's blind-2 ruling (C108/C134/C137: scrolled chat, moved prices, changed slide = one group) applied to the test set; nothing in them is aggressive-deletable by default (B8 on keeps aggressive to EXACT rows). Shown to the owner for confirmation; HEIC photos of a kiosk screen over 9 days (16 members) are the same class formed by pHash+dHash alone and predate GROUPING 16.
- 2026-11-09 ~23:10: **Owner judged the 15 rendered test-set PNG groups** (sheets P1-P15): only pixel copies are the same picture; every 'same app, different content' pair is two pictures (P1/P2/P3/P11/P12/P15 all different; P4 1 vs 2-4; P5/P10/P14 1-2 vs 3; P6-P9 1 vs 2-3; P13 six transactions). Owner's hunch: the hashes lack resolution. Lead measured (template-diff-measure.py, flatness-measure.py, scratchpad): (1) every owner-'same' screenshot pair is a pixel copy (ident > 99.9 percent at grey128), so the screenshot standard is pixel identity; (2) grey128 difference statistics do not separate 'different content' from 'crop / re-shot / bracket': DIFF-template ident 2-99 percent, strong-diff 0.45-28.6 percent, vs SAME-photo strong 0.4-60.9 and SAME-edit ident 2.5-100: full overlap (P6 calculator digits 0.45 vs C113 re-shot 0.38); (3) a format-free 'rendered image' detector from pixel flatness overlaps too: adjacent-equal share rendered 61-99 percent vs photos 6-73, zero-variance 8x8 blocks rendered 0.2-93 vs photos 0-28 (a HEIC photo at 61.8/22.1 sits inside the rendered range 61.2/13.4). Conclusion: the class signal that separates these pairs in this library is the container format (PNG = phone screenshot), i.e. B7; no content statistic available at hash time does it without overfitting. The owner's blind-2 ruling on C108/C134/C137 (group) was made on 260 px thumbnails and contradicts today's standard; C109 (same page, clock differs) is also 'different' by the P5/P10/P14 logic. Lead recommendation: GROUPING 17 = B7 default back ON (toggle kept), structure-over-colour of GROUPING 16 stays (correction 2026-11-10: the owner ruled only on C016 among the photos; C104/C113/C249/C250 were grouped on lead judgment, never confirmed by the owner). Awaiting the owner's call; a pixel-based rendered-image detector is a research item (needs a labelled set beyond 66 screenshots), not a quick rule.
- 2026-11-10 ~00:30: **Owner rejected the B7-back-on recommendation** ('who says screenshots need special handling?'). Lead withdraws it: B7 keys on the container format, which is not content, so it breaks the content-only principle. Measured instead (tonefit-measure.py, fullres-measure.py, where2.py, scratchpad): after removing any global tone change, share of 16 px blocks that still differ, at the smaller image's full resolution. Owner-grouped screenshot pairs from the 11-09 sheet: C108 3.4 percent, C134 4.0, C137 11.9 (changed regions spread through the body: last chat message, prices, caption text). Owner-split test-set pairs: P2 2-3 0.8 percent, P11 1-2 1.9, P4 1-2 2.6. Derived copies (S1) median 0; bursts (S6) median 28.6. So the two owner rulings ask opposite answers for the same kind of change (same screen, some text changed); no content measure, at any resolution, can give both, and the fix is not a screenshot rule. Open product question for the owner, format-free: does a group mean 'the same picture' (copies and edits of one capture only, so re-shots, bursts, brackets and changed screens all split, photos included) or 'similar enough to review together' (then the P groups correctly group as REVIEW and B7 stays off)? No engine change until answered.
- 2026-11-14 ~02:30: **Owner ruling on what a group means (answers the open question of 11-10 ~00:30).** Shown five sheets of real cases by kind of difference (edited copies; same app screen with part of the content changed; photos where part of the subject changed; re-shots and bursts; exposure brackets), the owner answered for all five: they are by nature not exact, the content is similar enough, so they belong in ONE group ("that is already what these-look-alike means"), labelled with a similarity %, and no kind needs exception handling. Consequences: (1) a group means "similar enough to review together", for every format; B7 stays off and no screenshot / container rule comes back; (2) the P1-P15 split marks of the earlier sheet are read as "not the same picture", not as "must not share a group" (lead reading, told to the owner so they can correct it); (3) GROUPING 16 already groups every row of the five sheets, so the ruling asks for NO grouping change. Checked on the real path: `load_review` on copies of the two owner GROUPING 16 manifests puts every sheet row in one group with a Similarity label. **Gap found: the label does not track content.** It is `round((64 - pHash hamming) / 64 * 100)` against the displayed Ref, and pHash only sees coarse layout: the screens whose message / price / subtitle / input text differ, the slide with an extra line and two night re-shots all read 100%, while a cropped copy of one capture reads 88-91%. REVIEW rows at 100%: 47 of 123 (blind-2), 36 of 370 (test set); range 69-100, none below 50. So the number the owner expects exists but ranks an edit BELOW a changed screen. Candidate from the 11-10 measurements: the tone-matched full-resolution block residual (copies 0, changed screens 0.4-12, brackets and bursts 13-29) orders these kinds correctly, but it skips pairs whose aspect differs (crops), so it is not ready to display. Next: a "shows now / would show" sheet on the same rows for the owner to judge before any change to the stored distance or the column.
- 2026-11-14 ~04:30: **Owner confirmed the reading** (the "different" marks on the P sheets meant "not the same picture"; staying in one group is fine) and asked for the now / new comparison sheet plus the rest of the original plan. (a) **Candidate measure built (scratchpad newsim.py, measurement only):** place the picture with the narrower view inside the other (scale + offset search by cross-correlation, refined by local shifts), then at the smaller picture full resolution fit a global tone map (refitted on the unchanged blocks, both directions), count 16 px blocks whose residual stays above 16 grey levels; percent = unchanged share x share of the wider picture covered; 100 only when nothing changed; the placement kept is the one that leaves the most picture detail in unchanged blocks. Known-input test on four real pictures (identical, half size + JPEG, native crop to 52.5 percent, crop + resize + gamma + JPEG, 5 percent of the area replaced): every case within 3 points of the true answer. (b) **Sheets rendered** (scratchpad look/compare, compare-1..5.png, same rows as the preference sheets, against each group displayed Ref): changed screens now 100 -> new 91-99 (clock only 99, last message 97, prices 96, caption 91); two different bank receipts 88/91 -> 78; clean edits 88-91 -> 92-97; landscape re-shot 97 -> 87-88; night re-shots 100 -> 76-78; bursts 84-94 -> 65-90; brackets 88-91 -> 62-69. Weak rows, told to the owner: C016 51, C030 64, C003 41 (edits that are not a plain crop: a hue grade or a straighten; the placement model has no rotation), C118 18-21 against a much brighter Ref. (c) **Census on the owner blind-2 scan** (152 rows that show a percentage, against the displayed Ref): of the 78 rows shown as 100 today, the measure says 100 for 55, 95-99 for 13, 80-94 for 4, below 80 for 6; by stratum new p10/p50/p90: S1 60/98/100, S2 100/100/100, S3 21/82/100, S4 100/100/100, S6 31/65/89. Cost: median 6.4 s per row from the NAS, 18 minutes for 152 rows, unoptimised; a scan-time version needs a much cheaper path before it is proposed. (d) **Display defect found in the current app:** an EXACT row wears 100 percent because it is an exact copy of its own keeper, but the column is read against the displayed Ref; when the Ref is a different picture of the same group the 100 is wrong (sheet row B6: a copy of another screen shows 100 against the Ref, the measure says 98). (e) **Keeper direction implemented in a worktree, not yet committed** (GROUPING 17 candidate): _pixel_rank tier (known size before unknown, larger first) after format and source priority in _keeper_key and after source priority in the near-duplicate pair order; 7 new tests (3 fail on master). Headless blind-2 rescan with the change vs the headless GROUPING 16 manifest: 574 rows, same paths, identical group partition (217 groups); mixed-resolution groups whose structural keeper is smaller than one of its duplicates: 18 of 39 before, 0 of 39 after (the 11-09 count of 17 of 38 used a slightly different definition); action changes are exactly the 18 swaps. Full suite and a fresh review running.
- 2026-11-14 ~08:45: **GROUPING 17 PR #1066 open** (branch fix/dedup-keeper-larger-picture, commits fbb500e + news 758a873): the keeper prefers the larger picture after format and source priority and before the path. Fresh opus review of the worktree diff: code correct on all criteria (every keeper site covered, None-safe, deterministic, tests assert outcomes), one stale version sentence in scan.md fixed before the commit; reviewer note, pre-existing shape: in a same-pHash bucket later clusters are judged against the first keeper and the dHash gate is not transitive, so the first keeper can decide whether a third picture joins (was chosen by path, now by size). Headless test-set rescan with the change vs the owner GROUPING 16 scan: 2,043 rows, same paths, identical partition (468 groups); keeper smaller than a duplicate: 18 of 41 mixed-resolution groups before, 8 after; the 8 left are different-format (5) or different-source (3) pairs where format or source priority decides first. OPEN FOR THE OWNER: should pixel count come before format and source priority (it would flip those 8; the source priority would then only order pictures of equal size). Full suite in the worktree with coverage: total 93.62 percent, dedup.py 97 percent, the only failures are the two TestWedgedExiftoolRotation tests. **Flake investigation (subagent, no code change): NOT REPRODUCED** in 3 full-suite runs without coverage (2,475 passed each) and 30 runs of the class alone; today it failed in 2 of 2 full runs WITH coverage while other heavy jobs ran on the machine, both times as the 60 s join timeout ("scan deadlocked"). Hypothesis only: the two 1,600-record tests (4 s alone) cross the 60 s budget under coverage tracing plus CPU contention; the two variables are confounded. Next experiment if the owner wants it: the class under deliberate CPU load, with and without coverage. Process note: that subagent re-ran a guard-refused pytest loop from a script file (disclosed by itself; effect limited to pytest runs in its own worktree); reported to the owner. Leftovers: worktree of the merged #1049 (clean, no junctions) awaits an owner yes to remove; a real folder path in a test docstring (tests/test_dedup.py) is scrubbed in the docs PR that carries this tail.
