---
name: scanner-perf-patterns
description: Audit photo-manager scanner and worker-thread code for known performance and threading anti-patterns. Use when /pr-review's diff touches scanner/**.py, app/views/workers/**.py, or adds a QThread / QRunnable / ThreadPoolExecutor — this skill flags per-row I/O in loops, nested O(N²) over filesystem, subprocess-in-loop without -stay_open batching, QThread.run() without progress/cancel, and missing timeouts. Composes with global photo-scanner-patterns for the domain boundary catalogue.
origin: local
---

# scanner-perf-patterns — Gate 9 rubric

Invoked by `/pr-review` Gate 9 when the diff touches scanner or
worker-thread code. Catches the specific bug patterns that have
hit this codebase before — most of them documented in the
global `photo-scanner-patterns` skill.

## Composes with global lens

**Before applying the patterns below, also read the global
`photo-scanner-patterns` skill** (Skill tool) to load the
boundary failure-mode catalogue: exiftool `-stay_open` batching,
PIL EXIF extraction quirks, pHash flat-image collision, NAS / SMB
latency, dedup union-find, single-read SHA+pHash+EXIF, Google
Takeout sidecar matching, Windows trailing-period folders.

That global skill is written for *implementers* (≈1000+ lines of
how-to). This skill is the *reviewer's* counterpart — it catches
the same issues as code review of the resulting diff, without
re-deriving the patterns.

## When to invoke

`/pr-review` invokes this skill when the diff touches:

- `scanner/**.py`
- `app/views/workers/**.py`
- Any new `QThread`, `QRunnable`, or `ThreadPoolExecutor`

Skip otherwise.

## Patterns to flag

### Per-row I/O inside a loop over files

- `for row in rows: data = Path(row.path).read_bytes()` —
  flag if the loop runs over thousands of files. The scanner
  explicitly does single-read SHA-256 + pHash + EXIF from one
  `read_bytes()` — don't add a second `read_bytes()` per row
  in a different stage.
- `for row in rows: Image.open(row.path)` inside a hot loop
  without batching → ⚠.

### Nested loop over filesystem paths

- `for a in all_paths: for b in all_paths: ...` — flag as O(N²)
  even if N looks small (NAS scans hit 100k+ files).
- Pairwise near-duplicate scanners must use the outer-vs-inner
  placement from `photo-scanner-patterns`; flag if the new code
  appears to do pHash compare in the inner loop.

### Subprocess in a loop without `-stay_open` batching

- `for path in paths: subprocess.run(["exiftool", path], ...)` —
  flag. The project batches via `-stay_open` for thousands of
  files; per-file spawn is the documented 10–100× slowdown.

### Blocking call inside a `QThread.run()` without progress / cancel

- New `class FooWorker(QThread)` whose `run()` has no `emit()`
  progress and no `if self._cancel: return` check → ⚠ for
  "user can't tell what's happening / can't abort".
- The pattern documented in the global `photo-scanner-patterns`
  skill — match it.

### `subprocess.run` without a timeout in user-facing code

- If a subprocess can hang on a malformed file (e.g.,
  `exiftool` on a corrupted HEIC), and the call is in a
  user-facing thread (not the dedicated scanner pipeline that
  has its own timeout layer), → ⚠ "add a `timeout=` kwarg".

## What NOT to flag

- A single `read_bytes()` per row in the scanner pipeline — that's
  the documented design.
- O(N²) over tiny lists (group decisions in a single dedup group,
  typically <100 elements) — that's bounded; don't be pedantic.
- A QThread without progress when the workload is sub-second
  (e.g., loading a small config file).
- `subprocess.run` of internal scripts the project itself ships
  with a known runtime.

## Output format

Emit findings under the `## Performance / threading (Gate 9)`
section of pr-review's chat report, one line per finding:

```
⚠ <file:line> — <pattern>: <evidence>
```

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `photo-scanner-patterns` (global) — boundary catalogue,
  composed at the top of this skill.
- `scanner/manifest.py`, `scanner/dedup.py`,
  `scanner/hasher.py` — code paths most often touched by
  scanner/perf-related PRs.
