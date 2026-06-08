# Scanner-perf PR template additions (from #604→#610 retro)

Five REQUIRED blocks for any scanner-perf PR. These should be added to `.github/PULL_REQUEST_TEMPLATE.md` (or its scanner-perf variant) and gated by `/pr-review`.

---

## Block 1

## Measured on (REQUIRED for scanner-perf PRs)

- **Sources tested:** [must include D:\Takeout-0508, H:\Photos\MobileBackup, J:\圖片 or explicitly justify any omission]
- **Harness:** `scripts/probe_*.py` path
- **Git SHA the probe ran against:** 
- **Args:** `limit=`, `hash_pool=`, `workers=`, `autotune=`
- **Raw output:** `docs/audits/data/<probe>-<sha>.json`
- **Wall-time:** baseline → fix (N× delta)
- **Devices NOT tested and why omission is safe:** 

PRs claiming `physical limit`, `bottleneck`, `ceiling`, `max(X,Y)`, `GIL-bound`, `compute-bound`, `read-bound`, any `MB/s`, any `N×` speedup, or any `~N` round-number perf assertion WITHOUT this block will be rejected by `/pr-review`.

---

## Block 2

## device_key impact check (REQUIRED if diff touches scanner/workers.py, scanner/autotune.py, or any device_key resolver)

- [ ] Ran `/impact-map device_key` and reviewed every consumer
- [ ] Verified `disk_incurs_seek_penalty` still returns correct True/False on the new key format (the 2-char drive-letter guard is load-bearing)
- [ ] Verified `hash_workers_for_root(device_key(real_HDD_path))` returns 1, not 4
- [ ] Added/updated integration test exercising the real (un-mocked) resolver → seek-penalty → worker-count chain

---

## Block 3

## Audit doc disclaimer check (REQUIRED if PR links an audit doc)

If the linked audit contains any of: `not measured`, `no adversarial-review`, `N=1`, `partial-warm`, `single A/B`, `Wait — actually`, `other rigs not tested`, `should have done` — pick ONE:

- [ ] Disclaimer removed because the measurement was run (link to new data)
- [ ] Fix re-scoped to opt-in flag (not default change) so the unmeasured surface area is opt-in
- [ ] `KNOWN UNVALIDATED — merging anyway because [reason]` block added below, and user has given explicit `yes`

A self-written disclaimer is a block-gate. It is not a footnote.

---

## Block 4

## Adversarial-review for default-changing perf fixes (REQUIRED)

If this PR changes a SHIPPING DEFAULT for performance reasons (force-process-pool, default-on autotune, raised floor, changed reader count), `/skill adversarial-review` must run and the converged finding must appear in the PR body. Aspirational `should have` reasoning does not substitute.

---

## Block 5

## Pushback gate (informational)

If the user has pushed back on this PR's premise ≥3 times in chat, the next response from the agent MUST lead with a probe-script invocation, not prose. Prose-counter-pushback is the failure mode this gate exists to catch.

---

