# GROUPING 11 design: EXACT pixel identity on a 128x128 grey signature

## Lead decisions (2026-11-08, after the planner returned; these override the text below where they differ)

- **Spill location (risk 3): the system temp directory, not next to the manifest.** `grey_store_path()` is replaced by a store that creates its file with `tempfile.NamedTemporaryFile(prefix="photo-manager-grey128-", suffix=".tmp", delete=False)` on first `put` and unlinks it in `close_and_delete()`. A manifest may live on the NAS; 655 MB of signature writes belong on local disk. The leftover-truncation benefit is replaced by unique names (a hard kill leaves one temp file, which the OS temp cleanup removes).
- **Census parity (risks 1, 2) is a merge gate the lead runs:** the 137-pair census is re-run through the real `_grey_signature` (drafted JPEG working image, full HEIC/PNG decode) and `_grey_identical`, expecting 126 EXACT-eligible and 11 not; the probe, SHA and output are recorded in STATE.md. If the drafted-JPEG asymmetry moves any honest pair above the gate, the lead re-calibrates before merge; the implementer does not touch the thresholds.
- **Risk 4 (B8 off + medium threshold keeps the demoted pairs deletable):** that is the user's explicit choice; say so in the scan.md B8 sentence, nothing else.
- **Risk 5 (dHash-unknown pairs become hints):** intended.
- **Risk 6 (Pillow `reduce` on mode F):** the implementer verifies it on the installed Pillow in the venv; if unsupported, use the `resize((32, 32), Image.BOX)` fallback the planner gives. Either way the boundary tests pin the arithmetic.
- **Risk 7 (rgb32 as three floats):** out of scope; follow-up.
- **Risk 9 (census link):** link `docs/audits/dedup-core-research-2026-11-05/STATE.md` (the 2026-11-08 B7 and pixel-identity entries) from the B7 sentences.
- The stale 7-tuple at tests/test_scan_pipeline.py:3158 is out of scope.
- This document lands in the repo as docs/audits/dedup-core-research-2026-11-05/grouping11-design.md with the PR.


## Summary

- **Code anchors verified.** I read the gate, the hasher, both scan paths, the tests and the docs at HEAD `aaa297b`. No edits were made. Two points are not verified and are marked inline: Pillow `reduce()` on mode "F", and the census instrument.
- **New signature.** A per-file `grey128` (16,384 bytes) is computed in the hasher next to `rgb32`. It rides on `HashResult` as a `GreySig` reference. In the parent process it is moved into a scan-local file store, once per file. The thread path moves it inside the compute done-callback. The process path moves it in a future done-callback. `_pixels_identical` keeps the dimension check and the rgb32 channel-shift check. The 32×32 mean-difference test is replaced by a block test and a tail test on grey128.
- **No numpy.** Production code never imports numpy; it only arrives as a transitive dependency of imagehash (`requirements.txt:14`). The gate uses Pillow C ops instead: `ImageChops.difference`, `histogram`, `reduce`. Pillow is a hard dependency (`requirements.txt:10`), so nothing new is declared.
- **Delete eligibility after the fix.** The 11 false pairs become `REVIEW_DUPLICATE`, which ranks "medium" (`core/services/auto_select.py:248-249`). They leave the aggressive-delete set because B8 is on by default and blocks every row that is not EXACT (`auto_select.py:351`, `core/services/auto_select_rules.py:63`). With B8 off and the threshold at "medium" they would still be eligible (see Risks).
- **One version bump beyond the lead's list.** `HASH_RECIPE_VERSION` must also go "5" to "6". The hasher's own rule requires a bump whenever per-file cost changes (`scanner/hasher.py:103-107`).

## Data flow (ASCII, ≤ 20 lines)

