"""Per-device read-concurrency knee detection for the #551 in-pipeline ramp.

Pure logic — no Qt, no Win32, no I/O, and no threads of its own. The pipeline
wiring (the per-device reader ``Semaphore``, the ``_gated_read`` permit
acquire/release, and the ``device_key``-keyed cache round-trip) lives in
``app/views/workers/scan_worker.py`` (#551 Phase 2); this module only holds the
measurement math so it is fully unit-testable at layer 1.

The model (issue #551):

* A scan's per-device reader pool is sized at the static MAX
  (``hash_workers_for_root``); a ``threading.Semaphore`` caps the number of
  *active* reads.
* The ramp widens that cap along :data:`READ_KNEE_LADDER` (1 → 2 → 4 → 8),
  measuring read throughput in **files/s** at each concurrency level, and stops
  once doubling the concurrency stops paying off (the *knee*).
* Throughput is **files/s, not bytes/s**: on a latency-bound NAS, bytes/s folds
  in mean-file-size drift that is correlated with concurrency via the unsorted,
  folder/size-clustered walk order, which would push the knee one notch high and
  over-subscribe the very NAS the probe exists to protect.
* A read is attributed to the concurrency level that was active when its read
  *began* — the ``level_tag`` the caller captures at permit-acquire — NOT to
  whichever level's window its completion happens to fall in. Each level's rate
  is computed from the *set* of its reads' completion timestamps at close time,
  so the measured per-level value is independent of completion / call order.

``ReadKneeRamp`` exposes two related-but-distinct values:

* :meth:`ReadKneeRamp.current_permits` — the live Semaphore budget. It is
  monotonic (the Semaphore is never narrowed live; a live narrow would be an
  unsafe teardown-race surface, see #551 Open risk 7), so it equals the highest
  ladder rung the ramp reached. To *detect* a knee at concurrency ``c`` the ramp
  must first measure ``2c`` (to observe the sub-threshold gain), so on a plateau
  the live budget overshoots the knee by one rung; the rest of *this* scan runs
  at that overshoot budget.
* :meth:`ReadKneeRamp.knee` — the detected optimum to **cache** (keyed per
  ``device_key``). The next scan of that device starts directly at
  ``Semaphore(knee())`` with no ramp, so it runs the whole scan at the true knee.

This module deliberately does NOT know the device key or whether a measurement
was contention-free ("sole-ramping"); those are caller concerns and live in the
scan_worker wiring + the knee-acceptance gate.
"""

from __future__ import annotations

import threading
from typing import Optional

# Bump to universally invalidate every cached read-knee (the cache is keyed by
# device_key + this token, so a measurement-algorithm change re-probes on the
# next scan). Purely a cache-keying token; only equality matters.
AUTOTUNE_RECIPE_VERSION = "1"

# Concurrency rungs the ramp climbs. Clamped per device to the static MAX
# (a confirmed HDD's MAX is 1, so its ladder is [1] and the ramp is a no-op).
READ_KNEE_LADDER = (1, 2, 4, 8)

# First c where files/s(2c)/files/s(c) - 1 < this → c is the knee (diminishing
# returns from doubling concurrency).
_KNEE_GAIN_THRESHOLD = 0.15

# Per-level measurement budget. A level closes once it has this many measured
# image reads AND has spanned _RAMP_MIN_SECONDS of wall-time (whichever is
# slower) — see Q2/Q8 in the #551 body for the standard-error rationale.
_RAMP_FILES_PER_LEVEL = 64
_RAMP_MIN_SECONDS = 0.5


