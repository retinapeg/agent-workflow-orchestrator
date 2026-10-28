# Agent Workflow Orchestrator: mobile build control

Design and implementation brief | 28 October 2026

## Decision

Extend `retinapeg/agent-workflow-orchestrator` in place. Keep its Python execution,
Git safety, provider adapters, deterministic checks and audit records. Add a
persistent multi-agent scheduler and a mobile-first web interface. The UI can use
“Agent Arena / Build control”, following the existing package name; this is not a
new repository, framework or separate life assistant.

**Product outcome:** Leo submits an engineering goal, the system decomposes it
when necessary, assigns independent jobs across enrolled Codex/Claude accounts,
checks changes, performs bounded repairs, integrates into a staging branch and
presents verified work for approval. He can inspect, redirect, pause and approve
from his Android phone without maintaining terminal sessions.

**Optimise for accepted changes per unit of available compute and low human
intervention.** Do not optimise for agent count, token consumption, artificial
busyness or permanently occupied workers. This is a proposed architecture, not a
benchmark claim that it is universally fastest.

## Evidence and boundaries

Remote `main` inspected at `689da43d74462d0bc2e548ac7b1a275efb791378`.
The three local copies, unpushed `dc19aec`, additional overnight queues and
uncommitted AcquisitionOps material are reported by Leo's local Codex session;
they were not accessible from this design session. Recheck the remote ref before
implementing because it may move.

Observed in remote code:

- `batch.py` implements a sequential queue, calls `run_mode.main`, and normally
  stops at the first unverified task. It discovers output using task names and
  timestamps. Do not reuse that discovery scheme for concurrent jobs.
- `gui.py` holds active `Job` objects and `Popen` handles in an in-memory
  dictionary. Its queue board parses console log text. That is not the durable
  source of truth for a restartable scheduler.
- `process.py` already provides environment filtering, capped output capture,
  timeouts and process-group cancellation. Reuse these controls rather than
  replacing them with a permissive shell loop. Add streaming through a tested
  backward-compatible interface.
- `run_mode.py` already parses checked task specifications and records unknown
  token/cost values as null. Preserve that accounting discipline.
- `gui.py` deliberately accepts localhost hosts only. Remote access needs a
  deliberate authenticated API/allowed-host design, not removal of the checks.

Source files:
`https://github.com/retinapeg/agent-workflow-orchestrator/blob/689da43d74462d0bc2e548ac7b1a275efb791378/src/agent_arena/batch.py`
`https://github.com/retinapeg/agent-workflow-orchestrator/blob/689da43d74462d0bc2e548ac7b1a275efb791378/src/agent_arena/gui.py`
`https://github.com/retinapeg/agent-workflow-orchestrator/blob/689da43d74462d0bc2e548ac7b1a275efb791378/src/agent_arena/process.py`
`https://github.com/retinapeg/agent-workflow-orchestrator/blob/689da43d74462d0bc2e548ac7b1a275efb791378/src/agent_arena/run_mode.py`

## Non-goals

No LeoOS memory ingestion, email triage, job-application sending, Agent Exchange
matching, AcquisitionOps product expansion, new research benchmarks, or redesign
of unrelated repositories. No autonomous self-modification of the running
controller. No global dotfile edits, broad trusted directories or config surgery.
No Kubernetes, Redis, Celery, vector database, separate orchestration framework,
cloud-only execution or mandatory paid API use for this first version.

Preserve historical contest mode and public research claims, but do not make two
agents implement every ordinary task. Competition is an explicitly selected
experiment, not the default build strategy.

## Architecture: one local application, bounded child processes

Phone PWA -> private HTTPS access -> Python control service
                                      | persistent state/events
                                      v
                                    SQLite
                                      |
                              deterministic scheduler
                                      |
                      bounded Codex / Claude subprocesses
                                      |
                         isolated per-attempt workspaces
                                      |
                   checks -> review -> bounded repair -> staging
                                      |
                          evidence -> approval -> publication

### Concrete stack

- **Backend:** Python in the existing `agent_arena` package. FastAPI for typed
  control endpoints, one server worker, one explicit scheduler owner. Reuse
  synchronous runner code via a bounded executor so it does not block HTTP.
- **State:** SQLite in WAL mode on the Mac's local filesystem. Short transactions,
  foreign keys, busy timeout, migrations, indexes and online-backup support.
  SQLite has one writer at a time; never hold a transaction during an LLM call,
  process wait, Git operation or test run. No network-synced database files.