```
run_hash_for_record / compute_from_bytes            scanner/hasher.py:369,448
  _hashes_from_data -> _grey_signature(img) -> 16,384 B   (after exif_transpose)
  HashResult(grey128=GreyBytes(b))          process pool: pickled, +16 KB/file
        |                                       |
  thread: compute done-cb _cb               process: fut.add_done_callback
  (scan_runner.py:1242)                     (scan_runner.py:1063)
        \________ _spill_grey(outcome, grey_store) ________/
                  | store.put(b) -> index   (lock; parent process only)
                  v
  <manifest>.grey128.tmp   fixed 16,384-byte records, opened on first put
                  | outcome.grey128 = StoredGreySig(store, index)
                  v
  out_q / as_completed -> _route_outcome -> hash_results[idx]   (:1084, :1298)
                  v
  classify (:1393) -> _format_verdict (dedup.py:799) -> _pixels_identical
                  | a.grey128.bytes() -> store.get(i)   (lock)
                  v
  grey_store.close_and_delete()  after classify (:1405) + run_pipeline finally
```

## Changes by file

### New: `scanner/grey_store.py` (about 90 lines, stdlib only)

It imports stdlib only, so `scanner/dedup.py` and `scanner/hasher.py` can both import it without a cycle.

- `GREY_SIDE = 128` and `GREY_SIG_BYTES = GREY_SIDE * GREY_SIDE` (16,384).
- `class GreySig(Protocol)` with one method: `def bytes(self) -> bytes`.
- `@dataclass(frozen=True) class GreyBytes` with field `data: bytes`, and `bytes()` returns `data`.
  - This is the in-memory form, used by the hasher and by tests.
  - It is picklable, so it can cross the process pool.
- `@dataclass(frozen=True) class StoredGreySig` with fields `store` and `index`, and `bytes()` returns `store.get(index)`.
  - It is created only in the parent and never pickled.
- `class GreySigStore`, with the full contract under "Store design".
- `def grey_store_path(output_path: str | Path) -> Path` returns the manifest path with `.grey128.tmp` appended to its name.
  - It must wrap the argument in `Path`. Pipeline tests pass `output_path` as a str (`tests/test_scan_pipeline.py:198`).

### `scanner/hasher.py`

1. **Import.** Add `from scanner.grey_store import GREY_SIDE`.
2. **New helper after `_pixel_signature` (`:164-172`).**
   ```python
   def _grey_signature(img: "Image.Image") -> bytes:
       """128x128 grey BOX thumbnail, 16,384 raw bytes: the structure half of
       the EXACT gate (dedup ``_grey_identical``). ``img`` is the EXIF-oriented
       RGB working image. Scan-time only; spilled to GreySigStore, never persisted."""
       return img.convert("L").resize((GREY_SIDE, GREY_SIDE), Image.BOX).tobytes()
   ```
   - The order is convert first, then resize, as the lead specified, so the census numbers carry over.
3. **Return tuples grow from 10 to 11 elements.** The new element is `grey128: Optional[bytes]`, placed after `rgb32`.
   - Annotations and docstrings change at `:177-186` and `:304-308`.
   - `:201` gets 11 `None`s.
   - `:277` and `:299` get a trailing `None`.
   - `:281-293` adds `_grey_signature(img)` after `_pixel_signature(img)`.
   - `:325` (the video early exit) gets 11 elements.
4. **Unpack sites.** Add `grey128` at `:391-394`, `:472` (`phash_quality = rgb32 = grey128 = None`) and `:474-477`.
5. **HashResult constructions.** At `:407-419` and `:487-499`, add `grey128=GreyBytes(grey128) if grey128 is not None else None`.
6. **Recipe version.** `HASH_RECIPE_VERSION = "6"` at `:132`, with a comment line: `# "6" — an eleventh element, the 128x128 grey signature grey128 (GROUPING 11). Hash VALUES unchanged; per-file cost is one more convert("L") + BOX resize.`
   - Its only consumer is the calibration fingerprint (`core/app_service/scan_runner.py:313-321`). Nothing pins its value in tests (grep of `HASH_RECIPE_VERSION` in tests finds only monkeypatches at `tests/test_scan_pipeline.py:975-977`).

### `scanner/dedup.py`

