# Round 3 — personal engineering director: binding contract

Owner's brief: `docs/ROUND3_BRIEF.md`. Visual reference: `docs/images/round3-reference.png`
(three phone screens: Home · Build overview · Block detail). Design rule, from the owner:
**Arena never makes the owner understand its machinery before using it.** Default views are
plain English. Code, terminal output, git hashes and file names appear only under *Advanced*.
Nothing is fabricated: when the data does not exist, the UI says so.

Preserve: worker engine, store transactions, auth/CSRF/pairing, activation, Tailscale access,
NXT Blue tokens and components, everything Round 1/2 delivered. No new packages or frameworks.

## A. Backend (Python, `src/agent_arena/control/`, tests in `tests/control/test_round3.py`)

### A1. Build (goal) archive — reversible, never destructive
- `POST /api/v1/goals/archive` `{ids: [goal_id]}` → `{archived: [ids], skipped: [{id, reason}]}`.
  A goal with any task ACTIVE, QUEUED, `integrating` or `awaiting_input` is **skipped**
  (`reason: "still running"`); unknown ids → `"not found"`. Sets `archived: true`,
  `archived_at` in the goal's data. Emits `goal.archived`.
- `POST /api/v1/goals/unarchive` `{ids}` → `{restored: [ids]}`; emits `goal.restored`.
- Both go through the existing `command()` idempotency and CSRF middleware. Nothing is deleted:
  tasks, attempts, transcripts, commits and evidence are untouched.
- Snapshot `goals[]` carries `archived` (bool) and `archived_at`.
- Project archive (existing) gains the same guard: 409 `"This project has a build in progress"`
  when the project has ACTIVE/QUEUED/integrating tasks.

### A2. Plain-English story — `story.py` (pure functions over recorded data) + endpoints
`GET /api/v1/goals/{id}/story`:
```
{goal_id, title, project:{id,name}|null, status, status_label, started, updated,
 agents_working, progress:{done,total}, summary, blocks:[Block], failure:Failure|null,
 needs_you:[{kind:'answer'|'approve'|'sign_in'|'permission', text, task_id}]}
status ∈ running | needs_you | queued | completed | blocked | cancelled
Block = {id, title, purpose, connects:[{id,title,relation:'needs'|'unblocks'}],
 agent:{provider,model,name}|null, stage:{index,label}, state, state_label,
 now:string|null, last_activity:float|null, stalled:bool, updated}
state ∈ running | queued | waiting | needs_you | completed | blocked | cancelled
state_label ∈ Planning | Implementing | Testing | Reviewing | Integrating | Waiting | Queued |
 Done | Blocked | Needs you | Stopped
```
- Blocks are the goal's work tasks in dependency (topological) order, then by creation; the
  director's plan task appears as a single "Planning" block only while planning is in progress
  or when it is the goal's only task.
- `title`: `display_title(task title)`, file names/paths removed.
- `purpose`: the task's director-written `purpose` (A5) when present, else
  `"Part of: <first sentence of the goal outcome>"`.
- `now` (only while an attempt is live): plain English from the newest meaningful live-tail line
  via `describe_line()`; `last_activity` = transcript mtime; `stalled` = live and no transcript
  write for ≥ 600 s.
- `describe_line(line)` (unit-tested): test → "Running the tests"; command → git… "Checking the
  project history"; rg/grep/find/ls/cat/sed/head/tail → "Reading the code"; npm/pnpm/pip/uv
  install → "Installing dependencies"; npm run build/tsc/vite → "Building the app";
  python/node script → "Running a script"; other → "Running a command"; file (edit/write/patch)
  → "Editing code"; assistant → the agent's own first sentence with code spans, paths (tokens
  containing `/` or matching `\w+\.\w{1,5}`), and hex ≥ 7 chars removed, ≤ 140 chars;
  error → "Hit an error and is working around it"; system → skipped.

`GET /api/v1/tasks/{id}/story`:
```
{block:Block, what, why, connects_text, stage_rail:[{label,state:'done'|'current'|'todo'|'failed'}],
 updates:[{t,text,tone}], now, decisions:[string], failure:Failure|null,
 advanced:{attempts:[{id,phase,provider,model,state,seconds}], checks:[{name,status}],
           candidate, integrated_commit, scope:[string]}}
```
- `stage_rail` labels exactly: Design · Implement · Test · Review · Merge (from `stageIndex`
  semantics: plan → Design, build → Implement, verify → Test, review/direct → Review,
  approve/integrate → Merge).
