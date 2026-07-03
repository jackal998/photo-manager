# Testing policy — full detail

> Read BEFORE writing or changing ANY test, adding a `[tool.coverage.run] omit`
> entry, or doing anything that shifts what a test layer covers. The hard floor
> (70% per-file / 80% global, metric-gaming ban, project-beats-global
> precedence) is summarized inline in the root `CLAUDE.md`; this file is the
> full strategy. The canonical per-module coverage map lives at
> [`docs/testing.md`](../../docs/testing.md).

## Testing ground rules — non-negotiable

The full testing strategy lives in [`docs/testing.md`](../../docs/testing.md);
the rules below are the hard floor that applies in every session.

**Precedence note:** if a global skill (e.g. `tdd-workflow`,
`python-testing`) recommends a higher coverage percentage or a
different testing posture, this section wins for this project. The
70% per-file floor here is the considered choice — see the rationale
below.

### What a test exists to do

A test catches bugs a real user would hit. If a test exercises code only
to make the coverage number larger, it is **not a test** — it is metric
gaming. Examples of metric gaming you must NOT do:

- Monkeypatching `QStandardItem.setData` to raise so the wrapped
  `except: pass` branches run.
- Forcing `_HASH_AVAILABLE = False` to cover the ImportError fallback
  when PIL is in fact a hard dependency.
- Stubbing an `Image` object without `getexif` to cover the
  defensive `if not exif: return None` guard.
- Any test whose only assertion is "this branch was reached".

If you find yourself writing one of these, stop. Either the branch
catches a real failure mode (in which case test it with a *real* failure
mode — a truncated file, a missing optional dep installed in CI, etc.)
or it is dead defense and the right move is a comment in the source,
not a synthetic test.

### Three layers, three homes

| Layer | What | Where it runs | Catches |
|---|---|---|---|
| 1 — Unit + mocks | `tests/test_*.py` | CI (`pytest`) + local | Refactoring bugs, parser logic, dispatch errors |
| 2 — Integration with real binaries (on-demand — see `docs/testing.md`) | `tests/integration/test_*.py` (`@pytest.mark.integration`, skip-if-missing) | Local only — CI doesn't have `exiftool` / RAW codecs / etc. | Boundary error modes hard to reproduce via the GUI. **No maintained suite** — add a spot-test only when a specific bug surfaces. Layer 3 covers the boundary happy paths. |
| 3 — End-to-end via `/qa-explore` | `qa/scenarios/sNN_*.py` | Local via `python -m qa.scenarios._batch` | Label drift, state-transition bugs, UX regressions |

**Probe layer** ([`tests/test_ui_probes.py`](../../tests/test_ui_probes.py) +
soft-probe blocks in qa scenarios) complements the three layers above
by catching cross-cutting structural invariants that scripted tests
can't: dropdown drift, missing method proxies, label uniqueness,
translation passthroughs, menu-gating holes, bridge-pattern gaps. Two
forms: static probes (AST/YAML inspection in
[`tests/test_ui_probes.py`](../../tests/test_ui_probes.py), run in CI) and
live soft-probes (`print("probe_status: …")` blocks injected into
`qa/scenarios/sNN_*.py` setups). See
[`docs/testing.md`](../../docs/testing.md) (Probes section) for the full
inventory and authoring recipe.

CI covers layer 1 only. Knowing which layer you're skimping on matters
more than the headline coverage number.

### When you write code

Three triggers, three test homes:

1. **Pure logic, no external deps** → unit test. Must clear the per-file
   70% floor.
2. **Touches a boundary** (subprocess, filesystem semantics,
   third-party lib whose behavior varies by version — `exiftool`,
   `rawpy`, `pillow-heif`, `send2trash`) → unit test for our side; let
   qa-explore (layer 3) cover the boundary happy path. **Add a layer-2
   spot-test only if you can name a specific failure mode that's hard
   to trigger through the GUI** (e.g. exiftool returning malformed
   output on a real corner-case file). Default: no extra test.
3. **User-facing flow** (button, dialog, menu, status bar) → extend or
   add a `qa/scenarios/sNN_*.py` driver.

### Coverage policy

- Per-file floor: **70%** on layer 1, enforced by
  `scripts/check_coverage_per_file.py`. The threshold sits at 70 (not
  80) precisely so honest tests can clear it without padding the
  defensive tail.
- Global floor: 80% in `pyproject.toml`. Headroom over 70-per-file is
  intentional.
- The only escape is `[tool.coverage.run] omit` in `pyproject.toml`.
  Each `omit` entry MUST carry a one-line comment naming (a) why it
  cannot run in unit tests and (b) where it IS covered (qa-explore
  scenario, integration test, manual smoke). Adding to omit is a
  deliberate, reviewable change — not a per-file slip.

### When you change a test

- If you remove an assertion, justify it in the commit message.
- If you wrap a flaky test in `@pytest.mark.skip`, explain why and
  link an issue to fix it.
- If you mark a test `@pytest.mark.skipif(...)`, state the condition
  and what gets lost when it skips.
- Never add a `pytest.skip()` inside a test body to make it pass — fix
  the test or delete it.

### When you remove tests

A test that doesn't catch bugs is worse than no test (it costs maintenance
and creates false confidence). If a test is genuine padding, deleting
it is correct — but say so in the commit message and explain what
*real* coverage gap remains afterward.

### Documentation duty

When you change anything that shifts what each layer covers (new module,
new omit entry, new integration test, new qa-explore scenario), update
the per-module table in [`docs/testing.md`](../../docs/testing.md). The doc
is the canonical answer to "what's covered, what's not, what's the
residual risk" — keep it honest.

The canonical feature inventory lives at [`docs/features.md`](../../docs/features.md).
Update it whenever user-visible behaviour changes (button label,
conditional dialog, action scope, new shortcut/menu, post-action
state change, new gating condition) — see the `update-docs` skill's
"User-visible behaviour changed?" row. Enforced at PR-creation time
by [`scripts/hooks/docs_guard.py`](../../scripts/hooks/docs_guard.py).
