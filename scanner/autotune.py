"""Phase-0 occupancy-probe sampler for the #551 auto-tuner.

Pure logic — no Qt, no Win32, no I/O.  The pipeline wiring lives in
``scan_worker.py`` behind an env gate; this module only holds the
sampler so it is fully unit-testable at layer 1.

Usage pattern (caller side):
    probe = OccupancyProbe()
    probe.sample(q.qsize(), q.maxsize)   # per-item or per-tick
    probe.note_starved()                 # when compute_inflight was exhausted
    summary = probe.summary()
    # → {"occ_ewma": 0.93, "n_samples": 1200, "starved": 12, "regime": "compute-bound"}
"""

from __future__ import annotations

# EWMA smoothing factor.  0.1 means each new sample carries 10% weight;
# the remaining 90% comes from history.  At alpha=0.1 a step change reaches
# 95% of its new level after ~29 samples — on a 128-slot queue that is ~4
# seconds of throughput, long enough to smooth transient bursts while still
# tracking genuine regime shifts within a few seconds of stable load.
_EWMA_ALPHA = 0.1

# Regime thresholds: queue perpetually full → compute-bound;
# perpetually near-empty → I/O-bound (reads outpace compute).
# Values match the #551 design brief.
_COMPUTE_BOUND_THRESHOLD = 0.90
_IO_BOUND_THRESHOLD = 0.15


class OccupancyProbe:
    """Maintain a running EWMA of hash_in_q occupancy.

    Thread-safety: the pipeline wiring calls ``sample`` from the parent
    drain loop (single thread) and ``note_starved`` from ``_compute_dispatch``
    (a separate thread).  GIL makes int/float field reads atomic on CPython,
    so the simple int increment for ``_starved`` is safe without a lock.
    The EWMA write in ``sample`` is single-threaded on the caller's side.
    """

    def __init__(self, alpha: float = _EWMA_ALPHA) -> None:
        self._alpha = alpha
        self._occ_ewma: float | None = None  # None until first sample
        self._n_samples: int = 0
        self._starved: int = 0

    def sample(self, qsize: int, maxsize: int) -> None:
        """Record one occupancy observation.

        Silently skips the sample when ``maxsize <= 0`` to avoid
        division-by-zero on an unbounded queue.
        """
        if maxsize <= 0:
            return
        occ = qsize / maxsize
        if self._occ_ewma is None:
            self._occ_ewma = occ
        else:
            self._occ_ewma = self._alpha * occ + (1.0 - self._alpha) * self._occ_ewma
        self._n_samples += 1

    def note_starved(self) -> None:
        """Record that compute_inflight was exhausted at one dispatch attempt."""
        self._starved += 1

    def summary(self) -> dict:
        """Return the current probe state as a plain dict.

        Keys: ``occ_ewma`` (float, 0.0 when no samples), ``n_samples`` (int),
        ``starved`` (int), ``regime`` (str: "compute-bound" | "io-bound" |
        "mixed/unclear" | "no-data").
        """
        ewma = self._occ_ewma if self._occ_ewma is not None else 0.0
        if self._n_samples == 0:
            regime = "no-data"
        elif ewma >= _COMPUTE_BOUND_THRESHOLD:
            regime = "compute-bound"
        elif ewma <= _IO_BOUND_THRESHOLD:
            regime = "io-bound"
        else:
            regime = "mixed/unclear"
        return {
            "occ_ewma": ewma,
            "n_samples": self._n_samples,
            "starved": self._starved,
            "regime": regime,
        }
