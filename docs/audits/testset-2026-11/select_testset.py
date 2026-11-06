"""Select a representative development test set from a photo scan manifest.

Read-only over the manifest. Writes selection.csv and .gitignore into the
output directory only. Usage:

    python -I select_testset.py <manifest.sqlite> <outdir>

Selection unit: a manifest group (every row sharing group_id), or one
ungrouped row on its own. Categories run in priority order and take disjoint
units, so no source_path can appear twice in the output.
"""

from __future__ import annotations

import csv
import itertools
import os
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import PureWindowsPath
from typing import NamedTuple

SEED = 20261106
CAP_BYTES = 400 * 1024 * 1024
SMALL_VIDEO_BYTES = 150 * 1024 * 1024
TOTAL_WARN_BYTES = 20 * 1024**3
SOLO = "solo:"
HINT_PREFIX = "hint only (not grouped): "
PARTNER_RE = re.compile(r"[^\s,;\\/]+\.[A-Za-z0-9]{2,5}")
FIXTURE_NAMES = {
    "mvimg_20200621_160439.jpg",
    "mvimg_20200621_160730.jpg",
    "img_8511.jpg",
    "img_1391.dng",
    "img_1393.dng",
    "img_1395.dng",
    "img_1397.dng",
}
RAW_EXT = {"dng", "cr2", "arw", "nef", "raf"}
JPEG_EXT = {"jpg", "jpeg", "heic"}
VIDEO_EXT = {"mov", "mp4", "avi", "m4v", "3gp", "mkv"}
LIVE_VIDEO_EXT = {"mov", "mp4"}
EXT_CLASS = {
    "jpg": "jpg", "jpeg": "jpg", "heic": "heic", "png": "png",
    "dng": "raw", "cr2": "raw", "arw": "raw", "nef": "raw", "raf": "raw",
    "mov": "mov_mp4", "mp4": "mov_mp4",
}
COLUMNS = [
    "category", "why", "group_id", "source_path", "source_hash",
    "file_size_bytes", "action", "match_confidence",
]


class Row(NamedTuple):
    path: str
    group_id: str | None
    source_hash: str | None
    size: int
    action: str
    confidence: str | None
    reason: str
    phash: str | None
    quality: int | None
    shot: datetime | None
    ext: str
    name: str
    drive: str
    folder: str

    @property
    def unit(self) -> str:
        return self.group_id or SOLO + self.path


class Cand(NamedTuple):
    stratum: str
    units: tuple
    why: str


class Ctx(NamedTuple):
    rows: list
    units: dict
    names: dict


def parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def make_row(rec: tuple) -> Row:
    path, gid, sha, size, action, conf, reason, phash, quality, shot = rec
    pure = PureWindowsPath(path)
    return Row(
        path=path,
        group_id=gid,
        source_hash=sha,
        size=size or 0,
        action=action or "",
        confidence=conf,
        reason=reason or "",
        phash=phash,
        quality=quality,
        shot=parse_dt(shot),
        ext=pure.suffix.lstrip(".").lower(),
        name=pure.name.lower(),
        drive=path[:1],
        folder=str(pure.parent).lower(),
    )


def load_rows(manifest: str) -> list[Row]:
    con = sqlite3.connect(manifest)
    try:
        con.execute("PRAGMA query_only = 1")
        recs = con.execute(
            "SELECT source_path, group_id, source_hash, file_size_bytes, action, "
            "match_confidence, reason, phash, phash_quality, shot_date "
            "FROM migration_manifest ORDER BY source_path"
        ).fetchall()
    finally:
        con.close()
    return [make_row(r) for r in recs]


def make_ctx(rows: list[Row]) -> Ctx:
    units: dict = defaultdict(list)
    names: dict = defaultdict(list)
    for r in rows:
        units[r.unit].append(r)
        names[r.name].append(r)
    return Ctx(rows, units, names)


def groups(ctx: Ctx) -> list:
    return [(k, m) for k, m in ctx.units.items() if not k.startswith(SOLO)]


def ext_class(ext: str) -> str:
    return EXT_CLASS.get(ext, "other")


def size_bucket(n: int) -> str:
    if n == 2:
        return "2"
    return "3-5" if n <= 5 else "6+"


