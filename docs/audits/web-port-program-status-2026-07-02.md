# Web-Port Program — Status Snapshot (2026-07-02)

> **SUPERSEDED as of 2026-07-16.** This snapshot is 2 weeks stale relative
> to the current tree. Current status lives in
> [`web-parity-matrix-2026-07.md`](web-parity-matrix-2026-07.md) (see its
> "2026-07-16 re-audit deltas" addendum for what shipped since this doc
> was written) and
> [`web-port-goal-audit-2026-07-16.md`](web-port-goal-audit-2026-07-16.md)
> (the full goal-vs-code re-audit). The body below is kept intact as the
> historical record — do not treat it as current.

> **What this file is.** A human-readable, long-lived checkpoint of the Qt → localhost-web
> port: the original plan, where we are, what is left, and how a fresh session picks it up.
> Written because the program is a large **56-PR stacked chain** and the GitHub epic
> trackers have gone stale (see §3). This is a *reference snapshot*, not a spec — the
> authoritative spec is `docs/design/web-port-tech-design.md` + the epic issues; the
> live rolling progress is `~/.claude/.../memory/project_web_port_phase2.md` + the PR chain.
> If this file and reality disagree, reality (the PR chain + the code) wins — regenerate this.

---

## TL;DR

- **Goal:** turn the Windows PySide6 (Qt) desktop app into a **localhost web app**
  (FastAPI + SSE backend, React + TanStack frontend, **pywebview** packaging) — for
  cross-platform reach and lower long-term tech debt via a clean Qt-free core.
- **Two owner-set hard constraints:** (1) core functionality unchanged, performance
  equal-or-better; (2) an **equivalent Playwright QA system** at parity with the Qt scenarios.