- `updates` (≤ 20, newest first) come only from recorded events/attempt outcomes, phrased:
  running "Started building" · verifying "Running the project's checks" · reviewing "Under
  independent review" · ready_for_approval "Verified and staged" · integrating "Merging" ·
  completed "Finished" · blocked "Stopped: <plain reason>" · repair_queued "Fixing what the
  review found" · cooldown "Waiting for an agent to become available" · attempt passed/failed
  per phase ("Checks passed", "Review approved", "Review asked for changes") · provider switch
  "Handed to <Codex|Claude> after a failure" · director.replanned "Director adjusted the plan" ·
  integration.integrated "Merged into main".
- `decisions`: director replans and review/director verdict summaries, plain English, ≤ 5.

`Failure = {what_failed, intended, recoverable, tried:[string], next, needs_you, action}`,
`action ∈ retry | answer | sign_in | permission | none`, built only from recorded state, reason,
repairs, retries, routing events, review findings and check statuses. Known technical reasons
are mapped to plain English (timeout; checks failed; review rejected; director rejected;
needs_auth; needs_permission; rate limited/cooldown; model unavailable; interrupted; no
eligible agent). Unrecognised reasons → "Stopped with an error Arena could not classify" and the
raw reason only in `advanced`.

### A3. Read-only Mac activity discovery — `discovery.py`
`GET /api/v1/mac-activity` (authenticated by the existing middleware; never part of the
snapshot; result cached 10 s in memory). Uses **psutil only** (already a dependency). Only the
current user's processes. Reads: name, first 6 cmdline args (redacted with `redact()`), cwd
(AccessDenied → null), create_time, listening ports of dev servers when psutil allows.
Never reads environment variables, never sends signals, attaches or writes anything.
```
{generated, groups:{managed:[Activity], external:[Activity], unknown:[Activity]},
 summary:[string], limitations:[string]}
Activity = {kind, label, repo, project, pid, started, last_activity, headless, ports:[int],
 evidence:[string], arena:{goal_id,task_id,attempt_id}|null}
kind ∈ claude | codex | arena_build | arena_director | agent | dev_server | tests
```
- Claude Code: executable basename `claude` or cmdline containing `claude-code`; Codex:
  basename `codex` or `@openai/codex`; other agents: aider, gemini, opencode, goose, cursor-agent.
  Dev servers: vite, next dev, webpack serve, `npm|pnpm|yarn run dev|start`, uvicorn, flask run,
  `http.server`, streamlit, jupyter. Tests: pytest, jest, vitest, `npm test`, go test,
  cargo test, playwright test. The Arena controller itself is not listed.
- **Managed**: the process (or an ancestor) matches an attempt's recorded identity
  (pid + create_time), or its cwd is inside `<home>/workspaces/attempt_<id>` or
  `<home>/chat-scratch`, or it is a running director turn. Arena tasks recorded as live with no
  matching process are listed under managed with label "Arena records this as running, but no
  agent process was found" (this is how "stuck" becomes visible).
- **External**: agent/dev/test processes with a resolvable working folder outside Arena.
- **Unknown**: agent-like processes whose working folder macOS withheld or that resolve to no
  repository.
- Repository = nearest ancestor of cwd containing `.git`; `project` = enrolled project with that
  repo. Paths in output are home-relative (`~/Projects/x`).
- Claude Code session evidence (metadata only, never file contents): newest `*.jsonl` mtime in
  `~/.claude/projects/<cwd with / and . replaced by ->/` and the same under `~/.claude-second`;
  `last_activity` from it; `subagents_recent` = count of `agent-*.jsonl` or `subagents/*.jsonl`
  modified in the last 10 min (stated as evidence, not as certainty).
- `summary` sentences, one per activity, e.g. "Claude is working in LeoOS (active 2 min ago).",
  "Arena is building Continuoum: 2 agents.", "A development server is running for Agent Exchange
  on port 5173." — composed only from the fields above.