- **Frontend:** React + TypeScript + Vite, in `web/` within this repository. Build
  static assets served by Python; Node is a build dependency, not another
  production daemon. No Next.js server or second source of scheduling truth.
- **Updates:** server-sent events (SSE) for server-to-phone events, ordinary
  authenticated HTTP commands in the other direction. Persist event IDs,
  replay from Last-Event-ID, deduplicate, and fall back to a fresh snapshot when
  an event cursor is too old. Closing the page never stops execution.
- **Host lifecycle:** installable, reversible macOS launchd service. Show service
  version, scheduler heartbeat and last event. Do not run multiple schedulers
  through web-server reload or multi-worker configuration.
- **Remote path:** Tailscale Serve, private tailnet only, HTTPS to a localhost
  service. No public Funnel, public tunnel, unauthenticated terminal endpoint or
  browser exposure of provider credentials.

Dependency choices should be pinned to versions actually installed/tested.
Do not silently upgrade working global CLI installations.

## Intelligent allocation, not a permanent manager-agent conversation

### Work model

Project -> Goal -> Task/dependency graph -> Attempt -> Evidence/approval.

A goal contains a plain-language outcome and completion criteria. A task contains
scope, dependencies, read/write ownership, expected outputs, acceptance checks,
review policy, priority, resource limits and permissions. An attempt records the
exact provider/account, CLI version, model/effort, base SHA, worktree, session ID,
limits, process identity, start/end times and result.

Routine explicit tasks skip planning. An ambiguous multi-part goal gets one
bounded planner invocation producing schema-validated tasks, normally no more
than eight. Validate no cycles, no out-of-project paths, no authority expansion
and testable completion criteria. Freeze shared interfaces before independent
work starts. The planner may propose wider scope but cannot approve it.

Only invoke a planner again for a concrete dependency failure or a material user
change, with a bounded replanning budget. Heartbeats, queue selection, status,
backoff and progress bookkeeping use ordinary code and consume no model calls.

### Scheduling policy

A job is eligible only when dependencies are satisfied, the project is enabled,
permissions cover it, an appropriate account is healthy, host capacity is
available and its repository/path leases do not conflict.

Select eligible work in this order:
1. Repairs/reviews/integration that unblock already-started deliverables.
2. User priority, explicit deadline urgency and dependency critical-path impact.
3. Age and weighted project fairness, so one project cannot starve the others.
4. Prefer a healthy capable worker with the least unnecessary context transfer.

Use understandable reasons, e.g. “Claude slot B selected: available; reviewer
must differ from author; this review unblocks the next integration.” Do not
invent model intelligence scores or use unmeasured speed rankings.

Initial operating profile: maximum **three concurrent model invocations**,
maximum one per enrolled account until local smoke tests establish support,
maximum two simultaneous writers per project with declared non-overlapping
scopes, one integration operation per project. All limits are UI-configurable
within supported account and host constraints. These are starting policy choices,
not claims about provider limits or the user's hardware capacity.

A reviewer/planner consumes a normal worker slot; it is not an invisible fourth
agent. Reduce dispatch under host memory/CPU pressure. Test/build processes have
separate host resource limits. Do not kill useful running work merely to hit a
utilisation target.

Use one implementation plus checks for low-risk changes. Use an independent
fresh-context review for normal code changes; prefer another provider when
available, not when doing so violates capacity or priority constraints. Require
review and explicit authority for security, deployment or permissions changes.
Speculative duplicate implementations are off by default.

### Accounts and compute

Accounts are explicit named execution slots, not hardcoded provider counts. Let
Leo enrol any/all of his three accounts after validating the installed CLI's
supported authentication/config-isolation behaviour. Show each account's enabled
state, active job, last auth/health check, cooldown and reported usage.

Use supported local Codex and Claude Code CLIs with subscription login where
supported. Do not extract OAuth tokens for a custom API client, mutate global
login files, share credentials between jobs or silently switch billing modes.
Codex documents CODEX_HOME. Claude documents CLAUDE_CONFIG_DIR for config/history,
but that alone is not proof of independent macOS keychain/auth isolation: test
with a harmless call and refuse a colliding setup. A second account that cannot
be isolated stays disabled with a concrete setup instruction, not fake “ready”.

Support all valid enrolled slots, but never rotate identities to evade a
provider restriction. Respect each account's limits. Treat quota/reset fields
as null unless a supported surface actually provides them. Token usage and an
API-equivalent estimate are not subscription quota or an invoice.

