---
name: test-padding-patterns
description: Detect coverage-padding anti-patterns in photo-manager Python tests. Use when /pr-review's diff adds or modifies tests/test_*.py or tests/integration/test_*.py — this skill flags monkeypatching stdlib/Qt methods to cover except-pass branches, forcing feature-flag constants to False, undocumented @pytest.mark.skip, pytest.skip() in test bodies, stub-AttributeError tricks, branch-reached-only assertions, and generic regression-test names. Composes with global python-testing for positive fixture patterns.
origin: local
---

# test-padding-patterns — Gate 10 rubric

Invoked by `/pr-review` Gate 10 when the diff adds or modifies
test files. This project explicitly forbids mock-driven coverage
padding — see CLAUDE.md "Testing ground rules". This skill
surfaces the specific anti-patterns called out there.

## Composes with global lens

**Before applying the patterns below, also read the global
`python-testing` skill** (Skill tool) to load positive fixture
patterns — pytest fixture scoping, parametrize over duplication,
when to use real fixtures vs mocks. That global skill provides
the *positive* shape; this skill provides the *negative* shape
(what NOT to do) calibrated to photo-manager's specific failures.

A test that violates a pattern below AND fails to match a positive
pattern from the global skill is a stronger signal than either
alone.

## When to invoke

`/pr-review` invokes this skill when the diff adds or modifies
files matching:

- `tests/test_*.py`
- `tests/integration/test_*.py`

Skip otherwise.

## Anti-patterns to flag (each ⚠ unless noted)

### Monkeypatching a stdlib / Qt method to raise just to cover an except-pass branch

- `monkeypatch.setattr(QStandardItem, "setData", lambda *a: 1/0)`
  — flag. CLAUDE.md's anti-pattern list names this exact one.
- `monkeypatch.setattr(Image, "getexif", lambda self: None)` —
  flag if used only to cover the `if not exif: return None`
  guard. The right test is a real fixture file with no EXIF.
- `monkeypatch.setattr(Path, "read_bytes", lambda *a: (_ for _ in ()).throw(OSError))`
  when no real OSError condition is reproduced — flag.

### Forcing a feature-flag constant to False to cover a fallback branch the project doesn't actually have

- `pm.scanner.hasher._HASH_AVAILABLE = False` — flag if PIL is
  a hard dep. The fallback branch is dead defense; document it
  in source, don't synthetic-cover it.

### `@pytest.mark.skip` with no comment

- `@pytest.mark.skip` at function scope → ⚠ "skipped without
  justification". A comment naming the reason + linking an
  issue is OK; raw skip is not.

### `pytest.skip(...)` inside a test body

Without a clearly external condition (e.g., "skipping on Windows
because `pillow-heif` isn't installed there"). If the body just
has `pytest.skip("not implemented yet")`, flag — the right move
is to delete the test or actually implement it.

### Stub object that lacks the attribute the SUT calls

Only to exercise the `AttributeError`-caught branch.

- `class FakeImage: pass; assert load_exif(FakeImage()) is None`
  when `load_exif` only catches `AttributeError` from missing
  `getexif` → flag. A real "image without EXIF" fixture
  exercises the same branch honestly.

### A test whose only assertion is "this branch was reached"

E.g., `assert called_path is True` after forcing the branch
with a mock — flag. Real tests assert observable behaviour, not
internal control-flow.

### A test added "to fix a bug" but named generically

(e.g., `test_save_dialog`, `test_scoring`). When the PR
description / linked issue says "fix bug X", the regression test
should name the bug it prevents — `test_pr_NNN_<symptom>` or
`test_issue_NNN_<symptom>`. Generic names regress silently when
someone refactors and accidentally reverts the fix; named tests
fail with a stack trace that points back to the original bug.

**Severity: `note:` (not ⚠) — naming is a hygiene preference,
not a defect.**

## What NOT to flag

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

## Severity

All ⚠ except the generic-name case (note:). Gate 10 doesn't
✗-block; it flags for reviewer attention because the line
between "real test of error branch" and "padding" requires
human judgement.

## Output format

Emit findings under the `## Test quality (Gate 10)` section of
pr-review's chat report:

```
⚠ <file:line> — <anti-pattern>: <evidence>
note: <file:line> — generic regression-test name: <evidence>
```

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `python-testing` (global) — positive fixture patterns;
  composed at the top of this skill.
- `CLAUDE.md` "Testing ground rules" — the authoritative
  source for what padding looks like in this project.
- `docs/testing.md` — three-layer model.
