+++
# Lab bug: a role whose calls report no cost is summarised as $0.0000 instead of null.
# Run from the agent-workflow-orchestrator repo (Leo runs this; first harness-vs-prompting pair):
#   python -m agent_arena run tasks/codex-cost-null.md \
#     --repo ~/agent-reliability-workspace/agent_reliability_lab
agent = "claude"
model = "claude-sonnet-5"
allowed_tools = [
  "Read", "Glob", "Grep", "Edit", "Write",
  "Bash(python3 -m pytest:*)",
  "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
]
max_turns = 40
timeout_seconds = 1800
context_warn_tokens = 150000
next_on_pass = "grade the prompt-only arm of codex-cost-null, then compare the pair in the ledger"

[[checks]]
name = "lab pytest passes"
argv = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
timeout_seconds = 600

[[checks]]
name = "coder cost is null when a coder call reports no cost"
argv = ["{python}", "{task_dir}/codex_cost_null_checks.py", "metrics"]

[[checks]]
name = "a lab test covers the null cost"
argv = ["{python}", "{task_dir}/codex_cost_null_checks.py", "lab-test"]

[[checks]]
name = "git status: only summarize.py, tests/ and earlier harness files"
argv = ["{python}", "{task_dir}/codex_cost_null_checks.py", "git-clean"]
+++

You are working in the agent_reliability_lab repository (the current directory).

The bug: Codex CLI calls report no cost, and each call record correctly stores
`list_cost_usd: null`. But `compute_metrics()` in `src/agent_reliability/analysis/summarize.py`
starts every role's cost at 0.0, so a coder with no reported cost is summarised as
`coder_list_cost_usd: 0.0` and the summary prints "coder $0.0000". An unreported cost is unknown,
not zero.

Fix it in `src/agent_reliability/analysis/summarize.py` only:

1. In `cost_by_role`, a role's `list_cost_usd` is the sum of its calls' costs only when every
   call of that role reported one. If any call of the role has `list_cost_usd` null, the role's
   `list_cost_usd` is null. Keep `cost_reported` as the count of calls that reported a cost.
2. `overhead.coder_list_cost_usd` is null when the coder's cost is null.
   `overhead.oversight_list_cost_usd` is null when any reviewer or reviser call lacks a cost;
   otherwise both are the same sums as today.
3. `render_summary()` must not print a dollar figure for a null cost. Print `not reported` in
   its place, for example "coder not reported; review + revision $0.1100".
4. When every call reports a cost, the metrics and the summary text must be exactly as before.

Add a test in `tests/` (extend `tests/test_pipeline_and_aggregation.py` or add a file) that
builds episodes where the coder calls have `list_cost_usd` of `None` and asserts that
`coder_list_cost_usd` is `None` and that the summary does not show "$0.0000" for the coder.

Then run `python3 -m pytest -q` and make it pass. Run that command exactly; append nothing to it
(no `; echo`, no pipes, no redirects), or it will be denied.

Do not edit any other file. Do not regenerate or edit anything under `results/`; the tracked
`metrics.json` and `summary.md` files stay as they are. Do not run `agent_reliability analyze`
or `run`.
The harness runs its own checks after you finish.
