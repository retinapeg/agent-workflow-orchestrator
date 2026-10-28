# Job C recovery report

Outcome: the minimal AcquisitionOps service flow, local dashboard implementation, reproducible scripted evaluation and documentation are delivered. **Browser acceptance is blocked by sandbox listener permissions. Independent coordinator verification is still pending. This is not a full-acceptance claim.**

Report written: 2026-10-27T00:18:58.284032+00:00. Work ended before the existing supervisor deadline of 2026-10-27 00:30:19 UTC and within the 27-minute implementation allowance. No additional coding workers or subagents were launched.

## Admission, ownership and preservation

- Target: `/Users/leonardaarons-ditson/agent-reliability-workspace/agent-workflow-orchestrator` only. Origin verified as `https://github.com/retinapeg/agent-workflow-orchestrator.git`.
- Existing branch `run-mode-ledger`; HEAD `dc19aec` preserved. Existing untracked `.DS_Store` was left untouched. No commits, pushes, resets, merges, worktrees, queue launches or edits to other repositories.
- The coordinator had already admitted task `t_f1d653a2` at 00:00:39 UTC. Existing worker PID `2940`, supervisor PID `2922`, session `01a17aa8-b9d1-7270-a371-fa2ec1651de4`, attempt `overnight-recovery-1`. These are read from its recovery receipt, not newly invented launch metadata. This tool execution's parent PID matched `2940`.
- Direct `ps` was denied in this sandbox. Admission was verified read-only from the coordinator's global SQLite reservation and its 23:58:53 UTC `c-ownership.json` process/session snapshot: original A/B workers absent, no queued turns or target owner. A's status recorded timed out; B's recorded exited. The native dispatcher and global admission ledger were not modified here. No claim of a fresh sandbox process-table scan is made.
- Initial available disk: 13,910,360 KiB (13.27 GiB), strictly above 8 GiB. Disk was checked repeatedly; final verification receipt records 14,292,406,272 bytes (13.31 GiB). The existing external supervisor also monitors disk and deadline. Jobs A/B were not interrupted.
- The worker used the existing authenticated session. It did not inspect credentials, add paid API routes, or invoke models from the application.

## Delivered behavior

- Fifteen frozen synthetic prospect records with illustrative segment response/conversion scores, including prompt-injection text, missing margin, duplicate contacts, suppression and no-consent controls.
- Expected-net-value selection under an integer-cent budget: greedy baseline and exact 0/1 knapsack scripted comparison. A $12 budget selects two studios with $40.80 expected net versus one consultancy at $36.
- Deterministic ingestion validation, quarantine of contradictory repeated IDs, review blockers, suppression, contact uniqueness, budget enforcement and approved-plan fingerprint checks. Invalid records are retained, explicitly reviewed and excluded; human review cannot make them eligible.
- Explicit reviewer/approver actions, typed APPROVE, durable actor attribution, contact reservation and operation creation in one SQLite transaction.
- Mock-only provider with unique durable receipts, no real network delivery, bounded three-attempt retries, injected transient/permanent failures and lost-acknowledgement recovery after process exit.
- Persistent simulated clock, delayed conversion outcomes, observed Brier score, realized value and cost accounting. Unobserved outcomes remain pending.
- Functional dashboard implementation for proposal, exclusion review, approval, execution/retry, failure injection, clock advancement, outcomes and audit. HTML/action and in-memory HTTP protocol tests passed; actual browser rendering/navigation remain unverified.
- Existing `AuditStore`, hashing, timestamps, manifests, `AgentResult`, `Phase`, `RunState` and JSON serialization reused directly. Existing worktree-coupled evaluator and coding orchestration were not invoked. A domain-specific SQLite state machine/evaluator handles campaign transactions.
- Architecture diagrams, README, mathematics/modelling/architecture learning guide, two-minute demo script and opt-in host browser check.

## Exact verification evidence

All commands ran from the target repository with `PYTHONPATH=src`. Machine-readable commands, exit codes, timings, log paths and SHA-256 hashes are in `docs/acquisition/verification/checks.json`.

