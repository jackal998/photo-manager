---
name: sqlite-migration-safety
description: Audit photo-manager's SQLite schema migrations for safety. Use when /pr-review's diff touches the _MIGRATIONS list in infrastructure/manifest_repository.py or the CREATE TABLE migration_manifest block in scanner/manifest.py — this skill enforces append-only ordering, backward-compatible defaults, idempotency, and companion edits to ManifestRow + schema SQL + README schema table.
origin: local
---

# sqlite-migration-safety — Gate 8 rubric

Invoked by `/pr-review` Gate 8 when the diff touches the
`_MIGRATIONS` list in `infrastructure/manifest_repository.py` OR
the schema SQL block in `scanner/manifest.py`
(`CREATE TABLE migration_manifest`). Skip otherwise.

photo-manager uses SQLite with a hand-rolled migration runner.
SQLite can't safely do `DROP COLUMN`, `RENAME COLUMN`, or
`ALTER COLUMN TYPE` without a full table rebuild — and existing
user manifests on disk would break if the migration list changes
out of order. This skill catches the specific failure modes that
would corrupt old manifests.

## When to invoke

`/pr-review` invokes this skill when ANY of these are touched in
the diff:

- `infrastructure/manifest_repository.py` (the `_MIGRATIONS` list)
- `scanner/manifest.py` (the `CREATE TABLE migration_manifest`
  schema block)
- Any dataclass that mirrors the manifest schema
  (`scanner/dedup.py` `ManifestRow`)

If none of these are touched, skip this skill entirely.

## Checks (each new migration entry)

### 1. Additive only

`ADD COLUMN` semantics (the project uses
`ALTER TABLE … ADD COLUMN`). Never `DROP COLUMN`,
`RENAME COLUMN`, or `ALTER COLUMN TYPE` — SQLite can't do those
safely without a table rebuild, and old manifests would break.
If you see one, ✗ flag.

### 2. Appended at end, not inserted into the middle

The order of `_MIGRATIONS` IS the migration order — each new
row must land after every existing row. Inserting into the
middle re-orders migration application and could fail mid-list
on an already-migrated DB. ✗ flag if mid-list insertion.

### 3. Backward-compatible defaults

- `INTEGER` / `REAL` / `TEXT` columns: nullable (no default)
  OR `DEFAULT 0` / `DEFAULT 0.0` / `DEFAULT ''` — safe.
- `NOT NULL` without a default on existing data → ⚠ (older
  manifests would fail the migration).
- The existing convention is `INTEGER NOT NULL DEFAULT 0`
  for flags and `REAL` (nullable) for scores. Match it.

### 4. Idempotency

The repo's `_apply_migrations` does `ALTER TABLE` and swallows
the "duplicate column" error — safe to re-run. Don't break that
invariant (e.g., by replacing with `CREATE TABLE`-style schema).

### 5. Companion edits

A new migration row MUST also appear:

- In the `CREATE TABLE migration_manifest` schema in
  `scanner/manifest.py` (so new manifests have the column
  from the start, not via migration).
- As a field on `ManifestRow` (or equivalent dataclass) in
  `scanner/dedup.py` if read by the scanner.

If absent in either, ⚠ flag the mismatch.

### 6. README schema table

The README has a manifest schema table. If the migration adds a
user-facing column (visible in the UI), `update-docs` should
have flagged a README touch. If README wasn't touched, ⚠
suggest updating the schema table.

## Output format

Emit findings under the `## SQLite migration safety (Gate 8)`
section of pr-review's chat report, one line per finding:

```
⚠ <_MIGRATIONS line> — <issue>: <evidence>
✗ <_MIGRATIONS line> — mid-list insertion: <evidence>
```

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `infrastructure/manifest_repository.py` — the `_MIGRATIONS`
  list and `_apply_migrations` runner.
- `scanner/manifest.py` — the canonical schema for new manifests.
- `update-docs/SKILL.md` — write-side companion that updates
  README schema table after the fact.
