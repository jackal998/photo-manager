# Multi-Agent Skill Audit — 2026-05-23

## Scope

- **Article (digest):** <https://blog.aihao.tw/2026/05/19/multi-agent-anti-patterns-and-patterns/>
- **Skills audited:**
  - [.claude/skills/work/SKILL.md](../../.claude/skills/work/SKILL.md)
  - [.claude/skills/parallel-brief-generator/SKILL.md](../../.claude/skills/parallel-brief-generator/SKILL.md)
- **Companion agents audited:**
  - [.claude/agents/researcher-agent.md](../../.claude/agents/researcher-agent.md)
  - [.claude/agents/developer-agent.md](../../.claude/agents/developer-agent.md)
  - [.claude/agents/qa-agent.md](../../.claude/agents/qa-agent.md)
- **Base SHA pinned:** `d1b15e08f17596ae5bb9ab3444256a1652dd0461`
- **Primary sources reviewed (8):**
  - Stanford arxiv 2604.02460 — *Single-Agent LLMs Outperform Multi-Agent Systems…*
  - MAST arxiv 2503.13657 — *Why Do Multi-Agent LLM Systems Fail?*
  - Anthropic *Building a Multi-Agent Research System* (engineering blog, 2026/1)
  - Anthropic *Multi-Agent Coordination Patterns* framework (2026/4, via secondary coverage)
  - Cognition *Don't Build Multi-Agents* (2025)
  - Google Research *Towards a Science of Scaling Agent Systems* (arxiv 2512.08296, 2026/2)
  - LangChain *Multi-agent* docs + LangGraph Swarm (2026)
  - Sutton's *Bitter Lesson* (referenced via Anthropic's dynamic-spawn framing)
- **Claims breakdown:** verified **9** / partial **3** / unverified **3** / contradicted **0** (of 15)

### Unretrievable sources (excluded from load-bearing recommendations)

- Twitter/X threads cited as primary for claims 4, 5, 6 (role-playing PM/QE, LLM-vs-human-silos, fixed-workflow-collapse) — labelled **unverified**.
- antigravitylab.net 12-pitfalls article — `ECONNREFUSED` on fetch (claim 10) — **partial** via Google study and MAST overlap, but specific list not primary-sourced.

## Verified best-practice inventory