| Command | Result |
|---|---|
| `.venv/bin/python -m pytest -q tests/acquisition tests/test_ledger.py tests/test_review_and_judge_validation.py` | Exit 0; **38 passed in 0.64s** (27 new AcquisitionOps tests and 11 selected existing tests). |
| `.venv/bin/ruff check src/agent_arena/acquisition tests/acquisition scripts/acquisition_browser_check.py` | Exit 0; all checks passed. Long literal lines in HTML/CSS and generated report prose have explicit E501-only exemptions; other rules apply. |
| `.venv/bin/mypy src/agent_arena/acquisition --follow-imports=silent` | Exit 0; no issues in six source files. Imported existing modules were not rechecked. |
| `git diff --check` | Exit 0. |
| `verify_manifest(Path('docs/acquisition/evaluation'))` through `.venv/bin/python -c` | Exit 0; 17 manifested artifacts verified. Full command in receipt. |
| `.venv/bin/python -m agent_arena.acquisition evaluate --output docs/acquisition/evaluation` | Exit 0; 12 frozen scenario/method rows. Reproduced in pytest with all non-temporal row values equal. |
| `.venv/bin/python -m agent_arena.acquisition demo --port 8765` | **Blocked:** `PermissionError: [Errno 1] Operation not permitted` at loopback socket bind. No browser success claimed; no permission bypass attempted. |

Coverage includes explicit approval gates; exclusion review; missing/contradictory records; truth-label isolation; note injection; integer budget checks; exhaustive optimizer oracle; duplicate event replay; concurrent event/approval races; changed plan rejection; unrelated database preservation; durable approval/restart; transient and permanent failure; retry cap; delayed outcome idempotency; HTML escaping; HTTP host/CSRF rejection; and the full HTTP action flow without a listener.

The process-crash test starts a normal Python test subprocess (not a coding worker), commits a mock receipt, exits with `os._exit(19)`, reopens SQLite and verifies exactly one delivery/charge per operation. Tests create only isolated synthetic DBs; no worktree-producing tests were run. The old full suite was intentionally not run because it includes worktree/orchestration cases outside this authorization.

## Evaluation results and interpretation

The evaluation is **SCRIPTED**, with no live agent inference. Greedy EV is compared with exact budget optimization, not with model intelligence. The six scenarios are normal, tight budget, insufficient budget, transient failure, lost acknowledgement and permanent failure; each uses both methods.

- Policy violations observed: **0 across 12 rows**.
- Valid completed campaigns: **8/12**. Expected terminal state: **12/12**. Two insufficient-budget and two exhausted-retry rows intentionally remain incomplete.
- Unapproved-execution negative control rejected in **12/12** rows.
- Predicted optimization regret: exact scripted method **0 cents** in every row; greedy baseline **480 cents** in each feasible row. This is against an exhaustive fixture oracle, not real-world lift.
- With the $24 budget, predicted expected net is $72.00 baseline and $76.80 scripted; this frozen outcome draw realizes $576.00 baseline and $276.00 scripted. The optimizer's higher expectation did **not** produce a better realized result in this draw. No performance superiority is claimed from this tiny sample.
- Live application model calls: **0**; model cost **$0**; mock tool fees **$0**. Simulated campaign spend is separately recorded per receipt. Scripted reviewer/approval events are labelled simulated interventions; actual human benchmark interventions are zero.
- Per-row measured local latency, EV estimation error against hidden synthetic truth, failures, attempts, Brier score, descriptive Wilson intervals and net-outcome standard deviation appear in `results.json`. Six correlated scenarios are not independent trials; no general reliability rate or causal effect is inferred.

## Repair count and usage

**One repair cycle used, zero remaining.** Initial new-suite result was 20 passed / 1 failed: the test wrongly expected a consultancy at a $12 budget, while two studios correctly yielded higher EV. The assertion was corrected against the economic calculation and exhaustive oracle. The same repair/verification pass completed type annotations, style checks, stronger restart/concurrency/HTTP tests and documentation. No second coding worker or external repair attempt was started.

Development CLI token totals and billed cost are **unknown in this worker turn**. The coordinator may attach the final authenticated CLI usage receipt after exit. Application evaluation cost zero must not be substituted for development cost.

## Remaining gaps and coordinator handoff