def stem(name: str) -> str:
    return os.path.splitext(name)[0]


def is_burst(members: list) -> bool:
    core = [r for r in members if r.confidence != "low"]
    times = sorted(r.shot for r in core if r.phash and r.shot)
    if len(times) < 2:
        return False
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    return (times[-1] - times[0]).total_seconds() > 2 and max(gaps) <= 2


def tz_gap(members: list) -> float | None:
    dated = [r.shot for r in members if r.shot]
    for a, b in itertools.combinations(dated, 2):
        d = abs((a - b).total_seconds())
        if 0 < d <= 14 * 3600 and d % 1800 == 0:
            return d
    return None


def live_videos(members: list) -> list:
    images = {stem(r.name) for r in members if r.ext in JPEG_EXT}
    return [r for r in members if r.ext in LIVE_VIDEO_EXT and stem(r.name) in images]


def partner_units(ctx: Ctx, row: Row) -> list:
    keys = []
    for token in PARTNER_RE.findall(row.reason[len(HINT_PREFIX):]):
        matches = [o for o in ctx.names.get(token.lower(), []) if o.path != row.path]
        same_folder = [o for o in matches if o.folder == row.folder]
        keys.extend(o.unit for o in (same_folder or matches))
    return keys


def burst_cands(ctx: Ctx) -> list:
    return [[
        Cand("all", (k,), f"burst: {len(m)} rows, consecutive gaps <= 2 s")
        for k, m in groups(ctx) if is_burst(m)
    ]]


def fixture_cands(ctx: Ctx) -> list:
    keys = sorted({r.unit for r in ctx.rows if r.name in FIXTURE_NAMES})
    return [[Cand("all", (k,), "fixture: named regression file in group") for k in keys]]


def exact_cands(ctx: Ctx) -> list:
    out = []
    for k, m in groups(ctx):
        hashes = [r.source_hash for r in m if r.source_hash]
        if len(hashes) != len(set(hashes)):
            p = m[0]
            stratum = f"{ext_class(p.ext)}|{p.drive}"
            out.append(Cand(stratum, (k,), f"exact copies: {len(m)} rows share a source hash"))
    return [out]


def near_dup_cands(ctx: Ctx) -> list:
    out = []
    for k, m in groups(ctx):
        if any(r.action == "REVIEW_DUPLICATE" and r.confidence == "high" for r in m):
            stratum = f"{size_bucket(len(m))}|{ext_class(m[0].ext)}"
            out.append(Cand(stratum, (k,), f"review duplicate, high confidence, {len(m)} rows"))
    return [out]


def hint_cands(ctx: Ctx) -> list:
    return [[
        Cand(ext_class(m[0].ext), (k,), "taken hint leaf: '; hint:' in reason")
        for k, m in groups(ctx) if any("; hint:" in r.reason for r in m)
    ]]


def isolated_cands(ctx: Ctx) -> list:
    out = []
    for r in ctx.rows:
        if not r.reason.startswith(HINT_PREFIX):
            continue
        partners = partner_units(ctx, r)
        keys = tuple(dict.fromkeys([r.unit, *partners]))
        out.append(Cand("all", keys, f"isolated hint, partner units found: {len(partners)}"))
    return [out]


def screenshot_cands(ctx: Ctx) -> list:
    return [[
        Cand("all", (k,), f"all-png group, {len(m)} rows")
        for k, m in groups(ctx) if all(r.ext == "png" for r in m)
    ]]


def live_cands(ctx: Ctx) -> list:
    small, large = [], []
    for k, m in groups(ctx):
        vids = live_videos(m)
        if not vids:
            continue
        vmin = min(v.size for v in vids)
        cand = Cand("all", (k,), f"live photo pair, video {vmin} bytes")
        (small if vmin < SMALL_VIDEO_BYTES else large).append(cand)
    return [small, large]


def raw_jpeg_cands(ctx: Ctx) -> list:
    out = []
    for k, m in groups(ctx):
        raw_stems = {stem(r.name) for r in m if r.ext in RAW_EXT}
        jpeg_stems = {stem(r.name) for r in m if r.ext in JPEG_EXT}
        if raw_stems & jpeg_stems:
            out.append(Cand("all", (k,), "RAW plus JPEG or HEIC with the same stem"))
    return [out]


