"""Generate a realistic ~13k-row test fixture for memory_probe.py.

Creates a SQLite manifest matching the schema in scanner/manifest.py +
infrastructure/manifest_repository.py (including the ``outcome``,
``is_locked`` columns from additive migrations).

Output: ~/AppData/Local/PhotoManager/probe-fixtures/probe_manifest.sqlite

Reproducible: uses a fixed PRNG seed (42).
Run: python scripts/generate_probe_fixture.py
"""

from __future__ import annotations

import random
import sqlite3
import string
from pathlib import Path

_SEED = 42
_N_ROWS = 13_000
_N_GROUPS = 2_500  # ~5.2 items/group average

_OUT = (
    Path.home()
    / "AppData"
    / "Local"
    / "PhotoManager"
    / "probe-fixtures"
    / "probe_manifest.sqlite"
)

_DDL = """
CREATE TABLE IF NOT EXISTS migration_manifest (
    id               INTEGER PRIMARY KEY,
    source_path      TEXT    NOT NULL,
    source_label     TEXT    NOT NULL,
    action           TEXT    NOT NULL,
    source_hash      TEXT,
    phash            TEXT,
    hamming_distance INTEGER,
    group_id         TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT '',
    file_size_bytes  INTEGER,
    shot_date        TEXT,
    creation_date    TEXT,
    mtime            TEXT,
    pixel_width      INTEGER,
    pixel_height     INTEGER,
    exif_tag_count   INTEGER,
    gps_present      INTEGER NOT NULL DEFAULT 0,
    xmp_derived      INTEGER NOT NULL DEFAULT 0,
    score            REAL,
    is_locked        INTEGER NOT NULL DEFAULT 0,
    outcome          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_source_hash ON migration_manifest(source_hash);
CREATE INDEX IF NOT EXISTS idx_phash       ON migration_manifest(phash);
CREATE INDEX IF NOT EXISTS idx_action      ON migration_manifest(action);
CREATE INDEX IF NOT EXISTS idx_group_id    ON migration_manifest(group_id);
"""

_INSERT = """
INSERT INTO migration_manifest
    (source_path, source_label, action, source_hash,
     phash, hamming_distance, group_id, reason,
     file_size_bytes, shot_date, creation_date, mtime,
     pixel_width, pixel_height,
     exif_tag_count, gps_present, xmp_derived, score,
     is_locked, outcome, user_decision)
VALUES
    (?, ?, ?, ?,
     ?, ?, ?, ?,
     ?, ?, ?, ?,
     ?, ?,
     ?, ?, ?, ?,
     ?, ?, ?)
"""


