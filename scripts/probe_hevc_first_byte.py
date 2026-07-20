"""#737 probe: HEVC-transcode first-byte latency against a RUNNING web app server.

Requests each file twice through ``GET /api/media?path=...&transcode=h264``
(``app/web/routes/media.py:143`` -> ``infrastructure/transcode_service.py``)
and measures time-to-first-byte (TTFB) and time-to-complete for each
request, streaming so TTFB reflects when the FIRST response chunk lands,
not the whole download.

This script never starts or stops the server, and never deletes cache
files — the transcode cache (keyed on ``sha1(source_path | mtime_ns)``,
see ``transcode_service.py:71``) is left exactly as the server manages it.
Instead, "was this file already cached before the probe ran" is inferred
empirically: request #1 and request #2 for the same file are both timed,
and if #1 is already about as fast as #2 (within
``_CACHE_HIT_RATIO_THRESHOLD``) then #1 was a cache hit, not a cold
transcode. Pass ``--assume-cold`` to additionally print a loud mismatch
warning if any file the caller believed was cold turns out to have
already been cached — the empirical determination stands regardless.

Read-only against the server: this issues GET requests only.

CLI usage
---------

    # --help always works, no server required
    python scripts/probe_hevc_first_byte.py --help

    # Against a real running server (owner starts it separately)
    python scripts/probe_hevc_first_byte.py --base-url http://127.0.0.1:8765 \\
        --root D:\\iphone-hevc --limit 20 --output probe_hevc.json

    # Explicit file list
    python scripts/probe_hevc_first_byte.py --paths hevc_files.txt \\
        --output probe_hevc.json
"""
from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DEFAULT_BASE_URL = "http://127.0.0.1:8765"
_DEFAULT_LIMIT = 20
_HEVC_CONTAINER_EXTS = {".mov", ".mp4", ".m4v"}
# request1_total <= request2_total * this ratio => request1 was already a
# cache hit (not a cold transcode). Heuristic, not a hard guarantee — a
# very fast cold-transcode on a tiny clip could false-positive as "cached".
_CACHE_HIT_RATIO_THRESHOLD = 1.5


def discover_paths(root: Path, limit: int) -> list[str]:
    paths: list[str] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in _HEVC_CONTAINER_EXTS:
            paths.append(str(p))
            if len(paths) >= limit:
                break
    return paths


def load_paths_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def _one_request(client, base_url: str, path: str) -> dict:
    """Stream one transcode request; return ttfb/total timings + status."""
    url = f"{base_url}/api/media"
    params = {"path": path, "transcode": "h264"}
    t0 = time.perf_counter()
    ttfb = None
    total_bytes = 0
    status = None
    with client.stream("GET", url, params=params, timeout=300.0) as resp:
        status = resp.status_code
        for chunk in resp.iter_bytes():
            if ttfb is None:
                ttfb = time.perf_counter() - t0
            total_bytes += len(chunk)
    total = time.perf_counter() - t0
    return {
        "ttfb_ms": round((ttfb if ttfb is not None else total) * 1000, 1),
        "total_ms": round(total * 1000, 1),
        "bytes": total_bytes,
        "status": status,
    }


def probe_file(client, base_url: str, path: str) -> dict:
    r1 = _one_request(client, base_url, path)
    r2 = _one_request(client, base_url, path)
    cache_hit_on_request1 = (
        r2["total_ms"] > 0 and r1["total_ms"] <= r2["total_ms"] * _CACHE_HIT_RATIO_THRESHOLD
    )
    return {
        "path": path,
        "request1": r1,
        "request2": r2,
        "cache_hit_on_request1": cache_hit_on_request1,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    p.add_argument("--paths", default=None, help="Text file, one path per line")
    p.add_argument("--root", default=None, help="Directory to scan for HEVC-container files")
    p.add_argument("--limit", type=int, default=_DEFAULT_LIMIT,
                    help="Max files when using --root")
    p.add_argument("--assume-cold", action="store_true",
                    help="Assert the cache was empty before this run; warns loudly on mismatch")
    p.add_argument("--output", default=None)
    args = p.parse_args(argv[1:])

    if not args.paths and not args.root:
        print("ERROR: one of --paths or --root is required", file=sys.stderr)
        return 2

    if args.paths:
        paths = load_paths_file(Path(args.paths))
    else:
        root = Path(args.root)
        if not root.is_dir():
            print(f"ERROR: --root {root} is not a directory", file=sys.stderr)
            return 2
        paths = discover_paths(root, args.limit)

    if not paths:
        print("ERROR: no files to probe", file=sys.stderr)
        return 2

    import httpx

    env = {
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "file_count": len(paths),
    }
    print(f"=== probe_hevc_first_byte === base_url={args.base_url} files={len(paths)}")

    results = []
    with httpx.Client() as client:
        # Preflight on the first file: distinguish "server unreachable" from
        # a per-file error so the caller gets a clean message, not a raw
        # ConnectError traceback buried in the first probe_file call.
        try:
            results.append(probe_file(client, args.base_url, paths[0]))
        except httpx.ConnectError as exc:
            print(f"ERROR: server not reachable at {args.base_url}: {exc}", file=sys.stderr)
            out = {"env": env, "args": vars(args), "error": f"connect_error: {exc}", "results": []}
            payload = json.dumps(out, indent=2, ensure_ascii=False)
            print(payload)
            if args.output:
                Path(args.output).write_text(payload, encoding="utf-8")
            return 3

        for path in paths[1:]:
            try:
                results.append(probe_file(client, args.base_url, path))
            except httpx.HTTPError as exc:
                results.append({"path": path, "error": str(exc)})

        for r in results:
            if "error" in r:
                print(f"  ERROR {r['path']}: {r['error']}")
                continue
            print(f"  {Path(r['path']).name}: "
                  f"ttfb1={r['request1']['ttfb_ms']}ms total1={r['request1']['total_ms']}ms  "
                  f"ttfb2={r['request2']['ttfb_ms']}ms total2={r['request2']['total_ms']}ms  "
                  f"cache_hit_on_request1={r['cache_hit_on_request1']}")
            if args.assume_cold and r.get("cache_hit_on_request1"):
                print(f"    WARNING: --assume-cold asserted but {r['path']} was ALREADY "
                      "cached before this probe ran (request1 was as fast as request2)")

    out = {"env": env, "args": {k: v for k, v in vars(args).items()}, "results": results}
    payload = json.dumps(out, indent=2, ensure_ascii=False)
    print("\n--- JSON ---")
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
