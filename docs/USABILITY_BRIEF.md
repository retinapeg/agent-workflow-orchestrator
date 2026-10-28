# Arena usability release — engineering brief (28 Oct 2026)

This file is the single brief for the "usable Arena" work. It is written so that it can be
handed verbatim to a Claude Code session (`claude` at the repository root, model `max`,
spawn as many subagents as useful) **or** executed by the lead agent's own subagents. The
contract section is binding for every stream; the stream prompts are self-contained.

## Diagnosis (measured on the live state, 390 px Android-emulated viewport)

| Symptom Leo reported | Root cause found |
|---|---|
| "Slow" | `GET /api/v1/overview` is **1,088,091 bytes** on the real state and the phone fetches it every 5 s *and* on every SSE event. `tasks.specification` (188 KB), `tasks.instructions` (60 KB), `goals.specification` (102 KB) and `attempts.result` (556 KB: `plan`, `summary`, check output, review text) ride along on every poll. |
| "Old builds clutter the interface" | Four disposable proof snapshots (`Orchestrator live proof`, `… parallel`, `… parallel2`, `… final`, repos under `.arena-control/proof-source*`) are enrolled projects: they fill Projects, the request picker, Build history and the Home list. There is no archive. |
| "Can't see what the agents are doing" | Build → *Live output* only filters the **last 30 global events**, so any build older than a few minutes shows *"No events recorded for this request yet."* even when it is integrated. While an attempt runs, the worker streams the CLI transcript to `attempts/<id>/transcript.jsonl` but nothing reads it back to the phone. |
| "Live builds are hard to find" | Home opens on a full-screen legacy form (Existing/New, Build/Fix/Improve, mic hero, chips, ChatGPT note); *Active builds* is one screen below the fold. The Builds tab picks the first request, not the live one; its picker labels are the raw spec text (`"Integrated · Arena Smoke 20261028 · OUTCOME: …"`); *Details* is expanded by default (page is 11,222 px tall); *Stop* is shown on a completed build. |
| "Conversational experience not usable" | Director turns take 11–70 s (`gpt-6.1-sol`) with a generic "thinking" state; the director's context has no live Arena status unless a project is selected; "Build started" bubble sends the owner to the Build tab rather than to *that* build. |

Everything else (worker engine, SQLite store, pairing/CSRF, launchd service, Tailscale, activation/rollback, NXT Blue visual system) works and is preserved untouched.

## Binding API contract (backend implements, frontend consumes)

1. **Slim snapshot** — `GET /api/v1/overview` keeps its shape but:
   - `tasks[]`: drop `specification`, `repair_evidence`, `instructions_pending` text (keep `instructions_pending_count`); `instructions` truncated to 400 chars + `instructions_truncated: true`; `reason` to 300 chars. Everything else unchanged.
   - `goals[]`: drop `specification`; `request` truncated to 300 chars; add `display_title` (see 5).
   - `attempts[]`: drop `workspace`, `artifact_dir`, `identity`; `result` keeps `status, reason, seconds, cost, usage, invocation, provider_started, provider_ended, attempt_id, token`, `checks[]` as `{name,status,seconds}` only (no output), `review` as `{approved, summary≤600, findings[]≤10×300}`, `summary` ≤ 600 chars, `artifacts` as a list of names; drops `plan`, `session_ids`. `routing` keeps `provider, model, effort, action, reason≤200`.
   - Target: < 150 KB on the current real state (was 1.09 MB). Test asserts < 200 KB on a fixture with 40 attempts carrying 20 KB results.
2. **Detail endpoints** (full records, fetched on demand):
   - `GET /api/v1/goals/{id}` → `{goal, tasks[full], attempts[full result], events[last 200 touching this goal/its tasks/their attempts, oldest first]}`.
   - `GET /api/v1/goals/{id}/events?after=<event id>` → `{events: [...], cursor}` for incremental polling.
   - `GET /api/v1/tasks/{id}` → full task.
3. **Live attempt tail** — `GET /api/v1/attempts/{id}/live` → `{state, phase, model, provider, started, elapsed, bytes, done: bool, lines: [{t: float|null, kind: 'assistant'|'tool'|'command'|'file'|'test'|'error'|'system', text ≤ 300}]}` built from the **last 64 KB** of `transcript.jsonl` (Codex JSON-lines or Claude stream-json), last 60 meaningful lines, redacted with the existing `redact()`. Works for finished attempts too. Never raises on a partial/corrupt trailing line.
4. **Project archive** — `POST /api/v1/projects/{id}/archive` and `/unarchive` (body `{revision}`) set `project.archived`. Archived projects: still in `overview.projects` with `archived: true`; rejected by `POST …/requests` and by auto-resolution (409 "Project is archived"); excluded from the director's enrolled-project catalog and from the scheduler's dispatch. One-time migration on store open (meta key `migration.archive_proof_projects`): projects whose `repo` is under the control home (`proof-source*`) are archived. Reversible from the UI.
5. **Titles** — `display_title(text)`: strip leading `OUTCOME:`/`Outcome:`/`Request:` labels and markdown, first sentence, ≤ 72 chars, ellipsis. Exposed on goals (`display_title`) and used for new goal titles in `engine.add_goal`.
6. **Director context** — `_context()` always includes `arena_status`: `{running: [{project, title, phase, agent, model, elapsed_seconds}], needs_you: [{project, title, why}], recent: [{project, title, outcome, when}][:5], paused}` so "what is running right now?" is answered from facts whether or not a project is selected. Chat `conversation.*` events unchanged.
7. **Snapshot stays cheap**: `snapshot()` must not read attempt artifact files.

