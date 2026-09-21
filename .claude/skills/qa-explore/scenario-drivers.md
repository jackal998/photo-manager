# qa-explore — scenario driver authoring

This file holds the QA scenario authoring conventions —
what a `qa/web/scenarios/sNN_*.py` driver looks like, how to
structure it, how to name slots. The main [`SKILL.md`](SKILL.md)
points here when Claude is extending or adding a scenario.

---

## Scenario drivers

Each scenario has a pre-built driver under `qa/web/scenarios/`. Drivers
are version-controlled, deterministic, and print structured `step:` /
`key=value` lines to stdout. Run a single driver with
`.venv/Scripts/python.exe -m qa.web._batch <name> --base-url
http://127.0.0.1:8765` while the server is running.

For the canonical, always-current list of scenarios see
[`qa/scenario_ids.py`](../../../qa/scenario_ids.py) (`ALL_SCENARIOS`),
[`qa/web/scenario_map.yml`](../../../qa/web/scenario_map.yml), or
`Glob("qa/web/scenarios/s*.py")`. Those registries are the source of
truth — this doc no longer enumerates scenarios inline, because the
table drifted twice and the maintenance cost outweighed the value
(#323). Slot numbers go `sNN_<short_slug>.py`; slots are append-only
(gaps from retired scenarios stay gaps so re-numbering doesn't churn
git history or break external issue references). Each driver's module
docstring describes what it covers, what it deliberately omits, and how
it diverges from the desktop scenario it was ported from.

Several drivers also call cross-scenario invariant probes from
`qa/web/_invariants.py` — they assert that the status bar matches
an expected shape after a manifest-changing action, that all
manifest-gated menu items toggle as one set, and that destructive
confirmation dialogs carry a count. Those probes print
`inv: <name> ok=<bool> ...` lines to stdout. Failures escalate to the
driver's existing FAIL path.

## Registering a new driver

Three places, or the scenario never runs:

1. `qa/scenario_ids.py` — add the id to `ALL_SCENARIOS`, with a comment
   saying what it covers.
2. `qa/web/scenario_map.yml` — add the row: `scenario`,
   `playwright_module`, `status: done`, and a `notes` field recording
   the assertions and the honest omissions.
3. `qa/web/testid_constants.py` — add any new testid, mirroring
   `frontend/src/testids.ts`.

`tests/test_all_scenarios_registered.py` fails loudly on any of the four
ways (1) and (2) can drift apart from each other or from the files on
disk; `scripts/check_testid_parity.py` covers (3).

Keep drivers short — they should encode the canonical happy path plus
the load-bearing assertions, nothing more. Open-ended exploration is the
LLM's job, on top of the driver's output.

## Conventions that exist because they were paid for

**Wait for the API response, not for the render.** A write helper that
clicks and returns immediately races the store update — that family of
flake (#850) is why the shared helpers wait on the response before
asserting. New helpers follow the same shape.

**Assert the committed value, not the rendered cell,** when the point
of the scenario is persistence. `GET /api/manifest` is the
authoritative serialisation; a rendered cell can be optimistic.

**Duplicate-to-observe.** The scanner drops singletons, so a fixture
that must appear in the manifest needs a byte-identical twin copied
into a tmpdir at runtime (the s05 pattern). Don't fight the scanner —
give it a pair.

**Own your temp state.** Each driver creates its own manifest under a
tmpdir and cleans up. Nothing writes to the repo fixture tree; the
scanner only reads it.

## Batch runner

```
.venv/Scripts/python.exe -m qa.web._batch                 # full sweep
.venv/Scripts/python.exe -m qa.web._batch s04_corrupted s09_walker_exclusions
```

For each id it: looks the id up in `scenario_map.yml` → imports the
named module → calls `run(page, base_url)` → records PASS / FAIL /
ERROR / SKIP. A `status: todo` or `status: skip` row is SKIP, never
FAIL, so a partially-ported set keeps the job green. Prints a final
SUMMARY table with the result per scenario.

The server is started once for the whole batch and reused — get the
user's "yes batch" before starting it, and kill it by PID at the end.