def undated_cands(ctx: Ctx) -> list:
    return [[
        Cand(r.folder, (r.unit,), "undated row, one per folder per pass")
        for r in ctx.rows if r.action == "UNDATED"
    ]]


def quality_cands(ctx: Ctx) -> list:
    return [[
        Cand(ext_class(r.ext), (r.unit,), f"phash_quality {r.quality} < 16 (quality-gated)")
        for r in ctx.rows if r.quality is not None and r.quality < 16
    ]]


def tz_cands(ctx: Ctx) -> list:
    out = []
    for k, m in groups(ctx):
        gap = tz_gap(m)
        if gap is not None:
            out.append(Cand("all", (k,), f"two dated rows differ by {int(gap)} s (tz-like)"))
    return [out]


def negative_cands(ctx: Ctx) -> list:
    out = []
    for r in ctx.rows:
        if r.group_id is not None or r.action not in ("", "UNDATED"):
            continue
        if r.reason.startswith(HINT_PREFIX) or "; hint:" in r.reason or r.name in FIXTURE_NAMES:
            continue
        out.append(Cand(f"{ext_class(r.ext)}|{r.drive}", (r.unit,), "ungrouped unique control"))
    return [out]


def video_cands(ctx: Ctx) -> list:
    return [[
        Cand(r.ext, (r.unit,), "ungrouped video under 150 MB")
        for r in ctx.rows
        if r.group_id is None and r.ext in VIDEO_EXT and r.size < SMALL_VIDEO_BYTES
    ]]


# (name, candidate function, quota or None for take-all)
CATEGORIES = [
    ("fixtures", fixture_cands, None),
    ("burst", burst_cands, None),
    ("exact_copies", exact_cands, 60),
    ("near_dup_high", near_dup_cands, 80),
    ("hint_pairs", hint_cands, 40),
    ("isolated_hints", isolated_cands, 20),
    ("screenshots", screenshot_cands, 30),
    ("live_photo_pairs", live_cands, 20),
    ("raw_plus_jpeg", raw_jpeg_cands, 20),
    ("undated", undated_cands, 40),
    ("quality_gated", quality_cands, 40),
    ("tz_shift", tz_cands, 10),
    ("negatives", negative_cands, 200),
    ("videos", video_cands, 10),
]


def units_fit(ctx: Ctx, keys: tuple) -> bool:
    return all(r.size <= CAP_BYTES for k in keys for r in ctx.units[k])


def take_free(bucket: list, seen: set):
    while bucket:
        cand = bucket.pop(0)
        if not seen.intersection(cand.units):
            return cand
    return None


def pick(cands: list, quota: int, taken: set, rng: random.Random) -> list:
    """Round-robin over strata, seeded shuffle inside each stratum."""
    buckets: dict = defaultdict(list)
    for cand in sorted(cands):
        buckets[cand.stratum].append(cand)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    order = sorted(buckets)
    seen, chosen, progress = set(taken), [], True
    while len(chosen) < quota and progress:
        progress = False
        for key in order:
            if len(chosen) >= quota:
                break
            cand = take_free(buckets[key], seen)
            if cand is not None:
                chosen.append(cand)
                seen.update(cand.units)
                progress = True
    return chosen


def run_category(name: str, fn, quota, ctx: Ctx, taken: set, rng: random.Random) -> dict:
    fitting = [[c for c in tier if units_fit(ctx, c.units)] for tier in fn(ctx)]
    pool = sum(len(t) for t in fitting)
    blocked = sum(1 for t in fitting for c in t if set(c.units) & taken)
    want = pool if quota is None else quota
    picked: list = []
    for tier in fitting:
        need = want - len(picked)
        if need <= 0:
            break
        got = pick(tier, need, taken, rng)
        for cand in got:
            taken.update(cand.units)
        picked.extend(got)
    cap_excluded = sum(len(t) for t in fn(ctx)) - pool
    return {
        "name": name, "quota": quota, "pool": pool, "blocked": blocked,
        "picked": picked, "cap_excluded": cap_excluded,
    }