- **Iron rule:** the integration base does **not** merge to `master` until the whole
  program is complete (draft integration PR #640).
- **Where we are:** Phases 0–3 done; Phase 4's QA-parity + video parity + reversible
  Qt-decoupling done; the **4b desktop shell (part 1)** just shipped (#732). The web app is
  **feature-complete**. What remains is the **final cutover only**.
- **Chain state:** 56 web-port PRs, **all OPEN, 0 merged**; `origin/master` still at
  `f1d1795`; stack tip = **#732** (`feat/web-port-cutover-4b-pywebview-shell`).

---

## 1. The original plan (ground truth: issue #647 + design doc + feasibility report)

**Purpose.** A view-layer + QA-harness *rewrite* (~11% backend reuse, est. 18–24 person-weeks),
justified by cross-platform reach + lower long-term tech debt. The Qt-free core makes the
headless-core / MVC extraction cheap — that extraction *is* the cross-platform foundation,
worth doing even if the web UI were deferred. Stack: FastAPI + SSE, React + TanStack, pywebview.

**Hard constraints (non-negotiable):**
1. Core functionality unchanged; performance equal or better.
2. An equivalent QA system after the change (owner identified QA as the biggest architectural
   difference) → Playwright at parity with the Qt scenarios.
3. (forward goal) cross-platform + MVC architecture so future tech debt stays small.

**Iron rule:** integration base (`docs/web-port-feasibility`) does not merge to `master` until
the whole program is complete.

**Phase plan (as originally filed):**

| Phase | Epic | Original scope | Est. | QA target |
|---|---|---|---|---|
| Prereq | #641 | Widen CI triggers to the integration base | — | — |
| Phase 0 | #642 | Qt-free core extraction (`scan_runner`, `AppService`, `image_service`→bytes) | 2–4 wk | — |
| Phase 1 | #643 | Scan API + SSE + cancel + image endpoint + Playwright bootstrap | 3–4 wk | ≥11 scenarios |
| Phase 2 | #644 | React frontend core (decision tree, preview, **pywebview**) | 4–5 wk | ≥40 scenarios |
| Phase 3 | #645 | Remaining dialogs + OS integration + i18n + resolve 9 unassigned concerns | 2–3 wk | ≥58 scenarios |
| Phase 4 | #646 | Playwright becomes primary gate + **remove PySide6** + flip gates to blocking | 1–2 wk | all 64 scenarios |

**End state (Phase 4 done):** web app replaces Qt; PySide6 removed from `requirements.txt`;
PyInstaller spec uses pywebview; `app/views/` archived in git history (superseded, not deleted);
`qt qa-batch` retired and replaced by `web-playwright-batch`; eval gates flipped to blocking;
`WEB_PORT_PHASE=4`.

**A caveat baked into the original plan:** the 5 keystone contracts' adversarial review "did NOT
converge on first pass" — **16 binding corrections** were recorded to be folded in during Phase 0
before the contracts lock. (See §3 — these need a verification pass.)

---

## 2. Current progress

**One-line position: we are at the tail of Phase 4.** The web app is feature-complete;
what's left is the final cutover.

| Phase | Status | PRs |
|---|---|---|
| Phase 0 — Qt-free core | ✅ done | #648 #649 #650 |
| Phase 1 — scan API + SSE + image + QA scaffold | ✅ done | #654 #655 #656 |
| Phase 2 — React scaffold + review backend + execute UI | ✅ done | #657 #659 #660 #663 #664 |
| Phase 3 — QA foundation + i18n + action backend + scenarios | ✅ done | #666–#681 |
| Phase 3.5 — filter scope, unlocked verdict, result-tree QA, advanced settings *(added)* | ✅ done | #682–#693 |
| Phase 4 — multi-select, persistence, prune/lock, shortcuts, scan guard, QA ports | ✅ done | #695–#724 |
| Cutover gates — perf benches (#725–#727) + 4a reversible Qt-decouple (#728) | ✅ done | #725–#728 |
| Video parity — V1 stream / V2 transcode / V3 grid *(added)* | ✅ done | #729 #730 #731 |
| **4b desktop shell — part 1 (launcher)** | ✅ done | #732 |
| **Phase 4 — the actual cutover (remove Qt, flip gates)** | ❌ **not started** | — |

**Scope added beyond the original phase plan (legitimate fill-ins, same goal):**
- **Phase 3.5 (#682–#693):** dialog- and QA-parity items; effectively part of Phase 3's
  "remaining dialogs," split out for reviewability.
- **Video V1/V2/V3 (#729–#731):** the `s11` HEVC video scenario was left as an *unresolved
  bucket* in the design doc (Appendix open question). Video playback later became a hard
  requirement, so it grew into its own workstream. This fills a plan gap; it is not a drift
  in direction.
- **4b split into part-1 (shell, #732) + part-2 (packaging, pending):** an execution decision.

---

## 3. Tracking drift — READ THIS ⚠️

**The epic issues #641–#647 have every checkbox UNCHECKED**, even though Phases 0–4 have
shipped. **Do not trust the epic checkboxes for progress.** Sources of truth, in order:
1. The PR chain (`gh pr list`) + the code.
2. `memory/project_web_port_phase2.md` (newest checkpoint at the tail).
3. This file.

**Also unverified — the 16 adversarial-review binding corrections.** The design-doc header says
the contracts were "NOT converged" and 16 corrections must be folded in during Phase 0. Several
clearly *were* implemented in code (e.g. `_CancelToken` replacing the dual cancel signal;
`core/app_service/path_safety.is_under_roots`; the STA WIC executor; the full-res `size=0` byte
bound). But **not all 16 have been independently re-verified** as landed. Recommended: a
verification pass mapping each of the 16 corrections → the PR/commit that implemented it,
**before 4c** (the irreversible removal). This is the single biggest "did we actually do what we
promised" gap.

---

## 4. Remaining work (ordered)

| # | Item | Who | Gated? |
|---|---|---|---|
| 1 | **Manual Windows smoke of the 4b window** (`set PHOTO_MANAGER_WEB=1 && python launcher.py`; needs `npm run build` first) | user | — |
| 2 | **4b-PR-2 — PyInstaller packaging:** pivot entry `main.py`→`launcher.py`; `collect_all('pythonnet')`/`('clr_loader')`; bundle `frontend/dist`; add a frontend-build step to `release.yml`; a non-headless WebView2 smoke | agent | no |
| 3 | **Verify the 16 binding corrections** landed (map each → PR/commit); + confirm #646's Phase-4 bench corrections shipped in #725–#727 (cold `size=0` large-DNG RSS budget; Qt-arm `QApplication(offscreen)` asserting `files_per_s>0`) | agent | no |
| 4 | **4c — IRREVERSIBLE PySide6 removal:** remove PySide6 from `requirements.txt`; archive `app/views/` in git history; retire `qt qa-batch`; delete Qt-importing tests + `qa/scenarios/**` | agent | **yes (irreversible)** |
| 5 | **Deliverable 5:** flip `web-eval-gates` from `continue-on-error` → blocking; set `WEB_PORT_PHASE=4`; user marks the web checks as required in branch protection (atomically with 4c) | agent + user | user action |
| 6 | Mark integration PR #640 ready | agent | — |
| 7 | **Drain-merge all 56 PRs to master, bottom-up** | user | user does every merge |
| 8 | Reconcile the stale epic checkboxes (#641–#647) so the tracker matches reality | agent | — |

---

## 5. 4c preconditions (gate before the irreversible Qt removal)

From the memory checkpoint (LEAD-tracked, supersets #646's acceptance criteria):

| Precondition | Status |
|---|---|
| Web video parity incl. HEVC playback proven | ✅ MET (V1+V2+V3, #729–#731) |
| WebView2 presence check at launch | ✅ MET (4b, #732) |
| `web-scenario-batch` ≥10 consecutive flake-free runs | ❌ not confirmed |
| Destructive-flow assertion-density spot-diff vs Qt (s15/s26/s11/s38) | ❌ not confirmed |
| Atomic required-check swap (Qt qa-batch → web-playwright-batch) | ❌ not done |
| 4b + 4c treated as one revert unit | ❌ not done (4b-PR-2 pending) |
| 16 binding corrections verified folded in (§3) | ⚠️ partial / unverified |

---

## 6. Gated actions still ahead (per CLAUDE.md)

- **4b-PR-2 packaging:** NOT gated (PyInstaller is already a build dep).
- **4c PySide6 removal:** IRREVERSIBLE — needs an explicit user "yes".
- **Deliverable 5 required-check flip:** the branch-protection change is the user's action in GitHub.
- **Drain-merge:** the user merges every PR themselves (never the agent).
- Any new `pip install` / external clone / third-party config ingest: gated.

---

## 7. Cold-session handoff — how to catch up and continue

1. Read `CLAUDE.md` (project + global).
2. Read the ground truth (don't rely on recollection):
   - GitHub issue **#647** (top tracker) + `docs/design/web-port-tech-design.md` (the contracts).
   - `memory/project_web_port_phase2.md` — **newest progress is at the tail**.
   - This file (§2–§5).
3. Confirm `git log origin/master --oneline -1` is still **`f1d1795`** (master must not have moved).
4. Confirm the stack tip = `feat/web-port-cutover-4b-pywebview-shell` (#732); base any new branch on it.
5. Run `gh pr list --state all` and confirm it's still **56 open / 0 merged**.
6. Pick up at **4b-PR-2 (packaging)** unless the user directs otherwise.
7. ⚠️ **Do not trust the epic checkboxes** (§3). Progress = memory + PR chain + code.

---

## 8. Key facts / gotchas for a successor

- **Worktree:** `C:\Users\J\repository\photo-manager\.claude\worktrees\web-port-phase2`.
  **Venv python:** `C:/Users/J/repository/photo-manager/.venv/Scripts/python.exe`
  (from the worktree it is `../../../.venv/...`, not `.venv/...`).
- **Never merge PRs** — the user merges every one in the GitHub UI. Surface "ready for your merge".
- **pin base to `origin/master` after `git fetch`**, never a stale local `master`.
- **pytest CI runs on `windows-latest`** (so `winreg` is available — the WebView2 check is honestly
  covered, not omitted). Coverage floors: 70% per-file, 80% global.
- **`qa_scenario_guard`** triggers only on `app/views/{handlers,dialogs,components,workers}/**.py`;
  root-level or `app/web/**` changes don't need a `[qa-not-needed]` token.
- **Every PR needs a `news/<PR>.<type>` fragment** (added *after* create, keyed by the PR number)
  or `require-news-fragment` fails.
- **Stacked PRs:** create with `--base <parent-branch>`, not `master`, so `pr-gates` diffs correctly.
- **pywebview is installed** in the venv (6.2.1 + pythonnet 3.1.0 + clr_loader 0.3.1 + cffi + bottle
  + proxy_tools). On Windows pythonnet is pulled unconditionally; clr_loader arrives via pythonnet.
- **Manual Windows smoke owed for 4b:** `npm run build` (in `frontend/`), then
  `set PHOTO_MANAGER_WEB=1 && python launcher.py` → native WebView2 window opens the app + video
  plays; and `python launcher.py` without the env var still boots Qt.
- **Testid pipeline:** `qa/web/testid_constants.py` → `scripts/gen_testid_ts.py` →
  `frontend/src/testids.ts` (regenerate + parity-check when adding testids).
- **Frontend CI gate** = `npm run lint` AND `npm run build` (`tsc -b` — NOT `tsc --noEmit`) AND
  `npm run test -- --run` (vitest uses esbuild, never type-checks).