def _rand_hex(n: int, rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def _rand_path(rng: random.Random, group_idx: int, item_idx: int) -> str:
    """Realistic NAS UNC path, 100-300 chars."""
    nas_share = rng.choice([
        r"\\LINXIAOYUN\photos",
        r"\\LINXIAOYUN\圖片",
        r"D:\Takeout-0508",
    ])
    subfolder_depth = rng.randint(2, 5)
    parts = [
        "".join(rng.choices(string.ascii_lowercase + string.digits, k=rng.randint(4, 12)))
        for _ in range(subfolder_depth)
    ]
    # Add some CJK chars in folder names to simulate real NAS paths
    if rng.random() < 0.3:
        parts.append("相片" + str(rng.randint(2015, 2024)))
    ext = rng.choice([".jpg", ".JPG", ".heic", ".HEIC", ".dng", ".DNG", ".png", ".mp4"])
    fname = f"IMG_{rng.randint(10000, 99999):05d}_{group_idx:04d}_{item_idx:03d}{ext}"
    path = nas_share + "\\" + "\\".join(parts) + "\\" + fname
    # Pad to ~150 chars if too short
    while len(path) < 100:
        path = path.replace(fname, "extra_subfolder\\" + fname)
    return path[:300]


def _rand_phash(rng: random.Random, base: str | None = None) -> str:
    if base is None:
        return _rand_hex(16, rng)
    # Near-dup: flip 0-4 bits
    as_int = int(base, 16)
    flips = rng.randint(0, 4)
    for _ in range(flips):
        bit = rng.randint(0, 63)
        as_int ^= (1 << bit)
    return f"{as_int:016x}"


def _rand_date(rng: random.Random) -> str:
    """ISO-format date string parseable by datetime.fromisoformat."""
    y = rng.randint(2010, 2024)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    h = rng.randint(0, 23)
    mi = rng.randint(0, 59)
    s = rng.randint(0, 59)
    return f"{y}-{m:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"


def generate(out: Path = _OUT, n_rows: int = _N_ROWS, n_groups: int = _N_GROUPS) -> Path:
    rng = random.Random(_SEED)
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.exists():
        out.unlink()

    conn = sqlite3.connect(str(out))
    conn.executescript(_DDL)

    rows: list[tuple] = []

    # Build group structure: first row in each group is KEEP (ref winner),
    # subsequent rows are REVIEW_DUPLICATE (near-dup).
    # ~10% of groups have a single KEEP (singletons).
    group_ids: list[str] = [_rand_hex(16, rng) for _ in range(n_groups)]
    ref_phashes: dict[str, str] = {gid: _rand_hex(16, rng) for gid in group_ids}

    # Distribute _N_ROWS across groups: each group has at least 1 row; extras
    # are distributed by a geometric-like distribution (mirrors real near-dup data).
    group_sizes: list[int] = [1] * n_groups
    remaining = n_rows - n_groups
    for _ in range(remaining):
        # Weight earlier groups (they tend to be larger in real data)
        idx = int(rng.triangular(0, n_groups - 1, 0))
        group_sizes[idx] += 1

    row_id = 1
    for g_idx, gid in enumerate(group_ids):
        g_size = group_sizes[g_idx]
        ref_phash = ref_phashes[gid]
        shot_base = _rand_date(rng)
        creation_base = _rand_date(rng)
        mtime_base = _rand_date(rng)

        for item_idx in range(g_size):
            is_ref = item_idx == 0
            action = "KEEP" if is_ref else ("EXACT" if rng.random() < 0.3 else "REVIEW_DUPLICATE")
            hamming = 0 if is_ref else rng.randint(1, 12)
            phash = ref_phash if is_ref else _rand_phash(rng, ref_phash)
            source_path = _rand_path(rng, g_idx, item_idx)
            source_label = rng.choice(["src0", "src1"])
            source_hash = _rand_hex(64, rng)
            reason = "" if is_ref else rng.choice(["near_dup", "exact_dup", ""])
            file_size = rng.randint(500_000, 120_000_000)
            pixel_w = rng.choice([3024, 4032, 4032, 8064, 1920, 2560])
            pixel_h = rng.choice([4032, 3024, 3024, 6048, 1080, 1440])
            exif_count = rng.randint(20, 120)
            gps = 1 if rng.random() < 0.4 else 0
            xmp = 1 if rng.random() < 0.1 else 0
            score = round(rng.uniform(0.0, 1.0), 4) if rng.random() < 0.7 else None
            is_locked = 1 if rng.random() < 0.02 else 0
            user_decision = "" if rng.random() > 0.05 else "delete"
            # Shot date: slight variation from base within same group
            shot = shot_base if rng.random() < 0.7 else _rand_date(rng)
            creation = creation_base if rng.random() < 0.7 else _rand_date(rng)
            mtime = mtime_base if rng.random() < 0.9 else _rand_date(rng)

            rows.append((
                source_path, source_label, action, source_hash,
                phash, hamming, gid, reason,
                file_size, shot, creation, mtime,
                pixel_w, pixel_h,
                exif_count, gps, xmp, score,
                is_locked, "", user_decision,  # outcome='' (in-review)
            ))
            row_id += 1

    conn.executemany(_INSERT, rows)
    conn.commit()
    conn.close()

    n_actual = sum(group_sizes)
    print(f"Generated {n_actual:,} rows across {n_groups:,} groups -> {out}")
    return out


if __name__ == "__main__":
    generate()
