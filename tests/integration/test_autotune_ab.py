"""GATE-2 (#551 Phase 4) — the read-knee autotune no-regression A/B.

Layer-2 / `@pytest.mark.integration`: this is a *timing* test (median-of-5
real scans), so it is skipped in CI — wall-clock medians are too jittery for a
shared runner. Run it locally to gate the default-ON flip and to capture the
medians that go in the PR body:

    PHOTO_MANAGER_RUN_INTEGRATION=1 .venv/Scripts/python.exe -m pytest \
        tests/integration/test_autotune_ab.py -s --no-cov

What it proves, and what it does NOT
------------------------------------
On a synthetic >2-in-flight latency **cliff** (the stand-in for a low-channel
NAS that 8 readers over-subscribe), the static reader count (MAX=8) runs every
read in the slow regime, while the default-ON steady state — a *cache-warm*
knee=2, i.e. ``Semaphore(2)`` with no ramp — runs every read in the fast
regime. The hard assertion is the no-regression guard band
``median(ON) ≤ median(OFF) × 1.10``; on this cliff ON is also strictly faster
than static-8 (the over-subscription is genuinely trimmed), reported for the PR
body.

HONEST SCOPE (carried into the #551 body): this bounds the **algorithm's**
sampling / fill-transient overhead on an *idealised* cliff. It is NOT a real
SMB-mux / OS-cache / wire-contention proof — that is unmeasurable on the
flat-plateau dev rig, which is exactly why the shipped floor is the
conservative N=8 (1584) and the 10% band is doing real work.
"""
from __future__ import annotations

import os
import statistics
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_RUN = os.environ.get("PHOTO_MANAGER_RUN_INTEGRATION")
_N_FILES = 160      # > the (lowered) ramp gate; long enough to dwarf fixed overhead
_PAIRS = 5          # median-of-5 alternating OFF/ON
_GUARD_BAND = 1.10  # median(ON) must not exceed median(OFF) × this


def _cliff_read(fast_max_inflight, *, fast_s=0.015, slow_s=0.09, data=b"x" * 16):
    """Same in-flight latency cliff as the GATE-1 unit tests: ≤ N concurrent
    reads are fast, the next rung up is ~6× slower, with deterministic per-read
    jitter so concurrent reads don't complete in synchronized (mis-measured)
    waves. Concurrency inside the read == the live permit budget."""
    lock = threading.Lock()
    state = {"inflight": 0}

    def fake_read(idx, record):
        with lock:
            state["inflight"] += 1
            n = state["inflight"]
        base = fast_s if n <= fast_max_inflight else slow_s
        jitter = 0.7 + 0.6 * (((idx * 2654435761) % 997) / 997.0)
        try:
            time.sleep(base * jitter)
            return idx, record, data
        finally:
            with lock:
                state["inflight"] -= 1

    return fake_read


def _time_one_scan(monkeypatch, tmp_path, *, autotune, knees, n_files):
    """Patch the scan pipeline down to the cliff read, run one full
    ``ScanWorker.run()`` on a single NAS device, and return the wall-clock
    seconds. OFF → static MAX=8; ON → cache-warm Semaphore(knee)."""
    import scanner.dedup as _dedup
    import scanner.hasher as _hasher
    import scanner.walker as _walker
    import scanner.workers as _workers
    from scanner.dedup import HashResult
    from scanner.walker import FileRecord
    from app.views.workers.scan_worker import ScanWorker

    records = [
        FileRecord(path=Path(rf"J:\img_{i}.jpg"), source_label="src", file_type="jpeg")
        for i in range(n_files)
    ]

    def fake_scan_sources(sources, **kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            for _ in records:
                cb()
        return list(records)

    def fake_compute(idx, record, d):
        return idx, HashResult(record=record, sha256=f"sha-{idx}", phash=None,
                               exif_date=None)

    monkeypatch.setattr(_walker, "scan_sources", fake_scan_sources)
    monkeypatch.setattr(_hasher, "compute_from_bytes", fake_compute)
    monkeypatch.setattr(_workers, "is_remote_drive",
                        lambda root: str(root).upper() == "J:")
    monkeypatch.setattr(_workers, "disk_incurs_seek_penalty", lambda root: False)
    monkeypatch.setattr(_dedup, "classify", lambda hrs, **kw: [])
    # The cliff: > 2 in-flight reads are slow — so MAX=8 over-subscribes and a
    # warm knee=2 does not. Applied last so it wins over any pipeline default.
    monkeypatch.setattr(_hasher, "read_for_record", _cliff_read(2))

    worker = ScanWorker(
        sources={"src": str(tmp_path)},
        output_path=str(tmp_path / "m.sqlite"),
        recursive_map={"src": False},
        workers=2,
        autotune_read_knee=autotune,
        autotune_knees=knees,
    )
    done = threading.Event()
    elapsed = {}

    def _go():
        t0 = time.monotonic()
        worker.run()
        elapsed["s"] = time.monotonic() - t0
        done.set()

    threading.Thread(target=_go, daemon=True).start()
    assert done.wait(timeout=120), "scan did not complete (deadlock?)"
    return elapsed["s"]


@pytest.mark.skipif(
    not _RUN,
    reason="layer-2 timing A/B; set PHOTO_MANAGER_RUN_INTEGRATION=1 to run locally",
)
def test_warm_autotune_no_regression_vs_static_max(qapp, tmp_path, monkeypatch):
    from scanner.autotune import AUTOTUNE_RECIPE_VERSION

    warm = {r"J:": {"knee": 2, "recipe": AUTOTUNE_RECIPE_VERSION}}
    off_times, on_times = [], []
    for _ in range(_PAIRS):
        # Alternate so any monotonic drift in the runner hits both arms equally.
        off_times.append(
            _time_one_scan(monkeypatch, tmp_path, autotune=False, knees=None,
                           n_files=_N_FILES)
        )
        on_times.append(
            _time_one_scan(monkeypatch, tmp_path, autotune=True, knees=warm,
                           n_files=_N_FILES)
        )

    med_off = statistics.median(off_times)
    med_on = statistics.median(on_times)
    print(
        f"\n[GATE-2 #551] N={_N_FILES} files, {_PAIRS} pairs (synthetic >2-in-flight cliff)\n"
        f"  OFF (static MAX=8): median={med_off:.3f}s  all={[round(t, 3) for t in off_times]}\n"
        f"  ON  (warm knee=2):  median={med_on:.3f}s  all={[round(t, 3) for t in on_times]}\n"
        f"  ratio ON/OFF={med_on / med_off:.3f}  (guard band ≤ {_GUARD_BAND})"
    )

    assert med_on <= med_off * _GUARD_BAND, (
        f"default-ON regressed: median(ON)={med_on:.3f}s > "
        f"median(OFF)={med_off:.3f}s × {_GUARD_BAND}. The ramp/cache path must not "
        f"be slower than the static reader count beyond the 10% guard band."
    )
