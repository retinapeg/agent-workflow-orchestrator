# Arena engineering decisions

**Status:** Accepted product policy, **not yet implemented or verified end to end**.
**Recorded:** 2026-10-30.
**Scope:** The existing Arena / agent-workflow-orchestrator. Reconcile this file with the latest local owner checkout and worktree before implementing; GitHub is not evidence that a newer local session was pushed.

These are durable owner decisions, not instructions to launch jobs, spend money, change providers, expose data, merge code, or modify neighbouring repositories. Do not override them through a model's own proposal. Change a decision only on explicit owner instruction and record the replacement with its reason.

## ADR-001 — Deepen existing products; no new orchestration stack

**Decision:** Arena owns autonomous engineering execution. LeoOS owns personal conversations, memory, commitments and daily coordination. Career Command / job-application-tracker owns application evidence and submission state. Connect through the narrow existing interfaces rather than combining these products or creating a new repository or framework.

**Priority:** One substantial, owner-visible LeoOS milestone and its real acceptance evidence. A completed capability is worth more than concurrent repo creation. Preserve the existing builders, accounts, history, releases and rollback paths; do not reset local uncommitted work. The current build record has evidence from proof projects but still identifies a real owner-repository build as an unverified gate.

## ADR-002 — Compute-aware routing, Codex first, Claude reserved

**Decision:** Treat included, genuinely available Codex subscription use as the **preferred engineering capacity**. Maximise productive use of that allowance before materially depleting Claude subscription allowances. This is not a requirement to burn idle tokens or wait on Codex when it cannot complete a time-critical or technically unsuitable task.

Decision order for each bounded task:
1. **No model** for deterministic checks, file metadata, scheduling, deduplication, simple transforms and comparison against a stored objective.
2. **Codex** for normal planning, implementation, repair and review when account usage is available and capability fits the task. Start with the least expensive / least intense *competent* model and effort; use an evidence-based escalation when it fails.
3. **OpenRouter** for eligible lower-risk and low-cost classification, extraction, summarisation, limited reviewer work or other model-independent subtasks **only when expressly enabled, configured with real provider-side spending caps, and allowed to receive the data**. OpenRouter is not automatically cheaper than included Codex use, and is not a silent fallback.
4. **Claude** for high-value independent cross-provider review, difficult integration or repeated verified failures where its expected benefit justifies consuming scarce subscription quota. It can be used earlier when the objective needs that capability; explain routing.
5. **Pause with an actionable blocker** rather than silently purchasing API capacity or switching to an unauthorised provider.

Do not hard-code model superiority. Record model, provider, account slot, effort, capability, routing reason, candidate quality and verified outcome. Route based on actual task results. Independent review must inspect a frozen candidate; the reviewer does not declare success over failing deterministic acceptance tests.

## ADR-003 — Honest quota and token economics

**Decision:** The controller maintains distinct ledgers for (a) plan allowance status; (b) reported tokens, including cached and nested subagent usage where available; (c) **actual** metered API spending; (d) explicitly labelled API-equivalent estimates; and (e) verified successful outcomes. Never describe estimated equivalent cost as an invoice, or unknown quota as 0 or infinity.

- Use provider-approved quota/usage sources: Codex usage dashboard, CLI status or supplied rate-limit fields; Claude usage dashboard/CLI status and actual quota-limit errors. Honour both short-window and weekly limits where applicable.
- If a provider does not expose machine-readable remaining allowance, show **unknown** and use conservative, bounded scheduling. Never fabricate a number or imply that undocumented subscription quota can be read.
- Persist throttles, provider cooldowns, next confirmed reset time, unknown outcomes, run status and account-slot ownership across controller restarts. On explicit rate limit, stop that provider and resume only after a reliable reset/owner update.
- Use fresh, bounded model sessions fed by compact objective/checkpoint/changed files, rather than repeatedly sending an entire growing conversation. Reuse prefix caching when it genuinely reduces expenditure without weakening evidence or memory.
- Capture task ID, model/provider, call category, input/output/cache/reasoning tokens when reported, elapsed time, retry count, real charges, pricing version, confidence/coverage, and **cost or usage per verified completed milestone**. Log null rather than inventing a missing number.
- No request solely to use remaining tokens. Useful work first, quota utilisation second.

Official context: OpenAI Codex plan usage and CLI status; Anthropic Claude Code usage guidance. These rules must be checked against the installed client version before implementing an API to read quota.

## ADR-004 — OpenRouter is optional, budgeted and privacy-constrained

**Decision:** Add an OpenRouter adapter behind the existing provider boundary; do not install an alternative orchestrator or overwrite the current Codex/Claude subscription sign-ins. OpenRouter is **disabled until authorised setup and a working capped key**.

- Default **incremental paid spend = $0**, including in Go Mad mode. A nonzero paid cap must be set explicitly by the owner, for a specified run or budget window, before paid requests are permitted.
- Require a server-side OpenRouter key limit/guardrail and a local preflight spend estimate plus independent in-flight limits. No auto-top-up, paid overage, or unknown-cost fallback. Stop before the budget can be exceeded; when balance/cap cannot be verified, fail closed on paid routes.
- Use a small allowlist of explicitly tested, versioned models with recorded input/output prices and supported tool/structured-output requirements. No arbitrary automatic provider substitution or unbounded fallback chain.
- For opted-in **non-sensitive** data, require suitable provider policy; favour Zero Data Retention and deny data collection/training where compatible. Confirm privacy settings and routing per endpoint; ZDR still sends input to a third party. Never transmit personal Gmail, CVs, medical history, finances, secrets or private memory to OpenRouter by default.
- Keep credentials in secure local configuration, never Git, prompts, logs or screenshots. Redact sensitive payloads in traces.