- `limitations` always states what could not be seen (withheld folders, subagents inside a
  session are inferred from session files, other users' processes are not inspected).
- Tests mock psutil (fake process table); no real process inspection in tests.

### A4. Director context (conversation.py)
- `_context()` always adds `mac_activity` (compact: summary, ≤ 30 activities without pids),
  `recent_outcomes` (finished/stopped goals in the last 24 h: title, project, outcome, when) and
  `failures` (Failure objects for blocked/needs-you goals in the last 48 h with top review
  findings and failing check names).
- `arena_status.running[]` gains `last_activity_seconds` and `stalled`.
- Prompt additions: answer "what's running on my Mac/laptop" ONLY from `mac_activity`, naming
  each activity once with its group (managed by Arena / discovered externally / could not be
  verified) and stating limitations; answer failures from `failures` (what failed, intended
  result, recoverable, what was tried, what happens next, whether the owner must act), no stack
  traces unless asked; never invent processes, repositories or progress. Build launch rules
  (BUILD_INTENT) unchanged.
- An assistant message answering a question that mentions running/laptop/Mac/machine/computer/
  doing/Claude/Codex/stuck/active carries `mac_activity` in its data for a structured card.
- `POST conversations/{id}/messages` accepts optional `about: {task_id}` or `{goal_id}`: the
  turn's context includes that block/build story and failure, and the conversation's project is
  set to it.

### A5. Planning
`PlannedTask.purpose: str = ""` (≤ 300). The director plan prompt asks for one plain-English
sentence per task saying why it matters to the owner, with no file names or code. Stored on the
created task as `purpose`.

## B. Frontend (React, `web/src/`) — follow the reference image

- **Navigation**: no bottom navigation on phones (≤ 860 px). Top bar: lion emblem + ARENA
  wordmark left, one menu button (☰, accessible name "Menu") right. Desktop keeps the sidebar
  with the same items as the menu.
- **Menu** (sheet): Chats · What's running on my Mac · Builds (history) · Projects · Agents ·
  Review (count) · Archived · Pause/Resume queue · Device session.
- **Home** (default screen): (1) one input card with mic, placeholder "Tell Arena what to
  build…" — submit posts to the director (current conversation, else a new one using the
  slice D default project) and opens Chat; (2) Needs you rows, only when present; (3) ACTIVE
  BUILDS (count): one row per project with live work — "Building <project>", "● N agents
  working · Started X ago", progress bar (done/total); tap → Build story; (4) READY (count,
  collapsible): non-archived projects not building, subtitle = last outcome; tap → latest build
  story (or Chat about the project if none); ▷ → Chat with the project selected; (5) FINISHED
  (collapsed by default): finished/stopped builds; "Select" → checkboxes, "Select all",
  "Archive selected (n)"; running builds never selectable; Ready project rows are selectable in
  the same mode. A compact "Since you were away" one-line banner (expandable) replaces the
  slice A panel. Removed from Home: page title, stats line, activity log, recent requests panel.
- **Build story** (screen 2): back, project name, status chip, "Started X ago · N agents",
  ⋯ menu (Advanced → existing technical Build view; Stop build when stoppable, with confirm;
  Archive when finished), progress bar, Failure card first when failed (plain English, next
  step, action button), story blocks on a vertical timeline (dependency order): agent avatar
  (letter glyph, Claude warm accent / Codex blue accent — no brand logos), "Claude (model)",
  stage chip, title, `now ?? purpose` (≤ 3 lines), subtle accent wave on running blocks
  (static gradient; respects reduced motion). Red-outline "Stop build" only while running.
- **Block detail** (screen 3): back, avatar, title, agent+model, stage chip; "What this block is
  doing" (what, why, connects as sentences); Progress rail Design · Implement · Test · Review ·
  Merge; Recent updates (Live chip while live; "Now: …" first; relative times); decisions;
  Failure card; Advanced (collapsed: attempts, checks, commit, scope, link to the existing
  transcript view); bottom input "Ask a question about this block…" → director with
  `about:{task_id}`, opens Chat.
- **What's running on my Mac**: sections "Managed by Arena", "Discovered on your Mac",
  "Couldn't verify"; plain sentences, "active N min ago"; limitations note; refresh; "Ask the
  director". Chat replies carrying `mac_activity` render the same component compactly.
- **Archived**: archived builds and projects, checkboxes, "Restore selected".
- **Chat**: behaviour from slices C/D unchanged; back ‹ to Home.
- No horizontal overflow at 360/390 px; every control has an accessible name; acceptance
  scripts that used the bottom nav are updated in the same change to use the Menu.

## C. Acceptance and release
`web/round3-acceptance.mjs` on a copy of the real state (390 px, Android UA, dispatch off),
covering the owner's definition of done 1–7, with `ARENA_DISCOVERY_FIXTURE` (JSON) allowed in
`tests/control/real_state_server.py` for deterministic screenshots plus one real discovery call
proving the endpoint runs. Then an independent review, full gates (ruff, pytest, npm build, all
acceptance scripts), deploy through the Mac watcher (health-checked activation with rollback),
and the morning report.