1. **Import.** `from scanner.grey_store import GREY_SIDE, GREY_SIG_BYTES, GreySig`. No cycle results.
2. **New HashResult field after `rgb32` (`:109`).**
   ```python
   # GROUPING 11 — the 128x128 grey signature of the EXACT gate
   # (``_grey_identical``), behind a reference: in memory (GreyBytes) or in the
   # scan-local GreySigStore (StoredGreySig). Never persisted. None demotes EXACT.
   grey128: Optional[GreySig] = None
   ```
3. **Constants (`:655-666`).**
   - Delete `FORMAT_DUP_MAX_RGB_DIFF`. Its only consumer is `:689-690`, and no test imports it.
   - Keep `FORMAT_DUP_MAX_CHANNEL_SHIFT`.
   - Add the following, and rewrite the block comment to describe the two-signature gate and cite the 2026-11-08 census (126 honest pairs: block ≤ 2.2 and p99.9 ≤ 4; 11 different pictures: block ≥ 6.6 and p99.9 ≥ 12).
   ```python
   GREY_BLOCK = 4             # 4x4-pixel blocks -> a 32x32 grid
   GREY_BLOCK_MAX = 4.0       # max block-mean |diff|
   GREY_TAIL_MAX = 8          # per-pixel |diff| ceiling for the 99.9th percentile
   GREY_TAIL_BUDGET = GREY_SIG_BYTES // 1000   # 16 pixels may exceed GREY_TAIL_MAX
   ```
4. **New `_grey_identical(a: bytes, b: bytes) -> bool`**, specified under "Gate algorithm".
5. **`_pixels_identical` (`:675-695`).** The new body:
   ```python
   if a.rgb32 is None or b.rgb32 is None or a.grey128 is None or b.grey128 is None:
       return None
   # dims check :685-688 unchanged
   shift = max(abs(x - y) for x, y in zip(_channel_means(a.rgb32), _channel_means(b.rgb32)))
   if shift > FORMAT_DUP_MAX_CHANNEL_SHIFT:
       return False
   try:
       return _grey_identical(a.grey128.bytes(), b.grey128.bytes())
   except OSError:
       return None   # store read failed: counted by the store, logged by scan_runner
   ```
   - Rewrite the docstring and keep the 90° paragraph, which is proven below.
   - The reason texts in `_format_verdict` (`:801-821`) do not change.
6. **The 90° swap needs no rotation; the two grey128 compare directly.**
   - `pixel_width/height` are read before `draft()` and `exif_transpose` (`hasher.py:240`).
   - The working image `img` is made after them (`hasher.py:260`, `:271`), and `_grey_signature(img)` runs on that `img` at `:292`.
   - So both signatures are of the display-oriented picture. Two display-identical files have the same display aspect, and the 128×128 squash treats them identically. Only the stored dimensions differ, by a swap, and `:687` already tolerates that.
   - The RAW preview path applies `exif_transpose` as well (`hasher.py:608-615`).
   - The existing test `tests/test_hasher.py:1242-1264` proves the same property for rgb32: a tagged Orientation=6 PNG equals the upright file, and the untagged one differs.
   - If a malformed tag makes `exif_transpose` fail (`hasher.py:261-270`), that file is hashed un-rotated. The diff is then large and the verdict is False, which is the safe direction.
   - I did not verify whether pillow-heif reports `size` before or after its transform. Either way the swap check covers it.
7. **Version (`:1013`).** `GROUPING_STRATEGY_VERSION = "11"`, preceded by:
   ```python
   # "11": EXACT's pixel identity is decided on a 128x128 grey signature (grey128,
   # spilled to a scan-local GreySigStore): max 4x4 block-mean |diff| <=
   # GREY_BLOCK_MAX and at most GREY_TAIL_BUDGET pixels over GREY_TAIL_MAX. The 32x32
   # RGB mean-difference test is gone; the rgb32 channel-shift test stays. A moved
   # person, a shifted second shot or a changed text line is REVIEW, not EXACT.
   ```
   - Also edit the "8" history comment at `:996-1001` to say that GROUPING 11 superseded condition 2.