def _is_positive_number(value) -> bool:
    """True iff ``value`` is a real, strictly-positive number (not bool/None).

    Used to reject the None / non-positive throughput a failed or device-gone
    read produces, so a bad sample falls open to ``None`` rather than crashing
    or yielding a garbage knee.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def knee_from_throughput(
    samples: dict,
    *,
    gain_floor: float = _KNEE_GAIN_THRESHOLD,
    cap: int = 8,
) -> Optional[int]:
    """Pick the read-concurrency knee from a ``{concurrency c: files/s}`` map.

    ``samples`` values are completed-image-reads-per-second per level (NOT
    bytes/s — see the module docstring for why size-insensitivity matters on a
    latency-bound NAS).

    Walk the measured ladder in ascending ``c``. For each adjacent *doubling*
    pair ``(c, 2c)`` present in ``samples``, if ``files/s(2c)/files/s(c) - 1 <
    gain_floor`` the knee is ``c`` (stop — a later noisy uptick must not
    un-knee it). If the curve never flattens, the knee is the largest measured
    ``c`` (≤ ``cap``). On a flat plateau the tie-break is toward the *smaller*
    ``c`` (we return ``c``, not ``2c``).

    Returns ``None`` — the caller substitutes the static reader count — when the
    input cannot yield a knee: empty ``samples``, fewer than two measured
    concurrencies (no doubling to compare), a non-doubling gap between the only
    measured pair, or any value that is ``None``/≤ 0 (a read that errored or a
    device that went away mid-probe).

    The returned knee is ALWAYS a key present in ``samples`` — never an
    unmeasured concurrency.
    """
    if not samples:
        return None
    measured = [c for c in READ_KNEE_LADDER if c in samples and c <= cap]
    if len(measured) < 2:
        return None
    last_valid: Optional[int] = None
    for i in range(len(measured) - 1):
        c, c2 = measured[i], measured[i + 1]
        if c2 != c * 2:
            # A gap in the measured ladder (e.g. 1 then 4) is not a clean
            # doubling — stop; the knee is the last validated rung.
            break
        t1, t2 = samples[c], samples[c2]
        if not _is_positive_number(t1) or not _is_positive_number(t2):
            return None
        last_valid = c2
        if (t2 / t1 - 1.0) < gain_floor:
            return c
    # Never flattened across the measured doublings → cap at the largest
    # concurrency we validated (None if not even one clean doubling existed).
    return last_valid


class ReadKneeRamp:
    """Per-device ramp state machine for the in-pipeline read-knee probe.

    Drive it from the scan's single ``as_completed`` reader-drain consumer:

    1. ``record(nbytes, now, level_tag=...)`` for every completed read, where
       ``level_tag`` is the permit budget captured when the read *acquired* its
       permit (:meth:`current_permits` at acquire time) and ``now`` is the
       read's completion timestamp.
    2. ``advance_if_level_done()`` after recording — it closes the current level
       when it has enough measured reads spanning enough wall-time, decides
       freeze-vs-step, and returns :meth:`current_permits` so the caller can
       ``release`` the delta permits to widen the Semaphore.

    Thread-safety: ``record`` / ``advance_if_level_done`` are called only from
    that single consumer thread, so there are no concurrent mutations. The
    ``threading.Lock`` guards the parent thread's :meth:`knee` / :meth:`summary`
    read at teardown against the consumer's writes — that cross-thread read is
    the only contention. No I/O, no Qt, no OS handle: a plain object, GC'd on
    scan exit, so it adds no teardown surface (on cancel it is simply abandoned).
    """

    def __init__(
        self,
        max_c: int,
        target_files_per_level: int = _RAMP_FILES_PER_LEVEL,
        min_seconds: float = _RAMP_MIN_SECONDS,
        gain_floor: float = _KNEE_GAIN_THRESHOLD,
    ) -> None:
        self._lock = threading.Lock()
        self._ladder = [c for c in READ_KNEE_LADDER if c <= max_c] or [1]
        # A level needs at least two measured reads to span an interval; clamp
        # the target so a degenerate caller value can't make a level un-closable.
        self._target = max(2, int(target_files_per_level))
        self._min_seconds = min_seconds
        self._gain_floor = gain_floor
        self._level_idx = 0
        self._samples: dict[int, float] = {}
        self._knee: Optional[int] = None
        self._frozen = False
        # Completion timestamps of nbytes>0 reads tagged with the CURRENT level,
        # reset on each step. Sorted at close, so call order never matters.
        self._level_ts: list[float] = []
        # Running min/max of the current level's timestamps — a cheap O(1) gate so
        # advance_if_level_done() can reject a not-yet-closable level without
        # sorting the growing list on every per-read call (matters on a fast SSD
        # whose level fills long before it spans _RAMP_MIN_SECONDS).
        self._level_tmin = float("inf")
        self._level_tmax = float("-inf")
        if len(self._ladder) == 1:
            # Single-rung ladder (a confirmed HDD pinned at 1): nothing to
            # measure — the only rung IS the knee, and the ramp is inert.
            self._knee = self._ladder[0]
            self._frozen = True

    # -- caller-facing reads ------------------------------------------------

    def current_permits(self) -> int:
        """The live Semaphore budget = the current ladder rung (monotonic)."""
        with self._lock:
            return self._ladder[self._level_idx]

    def is_ramping(self) -> bool:
        """True while still climbing; False once frozen (knee found / exhausted)."""
        with self._lock:
            return not self._frozen

    def knee(self) -> Optional[int]:
        """The detected optimum to cache, or ``None`` until the ramp freezes
        with a usable measurement. May be one rung BELOW :meth:`current_permits`
        on a plateau (see the class docstring)."""
        with self._lock:
            return self._knee

    def summary(self) -> dict:
        """Ramp-owned state for logging / the cache store. The caller augments
        this with ``device`` and ``sole_ramping`` (which this pure object cannot
        know) before emitting it on the ``read_knee_measured`` signal."""
        with self._lock:
            return {
                "ladder": list(self._ladder),
                "levels": dict(self._samples),
                "knee": self._knee,
                "current_permits": self._ladder[self._level_idx],
                "frozen": self._frozen,
            }

    # -- consumer-driven measurement ---------------------------------------

    def record(self, nbytes: int, now: float, *, level_tag: int) -> None:
        """Attribute one completed read to the level it was *read at*.

        ``level_tag`` is the permit budget captured at acquire time, so a read
        belongs to the concurrency it actually ran at regardless of when it
        completes. Reads tagged with an already-closed (draining) level, reads
        with no payload (``nbytes <= 0`` — video/gif/skip/ReadFailure), and any
        read once the ramp is frozen are ignored for the throughput signal.
        """
        with self._lock:
            if self._frozen:
                return
            if level_tag != self._ladder[self._level_idx]:
                return
            if not _is_positive_number(nbytes):
                return
            self._level_ts.append(now)
            self._level_tmin = min(self._level_tmin, now)
            self._level_tmax = max(self._level_tmax, now)

    def advance_if_level_done(self) -> int:
        """Close the current level if it is fully measured, then freeze or step.

        A level is done once it has ``fill_skip + target`` recorded reads whose
        post-fill completion timestamps span at least ``min_seconds``. The first
        ``fill_skip`` reads (= the new concurrency, for levels entered via a
        widen) are discarded as the fill transient: when the budget widens, the
        old in-flight reads drain at the lower concurrency while the new permits
        take a round-trip to fill, so the level head would otherwise be measured
        at an intermediate concurrency and bias the knee low.

        Returns :meth:`current_permits` (unchanged until a level closes and the
        ramp steps) so the caller can release the delta permits.
        """
        with self._lock:
            if self._frozen:
                return self._ladder[self._level_idx]
            fill_skip = 0 if self._level_idx == 0 else self._ladder[self._level_idx]
            if len(self._level_ts) < fill_skip + self._target:
                return self._ladder[self._level_idx]
            if (self._level_tmax - self._level_tmin) < self._min_seconds:
                # Cheap O(1) pre-gate: the post-fill span can't exceed the full
                # span, so if even the full span is too short there is nothing to
                # measure yet — skip the sort and keep accumulating.
                return self._ladder[self._level_idx]
            # Drop the earliest-completing fill-transient reads, then measure the
            # rest. Sorting makes the result independent of record() call order.
            # The done-check above (len >= fill_skip + target) plus target>=2
            # (clamped in __init__) guarantees measured has at least two reads.
            measured = sorted(self._level_ts)[fill_skip:]
            span = measured[-1] - measured[0]
            if span <= 0.0 or span < self._min_seconds:
                # Equal/zero span (a pathologically fast device) would divide by
                # zero; keep accumulating until the reads span real wall-time.
                return self._ladder[self._level_idx]
            rate = (len(measured) - 1) / span
            self._samples[self._ladder[self._level_idx]] = rate
            self._close_current_level()
            return self._ladder[self._level_idx]

    # -- internal transitions ----------------------------------------------

    def _close_current_level(self) -> None:
        """Decide freeze-vs-step after the current level's rate is recorded.

        Mirrors :func:`knee_from_throughput`'s pairwise walk so the live ramp and
        the pure function agree on every curve.
        """
        c = self._ladder[self._level_idx]
        if self._level_idx == 0:
            # First level: nothing to compare against yet. (A single-rung ladder
            # never reaches here — it freezes at construction.)
            self._step()
            return
        prev_c = self._ladder[self._level_idx - 1]
        # Every stored sample is a strictly-positive rate ((>=1 intervals) / (span>0)),
        # so the previous level's rate is always a safe, meaningful divisor.
        if (self._samples[c] / self._samples[prev_c] - 1.0) < self._gain_floor:
            # Diminishing returns: the knee is the previous rung. The live budget
            # stays at c (we already widened to measure it; never narrowed live).
            self._knee = prev_c
            self._frozen = True
        elif self._level_idx == len(self._ladder) - 1:
            # Top rung, never flattened → the device scales to the cap.
            self._knee = c
            self._frozen = True
        else:
            self._step()

    def _step(self) -> None:
        """Advance to the next ladder rung and reset the per-level accumulator."""
        self._level_idx += 1
        self._level_ts = []
        self._level_tmin = float("inf")
        self._level_tmax = float("-inf")


# -- read-knee cache (keyed per device_key alone) --------------------------
#
# The read knee is a DEVICE property (SMB channel count / RTT for a NAS, queue
# depth for an SSD), not a property of which folders are scanned. So the cache
# is keyed by device_key ALONE plus AUTOTUNE_RECIPE_VERSION — NOT by a
# source-path fingerprint — so a knee learned scanning one library is reused for
# every later scan of any library on the same physical device (#551 Phase 2).


def store_read_knee(settings, device_key: str, knee: int) -> None:
    """Persist a measured read-knee into ``scan.read_knee_cache``.

    Kept a plain function (not a dialog method) so the round-trip is
    unit-testable against a real ``JsonSettings`` without a Qt dialog —
    ``settings`` is any object exposing ``get``/``set``/``save`` (mirrors
    ``store_hash_pool_rates``). Each entry stamps the recipe version so a probe
    algorithm change invalidates every cached knee.
    """
    cache = settings.get("scan.read_knee_cache", {}) or {}
    cache[device_key] = {"knee": int(knee), "recipe": AUTOTUNE_RECIPE_VERSION}
    settings.set("scan.read_knee_cache", cache)
    settings.save()


def _valid_read_knee(entry) -> bool:
    """True iff ``entry`` is a usable cached read-knee for the CURRENT recipe.

    ``settings.json`` is hand-editable and the recipe can change between
    releases, so a corrupt / partial / stale-recipe entry must be a cache MISS
    (re-probe), never a crash — boundary validation per the project's
    input-at-boundaries rule. ``bool`` is rejected even though it is an ``int``
    subclass, so a hand-edited ``true`` is not mistaken for a knee of 1.
    """
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("knee"), int)
        and not isinstance(entry.get("knee"), bool)
        and entry.get("knee") > 0
        and entry.get("recipe") == AUTOTUNE_RECIPE_VERSION
    )
