---
name: impact-map
description: Use BEFORE editing any function, class, or module in photo-manager.
  Maps upstream callers and downstream dependencies so you know which tests must
  pass and which behaviors might shift. Pairs with update-docs (post-change).
origin: local
---

# Impact map — before editing a non-trivial symbol

The point: no code change ships in `photo-manager` without first knowing
who calls what and which test exercises that path. If a caller has no test
and you're about to change behavior the caller relies on, write the test
*before* the edit.

## When to activate

- Modifying a public function/class signature
- Changing return shape, raised exceptions, or side-effect behavior
- Renaming or deleting a symbol
- Touching a SQLite migration, a `settings.json` key, or a background worker
- Changing scanner pipeline stages (walker, hasher, exif, dedup, manifest)
- Changing any `infrastructure/` service that other modules import

Skip for: typo fixes, comment-only edits, or strictly internal refactors of
a private helper that is verifiably called from one place and is fully
covered by an existing test.

## Checklist (run all of it before the edit)

1. **Upstream callers** — `Grep` the symbol name across `app/`, `scanner/`,
   `core/`, `infrastructure/`, and `tests/`. List every call site with
   `file:line`. **Don't forget non-source consumers**: QA driver scripts
   under `qa/scenarios/`, doc tables in `docs/testing.md`, and string
   references in `app/views/` (button labels, menu titles, accessibility
   names) all break the same way #72 broke when we renamed the scan
   button without grepping.
2. **Downstream dependencies** — what does the function call into? Note any
   I/O (DB, FS, subprocess, Qt) it relies on, since changes there cascade.
   Boundary calls (subprocess to `exiftool`, `send2trash`, `rawpy`,
   `pillow-heif`, `Image.open` on edge formats) need *layer-2* tests, not
   just unit-level mocks. See [`docs/testing.md`](../../../docs/testing.md).
3. **Tests covering each call site, by layer** — for every caller in (1),
   identify which test exercises that path AND at which layer:
     - Layer 1 (`tests/test_*.py` — unit + mock)
     - Layer 2 (`tests/integration/` — real binaries, when present)
     - Layer 3 (`qa/scenarios/sNN_*.py` — qa-explore E2E)
   Flag callers with NO coverage at the layer that matters for the
   change. Boundary changes need layer 2; UI-touching changes need
   layer 3.
4. **Behavior contract** — write down (in chat, not a file) the inputs,
   outputs, raised exceptions, and side-effects you intend to *preserve*
   versus *change*.
5. **Plan test updates** — if behavior is changing, list the existing tests
   that must be updated. If a caller has NO test and your change could
   break it, add a test before editing the symbol.

   **What a real test looks like** (per [`CLAUDE.md`](../../../CLAUDE.md)
   testing rules): it triggers the failure mode with a *real* input — a
   truncated file, a missing optional dep, a malformed payload — and
   asserts the user-visible outcome. A test that mocks
   `QStandardItem.setData` to raise just so the wrapped `except: pass`
   runs is metric gaming, not bug-catching. If a defensive branch can't
   be triggered with a real input, leave it covered by a comment in the
   source, not by a synthetic assertion.

## Output format

A short table in chat, then proceed:

```
| Caller                                          | Test                                              | Action needed                              |
|-------------------------------------------------|---------------------------------------------------|--------------------------------------------|
| app/views/handlers/file_operations.py:127       | tests/test_file_operations.py::test_batch_update (L1)              | none                                       |
| scanner/dedup.py:88                             | (no L1)                                                            | add test before changing signature         |
| infrastructure/manifest_repository.py:204       | tests/test_manifest_repository.py::test_save (L1)                  | update assertion for new return shape      |
| infrastructure/delete_service.py:67             | tests/test_delete_service.py (L1, mocked)  +  no L2                | add layer-2 integration test if behavior at the send2trash boundary is changing |
| qa/scenarios/_uia.py:55 (button title constant) | qa/scenarios/sNN_*.py drivers (L3)                                 | update constant + commit message; layer-3 batch will catch drift on next run |
```

After the edit, switch to the `update-docs` skill to keep `README.md`,
`pyproject.toml`, and the per-module residual-risk table in
`docs/testing.md` in sync.

## Why this exists

The project's unit suite only protects what it covers. A change to a
function used in five places is only safe if all five places are tested
— or if you confirm in advance that the change preserves the behavior
the untested callers depend on. And "tested at the right layer": a
boundary change (subprocess, third-party lib) won't fail layer-1 mocks
even when the real boundary is broken. This skill makes the
caller-and-layer mapping explicit instead of assumed.
