"""Thumbnail-latency + full-res OOM-bound bench — the T4 web-port cutover gate.

Three checks, matching the OOM defenses the code ACTUALLY has (verified by
measurement 2026-06-29, not the stale design assumption of an unbounded
concurrent-ProRAW-postprocess regime):

  A. **cap** — the route's 40 MiB source-size guard (app/web/routes/image.py)
     returns 413 for a ``size=0`` request whose source file exceeds the cap,
     BEFORE any decode, and serves a sub-cap file normally.  This is the
     primary OOM defense for the web full-res path.

  B. **oom-bound** — serving a real ProRAW DNG at full resolution
     (``get_image_bytes(path, 0)``) stays under a peak-RSS budget.  Measurement
     shows iPhone ProRAW is served from its **embedded full-res JPEG**
     (image_service.py:627-665) — ``rawpy.postprocess`` (the 146 MB-array path)
     is never reached, and the FullResViewer is one-image-at-a-time — so this
     bounds the *real* per-request peak rather than a synthetic N-concurrent
     decode the route can't reach.  Skips when no local RAW fixture is present
     (CI has no RAW codecs; this is a dev-rig checkpoint).

  C. **latency** — warm-cache p95 for ``size=thumb`` requests stays under the
     SLO (default 200 ms).

Why the concurrent-postprocess semaphore (image_service.py:74,
``_FULLRES_DECODE_SEM``) is NOT exercised here: it only fires for a RAW with no
embedded thumb that is also < 40 MiB (atypical — e.g. a non-camera TIFF).
Forcing that path would require mocking away the embedded-thumb fast path =
testing a regime the real workload can't reach (metric-gaming).  The semaphore
remains correct defense-in-depth; this bench does not fake a path to it.

``scripts/*`` is coverage-omitted by design — a developer / CI validation tool.

CLI usage
---------

    # All checks (oom skips if qa/fixtures/raw_local/ is empty):
    python scripts/bench_thumbnail_latency.py

    # Just the latency SLO, tighter budget:
    python scripts/bench_thumbnail_latency.py --mode latency --p95-budget-ms 150

    # OOM-bound check on the local ProRAW fixtures (dev-rig checkpoint):
    python scripts/bench_thumbnail_latency.py --mode oom --output bench_oom.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import quote

# scripts/ is sys.path[0] when run directly; add the repo root for app/core.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RAW_FIXTURE_DIR = REPO_ROOT / "qa" / "fixtures" / "raw_local"
SANDBOX_DIR = REPO_ROOT / "qa" / "sandbox"
_CAP_TEST_DIR = REPO_ROOT / ".bench_web_port_artifacts" / "_cap_test"
# Mirror of app/web/routes/image.py:_FULLRES_MAX_SOURCE_BYTES (40 MiB).
_FULLRES_CAP_BYTES = 40 * 1024 * 1024


# ---------------------------------------------------------------------------
# Peak-RSS sampler — Windows working set via ctypes (no psutil dep); POSIX
# falls back to getrusage(ru_maxrss).
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    _psapi = ctypes.WinDLL("psapi.dll")
    _k32 = ctypes.WinDLL("kernel32.dll")
    _gpmi = _psapi.GetProcessMemoryInfo
    _gpmi.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    _gpmi.restype = wintypes.BOOL
    _gcp = _k32.GetCurrentProcess
    _gcp.restype = wintypes.HANDLE

    def _rss_bytes() -> int:
        c = _PMC()
        c.cb = ctypes.sizeof(c)
        if _gpmi(_gcp(), ctypes.byref(c), c.cb):
            return int(c.WorkingSetSize)
        return 0
else:  # pragma: no cover - dev rig is Windows; POSIX path is a best-effort fallback
    import resource

    def _rss_bytes() -> int:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ru * 1024 if sys.platform.startswith("linux") else ru


class _PeakRSS:
    """Context manager: polls RSS on a daemon thread, exposes the peak."""

    def __init__(self, interval: float = 0.01) -> None:
        import threading
        self._interval = interval
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._peak = 0
        self._threading = threading

    def __enter__(self) -> "_PeakRSS":
        self._peak = _rss_bytes()
        self._thread = self._threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            r = _rss_bytes()
            if r > self._peak:
                self._peak = r

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def peak_mb(self) -> float:
        return self._peak / 1e6


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


def find_raw_fixtures(fixture_dir: Path) -> list[Path]:
    """Real RAW fixtures present locally (gitignored; empty in CI)."""
    if not fixture_dir.is_dir():
        return []
    exts = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}
    return sorted(p for p in fixture_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in exts)


def find_thumb_image() -> Path | None:
    """A small sandbox image for the latency check (built by make_qa_sandbox)."""
    for sub in ("near-duplicates", "formats", "unique"):
        d = SANDBOX_DIR / sub
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                    return p
    return None


# ---------------------------------------------------------------------------
# A — cap test (route 40 MiB full-res guard)
# ---------------------------------------------------------------------------


def run_cap_test(client, thumb: Path | None) -> dict:
    """size=0 on a >40 MiB file → 413; on a sub-cap file → 200."""
    _CAP_TEST_DIR.mkdir(parents=True, exist_ok=True)
    big = _CAP_TEST_DIR / "oversize.bin"
    # Sparse file just over the cap — never decoded (413 fires on st_size first).
    with open(big, "wb") as f:
        f.seek(_FULLRES_CAP_BYTES + 1)
        f.write(b"\0")

    big_status = client.get(f"/api/image?path={quote(str(big))}&size=0").status_code
    result = {
        "cap_bytes": _FULLRES_CAP_BYTES,
        "oversize_file_bytes": big.stat().st_size,
        "oversize_status": big_status,
        "oversize_expected": 413,
    }
    if thumb is not None:
        small_status = client.get(
            f"/api/image?path={quote(str(thumb))}&size=0").status_code
        result["subcap_status"] = small_status
        result["subcap_expected"] = 200

    try:
        big.unlink()
    except OSError:
        pass

    passed = result["oversize_status"] == 413
    if "subcap_status" in result:
        passed = passed and result["subcap_status"] == 200
    result["pass"] = passed
    return result


# ---------------------------------------------------------------------------
# B — full-res OOM-bound test (real ProRAW served via embedded JPEG)
# ---------------------------------------------------------------------------


def run_oom_bound_test(fixtures: list[Path], budget_mb: float) -> dict:
    """Serve each fixture full-res; assert valid JPEG + peak-RSS delta < budget."""
    if not fixtures:
        return {"skipped": True,
                "reason": f"no RAW fixtures in {RAW_FIXTURE_DIR} "
                          "(populate via make_qa_large_source.py --include-large-raw)"}

    from infrastructure.image_service import ImageService
    svc = ImageService()

    per_file = []
    baseline_mb = _rss_bytes() / 1e6
    with _PeakRSS() as sampler:
        for f in fixtures:
            t0 = time.monotonic()
            data = svc.get_image_bytes(str(f), 0)
            dt = time.monotonic() - t0
            is_jpeg = len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8
            per_file.append({
                "file": f.name,
                "source_mb": round(f.stat().st_size / 1e6, 1),
                "served_bytes": len(data),
                "seconds": round(dt, 2),
                # >1.5s strongly implies rawpy.postprocess; faster = embedded JPEG.
                "path": "postprocess" if dt > 1.5 else "embedded/cache",
                "valid_jpeg": is_jpeg,
            })
    peak_delta_mb = max(0.0, sampler.peak_mb - baseline_mb)
    all_valid = all(r["valid_jpeg"] for r in per_file)
    return {
        "skipped": False,
        "baseline_mb": round(baseline_mb, 0),
        "peak_delta_mb": round(peak_delta_mb, 0),
        "budget_mb": budget_mb,
        "all_valid_jpeg": all_valid,
        "per_file": per_file,
        "pass": all_valid and peak_delta_mb < budget_mb,
    }


# ---------------------------------------------------------------------------
# C — warm-cache thumbnail latency (p95 SLO)
# ---------------------------------------------------------------------------


def run_latency_test(client, thumb: Path, *, warmup: int, measured: int,
                     size: int, p95_budget_ms: float) -> dict:
    """Warm the cache, then measure p95 latency of size=thumb requests."""
    url = f"/api/image?path={quote(str(thumb))}&size={size}"
    for _ in range(warmup):
        client.get(url)

    samples_ms: list[float] = []
    for _ in range(measured):
        t0 = time.monotonic()
        r = client.get(url)
        samples_ms.append((time.monotonic() - t0) * 1000.0)
        if r.status_code != 200:
            return {"error": f"status {r.status_code} for {url}", "pass": False}

    samples_ms.sort()
    # Nearest-rank p95.
    idx = min(len(samples_ms) - 1, int(round(0.95 * len(samples_ms))) - 1)
    p95 = samples_ms[max(0, idx)]
    return {
        "image": thumb.name,
        "size": size,
        "warmup": warmup,
        "measured": measured,
        "p50_ms": round(statistics.median(samples_ms), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(samples_ms[-1], 2),
        "p95_budget_ms": p95_budget_ms,
        "pass": p95 <= p95_budget_ms,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mode", choices=("all", "cap", "oom", "latency"), default="all")
    p.add_argument("--raw-fixture-dir", default=str(RAW_FIXTURE_DIR))
    p.add_argument("--thumb-size", type=int, default=256)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--measured", type=int, default=100)
    p.add_argument("--p95-budget-ms", type=float, default=200.0)
    p.add_argument("--oom-budget-mb", type=float, default=1024.0)
    p.add_argument("--output", default=None)
    args = p.parse_args(argv[1:])

    from fastapi.testclient import TestClient
    from app.web.main import create_app

    fixtures = find_raw_fixtures(Path(args.raw_fixture_dir))
    thumb = find_thumb_image()
    run_cap = args.mode in ("all", "cap")
    run_oom = args.mode in ("all", "oom")
    run_lat = args.mode in ("all", "latency")

    print("=== bench_thumbnail_latency ===")
    print(f"mode={args.mode}  thumb={thumb.name if thumb else None}  "
          f"raw_fixtures={[f.name for f in fixtures] or 'none'}")

    results: dict = {}

    # Point the app's path guard at every dir the checks touch.
    allowed = [_CAP_TEST_DIR, SANDBOX_DIR, Path(args.raw_fixture_dir)]

    if run_cap or run_lat:
        # Skip the SPA static mount (no dependency on a built frontend).
        app = create_app(frontend_dist=REPO_ROOT / "___no_spa_dist___")
        with TestClient(app) as client:
            app.state.allowed_roots = allowed
            if run_cap:
                results["cap"] = run_cap_test(client, thumb)
            if run_lat:
                if thumb is None:
                    results["latency"] = {
                        "skipped": True,
                        "reason": "no sandbox image; run make_qa_sandbox.py first",
                    }
                else:
                    results["latency"] = run_latency_test(
                        client, thumb, warmup=args.warmup, measured=args.measured,
                        size=args.thumb_size, p95_budget_ms=args.p95_budget_ms)

    if run_oom:
        results["oom_bound"] = run_oom_bound_test(fixtures, args.oom_budget_mb)

    # gate_pass: every check that actually RAN (not skipped) must pass.
    gate_pass = True
    for check in results.values():
        if check.get("skipped"):
            continue
        if not check.get("pass", False):
            gate_pass = False
    out = {"gate_pass": gate_pass, "results": results}

    print("\n=== summary ===")
    for name, r in results.items():
        verdict = "SKIP" if r.get("skipped") else ("PASS" if r.get("pass") else "FAIL")
        print(f"  {name}: {verdict}  {r}")
    print(f"\ngate_pass={gate_pass}")

    payload = json.dumps(out, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"wrote {args.output}")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