## Stream A — backend (Python, `src/agent_arena/control/`, tests in `tests/control/`)

Implement contract items 1–7 with tests (`tests/control/test_usability_api.py` + extend `test_conversation.py` for `arena_status`). Rules: no new dependencies; keep `ruff` clean and `mypy`-compatible style; do not touch `worker.py` run loop, `engine.py` claim/verify transactions, `activation.py`, auth or CSRF; `store.py` may gain the migration hook only. Stream parsing: reuse `parse_codex_stream`/`parse_claude_stream` shapes from `run_mode.py` for the vocabulary of event types, but the live tail must parse line-by-line and tolerate truncation. Run `.venv/bin/python -m pytest -q tests/control` and `ruff check src tests` before finishing.

## Stream B — frontend (TypeScript/React, `web/src/`, NXT Blue preserved)

1. `App.tsx`: coalesce snapshot refreshes (≤ 1 per 750 ms, trailing); global **Needs you** band under the top bar when `needsYou(data).count > 0` (one line, tap → opens the relevant build/review); keep tabs/order; `openGoal(id)` selects and scrolls.
2. **Home (`Overview.tsx`)**: status first. Order: Needs-you cards → Active builds (live card: project, `display_title`, stage bar, current agent + model, elapsed, last live line) → one `New request` button → Recent requests (archived projects hidden; `display_title`) → Activity log collapsed. Remove the hero form, mode toggles, chips and the ChatGPT line from the page. The `goal` dialog becomes the only request form: project select (non-archived, default "Let Arena decide" or the last used), one textarea with the existing mic button, one `Start building` button. Share-to-Arena handoff pre-fills that textarea and opens the dialog.
3. **Builds (`Build.tsx`)**: default selection = live build, else needs-you, else newest; picker shows `state · display_title · project`; *Details (director's plan…)* collapsed by default and loads `GET goals/{id}` on open; *Stop* / *Message agents* only when a task is stoppable/live; **Live output** = `goals/{id}/events` (last 40, oldest first) and, while an attempt is live, `attempts/{id}/live` polled every 2.5 s rendered as agent lines (`[hh:mm:ss] Codex · editing src/pomodoro/cli.py`), with elapsed and bytes; finished builds show their recorded events, never "No events recorded".
4. **Projects (`Projects.tsx`)**: archived projects in a collapsed `Archived (n)` section with *Unarchive*; `Archive` in the card actions; archived projects excluded from every project picker; Builds history hides archived projects' goals behind a `Show archived` toggle.
5. **Chat (`Chat.tsx`)**: thinking state shows elapsed seconds and what the director is doing (`Reading Arena Smoke 20261028…`); the *Build started* bubble has `Watch the build` → `openGoal(goal_id)`; the project chip hides archived projects.
6. `types.ts`: add `display_title`, `archived`, `LiveTail`, `GoalDetail` types; keep `needsYou`, `goalClass` logic.
Rules: no new packages; `npm run build` must pass (`tsc` strict); no horizontal overflow at 360/390 px; keep every existing accessible name used by `web/ui-acceptance.mjs`, `web/live-chat-check.mjs` and `tests/control/ui_server.py` working (`Overview`, `New request`, `What would you like to change?`, `Build this for me` may be renamed only if those scripts are updated in the same change).

## Stream C — acceptance (after A and B)

`web/usability-acceptance.mjs`: starts the real-state fixture (`tests/control/real_state_server.py`, which serves a copy of a control SQLite snapshot with `run_scheduler=False`), pairs, asserts: overview < 200 KB; Builds opens on the live/needs/newest request; archived projects absent from pickers and Home; Live output non-empty for an integrated build; no overflow on any tab at 390 px (Android UA); screenshots to `docs/build-evidence/usability/`. Then independent review of the combined diff, `ruff`, full `pytest`, `npm run build`, `node web/ui-acceptance.mjs`.

## Release

Commit on `main`, activate on the Mac with the existing activator (health-checked, auto-rollback):
`.venv/bin/python -m agent_arena.control.activation --home "$PWD/.arena-control" activate <sha> --port 8791`, then one Android glance by Leo.