1. **Browser/listener blocker:** the sandbox prohibits binding `127.0.0.1:8765`. Run the one-command demo and optional `scripts/acquisition_browser_check.py` on a permitted host with an already installed Playwright/Chromium. The host script was linted, not browser-executed here. Missing dependencies or permissions remain blockers; no installation or bypass was attempted.
2. **Independent acceptance:** the checks above are worker evidence. The coordinator must inspect this diff/artifacts and rerun appropriate tests independently before acceptance. Do not infer success from this prose alone.
3. **Notifications:** this worker emitted ordinary CLI progress events. No Telegram connector was available; queued/launch/completion/blocked Telegram delivery was not performed or verified here. The coordinator owns notification to chat `6922930983` using the real metadata above.
4. Scores are hand-specified synthetic segment estimates, not a newly trained or empirically validated model. There is no live agent-assisted benchmark, no real customer data, no real delivery and no paid API route.
5. Local approval names are not authenticated identities. This is a single-machine demo, not a multi-tenant service or tamper-proof audit system. Financial controls are per campaign, not organization-wide budgets.
6. There is no campaign cancellation, contact-reservation release, invalid-record editing interface, scheduler, real-time outcome ingestion, causal uplift modelling or production provider adapter. New contradictory data blocks execution; recovery may require a new proposal excluding those records.
7. Mock receipts share SQLite with workflow state. Recovery evidence applies to this local mock, not exactly-once semantics of a remote delivery provider. Permanent failures deliberately remain failed after three attempts.

## Run commands

```sh
PYTHONPATH=src python3 -m agent_arena.acquisition demo
PYTHONPATH=src .venv/bin/python -m pytest -q tests/acquisition
PYTHONPATH=src python3 -m agent_arena.acquisition evaluate --output /tmp/acquisition-report-UNIQUE
```

Resume the dashboard with `--db` and its printed synthetic DB path. Output directories for evaluation must be new; existing databases and reports are never reset.

## Changed/new artifacts

The original untracked `.DS_Store` is excluded from this list. Existing code and queues are unchanged; README has an appended AcquisitionOps section.

- `LEARNING_GUIDE.md`
- `README.md`
- `RECOVERY_JOB_C_REPORT.md`
- `docs/acquisition/README.md`
- `docs/acquisition/architecture.md`
- `docs/acquisition/evaluation/events.jsonl`
- `docs/acquisition/evaluation/fixture.json`
- `docs/acquisition/evaluation/manifest.json`
- `docs/acquisition/evaluation/report.md`
- `docs/acquisition/evaluation/results.json`
- `docs/acquisition/evaluation/run.json`
- `docs/acquisition/evaluation/traces/lost_acknowledgement-baseline.json`
- `docs/acquisition/evaluation/traces/lost_acknowledgement-scripted-agent.json`
- `docs/acquisition/evaluation/traces/permanent_failure-baseline.json`
- `docs/acquisition/evaluation/traces/permanent_failure-scripted-agent.json`
- `docs/acquisition/evaluation/traces/standard-baseline.json`
- `docs/acquisition/evaluation/traces/standard-scripted-agent.json`
- `docs/acquisition/evaluation/traces/tight_budget-baseline.json`
- `docs/acquisition/evaluation/traces/tight_budget-scripted-agent.json`
- `docs/acquisition/evaluation/traces/too_small-baseline.json`
- `docs/acquisition/evaluation/traces/too_small-scripted-agent.json`
- `docs/acquisition/evaluation/traces/transient_failure-baseline.json`
- `docs/acquisition/evaluation/traces/transient_failure-scripted-agent.json`
- `docs/acquisition/interview-demo.md`
- `docs/acquisition/verification/checks.json`
- `docs/acquisition/verification/diff-check.log`
- `docs/acquisition/verification/lint.log`
- `docs/acquisition/verification/manifest.log`
- `docs/acquisition/verification/tests.log`
- `docs/acquisition/verification/types.log`
- `scripts/acquisition_browser_check.py`
- `src/agent_arena/acquisition/__init__.py`
- `src/agent_arena/acquisition/__main__.py`
- `src/agent_arena/acquisition/core.py`
- `src/agent_arena/acquisition/data.py`
- `src/agent_arena/acquisition/evaluation.py`
- `src/agent_arena/acquisition/prospects.json`
- `src/agent_arena/acquisition/web.py`
- `tests/acquisition/test_evaluation.py`
- `tests/acquisition/test_http.py`
- `tests/acquisition/test_workflow.py`
