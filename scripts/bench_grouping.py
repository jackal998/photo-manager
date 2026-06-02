"""Benchmark near-duplicate candidate generation: brute force vs BK-tree (#526).

The scan pipeline's grouping stage was O(N²): ``scanner.dedup`` compared every
pHash candidate against every other (a 19,936-file scan ≈ 16k candidates ≈
128M pairwise comparisons — the "grouping 跑得很久" the user reported). PR #526
replaces the *pair enumeration* with a hand-rolled BK-tree (Hamming metric via
integer XOR popcount), keeping every verdict gate unchanged.

This script measures both paths at a range of candidate counts on a synthetic
but realistic distribution (cluster centres + bit jitter) and prints a table
plus the projected crossover. It is a developer tool, not a test — run it by
hand; ``scripts/*`` is excluded from coverage.

    python scripts/bench_grouping.py            # default sizes, threshold=10
    python scripts/bench_grouping.py 500 4000   # custom N list

Key finding (Windows / CPython 3.12, imagehash 4.x): the BK-tree is ~5-6×
faster at *every* N from 10 up — there is no N where brute force wins, because
the brute path's per-pair ``imagehash`` subtraction (numpy-backed) costs ~µs
while the tree's ``int.bit_count()`` popcount is several times cheaper, and
that constant-factor win compounds with the quadratic term. The real library
is far less densely clustered than this synthetic set, so production speedups
are larger still. See ``scanner.dedup._BKTREE_MIN_CANDIDATES``.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

# Running ``python scripts/bench_grouping.py`` puts scripts/ (not the repo
# root) on sys.path[0], so bootstrap the root before importing scanner.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imagehash

from scanner.dedup import _BKTree


def make_hashes(n: int, seed: int = 1) -> tuple[list[int], list]:
    """Return ``(int_hashes, imagehash_objs)`` for ``n`` clustered pHashes."""
    rng = random.Random(seed)
    ints: list[int] = []
    objs: list = []
    while len(ints) < n:
        centre = rng.getrandbits(64)
        for _ in range(rng.randint(1, 6)):
            if len(ints) >= n:
                break
            v = centre
            for _ in range(rng.randint(0, 7)):
                v ^= 1 << rng.randint(0, 63)
            ints.append(v)
            objs.append(imagehash.hex_to_hash(format(v, "016x")))
    return ints, objs


def brute_pairs(objs: list, threshold: int) -> int:
    """Legacy O(N²) scan using imagehash subtraction (the pre-#526 path)."""
    n = len(objs)
    pairs = 0
    for i in range(n):
        a = objs[i]
        for j in range(i + 1, n):
            d = a - objs[j]
            if 0 < d <= threshold:
                pairs += 1
    return pairs


def bk_pairs(int_hashes: list[int], threshold: int) -> int:
    """BK-tree candidate generation using integer popcount (the #526 path)."""
    n = len(int_hashes)
    pairs = 0
    tree = _BKTree(int_hashes[0], 0)
    for idx in range(1, n):
        tree.add(int_hashes[idx], idx)
    for i in range(n):
        for j in tree.query(int_hashes[i], threshold):
            if j > i and 0 < (int_hashes[i] ^ int_hashes[j]).bit_count() <= threshold:
                pairs += 1
    return pairs


def main(argv: list[str]) -> int:
    threshold = 10
    sizes = [int(a) for a in argv[1:]] or [100, 200, 500, 1000, 2000, 4000, 8000]
    print(f"threshold={threshold}")
    print(f"{'N':>7} {'brute_ms':>10} {'bk_ms':>9} {'speedup':>8} {'pairs':>8}")
    for n in sizes:
        ints, objs = make_hashes(n)
        t0 = time.perf_counter()
        pb = brute_pairs(objs, threshold)
        bms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        pk = bk_pairs(ints, threshold)
        kms = (time.perf_counter() - t0) * 1000
        if pb != pk:  # the two paths MUST agree on the candidate count
            print(f"  !! MISMATCH at N={n}: brute={pb} bk={pk}")
            return 1
        print(f"{n:>7} {bms:>10.1f} {kms:>9.1f} {bms / kms:>7.1f}x {pb:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
