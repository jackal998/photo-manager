# Near-duplicate grouping topology

How the scanner turns a similarity graph into duplicate **groups**, why it is a
**connected-component (union-find)** model, and what that decision implies for
the "—" passenger, aggressive auto-delete, and future changes. This document is
the canonical answer to *"what is a group, and why is it shaped this way?"*

**Status:** Decided 2026-06-03 (research under [#537](https://github.com/jackal998/photo-manager/issues/537);
root-cause + safety work in [#536](https://github.com/jackal998/photo-manager/issues/536)).
**Model:** a group is a *similarity connected component*; non-reference members
are labelled by their similarity to the displayed Ref, with a star (`N*%`) and a
nearest-member tooltip for transitive "passengers".

---

## 1. The model

Grouping runs in `scanner/dedup.py::classify` as the last pass,
`_assign_group_ids` — a **union-find (disjoint-set)** over two edge sources:

- **`duplicate_of` edges** minted by `_classify_near_duplicates` / the EXACT and
  format-duplicate passes. A pHash pair becomes an edge only after surviving the
  confirmation gates: Hamming ≤ `threshold` (default 10), dHash agreement
  ([#524](https://github.com/jackal998/photo-manager/issues/524)), mean-colour
  ([#462](https://github.com/jackal998/photo-manager/issues/462)), pHash entropy
  ([#516](https://github.com/jackal998/photo-manager/issues/516)), and minimum
  dimension ([#523](https://github.com/jackal998/photo-manager/issues/523)).
- **pair edges** (`_collect_pair_edges`) for same-stem Live-Photo / RAW+JPG sets,
  content-gated since [#539](https://github.com/jackal998/photo-manager/issues/539).

Candidate pairs come from a **BK-tree**, O(N log N), proven bit-identical to the
brute O(N²) scan (`TestBKTreeParity`,
[#526](https://github.com/jackal998/photo-manager/issues/526)). The BK-tree is
*candidate generation only* — it never changes the grouping verdict.

The component's canonical id is **`group_id` = the lexicographically smallest
`source_path` in the component**. This determinism is **load-bearing**:
`ManifestRepository.rescore(weights)` re-scores in memory against stored
`group_id`s with zero file I/O, so a `group_id` that flips between runs is a
**correctness** bug, not a cosmetic one.

Per-row explainability ships today: the tree's Similarity column recomputes
"X% similar to \<Ref\>" at render time
([#253](https://github.com/jackal998/photo-manager/issues/253),
`tree_model_builder._file_similarity`).

---

## 2. The "—" passenger — what it is, and where it actually comes from

A group renders one Ref-tier row as **Ref** and any *other* Ref-tier row as a
bare **"—"** (`tree_model_builder._pick_ref_winner` / `_file_similarity`). The
"—" reads as "a member of a duplicate group with no shown similarity to the
reference," which is confusing, and — before
[#540](https://github.com/jackal998/photo-manager/issues/540) — a scoreable "—"
row could be auto-marked for deletion.

The original [#536](https://github.com/jackal998/photo-manager/issues/536)
hypothesis was that **pHash single-linkage transitive chaining** (A~B~C grouped
when A≁C) produced the "—". **This is empirically false.** A 200,000-trial
reproduction driving `classify()` directly found **zero** pure-pHash components
with ≥2 Ref-tier rows: the in-`rows` guards in `_classify_near_duplicates`
(`dedup.py:631`/`:655`) plus the lower-priority-loses sort force **exactly one
never-flagged Ref-tier row per pure-pHash component**.

The "—" (= ≥2 never-flagged Ref-tier rows in one component) actually comes from:

1. the **unconditional, filename-based pair edge** — a name *collision* across
   merged folders / multiple cameras / Takeout unioned unrelated files
   (fixed at the source by [#539](https://github.com/jackal998/photo-manager/issues/539));
2. a **reconnected transitive edge** — see #538 below.

### Shipped guard rails

- **[#540](https://github.com/jackal998/photo-manager/issues/540) (merged)** —
  aggressive auto-delete is restricted to rows positively classified as
  duplicates (`action ∈ {EXACT, REVIEW_DUPLICATE}`); a Ref-tier "—" passenger is
  never auto-deleted. Fail-safe allowlist (any future non-duplicate action is
  excluded by default).
- **[#539](https://github.com/jackal998/photo-manager/issues/539) (merged)** —
  `_collect_pair_edges` drops a same-stem edge when both members carry a pHash
  whose Hamming distance exceeds `threshold` (positive evidence of different
  images). Video peers (no pHash) and same-shot RAW+JPG (agreeing pHash) are
  preserved. A pHash gate is used, **not** a shot-date gate, because a Live
  Photo's HEIC (`DateTimeOriginal`, camera-local) and its MOV (QuickTime date,
  often UTC) legitimately differ by a timezone offset.

---

## 3. The decision: keep connected-component / union-find

Five models were evaluated against this codebase's constraints. **Only
connected-component scored "adopt";** the rest were "reject-too-costly".

| Model | Real-world users | Verdict | Why |
|---|---|---|---|
| **Connected-component / union-find** *(current)* | immich (DSU group-id rewrite) | **adopt** | Keeps bursts whole (recall); **deterministic with zero machinery** (CC partition is unique for a fixed graph); perfect BK-tree fit; lowest migration (we already are it). |
| Agglomerative complete/avg linkage | — | reject | Splits true series; breaks determinism without extra machinery; poor BK-tree fit. |
| Maximal clique / k-core / density | dupeGuru (clique-on-ref) | reject | High series-splitting; *inverts* #538 (worse under-grouping); high migration. |
| Leader / retrieval-greedy | digiKam, FiftyOne, dupeGuru, **Czkawka (post-#1685)** | reject | Structurally kills "—", **but breaks determinism** (order/seed-dependent leader) and splits true bursts. |
| Community (Louvain/Leiden) / density (HDBSCAN) | — | reject | Seed-dependent (breaks determinism); doesn't even address the real "—" cause; high cost. |

**The decisive axis is determinism, not precision/recall.** Our lex-min
`group_id` is required for rescore-without-rescan; a connected-component
partition is *unique* for a fixed graph, while every surveyed leader/community
implementation is order- or seed-dependent (imagededup's kept file is
non-deterministic; immich uses a random UUID; Czkawka's author calls a leader
"frozen" with "no idea how to properly reparent it" — ties flip; Louvain is
explicitly seed-dependent). pHash Hamming is integer-valued, so ties are rampant
at 10k–200k photos. Adopting a leader model would turn a *cosmetic* bug into a
*correctness* bug, split genuine burst/edit series, and cost a from-scratch
rewrite — to fix something [#540](https://github.com/jackal998/photo-manager/issues/540)
already made safe and the relabel (below) makes legible.

### Strongest counter-argument, and the rebuttal

> *Switch to a leader/retrieval model and the "—" disappears structurally —
> every member attaches to exactly one representative via a direct edge, so it
> always has a real similarity number.*

True, and the survey's strongest cross-tool consensus. But (1) **it breaks our
determinism** — a greedy attach partition is not unique even with a lex-min
tiebreak, so `rescore` would re-score against shifted groups; those tools can
afford it because they have **no rescore-without-rescan contract**, and we do;
(2) **it splits true bursts** (a continuous-shutter / progressive-edit chain
where adjacent pairs are ≤ threshold but endpoints are not) into several groups,
worse for the review task and silently changing the delete-grouping #540 just
stabilised; (3) **its one benefit is already obtained** — #540 made "—"
delete-safe and the Direction-A relabel makes every member show a real number,
without the determinism break.

---

## 4. Consequences (the two follow-ups this decision defines)

- **[#538](https://github.com/jackal998/photo-manager/issues/538) — make the
  union-find a *true* transitive closure.** Today `_classify_near_duplicates`
  (`dedup.py:631`) drops a genuine near-dup edge when its only bridge is an
  already-classified file, orphaning a real near-dup to `group_id=None` (a
  recall bug — the union-find is an order-dependent *spanning forest*, not a
  closure). Fix: **decouple edge collection from classification** — record every
  pHash pair that passes the gates as a union-find edge regardless of whether an
  endpoint is already classified; keep classification (who is EXACT/REVIEW/Ref) a
  separate concern. Bump `GROUPING_STRATEGY_VERSION` (the verdict shifts for
  orphaned members → the [#486](https://github.com/jackal998/photo-manager/issues/486)
  calibration cache invalidates).
- **[#536](https://github.com/jackal998/photo-manager/issues/536) Direction A —
  relabel the passenger** (shipped as option D, render-time only,
  `tree_model_builder`). A passenger shows its similarity to the **displayed
  Ref** — the same reference every other row uses, so the column is internally
  consistent — with a **trailing star** (`N*%`) marking it as an
  indirect/transitive member; a **tooltip** names the *nearest* group member
  (the strongest actual link: `"N% similar to <file>"`). The bare "—" is reserved
  for a passenger with no comparable pHash (a Live Photo MOV). This is the
  keystone that makes the #538 recall fix *legible*: a reconnected near-dup reads
  `75*%` with a "97% similar to \<neighbour\>" tooltip rather than a blank "—".

`group_id` semantics, the DB schema, the scoring layer, and the BK-tree are all
**unchanged** by both follow-ups.

---

## 5. Validation plan

1. **Reproduce the #538 orphan** — a genuine near-dup currently lands
   `group_id=None`; after edge-decoupling it joins the component.
2. **Determinism preserved** — same input, shuffled order → identical
   `group_id`s (extend the parity tests with the lex-min invariant).
3. **Relabel** — a passenger renders "N% similar to \<nearest\>" not "—" (unit on
   `_file_similarity` + a `qa/scenarios/sNN` driver, since it touches
   `app/views/**`).
4. **Cache invalidation** — `GROUPING_STRATEGY_VERSION` bump confirmed to flow
   into `scan_worker.hash_pool_fingerprint`.

---

## 6. References

- Issues: [#536](https://github.com/jackal998/photo-manager/issues/536) (root cause + safety),
  [#537](https://github.com/jackal998/photo-manager/issues/537) (model research, this decision),
  [#538](https://github.com/jackal998/photo-manager/issues/538) (under-grouping),
  [#539](https://github.com/jackal998/photo-manager/issues/539) (same-stem gate),
  [#540](https://github.com/jackal998/photo-manager/issues/540) (aggressive-delete safety net).
- Code: `scanner/dedup.py` (`_assign_group_ids`, `_classify_near_duplicates`,
  `_collect_pair_edges`, `_BKTree`), `app/views/tree_model_builder.py`
  (`_pick_ref_winner`, `_file_similarity`), `core/services/auto_select.py`
  (`non_keepers_for_aggressive_delete`).
- Prior art read during the research: immich (union-find/DSU, recall),
  PhotoPrism (exact-key stacking, not perceptual), dupeGuru (clique-on-ref),
  digiKam (leader/retrieval), FiftyOne / cleanlab / DataEval (mixed),
  Czkawka / rmlint / fclones (leader/parent), and the CS clustering taxonomy
  (connected-components vs agglomerative vs clique/k-core vs leader vs
  community/density).