### `core/app_service/scan_runner.py`

1. **Lifecycle wrapper.** Rename the current `run_pipeline` body (`:563`) to `_run_pipeline(config, cancel_token, bus, grey_store)` and add a thin public wrapper:
   ```python
   def run_pipeline(config, cancel_token, bus) -> None:
       """... (docstring kept) GROUPING 11: owns the scan-local grey signature store."""
       grey_store = GreySigStore(grey_store_path(config.output_path))
       try:
           _run_pipeline(config, cancel_token, bus, grey_store)
       finally:
           grey_store.close_and_delete()
   ```
   - The body has 10 early `return`s (`:696`, `:702`, `:1082`, `:1292`, `:1334`, `:1386`, `:1411`, `:1421`, `:1477`, `:1493`). The `finally` covers all of them without re-indenting about 900 lines.
   - The file opens lazily on the first `put`, so a scan with no images creates nothing.
2. **Module helper `_spill_grey(outcome, store) -> None`.**
   ```python
   grey = getattr(outcome, "grey128", None)
   if not isinstance(grey, GreyBytes):
       return
   try:
       outcome.grey128 = StoredGreySig(store, store.put(grey.bytes()))
   except (OSError, ValueError, RuntimeError):
       outcome.grey128 = None      # store.failures already counted; REVIEW, never EXACT
   ```
   - It never raises. That matters because `Future._invoke_callbacks` logs and swallows exceptions from callbacks.
   - It mutates the HashResult in place, which is the existing precedent at `:1379`.
3. **Thread path (`:1242-1244`).**
   ```python
   def _cb(f, _dev=c_dev, _n=n):
       byte_budgets[_dev].release(_n)
       result = f.result()
       _spill_grey(result[1], grey_store)
       out_q.put(result)
   ```
   - The spill happens before `out_q`, which is unbounded (`:1149`). So `out_q` and the bounded `exif_queue` (`:737`, which holds HashResults through `:780`) only ever hold the small reference.
   - Concurrent compute threads call `put`, which is why the store has a lock.
4. **Process path (`:1062-1063`).** Every future is submitted up front. If the main loop stalls in `_route_outcome` on a full `exif_queue`, completed results pile up, so the spill must not wait for the main loop:
   ```python
   fut = pool.submit(run_hash_for_record, _idx, _r)
   fut.add_done_callback(_spill_done)   # parent-side: executor manager thread
   futures[fut] = _idx
   ```
   - `_spill_done(f)` is defined in the body: `if not f.cancelled() and f.exception() is None: _spill_grey(f.result()[1], grey_store)`.
   - There is a race, but it is harmless. CPython's `Future.set_result` notifies waiters before it runs callbacks, so `as_completed` (`:1066`) may route the HashResult before the spill. Both `GreySig` implementations return identical bytes, so whichever one classify sees is correct.
5. **Calibration samples need no change.** `_calibrate_hash_pool` (`:108-142`) discards its `run_hash_for_record` results. They carry `GreyBytes` briefly, in memory only, for the sample.
6. **Visible degradation.**
   - After the hash stage (after `:1336`): if `grey_store.failures`, log `"  WARNING: {n:,} pixel signature(s) could not be stored; those files get 'pixel check unavailable'"`.
   - After classify (`:1405`), log the same line for read failures, then call `grey_store.close_and_delete()` to free the disk before scoring and the manifest write. The wrapper's `finally` makes this idempotent.
7. **Process message size.** Results come back through the executor's pipe as one pickled result per file. Since Python 3.8 there is no practical size cap, and nothing in the repo caps it. The extra cost is 16 KB per file. At 40,000 files that is 655 MB in total over the run, streamed and never resident.

## Store design

