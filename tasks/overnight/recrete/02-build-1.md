+++
# Overnight recrete step 02-build-1. Run via the recrete-overnight queue.
agent = "codex"
model = "gpt-6-sol"
effort = "high"
network = true
full_access = true
subagents = true
repo = "~/Projects/recrete-site"
max_turns = 300
timeout_seconds = 5400
state_entries_in_prompt = 8
next_on_pass = "next step in the recrete-overnight queue"

[[checks]]
name = "syntax check passes"
argv = ["npm", "run", "check"]
timeout_seconds = 900

[[checks]]
name = "unit tests pass"
argv = ["npm", "run", "test:unit"]
timeout_seconds = 900

[[checks]]
name = "tests not reduced"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "tests-not-reduced", "^\\s*(test|it)\\(", "tests/*.js", "tests/*.cjs"]
timeout_seconds = 900

[[checks]]
name = "no secrets added"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "no-secrets"]
timeout_seconds = 900

[[checks]]
name = "backlog progress"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "backlog", "docs/OVERNIGHT_BACKLOG.md", "8", "1"]
timeout_seconds = 900
[[checks]]
name = "decision log kept"
argv = ["test", "-s", "docs/DECISIONS.md"]
timeout_seconds = 60

[[checks]]
name = "error log kept"
argv = ["test", "-s", "docs/ERROR_LOG.md"]
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
- You have internet access and full permissions, and nobody will approve anything: act, don't
  ask. Use the internet only to install dependencies and read documentation. Never push,
  deploy, publish, send messages or call paid/production APIs.
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

THIS STEP: build item 1
Open docs/OVERNIGHT_BACKLOG.md. Take the FIRST unticked item ("- [ ]"), implement it fully with tests, then
tick it ("- [x]") and add one indented evidence line under it: the files changed and the
test names that prove it. If that item turns out to need credentials or real external data,
mark it "- [x] BLOCKED: <reason>", then do the next item instead. Do exactly one item.
Run the full test suite before finishing.
