+++
# Dogfood job for `python -m agent_arena run`. Run from the agent-workflow-orchestrator repo:
#   python -m agent_arena run tasks/codex-smoke.md \
#     --repo ~/agent-reliability-workspace/agent_reliability_lab
agent = "claude"
model = "claude-sonnet-5"
allowed_tools = [
  "Read", "Glob", "Grep", "Edit", "Write",
  "Bash(python3 -m agent_reliability run:*)",
  "Edit(//tmp/codex-smoke.log)",
  "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
]
max_turns = 30
timeout_seconds = 3600
env = { PYTHONPATH = "src" }
context_warn_tokens = 150000
next_on_pass = "run the full v2 Codex batch and compare coders against v1"
# Calls made inside the lab batch (Codex coder, Claude reviewer): priced by the harness as
# nested cost. Must stay above the [[checks]] tables.
usage_import = { argv = ["{python}", "{task_dir}/codex_smoke_usage.py", "{run_dir}"] }

[[checks]]
name = "log has no BATCH STOPPED"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "log", "/tmp/codex-smoke.log"]

[[checks]]
name = "v2 config: reviewer claude-sonnet-5; differs from v1 only in repetitions, coder, reviewer"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "config"]

[[checks]]
name = "STATE.md records the five facts"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "state"]

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
name = "git status: only new run dir, STATE.md, v2 config, .claude/"
argv = ["{python}", "{task_dir}/codex_smoke_checks.py", "git-clean"]
+++

You are working in the agent_reliability_lab repository (the current directory).

1. In `configs/cross_review_v2_codex.json`, set the reviewer model to `claude-sonnet-5`.
   Change nothing else in that file.

2. Create `STATE.md` if it does not exist, then add a section headed
   `## Codex smoke setup` with these five bullet points, worded close to this:
   - The tool-loop commits are pushed.
   - The reviewer is pinned to claude-sonnet-5, so only the coder changes from v1.
   - gpt-6-sol runs at hard-coded medium effort, so the results compare coders, not providers.
   - The Codex model is declared, not read from usage.
   - The tool loop has only been run live with claude_cli.

3. Run exactly this command (PYTHONPATH=src is already set for you), with a Bash timeout of
   3000000 ms because the batch takes several minutes:

   python3 -m agent_reliability run --config configs/cross_review_v2_codex.json --tasks slugify median --label codex-smoke --no-latest > /tmp/codex-smoke.log 2>&1

   Run the command exactly as written; append nothing to it (no `; echo`, no pipes), or it
   will be denied.

   Then read /tmp/codex-smoke.log. If it shows that claude-sonnet-5 was rejected as a model,
   stop and report that. Do not change the model to "sonnet" or anything else, and do not rerun.

Do not edit any other file. Do not try to fix failures you see in the log; report them.
The harness runs its own checks after you finish.
