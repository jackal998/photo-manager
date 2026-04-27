---
name: update-docs
description: Use after implementing any fix or new feature in photo-manager to keep documentation in sync. Checks README.md and pyproject.toml against what actually changed. Applies only surgical edits to affected sections.
origin: local
---

# Update Docs After Each Fix / Feature

Activate this skill after implementing any non-trivial change to photo-manager so that documentation stays in sync with the code.

## When to activate

- After adding, renaming, or deleting a source file
- After adding a test file
- After changing the SQLite schema (new columns, renamed columns)
- After adding or removing a service, interface, or module
- After changing menu actions or UI flow
- After bumping the Python version
- After adding or removing a `settings.json` key
- After deprecating or deleting code

---

## Docs map for `photo-manager`

| File | What it tracks | Sections / spots to check |
|------|---------------|--------------------------|
| `README.md` | Project structure tree, test list, test count | `## Project structure` block; `tests/` subtree; `# NNN tests` comment |
| `pyproject.toml` | Python version for Black / Ruff / Pylint | `target-version = ["py3XX"]`, `target-version = "py3XX"`, `py-version = "3.XX"` |

---

## Checklist

Work through every item below. Check only the items relevant to your change.

### Files changed?
- [ ] Added a `.py` file → add it to `README.md` project structure tree at the right indentation level
- [ ] Added a test file `tests/test_*.py` → add it to `README.md` test list; bump the `# 270+ tests` count
- [ ] Removed or deprecated a file → mark `[deprecated]` in `README.md`; do **not** delete the entry
- [ ] Renamed a file → update `README.md`

### Schema changed?
- [ ] Added column(s) to `migration_manifest` → update `README.md` manifest schema table (§ "Scanner features" / manifest schema)
- [ ] Updated `_MIGRATIONS` list → verify migration note in `README.md` is still accurate

### Service / interface changed?
- [ ] Added or removed an infrastructure class → update `README.md` infrastructure tree

### UI / menu changed?
- [ ] Changed a dialog → update `README.md` dialogs tree

### Settings changed?
- [ ] Added a new `settings.json` key → add it to `README.md` Configuration section
- [ ] Removed a key → remove or annotate it in `README.md`

### Python version bumped?
- [ ] `pyproject.toml` — update `target-version` (Black + Ruff) and `py-version` (Pylint)

### Background worker / major flow changed?
- [ ] New `QThread` worker → add to `README.md` workers/ subtree

---

## How to apply

1. Read the changed source file(s) to understand exactly what changed.
2. For each checked item above, read the relevant doc section.
3. Apply **surgical edits** — replace only the stale sentence/row/bullet; do not rewrite entire sections.
4. After all edits, run `python -m pytest tests/ -q --tb=short` to confirm no regressions.
5. Commit the doc changes alongside the code change (or as an immediate follow-up commit on the same branch).

---

## Example edit patterns

**New file added** (`app/views/dialogs/export_dialog.py`):
```
# In README.md project structure, find the dialogs/ block and add:
│   │   │   ├── export_dialog.py            # Export decisions to CSV
```

**New settings key** (`"preview_max_side": 1024`):
```
# In README.md Configuration section, add the key to the example JSON block.
```

**Deprecated a file** (`app/views/dialogs/legacy_dialog.py`):
```
# In README.md project structure, change:
│   │   │   └── legacy_dialog.py
# to:
│   │   │   └── legacy_dialog.py            # [deprecated — legacy stub]
```
