# Web-Port Feasibility Evaluation — photo-manager → localhost web app

**Date:** 2026-06-19
**Base:** `origin/master` @ `f1d1795`
**Author:** Claude Opus 4.8 (Claude Code), via a 7-agent analysis workflow (all analysts on Sonnet)
**Status:** Evaluation only — no code changed. This document is the durable record of the assessment.

> **Citations note.** File:line citations were gathered by agents reading the
> `feat/review-ux-daylight` working tree (architecturally identical to master for
> every layer analysed). Line numbers are approximate and branch-relative; file
> names and structural claims are stable on master.

---

## 0. The question

Can photo-manager — today a Windows PySide6 (Qt) desktop app — become a **localhost web app** (Python backend + browser UI), under two hard constraints set by the owner:

1. **Core functionality unchanged; performance equal or better.**
2. **An equivalent QA system after the change** (the owner correctly identified QA as the biggest architectural difference).

A third, forward-looking goal was added: **cross-platform** down the road, and an **MVC-style architecture** so that future tech debt stays small.

---

## 1. Verdict

**Feasible — and this codebase is unusually well-suited to it — but it is a *rewrite of the view layer + QA harness*, not a "port".**

The honest reuse ratio:

| Bucket | ~Lines | Disposition |
|---|---|---|
| `scanner/` + `core/` + `infrastructure/` (minus `image_service.py`) | ~4,300 | **Transfers unchanged** (the proven backend logic) |
| `app/views/` (34 files) | ~14,900 | Rewritten as a web frontend + HTTP/SSE API |
| QA harness (`qa/scenarios/` + `_uia.py` + `_batch.py`) | ~22,000 | Rewritten against Playwright |
| `infrastructure/image_service.py` | ~880 | Rewritten (QImage → bytes/JPEG) |

≈ **11% reuse, 89% new code.** Single-developer estimate: **18–24 person-weeks**, incremental and reversible.

The two hard constraints, answered plainly:

- **Performance equal or better** — Scan throughput: **yes, guaranteed parity** (or marginally better). Thumbnail browsing: **parity is achievable but not free** — it needs deliberate engineering. Full-resolution RAW review: **the one structurally weaker surface** in a browser-delivery model.
- **Equivalent QA** — You cannot have an *identical* QA system (UIA is desktop-only), but you get an **equivalent-or-better one for ~94% of scenarios** (more deterministic, faster, parallel, far less flaky) and **lose true end-to-end coverage on ~6%** of native-OS scenarios, which downgrade to API/property assertions.

---

## 2. Why this codebase is unusually ready (the decisive enabler)

The existing layering is already a clean MVVM split. Verified by import-surface grep:

| Layer | Qt coupling | Disposition for a web backend |
|---|---|---|
| `scanner/` (13 files — the perf core) | **Zero Qt imports** | Move in untouched |
| `core/` (models + services) | **Zero Qt imports** | Untouched |
| `app/viewmodels/main_vm.py` | **Zero Qt imports** | Untouched |
| `infrastructure/` except `image_service` | **Zero Qt imports** | Minor adaptation (log paths, delete handle-releaser) |
| `infrastructure/image_service.py` | **The only backend Qt leak** (`QImage`, `QImageReader`) | Rewrite to return bytes/JPEG |
| `app/views/` (34 files) | All Qt | Replaced by the web frontend |

Module-level tally from the backend/perf analyst: of 24 backend modules, **20 transfer with zero changes, 3 need minor adaptation, and only 1 (`image_service.py`) needs a real rewrite.** For a mature Qt desktop app this is a rare degree of cleanliness — the prior MVVM discipline is what pays off here.

---

## 3. Performance parity — the detailed answer

### 3.1 Scan throughput: parity guaranteed

The scan pipeline has **zero coupling to the Qt event loop** (grep-confirmed: no `processEvents`, `QEventLoop`, `QTimer`, `qApp`; all timing uses `time.monotonic`/`perf_counter`). The three performance mechanisms are pure Python and transfer intact:

