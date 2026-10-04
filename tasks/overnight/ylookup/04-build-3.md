+++
# Overnight ylookup step 04-build-3. Run via the ylookup-overnight queue.
agent = "codex"
model = "gpt-6-sol"
effort = "high"
network = true
full_access = true
subagents = true
repo = "~/Projects/YLOOKUP"
max_turns = 300
timeout_seconds = 5400
state_entries_in_prompt = 8
next_on_pass = "next step in the ylookup-overnight queue"

[[checks]]
name = "tests pass"
argv = [".venv/bin/python", "-m", "pytest", "-q"]
timeout_seconds = 900

[[checks]]
name = "tests not reduced"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "tests-not-reduced", "^\\s*def test_", "tests/*.py"]
timeout_seconds = 900

[[checks]]
name = "no secrets added"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "no-secrets"]
timeout_seconds = 900

[[checks]]
name = "backlog progress"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "backlog", "docs/OVERNIGHT_BACKLOG.md", "8", "3"]
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
name = "build log kept"
argv = ["test", "-s", "docs/BUILD_LOG.md"]
timeout_seconds = 60

+++

Repository context:
YLOOKUP is the FundOps Control Room: capital-call documents become evidence-backed exceptions,
deterministic controls and auditable human decisions (Streamlit + SQLite). GOAL FOR THIS RUN:
make it more agentic, and runnable on anyone's laptop.
1. Portable: one-command setup and run on macOS, Linux and Windows (e.g. `make setup` plus a
   pure-Python fallback `python scripts/setup.py`), no hard-coded paths, sensible defaults,
   clear errors when something is missing, an optional Dockerfile, and a README quick start
   that works on a fresh machine. Add a test that the app and evals run from a clean temp copy.
2. Agentic loop: a bounded agent loop that takes a capital-call document through extract ->
   normalise -> reconcile -> independent review -> exception queue. It should plan, call the
   existing deterministic modules as tools, retry with evidence on failure, and stop for human
   decisions; it must have step and budget limits, and log every step. Make it runnable from a
   CLI (`python -m app.agent run <file>`) and from the Streamlit UI. Model providers
   (Anthropic/OpenAI) are optional, behind one interface, configured by env vars, and the
   default is a deterministic offline fake so it runs anywhere without keys.
AGENTS.md is binding: extend the canonical modules (never *_v2 duplicates); a model may
interpret text but must never do financial arithmetic, clear a control break or make a human
decision; keep typed values, provenance, abstention and append-only audit. Use .venv/bin/python
(create it if missing).

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
- Keep docs/BUILD_LOG.md: a timeline. At the start and end of every step, append a dated entry:
  what you're about to do and why; then what you did, what failed along the way, and what's
  next. It's for the owner to review later with an AI, so be specific.
- Never delete or weaken existing tests, never skip/xfail to make things pass, never remove
  assertions. Fix the code, not the test. New behaviour needs new tests.
- No secrets, API keys or personal data in code, fixtures or docs. External services (models,
  webhooks, cloud) go behind an interface with a deterministic fake used in tests.
- Keep the repo's own rules (AGENTS.md / CLAUDE.md / ROADMAP.md / AGENT_BOARD.md) above these
  instructions where they are stricter.
- Be truthful in docs: never claim accuracy, live capability or production readiness the
  code and tests don't demonstrate.
- Finish with exactly three lines: CHANGED: ..., RAN: ..., PROBLEMS: ...

THIS STEP: build item 3
Open docs/OVERNIGHT_BACKLOG.md. Take the FIRST unticked item ("- [ ]"), implement it fully with tests, then
tick it ("- [x]") and add one indented evidence line under it: the files changed and the
test names that prove it. If that item turns out to need credentials or real external data,
mark it "- [x] BLOCKED: <reason>", then do the next item instead. Do exactly one item.
Run the full test suite before finishing.
