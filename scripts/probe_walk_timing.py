"""H2 probe: pure enumeration timing over a target directory tree.

Times ``scanner.walker._iter_tree`` directly (the actual production
traversal — see ``scanner/walker.py:130``) rather than a re-implementation,
so this measures OUR walker's real enumeration cost, not an idealised
``os.walk``. Repeats the pass (default 2x): the first pass is cold
(directory metadata not yet in any cache), the second shows the SMB/
directory-cache effect a real scan benefits from on a re-scan of the same
tree.

Caveat on the dirs/files split: ``_iter_tree`` yields every entry (files
and directories) as a bare ``Path`` without telling the caller which is
which — that classification happens internally via
``entry.is_dir(follow_symlinks=False)`` during the walker's own descent
decision, but isn't returned. To report a dirs/files breakdown this probe
calls ``Path.is_dir()`` on each yielded entry, which is an EXTRA stat/
metadata round-trip beyond what the walker itself pays — on a NAS this
roughly doubles the per-entry metadata I/O actually measured here. Treat
``wall_s`` as an upper bound on pure walker cost, and prefer the
repeat-to-repeat DELTA (cold vs cache-warm) over the absolute number for
the H2 decision, since the extra stat cost is identical every repeat.

CLI usage
---------

    python scripts/probe_walk_timing.py --root D:\\nas-probe-corpus --repeat 2 \\
        --output probe_walk_timing.json
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

_DEFAULT_REPEAT = 2


def timed_pass(root: Path) -> dict:
    """One full ``_iter_tree`` pass; returns wall time + dirs/files counts."""
    from scanner.walker import _iter_tree

    t0 = time.perf_counter()
    n_dirs = 0
    n_files = 0
    for entry in _iter_tree(root, recursive=True):
        if entry.is_dir():
            n_dirs += 1
        else:
            n_files += 1
    wall = time.perf_counter() - t0
    total = n_dirs + n_files
    return {
        "wall_s": round(wall, 4),
        "dirs": n_dirs,
        "files": n_files,
        "entries": total,
        "entries_per_s": round(total / wall, 1) if wall > 0 else 0.0,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True)
    p.add_argument("--repeat", type=int, default=_DEFAULT_REPEAT,
                    help="Number of passes; repeat 1 is cold, later ones show dir-cache effect")
    p.add_argument("--output", default=None)
    args = p.parse_args(argv[1:])

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: --root {root} is not a directory", file=sys.stderr)
        return 2

    env = {
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
    }

    print(f"=== probe_walk_timing === root={root} repeat={args.repeat}")
    passes = []
    for i in range(args.repeat):
        result = timed_pass(root)
        label = "cold" if i == 0 else f"warm_{i}"
        result["pass"] = i + 1
        result["label"] = label
        print(f"  pass {i + 1} ({label}): wall_s={result['wall_s']}  "
              f"dirs={result['dirs']}  files={result['files']}  "
              f"entries_per_s={result['entries_per_s']}")
        passes.append(result)

    out = {"env": env, "args": {"root": str(root), "repeat": args.repeat}, "passes": passes}
    payload = json.dumps(out, indent=2, ensure_ascii=False)
    print("\n--- JSON ---")
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
