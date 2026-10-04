+++
# Overnight dronewatch step 07-wrap. Run via the dronewatch-overnight queue.
agent = "codex"
model = "gpt-6-sol"
effort = "high"
network = true
full_access = true
subagents = true
repo = "~/Projects/dronewatch"
max_turns = 300
timeout_seconds = 3600
state_entries_in_prompt = 8
next_on_pass = "next step in the dronewatch-overnight queue"

[[checks]]
name = "Python tests pass"
argv = [".venv/bin/python", "-m", "pytest", "-q"]
timeout_seconds = 900

[[checks]]
name = "dashboard JS tests pass"
argv = ["node", "--test", "tests/dashboard.test.cjs", "tests/diagnostics.test.cjs", "tests/operator.test.cjs"]
timeout_seconds = 900

[[checks]]
name = "Python tests not reduced"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "tests-not-reduced", "^\\s*def test_", "tests/*.py"]
timeout_seconds = 900

[[checks]]
name = "index.html frozen (ROADMAP rule)"
argv = ["git", "--no-optional-locks", "diff", "--quiet", "HEAD", "--", "index.html"]
timeout_seconds = 900

[[checks]]
name = "no secrets added"
argv = ["python3", "{task_dir}/../../overnight_checks.py", "no-secrets"]
timeout_seconds = 900

[[checks]]
name = "handoff written"
argv = ["test", "-f", "docs/OVERNIGHT_HANDOFF_2026-10-04.md"]
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
DroneWatch: an evidence-aware counter-UAS prototype (FastAPI + SQLite + tactical dashboard,
Kalman tracker, lost-contact containment, synthetic scenarios). GOAL FOR THIS RUN: a clean,
up-to-date repo, with visual detection pointed at Viso (viso.ai, "Viso Now"; the owner wrote
"visnow.ai", so confirm the right vendor and product from their official docs online).
1. Viso integration, ready but not connected: read Viso's current official docs (webhook
   format, auth, file/video processing API) and bring the integration up to date. That
   means the webhook endpoint (existing POST /webhook/viso) validating Viso's documented
   payload; auth via an env var (e.g. VISO_API_KEY / VISO_WEBHOOK_SECRET) that is absent by
   default; a health/status endpoint that honestly reports "Viso: not configured" when no key
   is set; a client stub for the outbound API that refuses to run without a key; redacted
   fixtures and tests for the webhook path; and a short docs/VISO_INTEGRATION.md saying exactly
   what to set once the key arrives. Never invent API fields the docs don't show; if the docs
   are unclear, say so and keep the adapter strict.
2. Clean and current: remove clutter that shouldn't be in git (__pycache__, local .db files,
   stale artefacts; check .gitignore), update dependencies to current compatible versions
   with tests passing, fix outdated docs so the README matches reality, keep CI working.
   Record every removal and why in docs/DECISIONS.md.
ROADMAP.md's "Non-negotiable rules" are binding (SYNTHETIC/REPLAYED_REAL/LIVE labels, no
unmeasured claims, index.html byte-for-byte frozen, datasets out of git). Use .venv/bin/python.

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

THIS STEP: wrap-up and handoff
1. Run the full test suite and record the results.
2. Update docs/OVERNIGHT_BACKLOG.md: an "Overnight results" section listing each ticked item with its evidence,
   anything blocked or left, and the recommended next three items.
3. Update the README only where the new behaviour needs documenting (truthfully, no hype).
4. Write docs/OVERNIGHT_HANDOFF_2026-10-04.md: the branch, what changed (file list), test commands and results, known
   issues, and what a reviewer should check first.
Don't start new features in this step.