- **Per-device `ThreadPoolExecutor` tuning** (`scanner/workers.py`): NAS=8 workers (SMB-latency-bound), HDD=1 (seek-minimising), SSD=`min(4, cpu_count)`.
- **`ByteBudget` back-pressure** (`scanner/byte_budget.py`) — prevents the ProRAW-DNG OOM regime.
- **`ReadKneeRamp` autotune** (`scanner/autotune.py`).

Porting work is mechanical: `ScanWorker.run()` (a `QThread`) is extracted to a standalone `run_pipeline()` function; `Signal.emit(...)` → `queue.put(...)`; `QThread.isInterruptionRequested()` → `threading.Event.is_set()` (**15 call sites**, must all be replaced — the WRITE-stage cancel gate is the most dangerous to miss, as a missed gate could overwrite a manifest with a partial result).

**One design rule:** run scans in a **dedicated worker process**, not inline in the uvicorn worker, so HTTP serving does not contend on the GIL with PIL decode. This is the single most important perf decision in the port.

### 3.2 Thumbnail browsing: parity achievable, not automatic

Today: in-process `QPixmap.fromImage` delivered via a Qt Signal. Web: every thumbnail is an HTTP round-trip + JPEG re-encode. The existing **content-hashed JPEG disk cache** (`image_service` already writes to `AppData/.../thumbs/v1/`) plus HTTP/2 + client prefetch can approach parity, but it must be engineered. The claim "perf is unchanged because the pipeline is Python" is **false for the thumbnail path** if implemented naively.

### 3.3 Full-resolution RAW review: the one genuine regression risk

A 100–130 MB ProRAW DNG decodes in-process to a `QPixmap` today. Over HTTP there are only two options:
- (a) large server memory budget + HTTP **Range streaming** of the decoded image, or
- (b) re-encode to JPEG (quality loss for pixel-level review).

For photography "pixel-peeping" review, browser delivery is structurally at a disadvantage here. This is the place where "equal or better" requires a **conscious tradeoff**, not just engineering.

### 3.4 Realtime progress channel: a non-issue

Use **SSE** (Server-Sent Events): progress is unidirectional server→browser; cancel is one HTTP POST. WebSocket buys nothing. The existing `_StageTracker` 1 Hz throttle is already transport-agnostic and **must be preserved** (a 100k-file scan without it would serialise 100k JSON objects/sec). Decouple scan-task lifetime from the SSE connection (server-side task registry keyed by scan-id; `EventSource` auto-reconnect with `Last-Event-ID`) so a browser refresh doesn't abort a running scan.

---

## 4. QA system portability — the owner's #1 concern (confirmed correct)

### 4.1 What exists today

`_batch.py` launches `main.py` as a subprocess, then drives it via **pywinauto / Windows UI Automation + ctypes**. The `_uia.py` helper library alone is **~2,465 lines / 74 functions**, built entirely on Win32 primitives (`EnumWindows`, `PostMessageW`, `MoveWindow`, COM `IFileSaveDialog`, UIA `ValuePattern`). 64 scenarios on master.

### 4.2 The real split (Playwright target)

| Class | Share | Notes |
|---|---|---|
| Clean port | ~50/64 | Business logic (scan, dedup, decisions, execute, regex, lock, prune, scoring, autotune). **Assertion logic transfers**; only the driver API changes. ~1–3 h each. |
| Needs redesign | ~10/64 | Window geometry persist (s39), locale-restart semantics (s22/s58), log-opens-Notepad (s18), exit-dirty prompt (s28). Rework = redefining what the web-equivalent behaviour *is*. ~4–8 h each. |
| No web analog | ~4/64 | Explorer-window spawn detection (s19, `CabinetWClass`), screen-pixel geometry precision (s39), Recycle-Bin integration, HEVC video codec backend (s11). Downgrade to API/property assertions. |

### 4.3 What's gained and lost

