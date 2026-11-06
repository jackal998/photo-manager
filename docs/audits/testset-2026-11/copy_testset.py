"""Copy the selected test set to a root that preserves drive-relative paths, then verify size + sha256.

Usage: python copy_testset.py <selection.csv> <ROOT> <report.csv>
Sources are read only. Targets: <ROOT>\\<drive letter>\\<path under the drive>. A target that already exists
with the right size and sha256 is skipped. Every row ends in the report with status ok / skipped / mismatch /
missing-source / error — nothing is retried silently.
"""
from __future__ import annotations

import csv
import hashlib
import shutil
import sys
import time
from pathlib import Path, PureWindowsPath


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def target_for(root: Path, source: str) -> Path:
    p = PureWindowsPath(source)
    drive = p.drive.rstrip(":")
    return root / drive / Path(*p.parts[1:])


def verify(dst: Path, size: int, digest: str) -> str | None:
    if dst.stat().st_size != size:
        return f"size {dst.stat().st_size} != {size}"
    got = sha256_of(dst)
    if digest and got != digest:
        return f"sha256 {got[:12]} != {digest[:12]}"
    return None


def copy_row(row: dict, root: Path) -> tuple[str, str]:
    src = Path(row["source_path"])
    size = int(row["file_size_bytes"] or 0)
    digest = (row["source_hash"] or "").lower()
    dst = target_for(root, row["source_path"])
    if not src.exists():
        return "missing-source", ""
    if dst.exists() and verify(dst, size, digest) is None:
        return "skipped", ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        return "error", str(e)
    problem = verify(dst, size, digest)
    return ("mismatch", problem) if problem else ("ok", "")


def main(selection: str, root_s: str, report: str) -> int:
    root = Path(root_s)
    rows = list(csv.DictReader(open(selection, encoding="utf-8", newline="")))
    counts: dict[str, int] = {}
    done_bytes = 0
    t0 = time.time()
    with open(report, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["status", "detail", "category", "source_path", "target"])
        for i, row in enumerate(rows, 1):
            status, detail = copy_row(row, root)
            counts[status] = counts.get(status, 0) + 1
            if status in ("ok", "skipped"):
                done_bytes += int(row["file_size_bytes"] or 0)
            w.writerow([status, detail, row["category"], row["source_path"], str(target_for(root, row["source_path"]))])
            if i % 50 == 0 or i == len(rows):
                out.flush()
                print(f"{i}/{len(rows)} {done_bytes / 1e9:.2f} GB {counts} {time.time() - t0:.0f}s", flush=True)
    print("DONE", counts)
    return 0 if set(counts) <= {"ok", "skipped"} else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
