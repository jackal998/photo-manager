# #591 RAW in-memory read — adversarial-review decision record (2026-06-06)

**Status:** CONVERGED (0 unrebutted load-bearing objections). Root cause + best
solution verified against ground truth. Implemented in this PR.

**Method:** [`adversarial-review`](../../.claude/skills/adversarial-review/SKILL.md)
— two independent peers (Opus + Sonnet, with Bash + the real DNG files) attacked
the *candidate fix*, round 1 blind, round 2 cross-attack; LEAD judged each
objection on real runs. Transcript: workflow `wf_854795cb-a5d`.

---

## The artifact

The proposed fix for #591: on rawpy 0.26.1 the module-level `rawpy.open_buffer`
does not exist, so the RAW in-memory decode path in `scanner/hasher.py` dead-ends
on `AttributeError` and falls back to `rawpy.imread(str(path))` — re-reading the
file from disk (3 touches per RAW). Candidate: replace the dead `open_buffer(data)`
calls with `rawpy.imread(io.BytesIO(data))` (rawpy's `imread` accepts a file
object), reusing the bytes already read for SHA-256.

## The reframe (the debate's main result)

**#591 is NOT a performance fix.** The ticket's "RAW read 3× over SMB → slower
scans" premise is largely false:

- SHA-256 **requires** the full bytes, so `read_bytes()` always runs first
  (`hasher.py`) — which **warms the OS/SMB page cache**. The two redundant
  `imread(path)` re-reads therefore hit warm cache (~1–6 ms), **not the wire**.
- Measured (both peers + LEAD): at matched cache state `imread(path)` ≈
  `imread(BytesIO)` within ~1–17 ms; a cold first-touch costs 0.26–1.1 s over SMB
  but `read_bytes` absorbs it. The headline "0.677s vs 0.392s" was a
  measurement-order/cache confound (whichever runs second wins).

So this is a **correctness + dead-code + false-docstring** fix at `priority: low`,
with a small NAS side-benefit — not a throughput win.

## Verified findings (LEAD re-ran each load-bearing claim)

- **Bug real:** `hasattr(rawpy, "open_buffer") == False`; the in-memory path
  returns None live and the code silently re-reads from disk.
- **Fix correct:** `imread(io.BytesIO(data))` yields a **bit-identical phash**
  (and sensor dims) vs `imread(path)` on both the **thumbnail** path and the
  **postprocess** path (pixel-exact, `np.array_equal`) across multiple DNGs →
  **no `HASH_RECIPE_VERSION` bump**, no grouping drift.
- **#75 preserved:** a non-camera TIFF raises `LibRawFileUnsupportedError`
  (a `LibRawError` subclass) on the BytesIO path too → caught → skips, not crash.
- **#587 RAM-neutral (refuted scare):** `io.BytesIO(data)` over immutable bytes
  does **not** copy (`getvalue() is data`, +0 MB heap, object is 80 bytes), so the
  byte-budget's `len(data)` accounting is unaffected. (Round-1 "3× transient RAM"
  objection refuted in round 2 + by LEAD.)
- **postprocess colour-drift (refuted scare):** decoded RGB byte-for-byte
  identical between path and BytesIO.

## Implementation constraints the fix respects

- **Fresh `io.BytesIO(data)` per call site** — a reused/non-zero-position buffer
  raises `LibRawIOError` (a `LibRawError` subclass) and would silently degrade to
  a disk re-read. Both sites (`_load_raw_preview_from_bytes` and the dims branch
  of `_hashes_from_data`) construct a fresh buffer.
- **Literal `io.BytesIO(data)`, never `imread(data)`** — passing raw `bytes`
  raises a `SystemError` (an `Exception` subclass → caught by the broad handler →
  a loud per-RAW `HashFailure`, not silent). The new test guards this.
- **Single-read claim scoped to valid camera RAW** — non-camera TIFFs routed to
  `raw` still hit the `_load_raw_preview(path)` fallback, which is what preserves
  #75 skip-not-crash.

## Decision

Ship the **simple per-site substitution**; **decline the "collapse two opens into
one" refactor** — it buys ~0 measured wall-time and adds a #187 dims-source
landmine (the test DNGs cannot distinguish `raw.sizes` from thumbnail dims) plus
the seek/reuse hazard. Alternatives (A) upgrade rawpy (gated, phash-drift risk)
and (B) `imread(path)` for both (still a disk re-read) are inferior.

**Test gap closed:** the existing RAW tests mock `rawpy` entirely, so the
in-memory path shipped untested (that is *why* the dead-end slipped through). This
PR adds a real, non-mocked test driving `rawpy.imread(io.BytesIO(data))` on a
non-camera TIFF (asserts SHA-only / no crash) — verified to fail on the
`imread(data)` typo.

## Convergence gate

Objections raised: **14** · load-bearing: **11** · unrebutted: **0** ⇒ converged.
Independence: Opus vs Sonnet. Refuted: the 3×-RAM claim and the postprocess
colour-drift fear. Residual (low, non-blocking): cross-format phash identity
beyond DNG (.arw/.cr2/.nef untestable here) and a true no-thumbnail postprocess
flow — mechanism (same LibRaw, identical bytes) makes drift very unlikely.