No paid API fallback unless explicitly enabled with its own budget. Missing
subscription credentials should produce `needs_auth`, not an API bill.

## Execution, integration and restart guarantees

### Task lifecycle

`draft -> ready -> leased -> running -> verifying -> reviewing -> integrating -> ready_for_approval -> completed`

Additional states: `waiting_dependency`, `waiting_capacity`, `cooldown`,
`needs_auth`, `needs_permission`, `repair_queued`, `interrupted`, `recovering`,
`blocked`, `cancelled`, `failed`. Keep task state separate from each attempt's
state. A human-readable reason accompanies every wait/failure.

Persist the lease and attempt ID before spawning. Return/pass that explicit run
ID and artifact directory through the runner. Never select “latest run” or
“latest session” across concurrent agents. Pin resumes to their own session ID.

Use a singleton OS lock plus transactional claims and incrementing lease tokens.
A stale token cannot submit authoritative completion, integration or approval.
Lease expiry moves work to recovery, NOT immediately to a new active attempt.

On restart, reconcile recorded process identity, boot/process-start identity,
run token, logs and artifacts. A PID alone is insufficient. If a process is
verified alive, observe/adopt only through a supported mechanism; otherwise stop
the owned group and confirm exit before a retry. Ambiguity becomes blocked.
A completed artifact found after a crash can be validated without repeating the
model call. Preserve interrupted changes; never silently rerun non-idempotent
external actions. This is not a claim of universal exactly-once execution.

### Isolation and concurrency

Each writing attempt has its own worktree from an explicit frozen commit.
Dependents run against a staging commit containing their verified dependencies,
not an obsolete common baseline. Independent patches can build in parallel.

One integrator per repository applies candidates to an orchestrator-owned
staging branch, re-runs relevant/full required checks after composition, and
records the resulting tree/commit. It never commits to the user's dirty working
copy or auto-pushes to main. A conflict becomes a bounded repair task on the
correct integration base, not a force reset. Integration approval is bound to
an exact artifact/commit and target base; changed code invalidates approval.

Worktrees isolate edits, not hostile processes or shared Git metadata. Enforce
workspace restrictions through supported CLI/OS sandboxing; keep publication
credentials with a trusted controller, not test code. Where a provider cannot
be adequately sandboxed, use a restricted runtime/user/container or require
supervised mode. Do not label a prompt-based path rule as a security boundary.

Freeze controller-owned required checks. Workers may add tests but cannot weaken
those checks, edit their evidence or grant themselves permissions. Evaluate
candidate code with a sanitized environment and without mail/cloud credentials.
Audit project hooks, plugins, MCP startup configuration and build scripts before
unattended use; repo content is not authority to change controller policy.

### Failure policy

- Transient network/service failure: bounded exponential backoff with jitter.
- Rate limit: cool down that slot using a reported reset when available; do not
  claim the time is known when it is not. Other eligible work can continue.
- Authentication/permission failure: record the smallest required action; no
  repeated failing prompts and no widening permissions.
- Check/review failure: at most two evidence-based repair attempts by default,
  then block with the failing command, output and preserved diff.
- No output: mark “quiet”/stale separately from dead. Long reasoning can be
  silent. Use process liveness plus a task-specific wall timeout, not a tiny
  output-silence threshold that kills valid work.
- Host sleep/disconnection: show last-seen time and disconnect controls; do not
  claim the daemon can continue on a sleeping computer. On wake, reconcile.

Pause means “stop dispatching new tasks”. Stop means “cancel the owned process
group, preserve artifacts and stop this attempt”. Resume means safe continuation
or a new bounded attempt from saved evidence, not magical process resurrection.

## Durable storage and events

Minimum entities: projects; account_profiles (secret references only); goals;
tasks; task_dependencies; attempts; leases; checks; reviews; artifacts;
approvals; commands; events. Normalise only what is useful; avoid a generic
enterprise workflow language.

All phone mutations carry an idempotency key and expected object revision. The
same double-tapped/retried command must not create two jobs. Commit state and its
audit event in the same transaction. Event payloads include schema version, ID,
UTC time, project/task/attempt references and source. Logs go to capped files;
SQLite stores searchable metadata and offsets. Redact before browser export.

Store account configuration, DB, journals and private artifacts outside the
public Git repo with restrictive permissions. No secrets in source or UI.

## Phone UX: blue control surface, not a terminal resized