| # | Practice | Source (claim # + URL) | Where /work addresses | Where /parallel-brief addresses | Gap | Severity |
|---|----------|------------------------|----------------------|--------------------------------|-----|----------|
| V1 | Task-architecture matching matters; one architecture does not dominate. | Claim 1, Google Research [arxiv 2512.08296](https://arxiv.org/abs/2512.08296) | Phase 2 complexity-to-workflow table [.claude/skills/work/SKILL.md:73-80](../../.claude/skills/work/SKILL.md#L73-L80) | N/A (orchestrator, not executor) | fully addressed | — |
| V2 | Single-agent often matches/outperforms multi-agent at equal token budget. | Claim 2, Stanford [arxiv 2604.02460](https://arxiv.org/abs/2604.02460) | "simple" defaults to LEAD direct, no subagents [.claude/skills/work/SKILL.md:76, 114-122](../../.claude/skills/work/SKILL.md#L114-L122) | N/A | fully addressed | — |
| V3 | Multi-agent burns 4-15× tokens vs single-agent baselines. | Claim 3, Anthropic [engineering blog](https://www.anthropic.com/engineering/multi-agent-research-system) | Phase 3 prints `Cost estimate: ~<N>× single-session` [.claude/skills/work/SKILL.md:101](../../.claude/skills/work/SKILL.md#L101); heuristic context-budget rule [.claude/skills/work/SKILL.md:325-329](../../.claude/skills/work/SKILL.md#L325-L329) | N/A | partial — no enforced cap, only an advisory print | medium |
| V4 | Context isolation is the load-bearing justification for spawning subagents. | Claim 11, Anthropic engineering blog | `isolation="worktree"` for developer-agent [.claude/skills/work/SKILL.md:125-141](../../.claude/skills/work/SKILL.md#L125-L141); fresh worktree each dev iteration [.claude/skills/work/SKILL.md:163-166](../../.claude/skills/work/SKILL.md#L163-L166) | Per-session worktree fan-out [.claude/skills/parallel-brief-generator/SKILL.md:7-15, 91-107](../../.claude/skills/parallel-brief-generator/SKILL.md#L91-L107) | fully addressed | — |
| V5 | Multi-agent benefits parallel **read/exploration**; **write/code** tasks suffer from conflicting decisions. | Claim 12, Anthropic + [Cognition](https://cognition.ai/blog/dont-build-multi-agents) | researcher-agent is read-only [.claude/agents/researcher-agent.md:8-13, 97-106](../../.claude/agents/researcher-agent.md#L97-L106); only ONE developer-agent at a time [.claude/skills/work/SKILL.md:124-141](../../.claude/skills/work/SKILL.md#L124-L141) | Multi-issue fan-out gated on file-disjoint check [.claude/skills/work/SKILL.md:37-44](../../.claude/skills/work/SKILL.md#L37-L44); explicit collision matrix [.claude/skills/parallel-brief-generator/SKILL.md:160-176](../../.claude/skills/parallel-brief-generator/SKILL.md#L160-L176) | fully addressed | — |
| V6 | Generator-Verifier pattern: verifier finds faults, does **not** re-execute. | Claim 13, Anthropic Multi-Agent Coordination Patterns (2026/4) — confirmed via secondary coverage | qa-agent is read-only except for the test runner (Bash) [.claude/agents/qa-agent.md:1-4, 115-127](../../.claude/agents/qa-agent.md#L115-L127); explicit "NEVER write, edit, or delete source files or tests" | N/A | fully addressed (in spirit) — but the pattern is not **named** in qa-agent.md or SKILL.md | low |
| V7 | Centralised coordination contains error amplification (4.4× vs 17.2× independent). | Claims 7-8, Google Research [arxiv 2512.08296](https://arxiv.org/abs/2512.08296) | LEAD is the central orchestrator; teammates never write to remotes (CLAUDE.md "Only LEAD writes to remotes"); merge gated [.claude/skills/work/SKILL.md:171-178](../../.claude/skills/work/SKILL.md#L171-L178) | N/A | fully addressed | — |
| V8 | Loop guards prevent runaway retries. | Claim 10 (Antigravity, partial) + MAST category (iii) task-verification failures [arxiv 2503.13657](https://arxiv.org/abs/2503.13657) | Hard cap 4 dev↔QA cycles [.claude/skills/work/SKILL.md:168-170, 331-333](../../.claude/skills/work/SKILL.md#L168-L170); one Phase-5 auto-iteration [.claude/skills/work/SKILL.md:293-302](../../.claude/skills/work/SKILL.md#L293-L302); developer-agent max-2 self-correct rounds [.claude/agents/developer-agent.md:68-76](../../.claude/agents/developer-agent.md#L68-L76) | N/A | fully addressed | — |
| V9 | Workflow flexibility: a pre-specified pipeline is brittle when complexity surprises you. | Claim 6 (unverified, Cognition-adjacent) | Plan presented to human at Phase 3 with "adjust" affordance [.claude/skills/work/SKILL.md:103](../../.claude/skills/work/SKILL.md#L103); partial-completion rule [.claude/skills/work/SKILL.md:335-337](../../.claude/skills/work/SKILL.md#L335-L337) | N/A | partial — Phase 2 mapping itself is static, but a human can override per-task | low |
| V10 | Dynamic subagent spawning (let the model decide) — Bitter-Lesson alignment. | Claim 15, Sutton's Bitter Lesson + Anthropic's lead-agent example | researcher-agent's runtime complexity scoring chooses workflow [.claude/agents/researcher-agent.md:52-60](../../.claude/agents/researcher-agent.md#L52-L60), but the *mapping* is hand-coded | N/A | partial — runtime scoring exists; downstream routing is a fixed table | low |
| V11 | Observability: structured trace of multi-agent runs (token, tool count, iteration). | Claim 10 (partial); MAST category (i) system-design issues | Heuristic "tool calls > 40 → suggest /compact" [.claude/skills/work/SKILL.md:325-329](../../.claude/skills/work/SKILL.md#L325-L329) — no per-subagent accounting | N/A | partial — proxy metric only, no per-iteration log | medium |
| V12 | Pre-flight discipline before fan-out (base SHA, collision matrix, slot assignment). | Operational best practice consistent with Cognition's context-engineering, no single source | N/A | Step 2 cross-bundle pre-flight [.claude/skills/parallel-brief-generator/SKILL.md:160-198](../../.claude/skills/parallel-brief-generator/SKILL.md#L160-L198); `.worktreeinclude` precondition check [.claude/skills/parallel-brief-generator/SKILL.md:91-107](../../.claude/skills/parallel-brief-generator/SKILL.md#L91-L107) | fully addressed | — |

## Anti-pattern inventory

| # | Anti-pattern | Source | Where /work avoids it | Where /parallel-brief avoids it | Gap | Severity |
|---|--------------|--------|----------------------|--------------------------------|-----|----------|
| A1 | Scaling agents blindly = "more is better". | Google [arxiv 2512.08296](https://arxiv.org/abs/2512.08296) — degrades up to 70% on sequential tasks | Complexity-gated; "simple" never adds subagents [.claude/skills/work/SKILL.md:76, 114-122](../../.claude/skills/work/SKILL.md#L114-L122) | Fan-out only on file-disjoint multi-issue [.claude/skills/work/SKILL.md:37-44](../../.claude/skills/work/SKILL.md#L37-L44) | fully addressed | — |
| A2 | Role-playing org structure (PM / QA Engineer / etc.). | Claim 4 (Twitter, unverified) — adjacent to Cognition's "conflicting decisions" | Subagents are *function-typed* (researcher / developer / qa), not human-role-typed; each definition states its job in mechanical terms | N/A | fully addressed | — |
| A3 | Pre-specified rigid pipelines that can't adapt mid-task. | Claim 6 (unverified) — adjacent to Cognition | Phase 3 human approval + "go but skip QA" override [.claude/skills/work/SKILL.md:103-108](../../.claude/skills/work/SKILL.md#L103-L108); partial-completion rule [.claude/skills/work/SKILL.md:335-337](../../.claude/skills/work/SKILL.md#L335-L337) | Briefs are time-independent + merge-order-flexible [.claude/skills/parallel-brief-generator/SKILL.md:253-264](../../.claude/skills/parallel-brief-generator/SKILL.md#L253-L264) | mostly addressed; Phase 2's complexity table is itself fixed | low |
| A4 | Independent agents without central error containment (→ 17.2× error amplification). | Google [arxiv 2512.08296](https://arxiv.org/abs/2512.08296) | LEAD-central, only LEAD writes to remotes (CLAUDE.md team-mode rules); merge is human-gated | N/A | fully addressed | — |
| A5 | Runaway retry loops. | Antigravity 12-pitfalls (partial) + MAST | 4-cycle dev↔QA cap [.claude/skills/work/SKILL.md:168-170](../../.claude/skills/work/SKILL.md#L168-L170); 1-iter Phase-5 cap [.claude/skills/work/SKILL.md:293-302](../../.claude/skills/work/SKILL.md#L293-L302); developer-agent 2-round self-correct [.claude/agents/developer-agent.md:68-76](../../.claude/agents/developer-agent.md#L68-L76) | N/A | fully addressed | — |
| A6 | Verifier that also re-executes (mixing generator/verifier roles). | Anthropic Apr 2026 coordination patterns | qa-agent.md hard constraints "NEVER write, edit, or delete source files or tests" [.claude/agents/qa-agent.md:115-118](../../.claude/agents/qa-agent.md#L115-L118) | N/A | fully addressed; not **named** as Generator-Verifier | low |
| A7 | Shared-context verifier (information degradation between generator and verifier). | Claim 14, attributed to Cognition Apr 2026 — **unverified** in primary fetch | qa-agent receives the full RESEARCH BRIEF + IMPLEMENTATION REPORT [.claude/agents/qa-agent.md:18-28](../../.claude/agents/qa-agent.md#L18-L28); this is **opposite** of claim 14 | N/A | conflicts with claim 14, but the claim is unverified — and Anthropic's framing of "context isolation enables compression" supports *sharing* a compressed brief, not raw traces | low (don't act on unverified claim) |
| A8 | Token-overflow / no budget cap. | Antigravity (partial) + Stanford info-efficiency framing | Advisory `Cost estimate` print only [.claude/skills/work/SKILL.md:101](../../.claude/skills/work/SKILL.md#L101); per-agent token budgets exist in agent files ([researcher 40k](../../.claude/agents/researcher-agent.md#L109-L114), [developer 60k](../../.claude/agents/developer-agent.md#L128-L134), [qa 30k](../../.claude/agents/qa-agent.md#L128-L134)) but not enforced at LEAD level | N/A | partial — per-agent advisory exists, no cumulative cap | medium |
| A9 | Context pollution between iterations. | Antigravity (partial) | Each dev iteration spawns a *new* worktree [.claude/skills/work/SKILL.md:163-166](../../.claude/skills/work/SKILL.md#L163-L166) ("Each iteration gets a clean worktree — don't re-use the previous one") | Fan-out sessions each get fresh worktree [.claude/skills/parallel-brief-generator/SKILL.md:7-15](../../.claude/skills/parallel-brief-generator/SKILL.md#L7-L15) | fully addressed | — |
| A10 | Observability gaps (can't trace which subagent burned which tokens / made which decision). | Antigravity (partial) + MAST category (i) | Heuristic only [.claude/skills/work/SKILL.md:325-329](../../.claude/skills/work/SKILL.md#L325-L329) | N/A | partial — no per-iteration audit trail | medium |

## Evolve proposal (minimal-diff)

Five proposals follow. All are surgical (≤ 30 line edits, no phase restructures). Severity-medium gaps get a proposal; severity-low gaps get a *named-mention* proposal (cheaper than skipping).

### Proposal 1 — Name the Generator-Verifier pattern in qa-agent.md preamble

- **What:** Add one sentence to [.claude/agents/qa-agent.md](../../.claude/agents/qa-agent.md) preamble (around line 9): *"You are the **verifier** half of a Generator-Verifier pair (Anthropic Multi-Agent Coordination Patterns, 2026/4): you find faults, you do not execute fixes."* Touches **1 line added, 0 removed**.
- **Why:** Anthropic's Apr 2026 framework explicitly names the pattern and warns about loops "without defining what verification actually means". Naming the pattern in the agent file connects the existing read-only discipline to durable terminology, helping future edits resist drift. Source: [Anthropic Multi-Agent Coordination Patterns framework](https://blockchain.news/news/anthropic-multi-agent-coordination-patterns-framework) (secondary coverage).
- **Cost:** 1 line, zero behaviour change, zero breaking change.
- **Alternatives considered:**
  - (a) Add the same sentence to /work SKILL.md Phase 4 — rejected, the qa-agent file is the load-bearing spot when the pattern is read in isolation.
  - (b) Skip — rejected, naming a pattern is the cheapest way to make a discipline durable across edits.
- **Decision lean:** **adopt**. One-line annotation, low cost, makes the existing discipline explicit.

### Proposal 2 — Convert the advisory token cost print to an explicit budget gate

- **What:** Change [.claude/skills/work/SKILL.md:101](../../.claude/skills/work/SKILL.md#L101) from `Cost estimate: ~<N>× single-session` to a two-line block:
  ```
  Cost estimate:    ~<N>× single-session
  Budget cap:       <N×base> tokens (default 500K) — LEAD surfaces if any subagent or dev↔QA cycle pushes the cumulative total past the cap
  ```
  Add a sentence to the "Self-management rules" section (around [.claude/skills/work/SKILL.md:325-329](../../.claude/skills/work/SKILL.md#L325-L329)) instructing LEAD to track the cumulative token estimate across subagent spawns and surface to the user when it crosses the cap — matching the existing "tool calls > 40" pattern. Touches **~6 lines added, 1 line edited**.
- **Why:** Stanford [arxiv 2604.02460](https://arxiv.org/abs/2604.02460) shows multi-agent's apparent gains disappear when token budgets are held equal. Anthropic itself reports [4×-15× token consumption](https://www.anthropic.com/engineering/multi-agent-research-system) for multi-agent vs chat. The current print is advisory only; a soft budget gate turns the literature's finding into a checkpoint without changing the workflow.
- **Cost:** 6-7 lines in SKILL.md; no agent-file change; no breaking change to existing invocations. The 500K default is a starting heuristic — user can redirect with "go with budget 1M" at Phase 3 approval.
- **Alternatives considered:**
  - (a) Hard cap that auto-stops at the budget — rejected, would conflict with project value "human approves gated actions" and could leave dev↔QA loops half-done.
  - (b) Per-subagent budgets only (already in agent files) — rejected, doesn't catch the *cumulative* cost across dev↔QA iterations which is where Stanford's framing bites.
  - (c) Skip — rejected, the heuristic-only stance is the gap.
- **Decision lean:** **adopt, default cap 500K**. User should confirm the cap value (see Open question Q1).

### Proposal 3 — Add a per-iteration audit-trail line to Phase 4 complex path

- **What:** After Phase 4 step 1 (developer-agent) and step 2 (qa-agent), insert a one-line LEAD action: `Append to /work-trace.log: iteration <N>, dev=<status>, qa=<verdict>, tool_calls=<N>, worktree=<branch>`. Touches **~4 lines added** in [.claude/skills/work/SKILL.md:124-170](../../.claude/skills/work/SKILL.md#L124-L170).
- **Why:** Antigravity Lab (partial source, unretrievable) and MAST category (i) both flag observability as a top-5 multi-agent pitfall. Currently a 4-cycle dev↔QA run leaves no durable record beyond LEAD's transcript — which gets compacted. A simple append-only log gives the user post-mortem traceability without changing the loop logic.
- **Cost:** 4 lines in SKILL.md; one new file under repo root (or `.claude/`, gitignored by default). Cleanup story: rotate / truncate on `/work` start.
- **Alternatives considered:**
  - (a) Structured JSON log per subagent invocation — rejected, too much ceremony for a 4-iteration cap.
  - (b) Stick with the heuristic "tool calls > 40" only — rejected, no per-iteration granularity.
  - (c) Echo to chat instead of writing a file — rejected, chat gets compacted; the value is *durability*.
- **Decision lean:** **defer pending Q3 answer** (user may prefer existing minimal observability over a new file).

### Proposal 4 — Cite the Google scaling study in /work's Anti-patterns section

- **What:** Add one bullet to [.claude/skills/work/SKILL.md:344-364](../../.claude/skills/work/SKILL.md#L344-L364) Anti-patterns list:
  > - ✗ Don't add subagents to a "medium" task to "be thorough" — Google Research (2026) shows independent agents amplify errors 17.2× vs 4.4× for centrally-coordinated ones, and adding agents degrades performance up to 70% on sequential tasks. The complexity table is the gate.
  
  Touches **2 lines added**.
- **Why:** The existing list has the *practice* ("don't fan out for a single issue") but not the *citation* showing why. Naming the source ties the rule to a primary finding — making it resist drift when someone wants to "just spawn another QA-agent for parallel coverage". Source: [Google Research arxiv 2512.08296](https://arxiv.org/abs/2512.08296).
- **Cost:** 2 lines, zero behaviour change.
- **Alternatives considered:**
  - (a) Add a numerical table of error-amplification figures — rejected, too much text for a bullet list.
  - (b) Skip — rejected, citations cheap.
- **Decision lean:** **adopt**.

### Proposal 5 — Cross-link MAST's three failure categories to existing /work guards

- **What:** Add a one-paragraph "Pattern coverage" footnote at the end of /work's Anti-patterns section [.claude/skills/work/SKILL.md:344-364](../../.claude/skills/work/SKILL.md#L344-L364):
  > **MAST failure-category coverage** (arxiv 2503.13657, 14 modes in 3 categories):
  > (i) system design issues → addressed by Phase 2 complexity gate + Phase 3 human plan-approval;
  > (ii) inter-agent misalignment → addressed by single-direction brief passing (researcher → developer → qa, no loops) and central LEAD ownership;
  > (iii) task verification → addressed by qa-agent (Generator-Verifier) + 4-cycle loop cap.
  
  Touches **~6 lines added**.
- **Why:** MAST [arxiv 2503.13657](https://arxiv.org/abs/2503.13657) is the load-bearing taxonomy in the literature. Cross-linking gives any future edit a checklist: if you touch /work, you must still cover all three MAST categories. Documentation duty per CLAUDE.md.
- **Cost:** 6 lines, zero behaviour change.
- **Alternatives considered:**
  - (a) Inline the cross-link in each existing bullet — rejected, makes the list harder to scan.
  - (b) Skip — rejected, MAST is the right structuring lens.
- **Decision lean:** **adopt**.

## Out-of-scope / deliberately not adopted

| # | Verified practice | Why we won't adopt |
|---|-------------------|-------------------|
| O1 | Claim 14 — *verifier without shared context with the generator* (attributed to Cognition Apr 2026) | **Unverified** in primary fetch; the source Cognition piece I could retrieve (*Don't Build Multi-Agents*) is anti-parallelism, not pro-isolated-verifier. Acting on this would mean stripping the IMPLEMENTATION REPORT from qa-agent's input — directly conflicting with Anthropic's "compressed brief enables faster verification" framing. Wait for a verified primary source before changing. |
| O2 | Claim 15 — *Dynamic, model-decided subagent decomposition* (Bitter Lesson) | The current `complexity → workflow` table is the documented pattern that makes routing **reliable** for this codebase. Bitter-Lesson dynamic spawning is philosophical, not actionable on a 5-mode Python project. Trades reliability for theoretical flexibility — wrong trade for this scope. |
| O3 | Agent Teams for the developer-agent path | Already documented as anti-pattern at [.claude/skills/work/SKILL.md:349](../../.claude/skills/work/SKILL.md#L349) (worktree isolation not solved for teammates). Stays. |
| O4 | Multi-agent shared-context "full agent traces" (Cognition's main rec) | The brief-passing model is more compatible with Anthropic's context-isolation rationale and avoids context-window blowup. Both literatures conflict on this; we side with Anthropic because subagent context-window separation is what makes the dev↔QA loop bounded. |

## Open questions for the user

These are concrete decisions the user can answer pick-one or yes/no — not vague prompts.

- **Q1.** **Cumulative token budget for /work Phase 4 dev↔QA cycles** (Proposal 2): adopt with default 500K? Pick: (a) yes, 500K default with "go with budget <N>" override at Phase 3; (b) yes, but lower default (250K); (c) yes, but higher (1M); (d) skip — keep the advisory-only print.

- **Q2.** **Name the Generator-Verifier pattern in qa-agent.md preamble** (Proposal 1): yes / no / suggest different wording.

- **Q3.** **Per-iteration audit log to `/work-trace.log`** (Proposal 3): pick (a) adopt, write to `.claude/work-trace.log` (gitignored); (b) adopt, but emit to chat each iteration instead of a file; (c) defer — current 40-tool-call heuristic is enough.

- **Q4.** **Cite Google scaling study in Anti-patterns** (Proposal 4): yes / no.

- **Q5.** **Cross-link MAST's three categories in Anti-patterns** (Proposal 5): yes / no / pick a different taxonomy to anchor against.

- **Q6.** **Do you want a follow-up audit on `.claude/agents/*` against MAST's 14 specific failure modes** (not just the 3 categories)? The 14-mode list requires fetching the MAST paper's body — currently only the abstract is verified. yes / no.

- **Q7.** **Should /parallel-brief-generator get any of these proposals applied symmetrically** (it currently has *none* of the token-budget, audit-trail, or Generator-Verifier framing — it's an orchestrator that hands off to cold sessions, but the briefs themselves could carry budget hints)? yes — also evolve briefs / no — out of scope for this audit / defer.

## Appendix — claim-by-claim verification log

| Claim # | Article framing | Primary source verdict | Notes |
|---------|-----------------|------------------------|-------|
| 1 | Architecture-task matching matters; more agents ≠ better. | **verified** | Google [arxiv 2512.08296](https://arxiv.org/abs/2512.08296); 180 configs, 5 architectures. |
| 2 | SAS matches/outperforms MAS at equal token budget. | **verified** | Stanford [arxiv 2604.02460](https://arxiv.org/abs/2604.02460); quoted abstract. |
| 3 | Perceived multi-agent gains = 3-10× more tokens. | **partial** | Anthropic engineering blog: actual figure is 4× (single agent) and 15× (multi-agent) vs chat — directionally same, ballpark off. |
| 4 | Role-playing PM/QE architectures fail. | **unverified** | Twitter threads (@sujingshen, Yeuoly) not retrievable. Adjacent to Cognition's "conflicting decisions". |
| 5 | LLMs lack human silos / attention constraints. | **unverified** | Twitter analysis; philosophical, not empirical. |
| 6 | Fixed workflows collapse on complexity. | **unverified** | @aneeshpappu Twitter. Cognition-adjacent. |
| 7 | Error amplification: 3-agent at 90% → 73%. | **partial** | Math correct (0.9³ ≈ 0.729); Google paper's load-bearing figure is the 17.2× / 4.4× framing. |
| 8 | 17.2× independent vs 4.4× centralised error magnification. | **verified** | Google study, confirmed via InfoQ + Techstrong coverage of arxiv 2512.08296. |
| 9 | MAST: 7 systems, 41-86.7% failure. | **partial** | Abstract confirms 1600+ traces, 7 frameworks, 14 modes in 3 categories. Specific 41-86.7% range in paper body (not in abstract). |
| 10 | Antigravity 12 pitfalls (runaway retry, token overflow, context pollution, cascading failure, observability gaps). | **partial** | antigravitylab.net unreachable; 5 named pitfalls cross-validated by MAST + Google study. |
| 11 | Context isolation justifies multi-agent. | **verified** | Anthropic engineering blog: "subagents facilitate compression by operating in parallel with their own context windows". |
| 12 | Parallelisation for read/exploration; not for code-writing. | **verified** | Anthropic + Cognition. Anthropic: "most coding tasks involve fewer truly parallelizable tasks than research". |
| 13 | Generator-Verifier: verifier finds faults, doesn't execute. | **verified** | Anthropic Apr 2026 coordination patterns framework, confirmed via secondary coverage. |
| 14 | Verifier without shared context with generator. | **unverified** | Attribution to Cognition Apr 2026 — *Don't Build Multi-Agents* (the available Cognition piece) recommends the *opposite* (full context sharing). |
| 15 | Dynamic subagent spawning aligned with Bitter Lesson. | **verified** | Sutton's Bitter Lesson is canonical; Anthropic's lead-spawns-subagent example matches. |

---

*Audit prepared 2026-05-23 in worktree `determined-ride-af7184`. No skill files or agent files were modified — this document is a proposal-only artefact. Implementation deferred to a follow-up session per the brief.*

---

## Self-check addendum — 2026-05-23

The original audit read the three companion agent files but did not follow the **transitive** call graph from /work and /parallel-brief-generator. This addendum patches two blind spots: (1) skill-chain coverage, (2) project-fit filtering of each proposal. **No proposal was dropped; three were revised; one new gap surfaced. The top-3 ordering changed slightly.**

### Skill-chain coverage audit

| Node | Status | Material to multi-agent conclusions? | Where it lands |
|------|--------|--------------------------------------|----------------|
| [.claude/agents/researcher-agent.md](../../.claude/agents/researcher-agent.md) | ✓ read in original | n/a — already cited | — |
| [.claude/agents/developer-agent.md](../../.claude/agents/developer-agent.md) | ✓ read in original | n/a — already cited | — |
| [.claude/agents/qa-agent.md](../../.claude/agents/qa-agent.md) | ✓ read in original | n/a — already cited | — |
| [.claude/skills/work/SKILL.md](../../.claude/skills/work/SKILL.md) | ✓ read in original | n/a — already audited | — |
| [.claude/skills/parallel-brief-generator/SKILL.md](../../.claude/skills/parallel-brief-generator/SKILL.md) | ✓ read in original | n/a — already audited | — |
| [.claude/skills/impact-map/SKILL.md](../../.claude/skills/impact-map/SKILL.md) | ✗ not read | **No** — it's a pre-edit checklist, not a multi-agent coordination element. Out of scope. | no row change |
| [.claude/skills/pr-review/SKILL.md](../../.claude/skills/pr-review/SKILL.md) | ✗ not read | **YES — material.** Has team mode with explicit token-cost print before spawning, auto-decline at small diff size (≤5 files / ≤300 lines), three-teammate cap with rationale, SendMessage findings-aggregation protocol, idle-timeout policy. Many proposals partially duplicate disciplines already present here. | amends rows V3, V6, V11, A8, A10 |
| [.claude/agents/docs-reviewer.md](../../.claude/agents/docs-reviewer.md) | ✗ not read | **YES — material.** Explicit "Read-only — never pushes" + idle-on-task-completion. Strengthens Generator-Verifier coverage in V6/A6. | amends V6, A6 |
| [.claude/agents/app-security-reviewer.md](../../.claude/agents/app-security-reviewer.md) | ✗ not read | **YES — material.** Explicit "Don't attempt to 'fix' a CRITICAL finding in-place. Report it; LEAD decides" — textbook Generator-Verifier discipline. | amends V6, A6 |
| [.claude/agents/quality-reviewer.md](../../.claude/agents/quality-reviewer.md) | ✗ not read | **YES — material.** Explicit rationale for bundling 3 gates into 1 teammate: *"combining them keeps the team size at three teammates instead of five"* — direct embodiment of Google's "more agents ≠ better". | amends V1, A1 |
| [.claude/skills/parallel-brief-generator/brief-template.md](../../.claude/skills/parallel-brief-generator/brief-template.md) | ✗ not read | **No.** Project-specific cold-session workflow; no new multi-agent coordination evidence beyond what SKILL.md already documents. | no row change |
| [.claude/skills/parallel-brief-generator/pm-reminders.md](../../.claude/skills/parallel-brief-generator/pm-reminders.md) | ✗ not read | **No.** Photo-manager-specific reminders (gates, scanner gotchas) — orthogonal to multi-agent claims. | no row change |
| [scripts/hooks/team_task_completed.py](../../scripts/hooks/team_task_completed.py) | ✗ not read (existence only) | **YES — material.** Hook exists, so /pr-review team mode already has structured task-completion observability. Affects Proposal 3's scope. | amends A10 |
| [scripts/hooks/team_teammate_idle.py](../../scripts/hooks/team_teammate_idle.py) | ✗ not read (existence only) | **YES — material.** Idle-watchdog hook. Means /pr-review's team mode also has stall-detection. Affects Proposal 3. | amends A10 |

**Net flagged:** 7 nodes (counted in the table above) had material impact (excluding hooks listed by existence only); 4 were pure scope-of-reading misses. None of the **9 original verified claims** flipped — the literature-side verdicts are unchanged. The amendments are entirely on the project-side mappings (column "Where /work addresses" and "Where /parallel-brief addresses" gain a third "Where /pr-review-team addresses" perspective for several rows).

#### Amendments to the original inventory tables

- **V1 (task-architecture matching)** — `self-check amended`. Append to the "Where /work addresses" cell: *"and quality-reviewer.md's explicit 3-vs-5 teammate bundling rationale at [.claude/agents/quality-reviewer.md:14-19](../../.claude/agents/quality-reviewer.md#L14-L19) is the in-project embodiment of Google's finding."*

- **V3 (4-15× token consumption)** — `self-check amended`. Original status "partial — no enforced cap". **Strengthen to: partial — no enforced cap, but precedent exists.** Append: *"`/pr-review` team mode has an auto-decline at ≤5 behaviour-bearing files OR ≤300 diff lines [.claude/skills/pr-review/SKILL.md:127-136](../../.claude/skills/pr-review/SKILL.md#L127-L136) and prints `note: team mode active (~4× single-session)` before spawning [.claude/skills/pr-review/SKILL.md:156-162](../../.claude/skills/pr-review/SKILL.md#L156-L162). /work's Phase 3 prints a similar cost line but has no analogous auto-decline at the boundary between LEAD-direct and subagent-pipeline."* Severity stays **medium**; the gap is real but project precedent now informs the proposal.

- **V6 (Generator-Verifier)** — `self-check amended`. Append: *"The three /pr-review teammates ([docs-reviewer](../../.claude/agents/docs-reviewer.md), [app-security-reviewer](../../.claude/agents/app-security-reviewer.md), [quality-reviewer](../../.claude/agents/quality-reviewer.md)) all explicitly disclaim write authority — see app-security-reviewer.md line 68: *'Don't attempt to fix a CRITICAL finding in-place. Report it; LEAD decides.'* The pattern is in heavy use across the project, just not **named**."* Gap status: still **low**, but Proposal 1 widens to cover them too.

- **V11 (observability)** — `self-check amended`. Append: *"For /pr-review team mode specifically, observability is partially wired via [scripts/hooks/team_task_completed.py](../../scripts/hooks/team_task_completed.py) and [scripts/hooks/team_teammate_idle.py](../../scripts/hooks/team_teammate_idle.py); the SendMessage findings-aggregation protocol [.claude/skills/pr-review/SKILL.md:206-229](../../.claude/skills/pr-review/SKILL.md#L206-L229) is the per-teammate audit. The /work complex-path dev↔QA loop has no equivalent — Proposal 3's scope narrows to /work only."* Gap stays **medium**.

- **A1 (naive scaling)** — `self-check amended`. Append: *"Quality-reviewer.md explicitly cites 3-vs-5 teammate sizing as deliberate; this is the project's in-codebase application of Google's finding."*

- **A6 (verifier-that-re-executes)** — same amendment as V6.

- **A8 (token overflow)** — same amendment as V3.

- **A10 (observability gaps)** — same amendment as V11.

### Proposal × project-fit matrix

5-filter check on each proposal: A=Security gates, B=Hook duplication, C=Auto-memory conflicts, D=Change size, E=Existing-skill overlap.

| Proposal | A | B | C | D | E | Net verdict |
|----------|---|---|---|---|---|-------------|
| **1** — Name Generator-Verifier in qa-agent.md | ✓ no new gate | ✓ no hook overlap | ✓ aligns with feedback_skill_composition (naming aids composition) | ✓ 1 line | △ pr-review teammates also embody pattern unnamed → **widen** | **revise (widen scope)** |
| **2** — Token budget gate on /work Phase 4 | ✓ surfacing the cap IS the gate; user approves at Phase 3 | ✓ no hook overlap | ✓ aligns with feedback_skill_composition + per-gated-action principle | ✓ ~7 lines | △ /pr-review already has the print pattern + auto-decline → **model on it** | **revise (use /pr-review pattern)** |
| **3** — Per-iteration audit-trail log | ✓ `.claude/` writes are auto-approved | △ partial overlap: team_task_completed.py logs team events, but ONLY for Teams, not for `Agent(isolation="worktree")` dev↔QA loops | ✓ no memory conflict | ✓ 4 lines + 1 file | △ context-budget skill exists but is audit-only, not enforcement → keep | **revise (narrow scope to /work complex path)** |
| **4** — Cite Google scaling in Anti-patterns | ✓ no gate | ✓ no hook overlap | ✓ no memory conflict | ✓ 2 lines | ✓ — but quality-reviewer.md's 3-vs-5 bundling is internal precedent worth cross-referencing | **keep (minor enrichment)** |
| **5** — Cross-link MAST 3 categories | ✓ no gate | ✓ no hook overlap | ✓ no memory conflict | ✓ 6 lines | △ /pr-review's gate decomposition is the same pattern at manager level — worth a one-line cross-mention | **keep (minor enrichment)** |

**Tally:** 3 revised, 2 kept (with enrichment), 0 dropped. Project-fit didn't kill anything — the project already has a sympathetic vocabulary for every proposal — but three need to be modelled on existing in-project patterns rather than invented from scratch.

### Amended proposals

#### Proposal 1 (revised — wider scope)

**What changed:** Original was a 1-line wording change to [.claude/agents/qa-agent.md](../../.claude/agents/qa-agent.md). **Revised:** add the same Generator-Verifier framing — *"You are the **verifier** half of a Generator-Verifier pair (Anthropic Multi-Agent Coordination Patterns, 2026/4): you find faults, you do not execute fixes."* — to **all four verifier agent files** (qa-agent + the three pr-review teammates). Each file gets the same one-sentence annotation, with the *"the **verifier** half"* phrase adjusted to the agent's role.

- **Why widen:** The three /pr-review teammates already embody Generator-Verifier (read-only, "describe what LEAD should do" instead of executing) but don't name the pattern. If Proposal 1 lands only on qa-agent.md, the project ends up with one named instance and three unnamed instances of the same discipline — the durability benefit is uneven.
- **New cost:** 4 lines added across 4 files (qa-agent.md, docs-reviewer.md, app-security-reviewer.md, quality-reviewer.md). Still trivial.
- **New decision lean:** **adopt, widen** to all four agent files.

#### Proposal 2 (revised — mirror /pr-review's pattern)

**What changed:** Original framed the budget cap as a new mechanism on /work Phase 4. **Revised:** model the syntax on /pr-review's existing pattern at [.claude/skills/pr-review/SKILL.md:127-162](../../.claude/skills/pr-review/SKILL.md#L127-L162) — which already has (a) an auto-decline at small diff size and (b) a `note: team mode active (~Nx single-session)` print before spawning.

The /work analogue:

- **Auto-decline at "simple" complexity** — already exists implicitly (Phase 2's simple → LEAD direct). Make it explicit by adding the literal phrase: *"complex / multi-issue paths spawn subagents at roughly 4× single-session cost; simple and medium paths stay in LEAD. Phase 2's complexity score IS the auto-decline gate."*
- **Token-budget print at Phase 3** — change the `Cost estimate: ~<N>× single-session` line to also include a cumulative budget figure that LEAD will surface at if any single iteration crosses it. Default 500K. Phrase as a print, not an enforcement step — same shape as /pr-review's `note:`.

- **New cost:** 5-6 lines in /work SKILL.md (down from 7), 0 new files, mirrors an existing project pattern.
- **New decision lean:** **adopt, modelled on /pr-review's auto-decline + note pattern**.

#### Proposal 3 (revised — narrower scope)

**What changed:** Original applied to *all* multi-agent runs in /work. **Revised:** scope strictly to /work's complex-path dev↔QA loop (which uses `Agent(isolation="worktree")`, NOT TeamCreate). The /pr-review team-mode side already has [scripts/hooks/team_task_completed.py](../../scripts/hooks/team_task_completed.py) and [scripts/hooks/team_teammate_idle.py](../../scripts/hooks/team_teammate_idle.py), plus the SendMessage findings-aggregation protocol; that side does NOT need a new log file.

- **New cost:** 4 lines in /work SKILL.md + one optional log file under `.claude/work-trace.log` (gitignored by inheritance).
- **New decision lean:** still **defer pending Q3 answer** — but with the scope clarification that the proposal does *not* touch team mode.

### Newly surfaced gaps

- **N1 — Pr-review team teammates embody Generator-Verifier but don't name it.** Absorbed into Proposal 1 (revised) above.
- **N2 — Cumulative cost across composed agent runs is unaccounted.** A /work complex path that lands at Phase 5 with `/pr-review team` is effectively a 7-agent run (researcher + developer + qa + 3 pr-review teammates + LEAD itself). Each layer prints its own cost note in isolation; no surface aggregates them. Severity: **low** (the per-layer prints + human plan-approval at Phase 3 cover most of the risk). Filed as an open question rather than a proposal — see Q8 below.
- **N3 — `Agent(isolation="worktree")` and `TeamCreate(...)` are two coordination primitives with overlapping responsibilities and divergent observability stacks.** Worktree-isolation has nothing equivalent to the team hooks; team mode has nothing equivalent to worktree isolation. /work uses the first; /pr-review uses the second. Severity: **low** (this is a known harness-level constraint flagged at /work line 349). Not a /work or /parallel-brief gap; flag for a separate harness-evolution audit.

### Open questions — additions

In addition to Q1-Q7 in the original audit:

- **Q8.** **Cumulative cost surface across composed agent runs** (new gap N2): pick (a) add a single line at /work Phase 5 entry that re-totals the cumulative cost-estimate from researcher + dev × iterations + qa × iterations + (if team-mode) +4×; (b) defer — the per-layer prints are enough; (c) add a `--budget <N>` flag to /work that becomes an inherited budget for all downstream invocations including /pr-review team. **Recommended: (b) defer**, but flagging for user view.

### Revised top-3 (post self-check)

The ordering changed:

1. **Proposal 1 (revised, widened) — Name Generator-Verifier across all 4 verifier agents.** Now ~4 lines across 4 files. The widening turns a single-file annotation into a project-wide pattern label, which is a much bigger durability payoff for the same trivial cost.
2. **Proposal 2 (revised) — Token budget gate modelled on /pr-review's existing 4× note + auto-decline pattern.** Lower line count (5-6 vs 7), reuses existing project vocabulary, and the auto-decline framing is more honest about what Phase 2's complexity table is actually doing.
3. **Proposal 5 (kept, enriched) — MAST 3-category cross-link.** Adding the one-line note that /pr-review's gate decomposition already addresses MAST (i) "system design issues" at the manager level makes the cross-link more honest: this isn't a new discipline, it's a naming move that connects existing in-project structure to literature.

Proposals 3 and 4 stay below the top-3 cut. Proposal 3 has open question Q3; Proposal 4 is 2 lines and uncontroversial.

---

*Self-check completed 2026-05-23. No skill or agent files modified. Audit doc is the only artefact.*

---

## Reconciliation — 2026-05-23

An independent third-party review was produced after the self-check addendum (see `docs/audits/multi-agent-skill-audit.independent-review.md`). The reviewer agreed with 4 of 5 proposals, partially disagreed on Proposal 2, sharpened Q8, and flagged one issue the original audit missed (Challenge 3). This section reconciles each disagreement.

### Disagreement ledger

| Topic | Reviewer verdict | My response | Verdict | Evidence cited |
|-------|------------------|-------------|---------|----------------|
| **Proposal 1 widening** | Agree — grep confirms all 3 pr-review teammates embody the pattern unnamed; widening is principled. | No action — reviewer agrees with self-check. | — (no disagreement) | n/a |
| **Proposal 2 reframing** | Partial disagree — /pr-review auto-decline fires on *diff-size* proxies (≤5 files / ≤300 lines), not on token grounds; component (b)'s 500K runtime tracker is net-new, not a "mirror". | **The reviewer is right on the framing.** The /pr-review precedent supports the *auto-decline at structural threshold* component (a), but does NOT have a runtime cumulative tracker — I oversold the "mirror" framing. Drop component (b)'s runtime 500K cap; keep component (a) as the explicit 1-line auto-decline clarification. Add the reviewer's separate 1-line clarification at [.claude/skills/work/SKILL.md:101](../../.claude/skills/work/SKILL.md#L101) clarifying what `<N>×` covers (researcher + dev iterations + qa iterations, excludes LEAD overhead). | **COMPROMISE** | [.claude/skills/pr-review/SKILL.md:127-136](../../.claude/skills/pr-review/SKILL.md#L127-L136) (diff-size proxies, NOT token measurements); [.claude/skills/pr-review/SKILL.md:156-162](../../.claude/skills/pr-review/SKILL.md#L156-L162) (static `note:` print, NOT runtime tracker). Reviewer's reading is correct. |
| **Proposal 3 drop vs revise** | Agree — re-ran context-budget overlap check; static config-inventory ≠ runtime audit trail. Defer holds. | No action — reviewer agrees with self-check. | — (no disagreement) | n/a |
| **Proposal 4** | Agree — trivial, verified source. | No action. | — (no disagreement) | n/a |
| **Proposal 5** | Agree — trivial, verified source. | No action. | — (no disagreement) | n/a |
| **Q8 promote/keep/drop** | KEEP DEFERRED with explicit revisit trigger. Worst-case 13× sits inside Anthropic's verified 15× envelope; /work-complex + /pr-review-team is one canonical task, not compositional blowout. | **The reviewer is right that "defer pending Q3" was too vague.** Their worst-case math (researcher 1× + dev×4 4× + qa×4 2× + LEAD 1× + pr-review-team 4× = ~13×) is a concrete envelope check the original audit didn't do. **Concede the vagueness; adopt their explicit trigger conditions.** | **CONCEDE** | Anthropic 15× envelope from [engineering blog](https://www.anthropic.com/engineering/multi-agent-research-system) (already cited in V3); reviewer's 13× math is arithmetic on documented per-layer caps. |
| **Reviewer's Challenge-3 issue** (researcher-agent complexity rubric ignores risk/blast-radius) | A 2-file change to `infrastructure/manifest_repository.py` migrations would score `simple` (≤3 files, no cross-cutting deps, plausibly full coverage, <1h) and route to LEAD-direct, **skipping the Generator-Verifier loop**, despite SQLite migrations being a Gate 8 high-risk area per /pr-review's composition graph. | **The reviewer is right and this is a real miss.** Original audit checked /work's complexity routing structurally but didn't probe whether the routing's *inputs* covered risk. Angle A (Impact + call graph) at [.claude/agents/researcher-agent.md:28-34](../../.claude/agents/researcher-agent.md#L28-L34) already collects per-file blast-radius — but the complexity-scoring rubric at [.claude/agents/researcher-agent.md:52-60](../../.claude/agents/researcher-agent.md#L52-L60) does not reference it. Concede and add as Proposal 6. | **CONCEDE** | Researcher-agent.md:55-60 criteria are file count + deps + coverage + time; no risk/blast-radius input. Gate 8 (SQLite migration safety) per [.claude/skills/pr-review/SKILL.md:103](../../.claude/skills/pr-review/SKILL.md#L103) treats migrations as load-bearing regardless of file count. Original audit missed this. |

### Final reconciled proposal set

What survives, in implementable form:

- **Proposal 1 (widened, unchanged from self-check).** Add the one-line Generator-Verifier framing — *"You are the verifier half of a Generator-Verifier pair (Anthropic Multi-Agent Coordination Patterns, 2026/4): you find faults, you do not execute fixes."* — to all four verifier agent files: [qa-agent.md](../../.claude/agents/qa-agent.md), [docs-reviewer.md](../../.claude/agents/docs-reviewer.md), [app-security-reviewer.md](../../.claude/agents/app-security-reviewer.md), [quality-reviewer.md](../../.claude/agents/quality-reviewer.md). **Cost: 4 lines, 4 files.**

- **Proposal 2 (compromised — runtime tracker dropped).** Two surgical edits in [.claude/skills/work/SKILL.md](../../.claude/skills/work/SKILL.md):
  - At line 76-80 (Phase 2 complexity table): add a 1-line note that the complexity score IS the auto-decline gate for subagent spawn (simple/medium stay in LEAD; complex/multi-issue spawns subagents at ~4× single-session cost). This makes implicit behaviour explicit, mirroring /pr-review's [auto-decline](../../.claude/skills/pr-review/SKILL.md#L127-L136) pattern *at the appropriate granularity*.
  - At [line 101](../../.claude/skills/work/SKILL.md#L101): add a 1-line clarification of what `<N>× single-session` covers (researcher + dev iterations + qa iterations; excludes LEAD overhead and Phase-5 /pr-review-team multiplier). This is what the reviewer flagged as the "load-bearing question the audit missed".
  - **Dropped:** runtime cumulative 500K tracker (component (b) from self-check). The /pr-review precedent doesn't support a runtime tracker; introducing one is a different kind of change than this audit can argue for. Filed implicitly as monitoring-territory under Q8's revisit triggers.
  - **Cost: 2 lines, 1 file.**

- **Proposal 4 (kept, unchanged).** Cite Google scaling study in /work's Anti-patterns section. **Cost: 2 lines, 1 file.**

- **Proposal 5 (kept, enriched per self-check, unchanged in reconciliation).** Cross-link MAST 3 categories with note that /pr-review's gate decomposition addresses MAST (i) at manager level. **Cost: 6 lines, 1 file** (same /work SKILL.md as Proposals 2 + 4).

- **Proposal 6 (new — from Challenge 3 CONCEDE).** Edit the complexity-scoring rubric at [.claude/agents/researcher-agent.md:52-60](../../.claude/agents/researcher-agent.md#L52-L60) to plumb the per-file blast-radius (already collected in Angle A) into the score. Specifically:
  - Add a row to the score-criteria table: *"any affected file has `blast-radius: high` (e.g. SQLite migrations, settings keys, background workers per the [impact-map](../../.claude/skills/impact-map/SKILL.md#L16-L23) activation list) → upgrade score by one tier."*
  - This means a 2-file migration touch can't route to "simple" by file count alone — it gets bumped to "medium" minimum, ensuring the dev↔QA Generator-Verifier loop fires.
  - **Cost: ~3 lines, 1 file.**

**Dropped:**
- Proposal 2's component (b) — runtime cumulative 500K token tracker. The /pr-review precedent doesn't support it and the audit shouldn't argue for a kind of mechanism the project hasn't established.

**Deferred (with explicit trigger to revisit):**
- **Proposal 3 — per-iteration audit-trail log.** Trigger to revisit: if a /work complex-path run leaves a stakeholder unable to retroactively reconstruct which dev↔QA iteration burned which tokens / made which decision, OR if Q3 returns "(a) adopt" from the user.
- **Q8 — cumulative cost surface across composed runs.** Replacing the previous vague "defer pending Q3" with the reviewer's explicit triggers: **revisit if (i) a single end-to-end /work-complex + /pr-review-team run crosses ~15× single-session in practice (Anthropic's verified upper envelope), OR (ii) Phase 3's `<N>×` estimate is observed to under-count actual cost by >2× on a representative task.**

### Self-bias check

- Proposals where I conceded: **2** (Q8 specificity; Challenge 3 — added as new Proposal 6).
- Proposals where I compromised: **1** (Proposal 2 — dropped runtime tracker, kept clarification edits).
- Proposals where I defended: **0**.
- Proposals where reviewer agreed up-front: **4** (P1, P3, P4, P5).

Defended (0) is NOT greater than conceded (2) + compromised (1). The self-bias rule's "if defended > conceded, explain why this isn't motivated reasoning" trigger does not fire. Conversely, conceding 3 out of 5 disputed points (including 1 silent miss the reviewer surfaced) is not a rubber-stamp pattern either — the reviewer's specific evidence (worst-case math envelope inside Anthropic's 15×; the researcher-agent rubric's missing blast-radius input) is concrete and verifiable, not opinion. Each concession traces to a check I can re-run against the project files now, not to a wish to look agreeable.

The compromise on Proposal 2 is the only call where the disagreement was framing rather than substance. The auto-decline-at-structural-threshold framing was honest; the runtime-cap framing was inventing a new mechanism while claiming precedent. Dropping the over-reach and keeping the precedent-aligned part is the correct trade.

### Implementation footprint estimate

Surviving proposals:

| Proposal | Lines | Files |
|----------|-------|-------|
| P1 (widened Generator-Verifier framing × 4 agents) | 4 | 4 |
| P2 (auto-decline clarification + `<N>×` scope note) | 2 | 1 |
| P4 (cite Google scaling) | 2 | 1 (same as P2/P5) |
| P5 (MAST 3-category cross-link) | 6 | 1 (same as P2/P4) |
| P6 (researcher complexity rubric blast-radius input) | 3 | 1 |
| **Total** | **17** | **6** |

Files touched: `qa-agent.md`, `docs-reviewer.md`, `app-security-reviewer.md`, `quality-reviewer.md`, `work/SKILL.md`, `researcher-agent.md`. This matches the reviewer's "~17 lines across 6 files" arithmetic exactly — a useful sanity check that we converged.

---

*Reconciliation completed 2026-05-23. The reconciled proposal set is the implementation brief for the follow-up session. No skill or agent files were modified by this reconciliation.*