| Member | Contract |
|---|---|
| `__init__(path)` | No I/O. Sets a `threading.Lock`, `count=0`, `failures=0`, `_fh=None` and `_closed=False`. |
| `put(data) -> int` | Raises `ValueError` if `len(data) != GREY_SIG_BYTES`, which means fail fast. Raises `RuntimeError` after close. Under the lock: on the first call it runs `path.parent.mkdir(parents=True, exist_ok=True)`, because the manifest's directory may not exist yet (`scanner/manifest.py:114` creates it only at write time). It then opens `open(path, "w+b")` in binary mode, so CRLF is never an issue, and truncation wipes a leftover from a killed scan. It seeks to `count*16384`, writes, returns `count` and increments it. An `OSError` increments `failures` and re-raises. |
| `get(index) -> bytes` | Under the lock: raises `IndexError` outside `[0, count)`, seeks, reads 16,384 bytes, and raises `OSError` on a short read. A failure increments `failures`. `BufferedRandom` flushes on seek, so a read after a write is coherent. |
| `close_and_delete()` | Idempotent. Under the lock: closes the handle, then `unlink(missing_ok=True)`, which must come after the close on Windows. A `PermissionError` from antivirus is logged and not raised, because the next scan truncates the file anyway. |
| `__enter__/__exit__` | Calls `close_and_delete`. Used by tests. |

- **Size.** At 40,000 images the temporary file is 655 MB. Reads during classify happen only for pHash-0 lossy pairs: one seek and a 16 KB read each, roughly 10-20 µs (an estimate, not measured).

## Gate algorithm

```python
def _grey_identical(a: bytes, b: bytes) -> bool:
    from PIL import Image, ImageChops          # lazy: dedup stays importable without PIL
    size = (GREY_SIDE, GREY_SIDE)
    d = ImageChops.difference(Image.frombytes("L", size, a), Image.frombytes("L", size, b))
    if sum(d.histogram()[GREY_TAIL_MAX + 1:]) > GREY_TAIL_BUDGET:   # p99.9 > 8
        return False
    return d.convert("F").reduce(GREY_BLOCK).getextrema()[1] <= GREY_BLOCK_MAX
```

- **d.** `ImageChops.difference` on mode "L" is exactly `|a-b|` as an integer from 0 to 255.
- **p99.9 test.** It is defined by count: at most 16 of the 16,384 pixels may have d > 8.
  - This equals the nearest-rank 99.9th percentile being ≤ 8: the 16,368th smallest value of d (`ceil(0.999·16384)`) must be ≤ 8.
  - It can differ from numpy's default linear percentile only when the two adjacent order statistics are far apart. The census margin (≤ 4 against ≥ 12) makes that irrelevant.
  - A count is integer-exact and easy to test.
- **Block test.** `reduce(4)` on "F" averages each 4×4 block into a 32×32 grid.
  - The sums are of integers and the division is by 16, a power of two, so float32 is exact.
  - **Not verified:** that Pillow 10 supports `reduce` on mode "F". The boundary tests below pin the result. If "F" is unsupported, use `d.convert("F").resize((32, 32), Image.BOX)`, which gives the same exact mean for a 4× integer reduction.
- **Order and cost.** The tail test runs first because it is the cheaper check. The whole function is about 30-80 µs per pair, all in C (an estimate).
  - The pure-Python alternative is about 3-6 ms per pair (an estimate). At 3,000 pairs that is about 15 s added to classify, which is why I rejected it.

## Tests (new / changed)

### New

**`tests/test_grey_store.py`**
- Round-trip three records in order.
- `put` with 16,383 bytes raises `ValueError`.
- `get(count)` raises `IndexError`.
- `close_and_delete` removes the file, a second call is a no-op, and `put` afterwards raises `RuntimeError`.
- A leftover file at the path is truncated on the first `put`.
- A missing parent directory is created.
- Eight threads each `put` distinct bytes, and every index returns its own bytes.
- No file exists when nothing was `put`.
- `pickle` round-trips a `HashResult` carrying `GreyBytes`. This is the real process-pool contract; the spy pool at `tests/test_scan_pipeline.py:683` never pickles.