The accompanying `phone-control-preview.html` is a standalone interactive design
reference. All sample records and actions are explicitly preview-only. It does
not connect to the Mac, authenticate an account or start a build. Port the visual
system into `web/`; do not introduce a second production app for the preview.

### Visual system

Deep navy surfaces; restrained electric/ice-blue accents; high-contrast text;
quiet borders; generous spacing; simple system typography. Minimum 44px touch
targets; visible keyboard focus; status communicated with text as well as colour;
respect reduced motion and mobile safe areas. Avoid animated orbs, particle
fields, fake terminal feeds, unreadable mini-charts and a desktop kanban scaled
to 20% on a phone.

### Four primary destinations

**Overview:** host connection/last-seen, running/ready/attention counts, current
priority, one prominent New goal action, worker status and recent verified work.
A plain-language explanation of what is moving and what is blocked comes before
logs. Keep critical controls reachable near the thumb.

**Projects:** priority, active/paused, authorised repo, acceptance progress, latest
verified change and next blocker. Detail contains goals, task list/dependencies,
checks, artifacts and project policy. Reorder priorities without dragging on a
small screen. Adding a project is explicit enrollment, not automatic permission
to scan/edit everything under HOME.

**Agents:** enrolment/health, provider/account labels, assigned task and phase,
model/effort, cooldown, observed usage and quota freshness. Enable/disable slots
and set a safe concurrency ceiling. No manufactured utilisation/remaining-quota
percentages.

**Review:** exact change summary and diff, independent review, required checks,
base/candidate SHA and risks. Actions: approve the specified operation, request a
specific change, or reject. “Checks passed” and “approved for publication” are
different statuses. Hide an unavailable action rather than suggesting it worked.

### Interaction contract

New goal: choose enrolled project -> type/dictate with the phone keyboard ->
show proposed scope/checks -> run within pre-authorised policy. Reuse the OS
keyboard's dictation first, rather than building a voice stack. A custom voice
feature is optional and must not delay the build engine.

Project steering: append an instruction to the correct goal/attempt. For a CLI
that cannot safely accept mid-turn input, clearly queue it for the next safe
boundary or offer an explicit interrupt/replan. Do not silently restart.

Do not ask Leo about ordinary file edits/tests within approved scope. Request
approval for publication/main merges, deployment, destructive operations,
credentials, paid API spending or additional repositories. Low-risk changes can
advance through staging automatically while those approvals wait.

The PWA caches only the application shell. Do not service-worker-cache sensitive
logs/API responses or replay write commands invisibly after reconnection. Stale
views are labelled; disabled writes resume only after server revalidation.

### Authentication

Bind the backend to loopback. Tailscale Serve provides private HTTPS access;
configure tailnet access for Leo's devices. Add device pairing/application
sessions, HttpOnly/Secure/SameSite cookies, CSRF/Origin protection, exact allowed
hosts, rate-limited pairing and session revocation. Do not rely on the old
`X-Arena: 1` header as authentication. Trust proxy identity headers only from a
verified local proxy, not arbitrary client values. Verify denied access paths.

## API shape (proposed, not existing commands)

- GET `/api/v1/overview`, `/projects`, `/agents`, `/events`
- POST `/api/v1/projects` (explicit enrollment)
- POST `/api/v1/projects/{id}/goals`
- POST `/api/v1/projects/{id}/pause` or `/resume`
- POST `/api/v1/tasks/{id}/instructions`, `/cancel`, `/retry`
- GET `/api/v1/attempts/{id}`, `/checks`, `/diff`, `/artifacts`
- POST `/api/v1/approvals/{id}/decision`
- POST `/api/v1/control/pause-dispatch` and `/stop-all`

No arbitrary browser-supplied shell endpoint. Control operations are typed and
scoped. User prompts are data passed to provider calls, not shell interpolation.

## Implementation order and acceptance

### 0. Preserve and consolidate once

Use the local audit already in progress. Preserve each unique branch/commit,
tracked diff and untracked work in a private, verified backup. Do not indiscriminately
`git add -A`: untracked files can contain credentials. Keep all three copies until
backup validation is recorded. Start an integration branch from current remote
main, carry over unique local work deliberately, retain the Anthropic API fix
and queues. AcquisitionOps is preserved, not made the product's centre.

Choose a canonical local path and record it. No destructive reset, force push,
blind stash/pop, global cleanup or edits outside authorised paths. Stop only for
an actual unresolved destructive conflict or missing authority, not for normal
engineering decisions. Keep this to one bounded reconciliation, not another
open-ended architecture audit.

