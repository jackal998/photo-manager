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
   `file:line`.
2. **Downstream dependencies** — what does the function call into? Note any
   I/O (DB, FS, subprocess, Qt) it relies on, since changes there cascade.
3. **Tests covering each call site** — for every caller in (1), identify
   which test file exercises that path. Flag callers with NO test coverage.
4. **Behavior contract** — write down (in chat, not a file) the inputs,
   outputs, raised exceptions, and side-effects you intend to *preserve*
   versus *change*.
5. **Plan test updates** — if behavior is changing, list the existing tests
   that must be updated. If a caller has NO test and your change could
   break it, add a test before editing the symbol.

## Output format

A short table in chat, then proceed:

```
| Caller                                          | Test                                              | Action needed                              |
|-------------------------------------------------|---------------------------------------------------|--------------------------------------------|
| app/views/handlers/file_operations.py:127       | tests/test_file_operations.py::test_batch_update  | none                                       |
| scanner/dedup.py:88                             | (no test)                                         | add test before changing signature         |
| infrastructure/manifest_repository.py:204       | tests/test_manifest_repository.py::test_save      | update assertion for new return shape      |
```

After the edit, switch to the `update-docs` skill to keep `README.md` and
`pyproject.toml` in sync.

## Why this exists

The project has 416 unit tests but they only protect what they cover. A
change to a function used in five places is only safe if all five places
are tested — or if you confirm in advance that the change preserves the
behavior the untested callers depend on. This skill makes that confirmation
explicit instead of assumed.