**`tests/test_dedup_grey_gate.py`** calls `_pixels_identical` and `_grey_identical` directly. Base is `bytes([100])*16384`.
- Uniform noise of 0 or +1 gives True.
- A 12×12 patch at +60 gives False. It fails both tests.
- An aligned 8×8 patch at +6 gives False through the block test only: no pixel exceeds 8, and the block mean is 6.
- 33 pixels (0.2 %) at +60, one per 4×4 block, give False through the tail test only: each block mean is 3.75, which passes.
- Boundaries:
  - One aligned block at +4 has a block mean of exactly 4.0 and gives True.
  - The same block with one pixel at +5 has a mean of 4.0625 and gives False.
  - 16 scattered pixels at +60 give True; 17 give False.
- `grey128` None on either side gives None.
- `rgb32` None gives None.
- Dimensions 4000×3000 against 3999×3000 give False.
- A 90° dimension swap with equal signatures gives True.
- A channel shift above 4 with identical grey gives False.

**`tests/test_hasher.py::TestGrey128Signature`** (beside `:1208`)
- A JPEG yields `out[10]` as bytes of length 16,384.
- The video and undecodable early exits give 11 elements with `out[10]` None.
- An orientation twin of `:1242-1264` asserts that grey128 of the tagged file equals the upright one, and the untagged one differs.

**Classify level, in `tests/test_dedup_exact_pixels.py`**
- Equal rgb32 with a 12×12 grey128 patch at +60 gives `REVIEW_DUPLICATE` with reason `PIXELS_DIFFER`, in the keeper's group.
- Equal rgb32 with grey noise of 0 or +1, a plain re-encode, stays `EXACT`.
- grey128 None on the duplicate gives `PIXEL_CHECK_UNAVAILABLE`.

**`tests/test_dedup_grouping10.py::TestPixelIdentityIsASecondSignal`**
- Same rgb32, a grey patch and no dHash gives a hint containing "pixels differ" and "dHash unavailable", and no group. This is the behaviour shift at `dedup.py:800`.

**`tests/test_scan_pipeline.py`**
- Two JPEGs with the same encoded pixels and different EXIF are a metadata-only copy: different SHA, identical pixels.
- Run once on the thread path and once with the `SpyProcessPool` pattern (`:683-691`).
- Assert one `EXACT` row, and that `grey_store_path(out)` does not exist afterwards.
- Add one assertion to an existing cancel test (for example the one around `:1460`) that no `.grey128.tmp` remains.

### Changed

| Location | Change |
|---|---|
| `tests/test_dedup.py:59` helper | Add `grey128: GreySig \| None = GreyBytes(bytes(16384))`. Without it every format-duplicate EXACT test flips to "pixel check unavailable". |
| `tests/test_dedup_grouping10.py:44` helper | The same default. |
| `tests/test_dedup_quality_gate.py:56` | Add `grey128=GreyBytes(bytes(16384))`. |
| `tests/test_dedup_time_gate.py:47` | The same. |
| `tests/test_dedup_exact_pixels.py:62` helper | Default `grey128=GREY_KEEPER`. Rewrite the module docstring (`:1-15`) to the new three conditions. |
| `test_b_one_channel_shifted` (`:104`) | Still REVIEW, now by channel shift 20. Fix the comment, which talks about mean diff. |
| `test_b_mad_5_5_with_shift_2` (`:122`) | Still EXACT. Comment that the mean difference is no longer a gate. |
| `test_g_mean_diff_exactly_six` (`:191`) | Becomes the grey block-mean 4.0 boundary, which stays EXACT. |
| `test_g_mean_diff_just_above_six_demotes` (`:195`) | Would fail, because it is now EXACT. Replace it with block mean 4.0625, which demotes. Delete `SIG_MAD_6_SHIFT_2` and `SIG_MAD_6_POINT_1` (`:45-50`). The commit message must justify the removed assertion (testing policy). |
| `test_c_rotated_dims_different_pixels` (`:142`) | Keep it. Add a grey-only variant: swapped dimensions plus a grey patch gives REVIEW. |
| All other tests in that file | Unchanged. `test_a`, `test_c` dims, `test_d`, `test_e`, `test_f`, the `test_g` shift tests, `test_h` and `test_i` keep their verdicts. |
| `tests/test_hasher.py` length assertions | `len(out) == 10` becomes 11 at `:1124`, `:1157`, `:1219`, `:1229`, `:1238`. |
| `tests/test_hasher.py` fixed unpacks | Add `_g` to the 10-name unpacks at `:306`, `:366`, `:1017`, `:1066`. Update the arity wording in the `TestRgb32PixelSignature` docstring (`:1209-1211`). |
| Unaffected | `test_dedup_burst_stack`, `test_dedup_hint_edges`, `test_dedup_live_pair`, `test_scanner_manifest`, `test_scanner_exif` and `integration/test_autotune_ab` set no rgb32, so their verdicts are already "unavailable" and do not change. |
| Noticed, not changed | `tests/test_scan_pipeline.py:3158` returns a 7-tuple, which is already stale against today's 10. It keeps failing the unpack and turning into a `HashFailure`. |

