# Scan NAS-starvation by the global ByteBudget — decision record (2026-06-06)

Issue #596 · fix PR for #596 · sibling cancel-deadlock #594/PR#595.

## Trigger
User: a real multi-device scan (D: Takeout HDD + H:/J: NAS `\\LINXIAOYUN`,
32 GB RAM) showed "忽高忽低" throughput that "doesn't look like the hardware is
maxed out." We live-monitored a faithful headless run of the real `ScanWorker`
(CPU / RAM / D: disk / network at 3 s; harness in `.claude/tmp-monitor/`,
deleted after) and instrumented `byte_budget._inflight` directly.

## Method
`/adversarial-review` (two independent peers, Opus + Sonnet; LEAD judge) on the
first diagnosis → **NOT converged**: it caught that the first read of the data was
wrong (I claimed "8 NAS threads idle" and blamed byte-budget *coupling* without
measuring `_inflight`; whole-OS RAM cannot see a 2 GB in-flight swing). Settled by
two cheap controls:

1. **NAS-only run** (drop D:): if stalls vanish → client-side, not NAS-server.
2. **Direct `_inflight` logging** (harness `ByteBudget.__init__` monkeypatch — no
   product change): is the budget actually the gate?

## Findings (all measured)
- HASH is **read-supply-limited**, NOT CPU- or RAM-bound (CPU median 14 %, >80 %
  only 3.9 %; RAM steady 19–20 GB / 32; the #587/#590 OOM fix holds).
- The NAS ran at **knee=1** (cached `read_knee_cache["\\LINXIAOYUN"]={knee:1}`),
  i.e. one active read at a time — yet alone it is healthy: **137 MB/s mean, zero
  stalls** (NAS-only control).
- With D: present the NAS is **idle ~46 % of HASH** (stalls up to 137 s). The
  decisive measurement:

  | metric | NAS-only control | 3-source (real) |
  |---|---|---|
  | `_inflight` fill mean | 0.2 % | **86.5 %** |
  | `_inflight` during NAS-idle | — | **100 % in 53/53 idle samples** |
  | NAS net mean | 137 MB/s | 47 MB/s |
  | longest stall | 0 s | 137 s |

- **Gate = the single GLOBAL 2 GiB `ByteBudget`.** D:'s clustered 100–130 MB iPhone
  ProRAW DNGs fill the one shared ceiling; the NAS reader then blocks at
  `byte_budget.acquire` despite its own small files and an idle link. `#587` added
  the global budget to stop the OOM — correct, but one ceiling shared across
  devices lets the slow big-file device starve the fast one.

Refuted alternatives: NAS-server-side pause (removing D: removed the stalls);
compute-pool drain (NAS-only `_inflight` 2.5 % ⇒ compute kept up); the first
"byte-budget coupling / 8 idle threads" framing (wrong: knee=1, and unmeasured).

## Decision — per-device byte budget
Split the total ceiling into **one `ByteBudget` per device**
(`scanner/byte_budget.py:per_device_budgets`, equal split). Each reader acquires
from its device's budget; the device is threaded through `hash_in_q` so the compute
done-callback releases from the same one. Properties:

- **OOM bound preserved:** sum of per-device budgets ≤ total (`n*(total//n) ≤
  total`) — the global peak in-flight is unchanged.
- **Single-device unchanged:** one device keeps the whole budget.
- **No deadlock:** each per-device budget keeps admit-one-over-budget.
- **Determinism unchanged:** `idx` still threads through; `classify` sees walk
  order.

Rejected: raising the global cap (blunt; re-opens #587 OOM on bigger libraries).
Orthogonal/out of scope: a dynamic working-set budget; the low cached NAS knee=1
(already does 137 MB/s, so a minor follow-up, not the lever here).

## Verification
Unit test `tests/test_byte_budget.py::TestPerDeviceBudgets` — equal split preserves
the OOM bound, single device keeps full, **a full device budget does NOT block
another device** (the isolation that is the fix). Full suite green; the existing
byte-budget pipeline-bound + cancel + per-device-pool tests still pass.
