---
name: app-security-patterns
description: Application-level security pattern scan for photo-manager Python code. Use when /pr-review's diff has behaviour-bearing source files — this skill flags SQL injection via f-strings, hardcoded secrets, unsafe deserialisation (pickle/yaml), shell injection (subprocess shell=True), eval/exec on diff content, and path traversal in write operations. Composes with the global security-review skill for generic OWASP-style guidance.
origin: local
---

# app-security-patterns — Gate 7 rubric

Invoked by `/pr-review` Gate 7 when the diff has behaviour-bearing
files. This skill scans for concrete dangerous patterns specific
to Python / SQLite / subprocess code paths in photo-manager.

## Composes with global lens

**Before applying the patterns below, also read the global
`security-review` skill** (Skill tool) to load its OWASP-style
checklist — input validation, secrets management, error message
leakage, dependency hygiene. That global skill provides the
generic backdrop; this skill layers photo-manager-specific
triggers on top.

The global skill's content is broader (covers web auth, CSRF,
rate limiting) but most of it doesn't apply to a desktop PyQt
app. Cherry-pick the relevant sections (secrets, input
validation, error messages) and combine with the Python-specific
patterns below.

## When to invoke

`/pr-review` invokes this skill when Gate 1 has classified the
diff as behaviour-bearing AND the diff touches Python source
files. Skip entirely for doc-only / translation-only / hooks-only
diffs.

## Patterns to flag

**Only flag ⚠ when you can point at a specific file:line and
name the pattern.** Do NOT flag "this looks insecure" without
concrete evidence.

### SQL injection via f-string / `%` formatting

- `cursor.execute(f"... {var} ...")` — flag.
- `conn.execute("...%s..." % var)` — flag.
- `cursor.execute("... " + var + " ...")` — flag.
- Parameterised queries (`cursor.execute("... ?", (var,))`) are
  fine; never flag those.
- The project uses `sqlite3` directly (see
  `infrastructure/manifest_repository.py`). Any new
  `execute(...)` call in a PR diff is worth a 5-second look.

### Hardcoded secrets

- Regex-style: `API_KEY\s*=\s*["'][A-Za-z0-9_-]{16,}["']`,
  `password\s*=\s*["'][^"']+["']`, `token\s*=\s*["'][A-Za-z0-9._-]{20,}["']`.
- GitHub tokens (`ghp_…`), AWS keys (`AKIA…`), JWT-shaped strings.
- Test fixtures using literally `"test-key"` / `"dummy"` are NOT
  secrets — don't flag those.

### Unsafe deserialisation

- `pickle.load(...)` / `pickle.loads(...)` on data from disk,
  network, or user input — flag.
- `yaml.load(...)` without `Loader=SafeLoader` — flag. Suggest
  `yaml.safe_load`.
- `marshal.loads(...)` from untrusted sources — flag.
- Internal-only pickled caches the project itself wrote (and
  fingerprints with a hash) MAY be acceptable; flag for review
  rather than ✗.

### Shell injection via `subprocess`

- `subprocess.run(..., shell=True)` with an f-string or `%`
  formatted argv — flag.
- `subprocess.Popen(f"... {var} ...", shell=True)` — flag.
- `subprocess.run(["bin", arg1, arg2])` (list form, no shell)
  is fine.
- The scanner pipeline shells out to `exiftool`. Any new
  subprocess call needs list-form argv with no shell, AND a
  timeout if it could hang on bad input.

### `eval` / `exec` / `compile` on diff content

- Any `eval(...)` / `exec(...)` of strings derived from file
  content, user input, or settings — flag ✗ (rare in this
  project; if it appears, it's almost certainly wrong).

### Path traversal

- User-supplied filename joined into a path without
  `pathlib.Path.resolve()` + containment check — flag when
  the resulting path is then opened for write/delete.
- Pure-read with `Image.open(path)` on scanned filesystem
  paths is fine — the project's job IS to read those.

## What NOT to flag

- Internal constants named `*_KEY` that are NOT secrets (e.g.
  `LOCK_KEY = "is_locked"` — column name, not credential).
- f-strings in log messages (`logger.info(f"scanning {path}")`).
- f-strings in `print()` for QA scenarios.
- Subprocess calls with hardcoded argv (no interpolation).
- Refactors that just move existing code without changing its
  shape — if the f-string SQL was already there pre-PR, it's not
  this PR's bug.

## Output format

When you DO flag, emit one line per finding under the
`## App-level security (Gate 7)` section of pr-review's chat
report:

```
⚠ <file:line> — <pattern name>: <one-line evidence quote>
```

Severity escalation: `eval`/`exec` on diff content → ✗.
Everything else → ⚠.

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `security-review` (global) — OWASP backdrop; composed at the
  top of this skill.
- `security-scan` (global, AgentShield) — Gate 6 of pr-review;
  scans `.claude/` config, NOT application code. This skill
  (Gate 7) does the complementary application-code scan.
