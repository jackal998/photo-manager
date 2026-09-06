"""Pure analysis layer for the #622 Phase 3 preview probe.

Everything here is Qt-free and filesystem-light: file discovery, the click
order, the roll-up of per-click rows into the issue's acceptance boxes, the
box-2 ratio maths, the artifact's schema check, and the artifact write. It is
split out of ``preview_phase3_probe.py`` — which owns the Qt harness that
produces those rows — because this half is exactly the half a unit test can
own, and a wrong number here would be cited as though it were measured.

See ``docs/audits/preview-phase3-runbook.md`` for the session commands and the
full JSON schema; ``tests/test_preview_phase3_probe.py`` for the tests.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

PROBE_NAME = "scripts/preview_phase3_probe.py"
PROBE_VERSION = 1

# Still-image extensions the preview pane decodes. Videos are excluded: their
# preview path is QMediaPlayer, not ImageService, and issue #622's Phase 3
# boxes are all about the image decode path.
DEFAULT_EXTS = (
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp",
    ".heic", ".heif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2",
)

# Decode-path labels recorded per click. Observed at the real ImageService
# seams by the harness, never inferred from timing.
PATH_EMBEDDED = "embedded_jpeg"          # rawpy extract_thumb satisfied the request
PATH_RAW_FULL = "raw_full_decode"        # thumb too small / absent -> raw.postprocess
PATH_FORCED_FULL = "forced_full_decode"  # --force-full-decode skipped the thumb
PATH_NON_RAW = "non_raw_source"          # Pillow / Shell-WIC: no rawpy involved
PATH_CACHE_HIT = "cache_hit"             # served from the byte-budget LRU or disk
PATH_UNKNOWN = "unknown"

# Issue #622 Phase 3 acceptance thresholds. Overridable on the CLI so a session
# can record against a different bar without editing code.
DEFAULT_RSS_THRESHOLD_MB = 600.0
DEFAULT_BOX2_RATIO = 5.0
DEFAULT_STEADY_WINDOW = 50

_REQUIRED_TOP_LEVEL = (
    "probe", "probe_version", "git_sha", "args", "argv", "host",
    "timestamps", "env", "per_click", "modal", "summary",
)


def discover_images(root: Path, exts: Iterable[str]) -> list[Path]:
    """Every still image under ``root``, sorted — the order a tree would show.

    Sorted case-insensitively by full path so two runs over the same directory
    click the same files in the same order; the box-2 comparison pairs runs by
    path, and a non-deterministic order would silently shrink the pairing.
    """
    wanted = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts}
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if Path(name).suffix.lower() in wanted:
                found.append(Path(dirpath) / name)
    return sorted(found, key=lambda p: str(p).lower())


def click_order(files: list[Path], clicks: int) -> list[Path]:
    """The click sequence: ``files`` in order, cycled until ``clicks`` long.

    Cycling matters for box 1: a library smaller than the click budget still
    has to produce a steady state, and re-visiting a file is what a real dedup
    pass does anyway (back to the group you just left).
    """
    if not files or clicks <= 0:
        return []
    return [files[i % len(files)] for i in range(clicks)]


def _stats(values: list[float]) -> dict:
    """min / p50 / mean / p95 / max over ``values``; all None when empty."""
    if not values:
        return {"n": 0, "min": None, "p50": None, "mean": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_idx = int(0.95 * (len(ordered) - 1))
    return {
        "n": len(ordered),
        "min": round(ordered[0], 3),
        "p50": round(statistics.median(ordered), 3),
        "mean": round(statistics.fmean(ordered), 3),
        "p95": round(ordered[p95_idx], 3),
        "max": round(ordered[-1], 3),
    }


def steady_window(per_click: list[dict], window: int) -> list[dict]:
    """The last ``window`` clicks — the steady-state slice for box 1.

    A window wider than the run returns the whole run rather than raising: a
    short smoke run must still produce a (clearly under-sized) box-1 reading
    instead of a crash, and ``summary.box1.steady_window_used`` records what
    was actually taken.
    """
    if window <= 0:
        return list(per_click)
    return list(per_click[-window:])


def first_source_load_by_path(per_click: list[dict]) -> dict[str, dict]:
    """First click per path that actually read the source (not a cache hit).

    The box-2 comparison must use cold decodes: the second click on a path is
    served by the LRU and would report the cache's latency, not the decode's.
    """
    out: dict[str, dict] = {}
    for row in per_click:
        path = str(row.get("path", ""))
        if not path or path in out:
            continue
        if row.get("ok") and row.get("source_loaded"):
            out[path] = row
    return out


def compute_box2_ratio(
    embedded_clicks: list[dict],
    forced_clicks: list[dict],
    *,
    threshold: float = DEFAULT_BOX2_RATIO,
) -> dict:
    """Box 2: how many times faster the embedded-JPEG path paints.

    Pairs the two runs BY PATH — same file, same bytes, one decode route each
    — and keeps only paths where the first run genuinely took the embedded
    route (:data:`PATH_EMBEDDED`) and the second genuinely took the forced full
    decode (:data:`PATH_FORCED_FULL`). Anything else (a JPEG, a DNG whose
    embedded thumb was too small, a cache hit) is not a comparison of the two
    routes and is excluded rather than averaged in.

    ``ratio_median`` is the median of the per-path ratios; ``ratio_of_medians``
    is reported beside it so a skewed pairing is visible instead of hidden.
    Returns ``status='not_measured'`` with a reason when no pair qualifies —
    which is the honest result on a library with no DNGs.
    """
    base = first_source_load_by_path(embedded_clicks)
    forced = first_source_load_by_path(forced_clicks)
    pairs: list[dict] = []
    for path, base_row in base.items():
        forced_row = forced.get(path)
        if forced_row is None:
            continue
        if base_row.get("decode_path") != PATH_EMBEDDED:
            continue
        if forced_row.get("decode_path") != PATH_FORCED_FULL:
            continue
        base_ms = float(base_row.get("ttfp_ms") or 0.0)
        forced_ms = float(forced_row.get("ttfp_ms") or 0.0)
        if base_ms <= 0.0 or forced_ms <= 0.0:
            continue
        pairs.append({
            "path": path,
            "embedded_ttfp_ms": round(base_ms, 3),
            "full_decode_ttfp_ms": round(forced_ms, 3),
            "ratio": round(forced_ms / base_ms, 3),
        })

    criterion = (
        "NAS DNG time-to-first-paint >= "
        f"{threshold}x faster on the embedded-JPEG path than on a full raw decode"
    )
    if not pairs:
        return {
            "criterion": criterion,
            "status": "not_measured",
            "reason": (
                "no path took the embedded-JPEG route in the baseline run AND "
                "the forced full-decode route in the comparison run — a library "
                "with no DNG (or no DNG whose embedded thumb reaches the "
                "viewport cap) cannot measure this box"
            ),
            "threshold": threshold,
            "n_paths": 0,
            "pairs": [],
            "pass": None,
        }

    ratios = [p["ratio"] for p in pairs]
    embedded_ms = [p["embedded_ttfp_ms"] for p in pairs]
    full_ms = [p["full_decode_ttfp_ms"] for p in pairs]
    median_embedded = statistics.median(embedded_ms)
    return {
        "criterion": criterion,
        "status": "measured",
        "threshold": threshold,
        "n_paths": len(pairs),
        "ratio_median": round(statistics.median(ratios), 3),
        "ratio_min": round(min(ratios), 3),
        "ratio_max": round(max(ratios), 3),
        "ratio_of_medians": (
            round(statistics.median(full_ms) / median_embedded, 3)
            if median_embedded > 0 else None
        ),
        "embedded_ttfp_ms": _stats(embedded_ms),
        "full_decode_ttfp_ms": _stats(full_ms),
        "pairs": pairs,
        "pass": bool(round(statistics.median(ratios), 3) >= threshold),
    }


def summarise_clicks(
    per_click: list[dict],
    *,
    steady_window_n: int = DEFAULT_STEADY_WINDOW,
    rss_threshold_mb: float = DEFAULT_RSS_THRESHOLD_MB,
) -> dict:
    """Roll ``per_click`` up into the ``summary`` block, box 1 included."""
    ok_rows = [r for r in per_click if r.get("ok")]
    ttfp = [float(r["ttfp_ms"]) for r in ok_rows if r.get("ttfp_ms") is not None]
    rss_all = [float(r["rss_bytes"]) / 1e6 for r in per_click if r.get("rss_bytes")]

    window = steady_window(per_click, steady_window_n)
    window_rss = [float(r["rss_bytes"]) / 1e6 for r in window if r.get("rss_bytes")]
    window_stats = _stats(window_rss)
    steady_p50 = window_stats["p50"]
    steady_max = window_stats["max"]

    box1 = {
        "criterion": (
            f"steady-state RSS over the last {steady_window_n} clicks "
            f"< {rss_threshold_mb} MB"
        ),
        "steady_window_requested": steady_window_n,
        "steady_window_used": len(window),
        "steady_state_rss_mb": window_stats,
        "threshold_mb": rss_threshold_mb,
        "pass": (None if steady_p50 is None else bool(steady_p50 < rss_threshold_mb)),
        "pass_on_max": (None if steady_max is None else bool(steady_max < rss_threshold_mb)),
    }

    lru_thumb = [int(r["lru_thumb_bytes"]) for r in per_click
                 if r.get("lru_thumb_bytes") is not None]
    lru_preview = [int(r["lru_preview_bytes"]) for r in per_click
                   if r.get("lru_preview_bytes") is not None]

    return {
        "clicks": len(per_click),
        "ok_clicks": len(ok_rows),
        "timeouts": sum(1 for r in per_click if r.get("timed_out")),
        "ttfp_ms": _stats(ttfp),
        "decode_path_counts": dict(
            Counter(str(r.get("decode_path", PATH_UNKNOWN)) for r in per_click)
        ),
        "ext_counts": dict(Counter(str(r.get("ext", "")) for r in per_click)),
        "rss_mb": {
            "first_click": round(rss_all[0], 3) if rss_all else None,
            "last_click": round(rss_all[-1], 3) if rss_all else None,
            "max": round(max(rss_all), 3) if rss_all else None,
        },
        "lru_occupancy_bytes": {
            "thumb_max": max(lru_thumb) if lru_thumb else None,
            "preview_max": max(lru_preview) if lru_preview else None,
            "thumb_final": lru_thumb[-1] if lru_thumb else None,
            "preview_final": lru_preview[-1] if lru_preview else None,
        },
        "box1": box1,
        "box2": {
            "criterion": (
                "NAS DNG time-to-first-paint 5x faster on the embedded-JPEG path"
            ),
            "status": "not_measured",
            "reason": (
                "single run: re-run with --force-full-decode --compare-json "
                "<this file> to have the probe compute the ratio"
            ),
            "n_paths": 0,
            "pass": None,
        },
    }


def validate_payload(payload: dict) -> list[str]:
    """Return the list of schema problems in ``payload`` (empty == valid).

    Documented in the runbook so the owner can check an artifact before
    attaching it, and so a future edit to :func:`build_payload` that drops a
    citation field fails a test instead of shipping an uncitable JSON.
    """
    problems: list[str] = []
    for key in _REQUIRED_TOP_LEVEL:
        if key not in payload:
            problems.append(f"missing top-level key: {key}")
    if not payload.get("probe"):
        problems.append("probe is empty")
    if not payload.get("git_sha"):
        problems.append("git_sha is empty")
    if not isinstance(payload.get("args"), dict) or not payload.get("args"):
        problems.append("args is not a non-empty object")
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for box in ("box1", "box2"):
            if box not in summary:
                problems.append(f"summary.{box} missing")
    else:
        problems.append("summary is not an object")
    if not isinstance(payload.get("per_click"), list):
        problems.append("per_click is not a list")
    return problems


def build_payload(
    *,
    args: dict,
    argv: list[str],
    git_sha: str,
    git_dirty: bool,
    host: dict,
    timestamps: dict,
    env: dict,
    per_click: list[dict],
    modal: dict,
    summary: dict,
) -> dict:
    """Assemble the one JSON artifact, citation fields first."""
    return {
        "probe": PROBE_NAME,
        "probe_version": PROBE_VERSION,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "args": args,
        "argv": argv,
        "host": host,
        "timestamps": timestamps,
        "env": env,
        "per_click": per_click,
        "modal": modal,
        "summary": summary,
    }


def git(*cmd: str) -> str:
    """Run a read-only git command in the repo root; '' on any failure."""
    try:
        out = subprocess.run(
            ["git", *cmd], cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=20, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` as UTF-8 JSON with LF endings.

    ``newline=""`` keeps Windows text mode from rewriting LF to CRLF, so the
    artifact hashes the same whichever machine produced it.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