**Gained:** Playwright is structurally far more deterministic than UIA. These UIA workarounds **disappear entirely**: ~150 lines of foreground-lock management, the 3-attempt open-menu retry, ~300 lines of dual-shape native-dialog detection, the `y_min=600` pixel-bucket hack, the `WM_LBUTTONDBLCLK` double-click surgery. These are the documented sources of most CI flake. The new suite is headless, parallelisable, and DPI-independent (DOM order vs pixel buckets). Static probes (`tests/test_ui_probes.py`, AST inspection) need **zero** rework.

**Lost:** ~6% of scenarios lose their strongest invariant. s39's contract — "the window reopens at the screen-pixel position the user left it" (a `QSettings`→`restoreGeometry` property) — **has no browser analog**; the localStorage replacement tests a weaker property. s19's "a real Explorer window opened" assertion is abandoned for an API-level wiring test.

**Honest scope:** the dominant cost is the `_uia.py` driver library — ~2,465 lines of accumulated Qt-specific workarounds. A Playwright rewrite is a **standalone 4–8 week project** that must run as a parallel workstream and never block the cutover (keep the Qt QA suite as the CI gate until Playwright reaches parity).

---

## 5. UI surface inventory & "is web really more flexible?"

The flexibility claim is **true for chrome, inverted for the three surfaces that define this app**:

- ✅ **More flexible:** toolbar, status bar, menus, simple dialogs, theming, i18n, full-res pan/zoom (CSS `transform` beats Qt `QScrollArea` + manual rescale), video controls (HTML5 `<video>` beats Windows `QMediaPlayer`).
- ❌ **Harder in a browser:**
  1. **The decision tree** — 4 custom `QPainter` delegates (decision control, lock icon, score bar, similarity badge), virtualised, keyboard-driven (D/K/P). Initially flagged as "the single hardest surface", but **confirmed in §9 against the actual design prototype**: all 4 cells are *trivial* interactive DOM (the prototype implements every one as `<span>`/`<button>`/`<div>`/`<svg>`). The real cost is not the cells but the **scaffolding** (virtual-scroll library integration, keyboard/multi-select, the dual Aperture/Daylight row layouts, column-state persistence) and the **QA rewrite** of every test touching this widget.
  2. **ScanDialog's live OS filesystem tree** (`QFileSystemModel`) — no browser equivalent; falls back to a backend `/api/browse` listing API or typed paths. UX changes.
  3. **OS integration** (reveal-in-Explorer, open-in-default-app) — unavailable on any remote deployment; on localhost the backend can `subprocess.Popen` it.

Surface difficulty distribution (25 catalogued surfaces): 6 trivial, 10 moderate, 3 hard, 2 native-only.

---

## 6. Local filesystem, native integration & packaging

Because the backend is **localhost Python, it keeps full filesystem access** (local disks, mapped Synology NAS `J:`, UNC paths). Only the *browser frontend* is sandboxed. Capability-by-capability:

| Capability | Today | Web workaround | Severity |
|---|---|---|---|
| Pick source folders / open-save manifest | `QFileSystemModel` tree, `QFileDialog` | Backend `GET /api/browse?path=` + JS tree; manifest = download/upload | medium |
| Thumbnails & preview | `ImageService` → `QImage` | `GET /api/image?path=&size=` serving the existing JPEG disk cache | low |
| Full-res viewer | `FullResViewerDialog` | `GET /api/image?size=0` + JS pan/zoom (OpenSeadragon / CSS transform) | low (but see §3.3) |
| Embedded video (MOV/HEVC) | `QMediaPlayer` (WMF, plays HEVC) | `<video>` + Range endpoint; **plain Chrome lacks HEVC** → pywebview/WebView2 or FFmpeg transcode | medium |
| Reveal in Explorer / open default app | `subprocess.Popen(['explorer', …])` | Backend `POST /api/reveal` runs the same shell call (same machine) | none (localhost) |
| Delete to Recycle Bin | `send2trash` | **No change** — runs in backend, Qt-free already | none |
| Exotic HEIC/DNG decode (WIC/Shell ctypes) | `ctypes.windll.shell32` | Works headless on Windows with `CoInitializeEx` per thread-pool worker | low |