### 1. Ship one vertical slice

A phone-sized screen lists one explicitly enrolled sandbox project. Submitting a
goal persists a task, launches one real CLI worker, streams evidence and finishes
with a trustworthy check result. Closing/reopening the page preserves the state.
Only use a harmless disposable test project for integration testing, not an
unrelated real repository. Local launch must work before remote provisioning.

### 2. Prove useful concurrency and recovery

Two independent jobs run concurrently, a third dependent job waits and then runs
against the correct verified base. Exercise all three eligible account slots
where actually available; do not claim them tested when auth is missing.
Implement leases, scoped worktrees, integration serialization and bounded
failure policy. Inject daemon crashes, stale completions, rate limits and
cancellation. Add evidence-linked independent review.

### 3. Finish mobile control and service lifecycle

Make all four screens work against real backend data. Add private remote access,
authenticated pairing, service install/uninstall, project/worker policy controls,
and honest disconnected/empty/error states. Wire frontend and backend early;
polish the same working app, not a disposable replacement.

### Required evidence before claiming finished

1. Legacy offline tests run and actual results recorded; do not repeat the README
   test count as a fresh result.
2. Concurrent jobs cannot write the same working tree; conflicting scopes wait.
3. Double-click/retry of a command does not duplicate a goal or live job.
4. Controller crash before spawn, after spawn and before result commit recovers
   without concurrent duplicate work or accepting stale completion.
5. Limits/cooldowns are respected; an independent project can still progress.
6. Integration checks run on combined changes, not only on isolated patches.
7. Required checks cannot be disabled by the candidate; unknown is not pass.
8. Stop terminates owned descendants; retry budgets stop repeated failures.
9. Lost/rotated credentials never trigger paid API fallback or leak to logs.
10. Phone workflow at 390px and desktop workflow are browser-tested, with no
    horizontal page overflow, usable focus/dialogs and no dead controls.
11. Remote unauthenticated/CSRF/stale-approval requests are rejected. Reconnect
    cannot replay an old write or approve a changed diff.
12. A real CLI run produces preserved diff, checks and artifacts. Fake providers
    cover fault tests but are not evidence of real provider integration.

Report measured end-to-end times, model calls, retries, verified changes and
human interruptions. Compare serial and parallel execution on the same small
representative workload when practical, recording provider variability. Do not
promise N agents yield N-times speed.

## Instructions to the implementation agent

You are already auditing the correct repo. Move from inspection into implementation
using this brief. Make routine engineering choices yourself within these bounds.
Keep a short `BUILD_STATE.md` with canonical path, preserved local work, current
milestone, exact test commands/results, local URL and concrete blockers. Keep
changes on a reviewable integration branch. Do not publish or deploy without
approval. Do not modify global CLI configuration.

Finish with the exact tested start/stop commands and local URL, private phone URL
only when configured and tested, screenshots of the actual app, test evidence,
what real accounts were exercised and what remains blocked. Do not call the
system complete because a frontend builds or a simulated provider test passes.

## Implementation reference notes, checked 28 October 2026

- Codex supports `codex exec`, JSONL output, explicit sandboxing and session-ID
  resume. Adapt to installed `--help`; do not hardcode removed flags or assume
  every CLI version supports the latest docs.
  `https://learn.chatgpt.com/docs/non-interactive-mode`
- Codex config/state can be scoped using CODEX_HOME; directory must exist.
  `https://learn.chatgpt.com/docs/config-file/environment-variables`
- Claude supports `claude -p` and structured/streaming output. Its documented
  `--bare` mode skips subscription login and requires API/provider credentials;
  do not choose it merely for speed in this subscription-first design. Headless
  mode can load repo hooks/MCP settings, so inspect and constrain those.
  `https://code.claude.com/docs/en/headless`
- CLAUDE_CONFIG_DIR controls Claude's config/history location; verify auth
  isolation separately on the actual macOS setup.
  `https://code.claude.com/docs/en/settings`
- SQLite WAL supports readers alongside a writer, but only one writer at a time
  and a local shared filesystem.
  `https://sqlite.org/wal.html`
- Tailscale Serve publishes a local service privately to the tailnet; Funnel is
  the separate public-sharing facility. The host must remain online.
  `https://tailscale.com/docs/features/tailscale-serve`
  `https://tailscale.com/docs/reference/tailscale-cli/serve`