## Docs

### `docs/features/scan.md`

- **`:231` Entry point.** "signature: scanner/hasher.py `_pixel_signature`" becomes "signatures: scanner/hasher.py `_pixel_signature` (colour) and `_grey_signature` (structure); scan-local store scanner/grey_store.py".
- **`:233` Behaviour.** Conditions (2) and (3) become:
  > (2) the largest per-channel mean shift of the 32×32 RGB signatures is ≤ 4 (`FORMAT_DUP_MAX_CHANNEL_SHIFT`), which catches a colour edit; (3) on the 128×128 grey signatures, no 4×4-pixel block differs by more than 4 on average (`GREY_BLOCK_MAX`), and at most 16 of 16,384 pixels (0.1 %) differ by more than 8 (`GREY_TAIL_MAX`), which catches a person who moved, a shifted second shot or a changed line of text.
- **`:234` Conditions.** "The signature is computed at scan time and not persisted to the manifest." becomes:
  > Both signatures are computed at scan time and never persisted. The grey signature (16 KB per image) is kept in a temporary file next to the manifest (`<manifest>.grey128.tmp`) and deleted after classification, so memory stays flat. A file whose signature cannot be stored gets "pixel check unavailable", never `EXACT`.

  The version sentence becomes: `GROUPING_STRATEGY_VERSION` is `"11"` (`"8"` introduced this check; `"11"` replaced the 32×32 mean difference with the grey block and tail test).
- **`:235` Related.** Add `tests/test_dedup_grey_gate.py`, `tests/test_grey_store.py` and `tests/test_hasher.py::TestGrey128Signature`.
- **Current-version claims.** Lines `:245` and `:266` say `"10"`; set them to `"11"`. Line `:278` says `"9"` and is already stale; set it to `"11"` as well.
- **`:100` B7 sentence.** "B7 is on because turning it off changes default grouping; flipping it is a later decision." becomes:
  > B7 stays on by default. A census on 2026-11-08 found that turning it off produced two false merges of bank-app receipt screenshots in 2,043 files, so the PRD v2 default of off was not adopted.

### `docs/audits/auto-select-prd-2026-11-v2.md`

- **`:194`.** "by default it is off, so screenshot pairs are not capped at low." becomes "by default it is on (owner decision after the 2026-11-08 census, §6), so a two-PNG match is a low-confidence hint."
- **`:229`.** The Default cell `off` becomes `on (2026-11-08)`.
- **`:239`.** The bullet becomes:
  > **B7 screenshots stay demoted by default** [B7]. The draft default was off ("screenshots are ordinary photos"). A 2026-11-08 census found that B7 off produced two false merges of bank-app receipt screenshots in 2,043 files, so the default is on. Off, a matching PNG pair is judged like a photo.

  This drops the stale "`duplicate_of` edge still groups the pair either way". Under GROUPING 10 a B7-on PNG match is only a hint.
- **`:390`.** "B7's demotion becomes a toggle, default off." becomes "B7's demotion becomes a toggle, default on (2026-11-08 census, §6)."