Official references:
- https://openrouter.ai/docs/api-reference/limits
- https://openrouter.ai/docs/guides/features/guardrails/overview
- https://openrouter.ai/blog/insights/zero-data-retention/

## ADR-005 — Three explicit operating modes

Modes control effort, parallelism and provider use, **not permissions**. Every mode retains the same root boundaries: authorised repositories only, deterministic checks, reversible local changes, immutable evidence, no unsolicited email/job submission, credential access, public deployment or paid spending.

| Mode | Entry | Compute behaviour | Exit |
| --- | --- | --- | --- |
| **Economy** (default) | Automatic | One primary writer, bounded verifier; Codex-first; short contexts; use models only on actual triggers; OpenRouter if explicitly funded and allowed; Claude chiefly for significant independent review or proven blockers | Ongoing |
| **Balanced** | Owner choice or task policy | More aggressive bounded escalation and an additional independent worker on non-overlapping work; retain Codex-first routing and Claude reserve | Return to Economy after the selected milestone |
| **Go Mad** | **Explicit current owner request** | Aggressive but bounded parallel engineering and verification using all genuinely available *authorised* subscription capacity; strongest competent models on high-value tasks; more independent tests/experiments; Codex utilisation prioritised before broadly drawing on Claude. OpenRouter remains capped and opt-in | Automatically expires at the end of its selected objective, a configured time/usage ceiling, cancellation, or a safety/billing blocker |

The literal user instruction **"go mad"** is permission to increase *compute effort on an approved objective*, not permission to spend, bypass provider limits, disable safety controls or rewrite project scope. Display current mode, accounts in use, planned jobs, usage status, budget and a **Stop all** control. Every provider invocation must be tied to a visible run and objective.

## ADR-006 — Escalation, durable continuation and quality gates

- Each objective has an owner-approved definition of done, frozen task boundaries, checkpoint, explicit acceptance tests and observable output. Never turn every chat turn into a build instruction.
- Prefer one writer per code path; parallel agents use isolated worktrees/non-overlapping ownership. Only one integrator merges. No change to a different repository without specific authorisation.
- On failure, inspect the logs and change the hypothesis. Two non-progressing attempts trigger a fresh diagnosis or stronger model; a subsequent bounded attempt must have a distinct proposed fix. Persist blockers rather than entering a repetition loop.
- After any restart, recover accepted milestones, failed attempts, actual quota state, objective and next action without duplicate external effects. Handle ambiguous provider outcomes conservatively.
- Required checks must pass in the exact target environment; record negative cases, actual rendered/user-visible behaviour and failure recovery. Offline/mock checks are labelled as such and cannot substitute for a real end-to-end acceptance run.
- The controller must support owner pause/stop, revocation, budget kill switch and local rollback. A stopped or rate-limited worker must not be replaced by a provider outside the authorisation boundary.

## ADR-007 — Implementation order (do not infer that any step is complete)

1. Inspect active local release, account slots, dirty Git state, existing usage ledger, current routing and tests. Preserve local work. Produce a short source-of-truth baseline.
2. Implement **quota and budget status as read-only first**: reliable reported values, unknowns, per-provider availability, reset/cooldown state, token ledger and spend caps. Test zero-budget behaviour.
3. Add the routing policy and mode selection behind the existing controller boundary without swapping the framework or losing current model adapters. Verify deterministic Codex-first routing and bounded Claude escalation under scripted quotas.
4. Add an **optional** OpenRouter-compatible provider adapter with a dummy-key/test fixture first; only then owner-configured capped live access, non-sensitive fixtures and privacy assertions.
5. Prove Economy → Go Mad → Economy through one substantial **existing-repo** objective, with visible use of each allowed account, bounded provider failure/restart, real UI, independent checks, kill switches and honest cost reporting. If a real run is not executed, label it unverified.
6. Use the finished route to ship a real LeoOS user-visible capability; no new repo or speculative feature work.

**Acceptance gates:** (A) Codex available ⇒ ordinary engineering task routes to Codex; (B) rate-limited Codex ⇒ no fabricated allowance, bounded appropriate fallback or pause; (C) exhausted Claude ⇒ no surprise API charge; (D) OpenRouter disabled/zero cap ⇒ 0 paid requests; (E) owner-approved cap ⇒ cannot exceed provider-side guardrail and local planned spend, including concurrency; (F) private source ⇒ no OpenRouter transmission by default; (G) Go Mad finite run ⇒ increased permitted parallelism, visible trace, reverts automatically; (H) crash/restart ⇒ no duplicated payments or task effects; (I) final result verified on owner-selected existing project, not only fake proof repositories.

## Changing the decisions

Amend this file only for a concrete reason, with a dated subsection recording the previous rule, replacement, reason, compatibility impact, and evidence. The code must not silently reinterpret these decisions. The implementation is not complete merely because this document exists.
