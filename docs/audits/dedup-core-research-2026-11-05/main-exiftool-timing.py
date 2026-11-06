"""Does exiftool -j stream one record per file, or buffer the whole batch?
Runs the app's exact tag list on N NAS files (from the June manifest) and on one
large MOV, printing the wall-clock time of each stdout line. Read-only.
Usage: python main-exiftool-timing.py <manifest> <n_heic> <mov_path_or_->"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time

TAGS = [
    "-j", "-G", "-DateTimeOriginal", "-CreateDate", "-QuickTime:CreateDate",
    "-GPSLatitude", "-GPSLongitude", "-QuickTime:GPSCoordinates",
    "-XMP-xmpMM:DerivedFrom", "-XMP:Rating", "-XMP:Subject",
    "-EXIF:Make", "-EXIF:Model", "-EXIF:ISO", "-EXIF:FocalLength",
    "-EXIF:ExposureTime", "-EXIF:FNumber", "-EXIF:Flash", "-EXIF:Orientation",
    "-EXIF:Software", "-EXIF:LensModel", "-EXIF:ColorSpace", "-EXIF:WhiteBalance",
    "-EXIF:SubSecTimeOriginal", "-EXIF:OffsetTimeOriginal",
    "-QuickTime:Duration", "-QuickTime:VideoFrameRate", "-QuickTime:ImageWidth",
    "-QuickTime:ImageHeight", "-QuickTime:AudioChannels",
    "-QuickTime:AudioSampleRate", "-QuickTime:CompressorName",
]


def run(label: str, paths: list[str], cap_s: float) -> None:
    t0 = time.monotonic()
    proc = subprocess.Popen(
        ["exiftool", *TAGS, *paths],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    stamps: list[tuple[float, str]] = []

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stamps.append((time.monotonic() - t0, line.rstrip()[:60]))

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        proc.wait(timeout=cap_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"[{label}] KILLED after {cap_s:.0f}s cap")
    th.join(timeout=5)
    total = time.monotonic() - t0
    print(f"[{label}] files={len(paths)} total={total:.1f}s lines={len(stamps)}")
    if stamps:
        print(f"  first line at {stamps[0][0]:.1f}s: {stamps[0][1]}")
        # time of each new record start ('{' at column 0 or after '[')
        starts = [t for t, s in stamps if s.startswith("{") or s.startswith("[{")]
        print(f"  record starts ({len(starts)}): " + ", ".join(f"{t:.1f}" for t in starts[:12])
              + (" ..." if len(starts) > 12 else ""))
        print(f"  last line at {stamps[-1][0]:.1f}s")
    err = proc.stderr.read() if proc.stderr else ""
    if err.strip():
        print("  stderr:", err.strip()[:200])


def main() -> None:
    manifest, n_heic, mov = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    con = sqlite3.connect(manifest)
    con.execute("PRAGMA query_only = 1")
    heics = [r[0] for r in con.execute(
        "select source_path from migration_manifest "
        "where source_path like 'H:%' and lower(source_path) like '%.heic' "
        "order by id limit ?", (n_heic,))]
    run(f"{n_heic} HEIC on H:", heics, 170)
    if mov != "-":
        run("one large MOV", [mov], 170)


if __name__ == "__main__":
    main()