def select(ctx: Ctx) -> list:
    rng, taken, results = random.Random(SEED), set(), []
    for name, fn, quota in CATEGORIES:
        results.append(run_category(name, fn, quota, ctx, taken, rng))
    return results


def emit(ctx: Ctx, results: list) -> list:
    out = []
    for res in results:
        for cand in res["picked"]:
            for key in cand.units:
                for r in ctx.units[key]:
                    out.append((res["name"], cand.why, r.group_id or "", r))
    return out


def verify(ctx: Ctx, out: list) -> None:
    paths = [r.path for *_, r in out]
    if len(paths) != len(set(paths)):
        raise SystemExit("FAIL: duplicate source_path in selection")
    selected = set(paths)
    gids = {r.group_id for *_, r in out if r.group_id}
    incomplete = [r.path for r in ctx.rows if r.group_id in gids and r.path not in selected]
    if incomplete:
        raise SystemExit(f"FAIL: incomplete groups, first: {incomplete[:3]}")
    names = {PureWindowsPath(p).name.lower() for p in paths}
    absent = sorted(FIXTURE_NAMES - names)
    if absent:
        raise SystemExit(f"FAIL: fixture basenames absent: {absent}")


def write_outputs(outdir: str, out: list) -> str:
    csv_path = os.path.join(outdir, "selection.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for name, why, gid, r in out:
            writer.writerow([name, why, gid, r.path, r.source_hash or "",
                             r.size, r.action, r.confidence or ""])
    gitignore = os.path.join(outdir, ".gitignore")
    existing = open(gitignore, encoding="utf-8").read().splitlines() if os.path.exists(gitignore) else []
    if "selection.csv" not in existing:  # never overwrite: the folder's .gitignore also hides other local files
        with open(gitignore, "a", encoding="utf-8", newline="") as fh:
            fh.write("selection.csv\n")
    return csv_path


def report_categories(results: list, out: list) -> None:
    print(f"{'category':<18}{'quota':>6}{'pool':>7}{'blocked':>9}{'units':>7}{'files':>7}{'GB':>8}  status")
    for res in results:
        rows = [o[3] for o in out if o[0] == res["name"]]
        size = sum(r.size for r in rows)
        units = len({r.unit for r in rows})
        quota = "all" if res["quota"] is None else res["quota"]
        status = "ok"
        if res["quota"] is not None and len(res["picked"]) < res["quota"]:
            status = f"SHORT by {res['quota'] - len(res['picked'])}"
        print(f"{res['name']:<18}{str(quota):>6}{res['pool']:>7}{res['blocked']:>9}"
              f"{units:>7}{len(rows):>7}{size / 1024**3:>8.2f}  {status}")


def report_totals(out: list) -> None:
    rows = [o[3] for o in out]
    total = sum(r.size for r in rows)
    print(f"\nTOTAL files={len(rows)} bytes={total} GB={total / 1024**3:.2f}")
    print("per drive (files, GB):")
    for drive, n in sorted(Counter(r.drive for r in rows).items()):
        gb = sum(r.size for r in rows if r.drive == drive) / 1024**3
        print(f"  {drive}: {n}, {gb:.2f}")
    print("per extension (files):")
    for ext, n in Counter(r.ext or "(none)" for r in rows).most_common():
        print(f"  {ext}: {n}")
    if total > TOTAL_WARN_BYTES:
        print("OVER 20 GB. Dominant category by bytes:")
        by_cat = Counter()
        for name, _, _, r in out:
            by_cat[name] += r.size
        name, size = by_cat.most_common(1)[0]
        print(f"  {name}: {size / 1024**3:.2f} GB")


def main(argv: list) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: select_testset.py <manifest.sqlite> <outdir>")
    manifest, outdir = argv[1], argv[2]
    os.makedirs(outdir, exist_ok=True)
    ctx = make_ctx(load_rows(manifest))
    results = select(ctx)
    out = emit(ctx, results)
    verify(ctx, out)
    csv_path = write_outputs(outdir, out)
    report_categories(results, out)
    report_totals(out)
    print(f"\nVERIFIED: no duplicate paths, all groups complete, fixtures present")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
