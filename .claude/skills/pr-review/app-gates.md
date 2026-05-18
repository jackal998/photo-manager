# pr-review — application-level gates (7–11)

This file holds the detailed rubrics for `/pr-review`'s
application-level pattern matching. The main
[`SKILL.md`](SKILL.md) keeps Gates 1–6 (semantic drift + harness
routing — the core review flow) and points here for the
application-level rubric details.

Read this file when the corresponding gate's trigger fires on a
diff. If a gate's trigger doesn't fire (e.g., no `_MIGRATIONS`
touch → skip Gate 8), don't bother reading its section.

## Contents

- [Gate 7 — app-level security pattern scan](#gate-7--app-level-security-pattern-scan) — SQLi, secrets, deserialisation, shell injection, eval/exec, path traversal
- [Gate 8 — SQLite migration safety](#gate-8--sqlite-migration-safety) — additive only, appended at end, backward-compatible defaults, idempotency, companion edits
- [Gate 9 — performance & threading](#gate-9--performance--threading) — per-row I/O, O(N²), subprocess in loop, QThread without progress/cancel, missing timeout
- [Gate 10 — test-padding detection](#gate-10--test-padding-detection) — monkeypatch-to-cover-defensive, forced feature flags, undocumented skip
- [Gate 11 — PII audit on project skills](#gate-11--pii-audit-on-project-skills) — home paths, IPv4, credential-shaped literals, with the pattern-vs-literal filter rule

---

## Gate 7 — app-level security pattern scan

Scan the diff for concrete dangerous patterns. **Only flag ⚠ when
you can point at a specific file:line and name the pattern.** Do
NOT flag "this looks insecure" without concrete evidence.

Patterns to flag (each ⚠ with file:line):

- **SQL injection via f-string / `%` formatting**
  - `cursor.execute(f"... {var} ...")` — flag.
  - `conn.execute("...%s..." % var)` — flag.
  - `cursor.execute("... " + var + " ...")` — flag.
  - Parameterised queries (`cursor.execute("... ?", (var,))`) are
    fine; never flag those.
  - The project uses `sqlite3` directly (see
    `infrastructure/manifest_repository.py`). Any new
    `execute(...)` call in a PR diff is worth a 5-second look.

- **Hardcoded secrets**
  - Regex-style: `API_KEY\s*=\s*["'][A-Za-z0-9_-]{16,}["']`,
    `password\s*=\s*["'][^"']+["']`, `token\s*=\s*["'][A-Za-z0-9._-]{20,}["']`.
  - GitHub tokens (`ghp_…`), AWS keys (`AKIA…`), JWT-shaped strings.
  - Test fixtures using literally `"test-key"` / `"dummy"` are NOT
    secrets — don't flag those.

- **Unsafe deserialisation**
  - `pickle.load(...)` / `pickle.loads(...)` on data from disk,
    network, or user input — flag.
  - `yaml.load(...)` without `Loader=SafeLoader` — flag. Suggest
    `yaml.safe_load`.
  - `marshal.loads(...)` from untrusted sources — flag.
  - Internal-only pickled caches the project itself wrote (and
    fingerprints with a hash) MAY be acceptable; flag for review
    rather than ✗.

- **Shell injection via `subprocess`**
  - `subprocess.run(..., shell=True)` with an f-string or `%`
    formatted argv — flag.
  - `subprocess.Popen(f"... {var} ...", shell=True)` — flag.
  - `subprocess.run(["bin", arg1, arg2])` (list form, no shell)
    is fine.
  - The scanner pipeline shells out to `exiftool`. Any new
    subprocess call needs list-form argv with no shell, AND a
    timeout if it could hang on bad input.

- **`eval` / `exec` / `compile` on diff content**
  - Any `eval(...)` / `exec(...)` of strings derived from file
    content, user input, or settings — flag ✗ (rare in this
    project; if it appears, it's almost certainly wrong).

- **Path traversal**
  - User-supplied filename joined into a path without
    `pathlib.Path.resolve()` + containment check — flag when
    the resulting path is then opened for write/delete.
  - Pure-read with `Image.open(path)` on scanned filesystem
    paths is fine — the project's job IS to read those.

Anti-patterns (do NOT flag):

- Internal constants named `*_KEY` that are NOT secrets (e.g.
  `LOCK_KEY = "is_locked"` — column name, not credential).
- f-strings in log messages (`logger.info(f"scanning {path}")`).
- f-strings in `print()` for QA scenarios.
- Subprocess calls with hardcoded argv (no interpolation).
- Refactors that just move existing code without changing its
  shape — if the f-string SQL was already there pre-PR, it's not
  this PR's bug.

When you DO flag, emit one line per finding:

```
⚠ <file:line> — <pattern name>: <one-line evidence quote>
```

Severity escalation: `eval`/`exec` on diff content → ✗.
Everything else → ⚠.

---

## Gate 8 — SQLite migration safety

Fire only when the diff touches the `_MIGRATIONS` list in
`infrastructure/manifest_repository.py` OR the schema SQL block in
`scanner/manifest.py` (`CREATE TABLE migration_manifest`).

For each new entry, check:

1. **Additive only.** `ADD COLUMN` semantics (the project uses
   `ALTER TABLE … ADD COLUMN`). Never `DROP COLUMN`,
   `RENAME COLUMN`, or `ALTER COLUMN TYPE` — SQLite can't do
   those safely without a table rebuild, and old manifests would
   break. If you see one, ✗ flag.

2. **Appended at end, not inserted into the middle.** The order
   of `_MIGRATIONS` IS the migration order — each new row must
   land after every existing row. Inserting into the middle
   re-orders migration application and could fail mid-list on an
   already-migrated DB. ✗ flag if mid-list insertion.

3. **Backward-compatible defaults.**
   - `INTEGER` / `REAL` / `TEXT` columns: nullable (no default)
     OR `DEFAULT 0` / `DEFAULT 0.0` / `DEFAULT ''` — safe.
   - `NOT NULL` without a default on existing data → ⚠ (older
     manifests would fail the migration).
   - The existing convention is `INTEGER NOT NULL DEFAULT 0`
     for flags and `REAL` (nullable) for scores. Match it.

4. **Idempotency.** The repo's `_apply_migrations` does `ALTER
   TABLE` and swallows the "duplicate column" error — safe to
   re-run. Don't break that invariant (e.g., by replacing with
   `CREATE TABLE`-style schema).

5. **Companion edits.** A new migration row MUST also appear:
   - In the `CREATE TABLE migration_manifest` schema in
     `scanner/manifest.py` (so new manifests have the column
     from the start, not via migration).
   - As a field on `ManifestRow` (or equivalent dataclass) in
     `scanner/dedup.py` if read by the scanner.
   - If absent in either, ⚠ flag the mismatch.

6. **README schema table.** The README has a manifest schema
   table. If the migration adds a user-facing column (visible
   in the UI), `update-docs` should have flagged a README touch.
   If README wasn't touched, ⚠ suggest updating the schema table.

Output format:

```
⚠ <_MIGRATIONS line> — <issue>: <evidence>
```

---

## Gate 9 — performance & threading

Fire when the diff touches `scanner/**.py`, `app/views/workers/**.py`,
or adds a `QThread` / `QRunnable` / `ThreadPoolExecutor`. Look for
the specific bug patterns that have hit this codebase before
(see the `photo-scanner-patterns` skill).

Patterns to flag:

- **Per-row I/O inside a loop over files.**
  - `for row in rows: data = Path(row.path).read_bytes()` —
    flag if the loop runs over thousands of files. The scanner
    explicitly does single-read SHA-256 + pHash + EXIF from one
    `read_bytes()` — don't add a second `read_bytes()` per row
    in a different stage.
  - `for row in rows: Image.open(row.path)` inside a hot loop
    without batching → ⚠.

- **Nested loop over filesystem paths.**
  - `for a in all_paths: for b in all_paths: ...` — flag as O(N²)
    even if N looks small (NAS scans hit 100k+ files).
  - Pairwise near-duplicate scanners must use the outer-vs-inner
    placement from `photo-scanner-patterns`; flag if the new code
    appears to do pHash compare in the inner loop.

- **Subprocess in a loop without `-stay_open` batching.**
  - `for path in paths: subprocess.run(["exiftool", path], ...)` —
    flag. The project batches via `-stay_open` for thousands of
    files; per-file spawn is the documented 10–100× slowdown.

- **Blocking call inside a `QThread.run()` without progress / cancel.**
  - New `class FooWorker(QThread)` whose `run()` has no `emit()`
    progress and no `if self._cancel: return` check → ⚠ for
    "user can't tell what's happening / can't abort".
  - The pattern documented in
    [`photo-scanner-patterns`](../personal/photo-scanner-patterns/SKILL.md)
    (if present, else in skills index) — match it.

- **`subprocess.run` without a timeout in user-facing code.**
  - If a subprocess can hang on a malformed file (e.g.,
    `exiftool` on a corrupted HEIC), and the call is in a
    user-facing thread (not the dedicated scanner pipeline that
    has its own timeout layer), → ⚠ "add a `timeout=` kwarg".

Anti-patterns (do NOT flag):

- A single `read_bytes()` per row in the scanner pipeline — that's
  the documented design.
- O(N²) over tiny lists (group decisions in a single dedup group,
  typically <100 elements) — that's bounded; don't be pedantic.
- A QThread without progress when the workload is sub-second
  (e.g., loading a small config file).
- `subprocess.run` of internal scripts the project itself ships
  with a known runtime.

Cross-reference: this gate complements but does not duplicate
the `photo-scanner-patterns` skill — that skill is invoked
during *writing* code; Gate 9 catches the same issues during
*reviewing* the resulting diff.

---

## Gate 10 — test-padding detection

This project explicitly forbids mock-driven coverage padding
(see [`CLAUDE.md`](../../../CLAUDE.md) "Testing ground rules").
Gate 10 surfaces the specific anti-patterns called out there.

Fire only when the diff adds or modifies files matching
`tests/test_*.py` or `tests/integration/test_*.py`.

Flag ⚠ when ANY of these appear in a new/modified test:

- **Monkeypatching a stdlib / Qt method to raise just to cover an
  except-pass branch.**
  - `monkeypatch.setattr(QStandardItem, "setData", lambda *a: 1/0)`
    — flag. The CLAUDE.md anti-pattern list names this exact one.
  - `monkeypatch.setattr(Image, "getexif", lambda self: None)` —
    flag if used only to cover the `if not exif: return None`
    guard. The right test is a real fixture file with no EXIF.
  - `monkeypatch.setattr(Path, "read_bytes", lambda *a: (_ for _ in ()).throw(OSError))`
    when no real OSError condition is reproduced — flag.

- **Forcing a feature-flag constant to False to cover a fallback
  branch the project doesn't actually have.**
  - `pm.scanner.hasher._HASH_AVAILABLE = False` — flag if PIL is
    a hard dep. The fallback branch is dead defense; document it
    in source, don't synthetic-cover it.

- **`@pytest.mark.skip` with no comment.**
  - `@pytest.mark.skip` at function scope → ⚠ "skipped without
    justification". A comment naming the reason + linking an
    issue is OK; raw skip is not.

- **`pytest.skip(...)` inside a test body** without a clearly
  external condition (e.g., "skipping on Windows because
  `pillow-heif` isn't installed there"). If the body just has
  `pytest.skip("not implemented yet")`, flag — the right move is
  to delete the test or actually implement it.

- **Stub object that lacks the attribute the SUT calls, only to
  exercise the `AttributeError`-caught branch.**
  - `class FakeImage: pass; assert load_exif(FakeImage()) is None`
    when `load_exif` only catches `AttributeError` from missing
    `getexif` → flag. A real "image without EXIF" fixture
    exercises the same branch honestly.

- **A test whose only assertion is "this branch was reached"**,
  e.g., `assert called_path is True` after forcing the branch
  with a mock — flag. Real tests assert observable behaviour, not
  internal control-flow.

Anti-patterns (do NOT flag):

- Legitimate mocks of EXTERNAL services (network, GitHub API,
  hardware) where the boundary is genuinely uncontrollable in
  CI — those are fine.
- Mocks of pure-functional helpers to isolate the unit under
  test — fine. The flag is specifically about mocking to reach
  defensive code that doesn't have a real failure mode.
- Tests that use real fixtures (corrupted-image files, manifests
  with missing columns) to trigger error branches — those are
  the correct shape.
- Tests with detailed `# why this monkeypatch is realistic`
  comments naming a real-world condition the patch simulates
  (slow NAS, dropped network, OOM). The comment IS the
  justification.

Severity: all ⚠. Gate 10 doesn't ✗-block; it flags for reviewer
attention because the line between "real test of error branch"
and "padding" requires human judgement.

---

## Gate 11 — PII audit on project skills

Fire only when the diff adds or modifies files under
`.claude/skills/<name>/` (NOT `.claude/skills/personal/<name>/` —
that path is gitignored by design). Same scope as the
"PII audit before committing a project skill" rule in
[`CLAUDE.md`](../../../CLAUDE.md) — Gate 11 enforces what CLAUDE.md
asks the author to do manually.

For each added/modified line in those files, look for:

- **Absolute home paths.** `C:\Users\<name>\…`, `/Users/<name>/…`,
  `/home/<name>/…`. A real path leaks the author's username.
  Placeholders (`<USER>`, `~/`, `$HOME`, `%USERPROFILE%`) are
  fine.
- **IPv4 addresses.** `\d+\.\d+\.\d+\.\d+`. Most often NAS,
  Synology, or VPN endpoints. Filter out:
  - Software version numbers (`1.0.0.0`, `pyqt6 6.6.1`)
  - RFC 5737 documentation IPs (`192.0.2.0/24`, `198.51.100.0/24`,
    `203.0.113.0/24`)
  - RFC 1918 ranges *in commented examples* (`192.168.0.0/24` in
    "block this subnet" context — fine; bare `192.168.1.42` as a
    config target — flag)
- **Credential-shaped literals.** Tokens with provider-specific
  prefixes — `ghp_…` (36+ chars), `AKIA[0-9A-Z]{16}`, JWT (three
  dot-separated base64 segments ≥10 chars each), generic ≥32-char
  high-entropy strings next to `key=` / `token=` / `password=` /
  `secret=`.

**Critical filtering rule — pattern descriptions are NOT literal values.**
A skill that documents its own scan (like Gate 7's pattern list,
or a regex example in a README) will contain text like
`password\s*=\s*["'][^"']+["']` — that's the REGEX, not a
hardcoded password. DO NOT flag pattern descriptions.

Specifically, do NOT flag:

- Pattern strings inside backticks / code blocks that
  describe what to LOOK for (regex literals, glob patterns,
  CLI invocations).
- Variable names containing the trigger word: `LOCK_KEY`,
  `auth_token_param`, `password_field`, `api_key_setting`.
- Test-fixture placeholders: `"test-key"`, `"dummy-token"`,
  `"changeme"`, `"…"`, `"<your-token-here>"`.
- Comments referencing the concept: `# Don't commit secrets`,
  `// API key goes in env var`.
- Strings whose value is obviously a category label, not a
  credential: `"key=value"` (a format-string description),
  `"password"` (a UI label).

When in doubt, surface the match in chat and ASK the user:
"line N matches pattern X — placeholder or real?" rather than
silently flagging or silently dismissing. This is one of the
rare gates where false-positive-aversion AND false-negative-
aversion BOTH matter (an unflagged real token is catastrophic;
a noisy false flag erodes trust).

Severity escalation:

- ✗ for a confirmed-real GitHub / AWS / Slack token. Recommend
  rotate-then-force-push-to-scrub. Don't ship.
- ⚠ for likely-real home path / IP / generic credential shape.
- ℹ️ for "matches pattern but probably FP — confirm please".

If the diff doesn't add or modify any
`.claude/skills/<name>/` file (and `.claude/skills/personal/`
is correctly gitignored — Gate 11 doesn't reach into personal
skills), skip this gate entirely.
