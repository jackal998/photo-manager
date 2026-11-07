"""Probe: are colour-graded format duplicates labelled EXACT?

Read-only. Opens both manifests with query_only; decodes only test-set files.
Prints basenames only (no library folder names).

Part 1 (v6 manifest, no decode): EXACT rows split into format duplicates
(reason prefix written by scanner/dedup.py _classify_format_group) and byte
copies, plus extension pairs, dimension equality, and file-size deltas.

Part 2 (test set): every EXACT format-duplicate pair is decoded to a 32x32 RGB
thumbnail and compared to its keeper; each pair is classified; a gate sweep
reports how many would stay EXACT at several thresholds.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # docs/audits/testset-2026-11/ -> repo root
V6 = REPO / "migration_manifest_v6.sqlite"
TESTSET = REPO / "migration_manifest_testset.sqlite"
sys.path.insert(0, str(REPO))

FORMAT_PREFIX = "format duplicate of "
FORMAT_NAME_RE = re.compile(r"^format duplicate of (.+?) \(")
RAW_EXT = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw"}
THUMB = (32, 32)
SIZE_SUSPECT = 0.20
GREY_STRUCT = 15.0
GATE_THRESHOLDS = (3, 6, 10)
CLASSES = ("identical-ish", "re-encode", "colour-graded", "different-content")


def connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    return con


def load_groups(con: sqlite3.Connection) -> dict:
    groups: dict = defaultdict(list)
    for row in con.execute("SELECT * FROM migration_manifest WHERE group_id IS NOT NULL"):
        groups[row["group_id"]].append(dict(row))
    return groups


def find_keeper(row: dict, members: list) -> dict | None:
    """Keeper named in the EXACT reason, else first member with a different hash."""
    others = [
        o for o in members
        if o["source_path"] != row["source_path"]
        and o["source_hash"] != row["source_hash"]
    ]
    named = FORMAT_NAME_RE.match(row["reason"] or "")
    if named:
        for o in others:
            if Path(o["source_path"]).name == named.group(1):
                return o
    return others[0] if others else None


def build_pairs(groups: dict) -> tuple[list, Counter]:
    """(row, keeper) for each EXACT format duplicate; stats for every EXACT row."""
    stats: Counter = Counter()
    pairs = []
    for members in groups.values():
        for row in members:
            if row["action"] != "EXACT":
                continue
            if not (row["reason"] or "").startswith(FORMAT_PREFIX):
                stats["byte_or_other"] += 1
                twin = any(
                    o is not row and o["source_hash"] is not None
                    and o["source_hash"] == row["source_hash"]
                    for o in members
                )
                stats["byte_with_sha_twin" if twin else "byte_without_sha_twin"] += 1
                continue
            stats["format"] += 1
            keeper = find_keeper(row, members)
            if keeper is None:
                stats["format_no_keeper"] += 1
                continue
            if keeper["source_hash"] == row["source_hash"]:
                stats["format_sha_equal_keeper"] += 1
            pairs.append((row, keeper))
    return pairs, stats


def ext_pair(a: dict, b: dict) -> str:
    exts = sorted([Path(a["source_path"]).suffix.lower().lstrip("."),
                   Path(b["source_path"]).suffix.lower().lstrip(".")])
    return "|".join(exts)


def dims_state(a: dict, b: dict) -> str:
    wa, ha, wb, hb = a["pixel_width"], a["pixel_height"], b["pixel_width"], b["pixel_height"]
    if None in (wa, ha, wb, hb):
        return "unknown"
    if (wa, ha) == (wb, hb):
        return "equal"
    if (wa, ha) == (hb, wb):
        return "swapped"
    return "differ"


def size_delta(a: dict, b: dict) -> float | None:
    sa, sb = a["file_size_bytes"], b["file_size_bytes"]
    if not sa or not sb:
        return None
    return abs(sa - sb) / max(sa, sb)


def summarise_v6(pairs: list, stats: Counter) -> None:
    print("== PART 1: v6 manifest (manifest only) ==")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    print(f"  format_pairs_total: {len(pairs)}")
    print("  extension pairs:")
    for ext, n in Counter(ext_pair(r, k) for r, k in pairs).most_common():
        print(f"    {ext}: {n}")
    print("  dimension state:")
    for state, n in Counter(dims_state(r, k) for r, k in pairs).most_common():
        print(f"    {state}: {n}")
    print("  size delta > 20% (suspect re-encode/grade), by dimension state:")
    suspects: Counter = Counter()
    for r, k in pairs:
        d = size_delta(r, k)
        if d is None:
            suspects["size-unknown"] += 1
        elif d > SIZE_SUSPECT:
            suspects[dims_state(r, k)] += 1
    for state, n in suspects.most_common():
        print(f"    {state}: {n}")


def decode_thumb(p: Path):
    from PIL import Image, ImageOps

    if p.suffix.lower() in RAW_EXT:
        from scanner.hasher import _load_raw_preview

        img = _load_raw_preview(p)
        if img is None:
            raise ValueError("no raw preview")
        return img.convert("RGB").resize(THUMB, Image.BOX)
    with Image.open(p) as im:
        oriented = ImageOps.exif_transpose(im)
        return oriented.convert("RGB").resize(THUMB, Image.BOX)


def signature(img) -> dict:
    rgb = list(img.getdata())
    grey = list(img.convert("L").getdata())
    n = len(rgb)
    means = tuple(sum(px[c] for px in rgb) / n for c in range(3))
    hist = Counter((r >> 6, g >> 6, b >> 6) for r, g, b in rgb)
    return {"rgb": rgb, "grey": grey, "means": means, "hist": hist}


def decode_signature(path_str: str) -> tuple:
    """Return (signature, error_label); exactly one is None."""
    p = Path(path_str)
    if not p.exists():
        return None, "unreachable"
    try:
        return signature(decode_thumb(p)), None
    except Exception as exc:  # decode boundary: a failure is counted, never guessed
        return None, type(exc).__name__


def load_sig(path_str: str, cache: dict) -> tuple:
    if path_str not in cache:
        cache[path_str] = decode_signature(path_str)
    return cache[path_str]


def pair_metrics(a: dict, b: dict) -> dict:
    n = len(a["rgb"])
    mad = sum(
        abs(x - y)
        for pa, pb in zip(a["rgb"], b["rgb"])
        for x, y in zip(pa, pb)
    ) / (3 * n)
    grey = sum(abs(x - y) for x, y in zip(a["grey"], b["grey"])) / n
    shift = max(abs(x - y) for x, y in zip(a["means"], b["means"]))
    keys = set(a["hist"]) | set(b["hist"])
    hist = sum(abs(a["hist"][k] - b["hist"][k]) for k in keys) / n
    return {"mad": mad, "grey": grey, "shift": shift, "hist": hist}


def classify(m: dict) -> str:
    if m["grey"] > GREY_STRUCT and m["mad"] >= 2:
        return "different-content"
    if m["mad"] < 2:
        return "identical-ish"
    if m["shift"] > 4 or m["mad"] > 6:
        return "colour-graded"
    return "re-encode"


def measure_pairs(pairs: list) -> tuple[list, Counter]:
    cache: dict = {}
    measured = []
    errors: Counter = Counter()
    for row, keeper in pairs:
        sig_a, err_a = load_sig(row["source_path"], cache)
        sig_b, err_b = load_sig(keeper["source_path"], cache)
        err = err_a or err_b
        if err:
            errors[err] += 1
            continue
        m = pair_metrics(sig_a, sig_b)
        measured.append({
            "row": row, "keeper": keeper, "m": m,
            "cls": classify(m), "dims": dims_state(row, keeper),
        })
    return measured, errors


def fmt_size(n) -> str:
    return f"{n / 1e6:.1f}MB" if n else "?"


def print_example(item: dict) -> None:
    r, k, m = item["row"], item["keeper"], item["m"]
    print(
        f"  [{item['cls']}] row={Path(r['source_path']).name} "
        f"keeper={Path(k['source_path']).name} exts={ext_pair(r, k)} "
        f"dims={item['dims']} ({r['pixel_width']}x{r['pixel_height']} vs "
        f"{k['pixel_width']}x{k['pixel_height']}) "
        f"size={fmt_size(r['file_size_bytes'])}/{fmt_size(k['file_size_bytes'])} "
        f"mad={m['mad']:.2f} shift={m['shift']:.2f} grey={m['grey']:.2f} hist={m['hist']:.3f}"
    )


def print_distribution(measured: list, errors: Counter) -> None:
    print("== PART 2: test set (decoded 32x32 RGB) ==")
    print(f"  measured format pairs: {len(measured)}")
    print(f"  not measured: {sum(errors.values())} {dict(errors)}")
    print("  class distribution (by dimension state):")
    for cls in CLASSES:
        grp = [x for x in measured if x["cls"] == cls]
        states = Counter(x["dims"] for x in grp)
        print(f"    {cls}: {len(grp)} {dict(states)}")


def gate_sweep(measured: list) -> None:
    print("== GATE SWEEP: gate = same dims AND mad <= T, else demote ==")
    for t in GATE_THRESHOLDS:
        stays = [x for x in measured if x["dims"] == "equal" and x["m"]["mad"] <= t]
        demoted = Counter(
            x["cls"] for x in measured
            if not (x["dims"] == "equal" and x["m"]["mad"] <= t)
        )
        print(f"  T={t}: stay EXACT {len(stays)} of {len(measured)}")
        print(f"    demoted by class: {dict(demoted)}")


def print_examples(measured: list) -> None:
    print("== EXAMPLES ==")
    shown = [x for x in measured if x["cls"] == "colour-graded"][:8]
    for cls in ("re-encode", "identical-ish", "different-content"):
        shown += [x for x in measured if x["cls"] == cls][:1]
    for item in shown[:12]:
        print_example(item)


def main() -> int:
    con = connect_ro(V6)
    v6_pairs, v6_stats = build_pairs(load_groups(con))
    con.close()
    summarise_v6(v6_pairs, v6_stats)

    tcon = connect_ro(TESTSET)
    ts_pairs, ts_stats = build_pairs(load_groups(tcon))
    tcon.close()
    print(f"== TEST SET stats: {dict(ts_stats)} ==")

    measured, errors = measure_pairs(ts_pairs)
    print_distribution(measured, errors)
    gate_sweep(measured)
    print_examples(measured)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
