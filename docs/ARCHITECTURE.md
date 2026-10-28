# Autobuild (Agent Workflow Orchestrator) — architecture explainer

*Written 28 October 2026 for sharing with another AI assistant or engineer. Everything
here describes code that exists and has been exercised live; nothing is aspirational.*

## What it is

A phone-operated autonomous software builder that runs on one Mac. The owner opens a
private web app, describes a change in plain language, and leaves. A director model
turns the request into an engineering specification, coding agents (OpenAI Codex CLI and
Anthropic Claude Code CLI, both on the owner's existing subscriptions — no API keys)
implement it in isolated workspaces, deterministic checks and an independent review run,
and verified work is integrated into the project's local `main`. The web UI is an
observation window and a decision surface, not a workflow the owner has to drive.

```
phone/Mac browser ──HTTPS (Tailscale Serve, tailnet-only)──▶ FastAPI controller (launchd login service)
                                                               │  SQLite ledger: projects, goals, tasks,
                                                               │  attempts, approvals, sessions, events
                                                               │  scheduler thread: claim → spawn guardian
                                                               ▼
                                            worker guardian (own process group, per attempt)
                                            ├─ clone staging → isolated workspace at a frozen base commit
                                            ├─ invoke `codex exec` or `claude -p` with a bounded prompt
                                            ├─ frozen checks in the macOS sandbox (sandbox-exec)
                                            ├─ scope validation, candidate commit, diff, result.json
                                            └─ transcripts/artifacts on disk (hash-manifested)
                                                               │
                                       independent review (other provider) → director evidence review
                                                               │
                                       composition checks on the staging branch → approval record
                                                               │
                                       policy: manual tap or auto → fast-forward of the source's local main
```

## Components (all under `src/agent_arena/control/`)

| Module | Role |
|---|---|
| `api.py` | FastAPI app. Device pairing + sessions, CSRF/Origin/Host guards, `GET /overview` snapshot, SSE `/events`, mutations (requests, goals, project policy, agents, control, task actions, approvals, devices), static UI. |
| `engine.py` | The scheduler. `tick()` reconciles attempts (alive / finished / orphaned), queues publications, claims the next eligible task atomically (SQLite transaction + lease token), spawns a guardian. `finish()` is the state machine for results (plan/replan/build/review/direct phases, repairs, retries, cooldowns, blocked). `integrate()` composes a verified candidate onto the project's staging branch and creates an approval. |
| `worker.py` | The guardian process (`python -m agent_arena.control.worker <home> <attempt>`). Registers its identity, builds the phase prompt, runs the CLI via a process-group runner, parses the stream, applies validated tool-free file operations for Claude, runs frozen checks, enforces scope/protected paths, commits the candidate, writes `result.json` atomically. |
| `routing.py` | Picks provider/model/effort per task from the Codex CLI's advertised model catalog, task complexity/risk, repair history and recorded outcomes. Director = Codex `gpt-6-astra` at `max`; a probe-validated Claude `claude-fable-5-1` at `max` stands in when no Codex slot is free. |
| `planning.py` | Pydantic schemas for the director specification (outcome, observations, acceptance, constraints, strategy single/parallel/sequential/clarify, tasks with scopes and dependencies) and bounded plans; dependency-graph validation; the director prompt and context builder. |
| `publication.py` | Exact-commit integration into the enrolled source's **local** `main`: intent + receipt files bridge a crash between git and SQLite; refuses dirty or diverged sources; fast-forward only; never pushes. |
| `recovery.py` | Orphan handling when a guardian dies: verifies the recorded CLI child by pid + create time + boot time before signalling it; bounded recoveries. |
| `store.py` | SQLite (WAL) with JSON documents per row, optimistic `rev`, idempotent command log, event stream, session table, pairing codes. |
| `remote.py`, `diagnostics.py`, `configuration.py`, `__main__.py` | Tailscale Serve setup, host preflight (`doctor`), origin validation, CLI (`start/stop/serve`, `install-service`, `pair`, `enroll-*`, `check-accounts --probe`, `backup`). |
| `web/` | React 19 + TypeScript + Vite UI: Overview, Projects, Build (director interpretation, dependency lanes, workers, checks, reviews, integration rail), Agents, Review. Dictation via the browser SpeechRecognition API. Service worker caches only the shell. |

## Lifecycle of one request

1. **Request** — `POST /projects/{id}/requests` with the owner's sentence creates a goal and a
   `plan` task flagged `director=True` (timeout 15 min). Acknowledged in ~0.1 s.
2. **Director (M0/M1)** — a worker runs Codex `gpt-6-astra` (max) read-only in a clone of the
   project with the repository context, recent commits, prior tasks/evidence and frozen
   checks. It returns a JSON specification; `validate_director_spec` enforces scope within
   the enrolled authority, acyclic dependencies and testable acceptance criteria. Child
   build tasks are created with fresh budgets. `clarify` strategy parks the request with one
   behaviour question for the owner.
3. **Build (M2)** — eligible tasks are claimed (one per account, scope-overlap guard, two
   writers max per project, capacity limit). Codex builds in a workspace-write sandbox;
   Claude returns whole-file operations the controller applies after path validation.
   Frozen checks run in the macOS sandbox; the candidate's bytes are fingerprinted before
   and after checks so nothing unverified can be committed.
4. **Review (M3)** — the other provider reviews the diff against the task in a fresh
   context and must return `{"approved": bool, "findings": [...]}`.
5. **Director evidence review** — the director judges diff + checks + review against the
   agreed outcome (it may only reject for a concrete defect in that evidence).
6. **Composition (M4)** — the candidate is applied to the project's staging branch in a
   worktree, checks rerun on the combined result, a staged commit is made and an approval
   record covers all staged tasks.
7. **Integration (M5)** — `manual`: the owner inspects the combined diff, accepts, then
   authorises integration; `auto_local_main`: the controller does both once every gate has
   passed. `publish()` fast-forwards the source's local `main` with intent/receipt
   recovery. Remote push is never automatic.
8. **Repair** — failed checks/review feed `repair_evidence` back; the director produces one
   corrective task (fresh build from base); budgets: 2 repairs, 2 retries, 2 recoveries;
   provider rate limits cool the account down and may switch providers.

## Reliability properties (tested)

- Atomic claiming with lease tokens; stale results are rejected by token/attempt id.
- Guardians run in their own session; the HTTP controller can restart under them and the
  restarted scheduler reconciles results without duplicate dispatch (proven live).
- Cancellation signals the owned process group and only reports "stopped" when the group
  is confirmed gone; otherwise the task is `interrupted` with the reason.
- Launchd `KeepAlive` service with `caffeinate -i`; uvicorn graceful-shutdown bounded so
  an open event stream cannot block a restart.
- Security: loopback-only listener; Tailscale Serve (never Funnel) provides TLS inside the
  tailnet; device pairing codes (30 min, one use, several may be outstanding); 30-day
  sliding HttpOnly/Secure/SameSite=Strict sessions with per-device revocation; CSRF token
  on mutations; Host/Origin allow-lists; CSP; secrets redacted from transcripts; workers
  get a scrubbed environment and cannot read agent config in enrolled repos.
- Everything is on disk: transcripts, prompts, invocation argv, checks, diffs, receipts,
  with SHA-256 manifests; the UI never renders fabricated progress.

## Boundaries and what the owner still decides

- Enrolment of a repository (manifest with `scope`, `checks`, `protected`, policy) happens
  on the Mac, never from the phone.
- Behaviour questions the director cannot infer, rejected work, remote publication,
  spending and anything outside the enrolled scope.
