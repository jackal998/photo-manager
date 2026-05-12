"""Unit tests for ``qa.scenarios._batch.select_shard``.

The CI workflow ``.github/workflows/qa-batch.yml`` slices ``ALL_SCENARIOS``
across three parallel GitHub Actions jobs via ``--shard / --total-shards``.
Three invariants must hold or the batch becomes meaningless:

  1. Pairwise disjoint — no scenario runs twice (wasted runner minutes).
  2. Union equals ALL_SCENARIOS — every scenario runs exactly once.
  3. s23a and s23b stay on the same shard — s23b reads what s23a wrote;
     splitting them across shards breaks the scenario silently.

Tests run the real function over the real ``ALL_SCENARIOS`` list. We're
not mocking the dispatch; we're checking the selection math against the
production input.
"""
from __future__ import annotations

import pytest

from qa.scenarios._batch import ALL_SCENARIOS, select_shard


# 1 is degenerate (one shard = everything) but it's a useful sanity check.
# 5 is the production CI matrix size; 2/3/4 bracket it so the invariants
# are pinned across the range of values someone might bump to.
_SHARD_COUNTS = [1, 2, 3, 4, 5]


@pytest.mark.parametrize("total_shards", _SHARD_COUNTS)
def test_shards_are_pairwise_disjoint(total_shards: int) -> None:
    shards = [
        select_shard(ALL_SCENARIOS, k, total_shards)
        for k in range(1, total_shards + 1)
    ]
    for i in range(len(shards)):
        for j in range(i + 1, len(shards)):
            overlap = set(shards[i]) & set(shards[j])
            assert overlap == set(), (
                f"shard {i+1} and shard {j+1} overlap "
                f"(N={total_shards}): {sorted(overlap)}"
            )


@pytest.mark.parametrize("total_shards", _SHARD_COUNTS)
def test_shards_union_equals_all_scenarios(total_shards: int) -> None:
    shards = [
        select_shard(ALL_SCENARIOS, k, total_shards)
        for k in range(1, total_shards + 1)
    ]
    union: set[str] = set().union(*shards)
    assert union == set(ALL_SCENARIOS)


@pytest.mark.parametrize("total_shards", _SHARD_COUNTS)
def test_s23_pair_lives_on_same_shard(total_shards: int) -> None:
    """s23b reads settings s23a wrote — never split them."""
    for k in range(1, total_shards + 1):
        shard = select_shard(ALL_SCENARIOS, k, total_shards)
        has_a = "s23a_set_settings" in shard
        has_b = "s23b_verify_settings" in shard
        assert has_a == has_b, (
            f"shard {k}/{total_shards} splits the s23 pair: "
            f"s23a={has_a}, s23b={has_b}"
        )


@pytest.mark.parametrize("total_shards", _SHARD_COUNTS)
def test_s23a_runs_before_s23b_within_shard(total_shards: int) -> None:
    """Within whichever shard owns them, s23a must precede s23b."""
    for k in range(1, total_shards + 1):
        shard = select_shard(ALL_SCENARIOS, k, total_shards)
        if "s23a_set_settings" in shard:
            assert shard.index("s23a_set_settings") < shard.index(
                "s23b_verify_settings"
            )


def test_shard_count_balance_is_reasonable() -> None:
    """Production shards should differ in size by at most 2 scenarios.

    Sorted-stride over U units into M shards is balanced by construction
    (each shard has floor(U/M) or ceil(U/M) units → delta of at most 1).
    Pairing s23 into one unit means whichever shard owns it gets one
    extra scenario when units are flattened back, so the delta can rise
    by 1 more — bounded at 2. Concretely at M=5 with 40 scenarios / 39
    units, sizes are 9/8/8/8/7 (the 9 is the pair-holding shard).
    If a future addition tilts it past 2 (e.g. a third s23-style pair),
    the imbalance is worth catching here rather than as a slow CI shard.
    """
    total_shards = 5  # keep in sync with .github/workflows/qa-batch.yml matrix
    sizes = [
        len(select_shard(ALL_SCENARIOS, k, total_shards))
        for k in range(1, total_shards + 1)
    ]
    assert max(sizes) - min(sizes) <= 2, (
        f"shard sizes unbalanced: {sizes}"
    )


def test_invalid_shard_number_raises() -> None:
    with pytest.raises(ValueError):
        select_shard(ALL_SCENARIOS, 0, 5)
    with pytest.raises(ValueError):
        select_shard(ALL_SCENARIOS, 6, 5)
    with pytest.raises(ValueError):
        select_shard(ALL_SCENARIOS, 1, 0)