### No change needed

- `docs/grouping-topology.md` never mentions the gate. Its `:149` and `:177` are only generic version-bump notes.
- README only mentions `phash_quality` (`README.md:525`), not the EXACT recipe.

### Also required

- `docs/testing.md`: a per-module row for `scanner/grey_store.py`, by the documentation duty.
- A `news/<PR>.fixed` fragment, or the news-fragment CI gate fails.

## Migration & version

- `GROUPING_STRATEGY_VERSION` goes "10" to "11" and `HASH_RECIPE_VERSION` goes "5" to "6". Both feed only the calibration cache key (`scan_runner.py:313-321`), which invalidates once.
- There is no manifest schema change, because `grey128` is never persisted.
- A manifest scanned under "10" keeps its old `EXACT` verdicts until it is re-scanned, the same as every earlier bump.

## Risks & open questions for the lead

1. **Census instrument parity (blocks merge).** The brief says both "every pair decoded at full resolution" and "the oriented working image". The gate runs on the drafted working image: JPEGs decode at about 1/8, roughly 504×378 for 4032×3024 (`hasher.py:136-138`), while HEIC and PNG decode fully. Merge acceptance should re-run the 137-row census through the real `_grey_signature`, expecting 126 EXACT and 11 REVIEW, and record the probe, SHA, arguments and output.
2. **Asymmetric decode in HEIC+JPEG pairs.** One side is a 128 BOX of a 12 MP image, the other a 128 BOX of a DCT-scaled image of about 500 px. I don't know whether the census contained such pairs. If it did not, the 2.2 / 4 honest maxima may not cover them.
3. **Spill location.** "Next to the manifest" means 655 MB of writes at 40,000 files to wherever the user saves the manifest, possibly the NAS. The writes run in the process-pool manager thread and the compute callbacks. A full disk degrades safely to "unavailable", with a log line. Recommendation: put it in `tempfile.gettempdir()`, which is local disk. That is your call.
4. **Still deletable with B8 off.** With B8 off and the threshold at "medium", the 11 pairs, now REVIEW at medium, remain aggressive-delete eligible (`auto_select.py:345-352`).
5. **dHash-unknown pairs.** Identical pixels count as the second signal (`dedup.py:800`). A stricter gate turns some of those EXACT groups into ungrouped hints. This is intended, but users will see it.
6. **Blind spot.** A local edit that changes only colour, at the same luminance, is invisible to grey128 and diluted in the rgb32 shift. The old 32×32 mean difference diluted it too.
7. **Follow-up.** After this change the only consumer of `rgb32` is `_channel_means`. A tuple of three floats would free about 120 MB at 40,000 files. This is out of scope; you decided rgb32 stays.
8. **Leftover file.** A hard kill leaves `.grey128.tmp`. The next scan to the same manifest truncates it; a different output path leaves an orphan.
9. **Where to link.** Where does the 2026-11-08 census artifact live? The B7 sentences should link it.

### Performance

These are estimates, not measured. The PR should include a micro-probe over the dev set.

- **JPEG.** `convert("L")` plus a BOX resize on a working image of about 500 px costs about 0.2-0.5 ms per file.
- **HEIC at 12 MP.** About 10-25 ms against a decode of about 150-300 ms.
- **Peak RAM.** The new code adds one transient grey copy at a time, about 12 MB for a 12 MP image. imagehash and `_phash_quality` (`hasher.py:160`) already make the same full-size "L" copy one after another. So the #453 per-worker ceiling (`hasher.py:37-44`) does not move.

## Effort (hours)

| Item | Hours |
|---|---|
| `grey_store.py` and its tests | 2 |
| Hasher changes and its tests | 2 |
| Dedup gate and the grey-gate tests | 2 |
| Rewrite of the exact-pixels tests and the test helpers | 1.5 |
| scan_runner wiring and pipeline tests | 2.5 |
| Docs, PRD edits and news fragment | 1 |
| Census re-run through the real code path, plus perf probe | 1.5 |
| **Total** | **12.5** |