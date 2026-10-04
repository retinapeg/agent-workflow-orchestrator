+++
# Full v2 Codex batch (12 tasks x 3 reps) through the harness. Commit the lab first:
# the git-clean check expects only the new run dir and STATE.md to differ.
#   arena run tasks/v2-full.md --repo ~/agent-reliability-workspace/agent_reliability_lab
agent = "claude"
model = "claude-sonnet-5"
allowed_tools = [
  "Read", "Glob", "Grep",
  "Bash(python3 -m agent_reliability run:*)",
  "Edit(//tmp/v2-full.log)",
  "Bash(git status:*)",
]
repo = "~/agent-reliability-workspace/agent_reliability_lab"
max_turns = 15
timeout_seconds = 9000
env = { PYTHONPATH = "src", LAB_LABEL = "v2-full" }
context_warn_tokens = 150000
next_on_pass = "compare v2 (Codex coder) against v1 (Claude coder) in the lab summaries"
usage_import = { argv = ["{python}", "{task_dir}/codex_smoke_usage.py", "{run_dir}"] }

[[checks]]
name = "log has no BATCH STOPPED"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "log", "/tmp/v2-full.log"]

[[checks]]
name = "every episode status=complete"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "episodes-complete"]

[[checks]]
name = "visible and hidden results present"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "results-present"]

[[checks]]
name = "coder calls have failure=null"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "coder-ok"]

[[checks]]
name = "usage present on every call"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "usage-present"]

[[checks]]
name = "models_used = gpt-6-sol + claude-sonnet-5"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "models-used"]

[[checks]]
name = "git status: only the new run dir and STATE.md"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "git-clean"]
+++

You are working in the agent_reliability_lab repository (the current directory).

Run exactly this command (PYTHONPATH=src is already set for you), with a Bash timeout of
8000000 ms because the full batch takes a long time:

python3 -m agent_reliability run --config configs/cross_review_v2_codex.json --label v2-full --no-latest > /tmp/v2-full.log 2>&1

Run the command exactly as written; append nothing to it (no `; echo`, no pipes), or it will be
denied. Then read the last 40 lines of /tmp/v2-full.log and report what you see.

Do not edit any file. Do not rerun. Do not try to fix failures; report them.
The harness runs its own checks after you finish.