**Packaging recommendation: `pywebview` wrapping FastAPI on a localhost random port.** Rationale: single double-click executable (uvicorn in a daemon thread + `webview.create_window`); WebView2 uses the OS codec stack so **HEVC `.MOV` plays** (plain Chrome fails); OS shell calls stay in-process; existing PyInstaller spec extends cleanly; ~15 MB vs Electron's ~150 MB; no Rust/Node toolchain (vs Tauri). During development, a plain browser tab is simpler; pywebview is a runtime config of the same codebase for shipping.

> **Architectural cost of pywebview:** choosing it for HEVC turns the product from a "pure browser app" into an "embedded-webview app" (Electron-like). The trade is that you **lose the option to run the UI headless on a remote Linux server** — acceptable for a single-user desktop replacement, important to note for the cross-platform goal (§8).

---

## 7. Recommended target architecture

- **Backend:** FastAPI + uvicorn (single process), wrapping `scanner`/`core`/`main_vm` unchanged. SSE for scan progress. `pydantic` (already a dependency) for schemas. Scans in a dedicated worker process. `image_service` rewritten to return bytes; WIC COM calls in a `ThreadPoolExecutor` with a `CoInitializeEx` initializer.
- **Frontend:** **React + Vite + TypeScript + TanStack Table v8 + TanStack Virtual + ShadCN/UI.** TanStack Table v8 is the only candidate that handles per-cell custom interactive renderers + virtualisation + low-latency keyboard simultaneously. (Re-validated against the actual prototype in §9.)
  - **Rejected:** NiceGUI / Reflex (server-side state over WebSocket adds a round-trip to every D/K/P keystroke — unacceptable for a keyboard-driven review table; generated Vue/Next code is opaque, against the "transparent/reviewable" value). htmx (partial-swap can't drive a virtualised thousands-row interactive table at frame rate).
- **Packaging:** pywebview (see §6).

---

## 8. Cross-platform & the MVC architecture that minimises future tech debt

This is the strongest strategic argument in the whole evaluation, and it changes the recommendation.

**The web port's first phase IS the cross-platform MVC foundation.** Define a clean, Qt-free, framework-agnostic **application-core service layer** (the Model + Controller in MVC terms) that every view consumes:

```
            ┌─────────────────────────────────────────────┐
            │   Headless application core (Qt-free)        │
            │   • scanner/  • core/  • infrastructure/     │
            │   • NEW: a service/ facade exposing every    │
            │     operation as a plain Python API:         │
            │     scan(), load_manifest(), group(),        │
            │     sort(), decide(), execute(), get_image() │
            └───────────────▲──────────────▲───────────────┘
                            │              │
              ┌─────────────┘              └─────────────┐
        ┌─────┴───────┐                          ┌────────┴────────┐
        │  Qt adapter │  (today, thin)           │  Web adapter    │ (FastAPI
        │ app/views/  │                          │  routes→JSON/SSE│  + React)
        └─────────────┘                          └─────────────────┘
            (future: a native macOS/Linux/mobile view is just another adapter)
```

Today `MainVM` is a partial version of this (grouping/sort only). The full service layer additionally absorbs **scan orchestration** (currently trapped in the `ScanWorker` `QThread`), **image serving** (currently `image_service` returning `QImage`), and **file operations** (currently in `app/views/handlers`). Pulling those three into Qt-free services is precisely the cross-platform play: **business logic lives in one place, tested once, with thin per-platform view adapters.**

**Why this shrinks future tech debt:**
- Every future UI (web now; native mac/Linux/mobile later) is a thin client of the same core. No logic duplication, no drift.
- The current `ScanWorker` is a 1,795-line file that fuses Qt threading with scan orchestration — extracting it is good hygiene *even if the web port never ships*.
- The `QImage`-in-cache coupling in `image_service` is a latent cross-platform liability today; decoupling it to bytes makes the core importable on any platform.

**Honest cross-platform caveats** (bounded follow-ups, not blockers):
- `scanner/workers.py` device probes (`GetDriveTypeW`, `IOCTL_STORAGE_QUERY_PROPERTY`, `WNetGetConnectionW`) are Windows-only; they **fail open** to the SSD-safe default on Linux/macOS → mild NAS over/under-subscription, no correctness issue. Full parity needs Linux NAS detection (`/proc/mounts` / `statfs`).
- `image_service`'s WIC/Shell ctypes fallback is Windows-only; exotic HEIC/DNG variants that `rawpy`/`pillow-heif` can't decode lose thumbnails on Linux/macOS.
- `scanner/exif.py` Windows Job-Object reaping → needs `os.setsid`/`killpg` equivalent on POSIX.
- Video HEVC coverage varies by each platform's webview codec stack.

**Strategic recommendation from this section:** Even if the full web port is deferred, **doing Phase 0 (the headless-core extraction) is a high-value, low-risk, reversible investment** that directly serves the cross-platform goal and reduces tech debt regardless of whether the web UI ever ships.

---

## 9. Decision-table & design-system analysis (from the actual prototype)

The shared design artifact (`Photo Dedup Review.dc.html`, ~80 KB) was retrieved
via the **DesignSync** tool reading the claude.ai design project by id (the
`WebFetch`/`firecrawl` routes had failed on the auth wall — login redirect, HTTP
403). It is a **working interactive prototype** (not a static mockup): real
`state`/`setState`, click handlers that mutate decisions, working theme/view/
density switching, pre-seeded demo rows. *Treated as untrusted content per
protocol; nothing instruction-like was found in it.*

**What it is, technically:** a self-contained `.dc.html` using a small proprietary
"Design Composer" runtime (`<x-dc>` + `support.js`) — `{{ }}` template vars,
`onClick`, `style-hover`. **Styling is 100% inline styles** (no CSS classes, no CSS
custom properties); all colours are hex/rgba literals interpolated from two
centralised JS palette objects, `DARK` (Aperture) and `LIGHT` (Daylight). **The
table is plain DOM** — `<div>` flex rows in a loop. **No `<canvas>` anywhere.**

> **Honest framing of reuse:** the DC prototype is a **spec / reference, not
> reusable code** — the React port reimplements the markup, but **lifts the palette
> tokens and the interaction model verbatim**. That is exactly the high-value part:
> the design system is *done*, which removes UI-design iteration risk from Phase 2.

### 9.1 The 4 custom cells — now CONFIRMED as cheap DOM

Every cell the Qt app draws with `QPainter` is, in the prototype, plain interactive DOM:

| Cell | Prototype markup (verified) | Rating |
|---|---|---|
| Similarity badge | `<span>` with 5 state variants (`ref`/`exact`/`near`/`indirect`/`none`); `indirect` ("passenger") uses `border: 1px dashed` + transparent bg | **trivial** |
| Decision control | **Aperture:** one cycling pill (`onCycle` rotates keep→delete→remove→undecided). **Daylight:** 4 real `<button>`s in a strip (`onKeep/onDelete/onRemove/onUndecided`) | **trivial** |
| Score bar | nested `<div>`: track + `width:{score*100}%` gradient fill + monospace number (`—` when null) | **trivial** |
| Lock icon | inline `<svg>` padlock in a `<button>`, 2 colour states (accent when locked) | **trivial** |

This **confirms** the earlier conclusion and removes the last doubt: the per-cell
rebuild is cheap. (Two small notes for the build: the decision model is now
**4-state** — keep/delete/remove/**undecided** — not the Qt 3-segment control; and
the Daylight unlocked lock colour is hardcoded to a dark value `#39434f`, a
prototype oversight to fix on port.)

### 9.2 Density — a real toggle, mechanism captured

Compact/Comfortable lives in the status bar and is **not** a single row-height
variable — it branches a small metrics object that drives several dimensions:

```js
const dens = s.density==='compact'
  ? {pad:'4px 12px', thumb:26, gap:11, name:12.5, cell:11.5, track:48}
  : {pad:'9px 12px', thumb:42, gap:12, name:14,   cell:12.5, track:54};
```

(row padding, thumbnail px, column gap, filename + cell font sizes, score-track
width). Trivial in React — a density context selecting one of two token sets.

### 9.3 "Daylight" light theme — verbatim tokens (the prototype's gift)

Centralised in the `LIGHT` palette object; switched by `state.dir` selecting
`LIGHT` vs `DARK` and interpolating into inline styles (no CSS-var swap). **For the
React port, lift these into CSS custom properties** (`:root[data-theme=daylight]`):

```
text:#2c2823  dim:#867d70  dim2:#a89f8f  accent:#bd6b39  danger:#c4503f
rowBorder:#efe7d8  rowSel:#fbf3e9  delRow:#fdf3f0  hover:#faf5ec  track:#ece4d6
scoreFill: #cdab86 → #b8946a   densActive: bg #bd6b39 / fg #fff
sim.ref      fg:#a85f2e bg:#f7ebda bd:#e0bd92
sim.exact    fg:#6a4fb0 bg:#eee8fa bd:#cdbef0
sim.near     fg:#3f6fa8 bg:#eef4fb bd:#b9d0ea
sim.indirect fg:#7a756b bg:#ffffff bd:#cfc8bb  (dashed)
sim.none     fg:#8a8278 bg:#f1ece2 bd:#ddd5c8
dec.keep      fg:#2f8a5a bg:#e7f3ec bd:#bcdcc7
dec.delete    fg:#fff    bg:#c4503f bd:#c4503f
dec.remove    fg:#6f6a60 bg:#f1ece2 bd:#ddd5c8
dec.undecided fg:#9a9388 bg:transparent bd:#d8cfc0
```
Surfaces (Daylight): app `#f5efe6` · titlebar `#f3ede3` · toolbar `#faf6ee` ·
preview/card `#fffdf9` · status `#f3ede3` · group-card border `#ece3d4`.
(The dark "Aperture" palette is equally complete — `text:#e6edf3`, `accent:#d8a657`,
`danger:#e5534b`, etc.)

### 9.4 New nuance the prototype reveals: two distinct row layouts

Aperture renders a **flat compact table**; Daylight renders **card-per-group**
(`background:#fffdf9; border-radius:16px`, a card per duplicate group with its own
decision-button strip). This is **added scope** not in the original estimate — the
React port needs **two row renderers**, selected by theme — but it is modest and
the prototype fully specifies both.

### 9.5 What the prototype does NOT solve (so the cost centres stand)

- **No virtualisation** — only 9 demo rows, rendered flat. A real library of
  thousands still requires TanStack Virtual / AG Grid. Unchanged scaffolding cost.
- **No keyboard model** — no D/K/P (or arrow) handling in the markup. The
  keyboard + multi-row-selection interaction is still net-new build (and still the
  "moderate" part of the decision control, per §5).
- **Scan flow, filter input, real image loading, zoom** are stubs/placeholders.
- **QA** — the prototype is orthogonal to the test-harness rewrite (§4), the
  dominant cost, which no design can shortcut.

**Net effect:** the prototype **lowers frontend risk to roughly its floor** — the
design system, palette, density model, and interaction shape are all settled and
DOM-based. The two real cost centres are **unchanged**: table scaffolding
(virtualisation + keyboard/multi-select + the dual layout) and the **QA-harness
rewrite**. The §10 effort band and §12 recommendation stand; the design's
main contribution is removing *UI-design uncertainty* from Phase 2.

---

## 10. Migration path (incremental, reversible)

Each phase keeps the Qt app working and `qa-batch` as the CI gate until the final cutover.

| Phase | Weeks | Work | Gate |
|---|---|---|---|
| **0 — Service extraction** (zero user-visible change) | 2–4 | Extract `run_pipeline()` w/ `threading.Event` cancel; rewrite `image_service` → bytes (Qt side adds one `QImage.fromData()` line); parametrise `logging.py` paths; FastAPI skeleton + `/api/thumbnail`, `/api/browse`. **Also benefits the Qt app + cross-platform core.** | Qt + qa-batch |
| **1 — Scan API + SSE + QA parallel start** | 3–4 | `/api/scan/*` w/ SSE; begin Playwright rewrite (port `_uia.py` helpers first). | Qt primary; Playwright shadow |
| **2 — Frontend core** | 4–5 | React + TanStack decision tree (**prototype cell renderers week 1**); thumbnail grid; preview pane; pywebview shell. | Playwright reaches core parity |
| **3 — Remaining dialogs + OS integration** | 2–3 | ExecuteActionDialog, ScanDialog browse, `/api/reveal`/`/api/open` (localhost-gated), manifest up/download, i18n. | Playwright ≥90% |
| **4 — Cutover** | 1–2 | Playwright becomes primary CI gate; remove PySide6; update PyInstaller spec; archive `app/views/` in git history. | Playwright 100% (minus the 4 native-only) |

**Total: 18–24 person-weeks.** Top three schedule risks: (1) the TanStack inline decision-cell renderer — de-risk with a week-1 prototype; (2) QA-rewrite slip — never let it block Phase 4; (3) WIC COM STA threading edge cases.

---

## 11. Risk register (from the adversarial skeptic)

| Risk | Severity | Disposition |
|---|---|---|
| QA parity under-counted (`_uia.py` is the real cost; s39/s19 lose strongest assertions) | serious | Manageable; budget 4–8 wks standalone; accept property-test downgrade on ~6% |
| UX regression: full-res RAW over HTTP + HEVC video (NOT the delegate cells — §9 confirms all 4 are trivial DOM in the prototype; the only "moderate" is keyboard/multi-select scaffolding) | serious | Cells → TanStack custom renderers (cheap, palette already specified); real cost is virtual-scroll + dual-layout + QA; RAW → Range/JPEG tradeoff; HEVC → pywebview |
| "Port vs rewrite" framing | serious | Acknowledged: ~11% reuse / 89% new. Backend logic is proven and transfers; UX is rebuilt |
| Perf "unchanged" false for thumbnail path | manageable | Engineer the image server (cache + HTTP/2 + prefetch); not automatic |
| Hidden Qt coupling (`QImage`→Signal→`QPixmap` chain; 15 cancel sites; `QGuiApplication.primaryScreen()` in `image_tasks`) | manageable | Systematic; the `QImage`-in-cache budget metric (`sizeInBytes()`) → `len(jpeg_bytes)` |

Skeptic's overall call: *feasible_but_not_worth_it* **if** the only motive is UI-styling flexibility. This document's reconciled position differs once the cross-platform/MVC goal (§8) is weighed in — see §12.

---

## 12. Recommendation

The technical risk is **low** (the core transfers cleanly; the QA actually gets *more* reliable; the migration is incremental and reversible). The cost is **real** (a view-layer + QA-harness rewrite, 18–24 weeks). The decisive factor is **motive**:

- **If the goal is only "nicer UI styling"** → not worth a 20-week rewrite; Qt theming + the existing density/Daylight work already in flight gets you far, and the three core surfaces get *harder* in a browser.
- **If the goal includes cross-platform reach + shrinking long-term tech debt** (the stated forward goal) → this is a **sound, low-architectural-risk investment**, *because* the clean Qt-free core makes the headless-core/MVC extraction cheap and that extraction is the cross-platform foundation. Start with **Phase 0**, which pays off even if the web UI is deferred.

**The go/no-go is the owner's** — it is a product/strategy decision (what is this app *for*, long-term), not a pure technical one. The recommended low-regret first step is the **Phase 0 headless-core spike**: extract `run_pipeline()` and decouple `image_service` from `QImage`. It is reversible, improves the current Qt app, and is the cross-platform MVC foundation regardless of the eventual UI decision.
