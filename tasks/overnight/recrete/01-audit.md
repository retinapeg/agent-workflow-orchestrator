+++
# Overnight recrete step 01-audit. Run via the recrete-overnight queue.
agent = "codex"
model = "gpt-6-sol"
effort = "high"
network = true
full_access = false
subagents = true
repo = "~/Projects/recrete-site"
max_turns = 300
timeout_seconds = 3600
state_entries_in_prompt = 8
next_on_pass = "next step in the recrete-overnight queue"

[[checks]]
name = "no secrets added"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "no-secrets"]
timeout_seconds = 900

[[checks]]
name = "backlog written (8+ items)"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "backlog", "docs/OVERNIGHT_BACKLOG.md", "8", "0"]
timeout_seconds = 900
[[checks]]
name = "decision log kept"
argv = ["test", "-s", "docs/DECISIONS.md"]
timeout_seconds = 60

[[checks]]
name = "error log kept"
argv = ["test", "-s", "docs/ERROR_LOG.md"]
timeout_seconds = 60

[[checks]]
name = "harness code and checks untouched (no agent edited its own referee)"
argv = ["git", "--no-optional-locks", "-C", "{task_dir}/../../..", "diff", "--quiet", "HEAD", "--", "src", "tasks", "pyproject.toml"]
timeout_seconds = 60

+++

Repository context:
Recrete: a private autobiographical-memory app (web/app/backend split; local-first storage;
voice capture already exists in app/lib/voice.js; WhatsApp parser exists and is tested). Product
direction now, from the founder: a reality check, not nostalgia. Right after an event (e.g. a first
date) you record a short voice note on how it felt; later you ask "how did I feel about X?" and
get the RAW recording first, then a clearly labelled AI reading of how you said you felt, plus
photos and messages from around that time. The raw recording is ground truth; AI may summarise
but must never overwrite or reinterpret it, and the person is the authority on their own
experience (never an "AI mood detector"). Privacy is a feature: local-first, nothing leaves the
device without explicit consent. Prioritise: recall over local voice notes and moments by time,
person and tag; a raw-first answer view; capture prompts (e.g. "How was it?" after an event); the
WhatsApp import UI wired to the tested parser (AGENT_BOARD item); and a faithfulness eval
comparing AI summaries against transcripts on synthetic fixtures, flagging distortion. AI calls
go behind an interface with a deterministic fake in tests. Use `npm run check` and
`npm run test:unit`; don't depend on Playwright browsers.

HARD RULES (all steps)
- You run in a sandbox: you can write only inside this repository, and you have internet
  access. Nobody will approve anything: act, don't ask. Use the internet only to install
  dependencies (into this repo, e.g. .venv / node_modules; use pip --no-cache-dir) and read
  documentation. Never push, deploy, publish, send messages or call paid/production APIs.
- Work only inside this repository; never touch files outside it. No git commit, push, reset,
  checkout or branch changes; leave all work uncommitted for the human to review.
- You may spawn sub-agents for parallel work (e.g. one writes tests while another implements),
  but you own the result: integrate their work and run the full suite yourself.
- Keep two logs, appending dated entries (never rewrite history):
  docs/DECISIONS.md: architecture decision records (context, decision, alternatives
  considered, consequences) for every non-trivial design choice;
  docs/ERROR_LOG.md: every error, failed test run, blocked item or dead end, what caused
  it and how it was resolved (or why it wasn't).
- Never delete or weaken existing tests, never skip/xfail to make things pass, never remove
  assertions. Fix the code, not the test. New behaviour needs new tests.
- No secrets, API keys or personal data in code, fixtures or docs. External services (models,
  webhooks, cloud) go behind an interface with a deterministic fake used in tests.
- Keep the repo's own rules (AGENTS.md / CLAUDE.md / ROADMAP.md / AGENT_BOARD.md) above these
  instructions where they are stricter.
- Be truthful in docs: never claim accuracy, live capability or production readiness the
  code and tests don't demonstrate.
- Finish with exactly three lines: CHANGED: ..., RAN: ..., PROBLEMS: ...

THIS STEP: audit and backlog (no feature code yet)
1. Read the README, the repo's own agent/roadmap docs and the tests, and run the test suite.
2. Write docs/OVERNIGHT_BACKLOG.md: a prioritised checklist of 8-10 items using "- [ ] " lines. Each item must:
   be completable in one focused session (roughly 30-60 minutes of work); be checkable by a
   new or existing automated test; follow the repo's own roadmap/priorities; and need no
   credentials or real external data. Under each item, add one line saying how it
   will be verified. Order the items so each builds on the one before.
3. Add a short "Current state" section at the top: what works, test counts, known gaps.
Install dependencies if anything is missing. Create docs/DECISIONS.md and
docs/ERROR_LOG.md if absent and log the baseline (test results, environment, any errors).
Don't change any feature code in this step.
