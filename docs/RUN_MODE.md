# `run` mode

```
python -m agent_arena run TASK.md --repo PATH [--runs-dir ~/.agent-arena/runs] [--pricing pricing.toml]
```

Runs **one** coding agent (Claude Code or Codex CLI) headless in `PATH`. The run is bounded,
the harness checks the result itself, and every call's tokens are accounted for. It
replaces the human relay of "paste instructions in, copy results out, check by hand".

## Flow

1. Parse `TASK.md`: TOML front matter between `+++` lines (agent, model, tool allowlist,
   turn cap, timeout, checks), then the Markdown instructions.
2. Build the prompt from three parts: fixed safety rules, the **last N `STATE.md` entries**
   (where earlier sessions left off) and the instructions.
3. Run the agent under process-group kill on timeout. Claude gets `--allowedTools`,
   `--max-turns`, `--permission-mode dontAsk`, no MCP servers and a deny list (git
   commit/push/reset/checkout/clean, rm, `~/.ssh`, `.env`). Codex gets only
   `--sandbox workspace-write` and the timeout. The report says which bounds the CLI
   could not enforce. `--max-turns` is not listed in `claude --help` (2.1.288) but is
   accepted and enforced: a run that hits it ends with `error_max_turns`.
4. Save the full transcript and stderr, the prompt and argv to a **write-once** run directory.
   The directory is created with `exist_ok=False`, hash-manifested, then made read-only.
5. Run the checks as argv arrays, never a shell. Exit 0 = PASS, 1 = FAIL, anything else or
   a timeout = UNKNOWN.
6. Print the report and append a 4-line entry to `PATH/STATE.md`: what ran, the result and
   the next action.

Exit code: 0 all PASS, 1 any FAIL, 2 UNKNOWN but no FAIL, 3 harness error.

## Token & cost accounting

| field | Claude (`stream-json`, one record per assistant message id) | Codex (`turn.completed`) |
|---|---|---|
| model | `message.model` (reported) | declared in TASK.md (codex does not report it) |
| input | `input_tokens` (excludes cache) | `input_tokens` (includes cached) |
| cache_read | `cache_read_input_tokens` | `cached_input_tokens` |
| cache_write | `cache_creation_input_tokens` | n/a |
| output | `output_tokens` from the `message_delta` stream event (needs `--include-partial-messages`) | `output_tokens` |
| reasoning | n/a (billed as output) | `reasoning_output_tokens` if present |

* A field the CLI did not report is `null`, never 0. Totals are `null` if any call is `null`.
* The `usage` on a Claude `assistant` event is the snapshot from `message_start`: its
  `output_tokens` is a few tokens, not the final count, and it repeats unchanged for every
  content block of the message. Only `message_delta` has the final count, so a call without
  one has `output: null`.
* The per-call sums are compared with the `usage` totals in the CLI's `result` event
  (`usage.cli_result_totals` in `result.json`). Any difference is a warning in the report.
* `cli_reported_cost` is Claude's `total_cost_usd`, kept as reported and never merged.
* The **API-equivalent estimate** uses only `pricing.toml` entries with `source` and
  `retrieved`. Any unpriced model or unreported counted field makes it `null` with a
  warning. Subscription use has no per-token marginal cost.
* **Ledger:** this covers every run in the runs dir, failed ones included. It reports the total
  estimate and the **cost per verified outcome** (total ÷ runs where every check passed).
* **Exclusions:** run directories are write-once, so a bad or non-comparison run is listed in
  `experiments/excluded_runs.toml` (`run_id` + `reason`) instead of being edited. The report's
  Ledger line and `ledger summary` leave those runs out and say how many; the CSV keeps them
  with the reason in its `excluded` column. `--excluded PATH` points at another file.

## Permissions

* Every denied tool call in the CLI's `permission_denials` is a warning in the report and is
  saved under `invocation.permission_denials`. A CLI error such as `error_max_turns` is also
  a warning. Neither changes the verdict: the checks alone decide it.
* A `Bash(cmd:*)` rule allows the bare command only. Measured on claude 2.1.288: an output
  redirect (`> file`), even into the repo, and anything appended (`; echo ...`) are denied,
  and plain `Write` or `Edit` entries do not change that. A path-scoped rule for the redirect
  target does allow it, for example `Edit(//tmp/codex-smoke.log)`.

## Long-running use and context windows

Each run is a fresh session (`--no-session-persistence`), so no transcript grows across runs.
Continuity lives outside the model: `STATE.md` (the last `state_entries_in_prompt` entries are
injected) and the run directories. The report shows **peak context** per call
(input + cache read + cache write). Above `context_warn_tokens` it warns you to split the
task. Within one run, `max_turns` caps how far the context can grow.

## Dogfood

`tasks/codex-smoke.md` drives `agent_reliability_lab`. It edits the v2 Codex config, writes
STATE.md and runs a 2-task smoke batch. Then 9 harness-side checks
(`tasks/codex_smoke_checks.py`) run against the lab's own run artifacts and `git status`.

## Eval ledger: harness vs. prompting alone

`experiments/harness_vs_prompting.csv` is **derived**: it is rebuilt from the run directories
after every `agent_arena run`, and nobody edits it by hand. One row per attempt:

| arm | how the row gets there | metrics source |
|---|---|---|
| `harness` | automatically, after `python -m agent_arena run ...` | harness transcript |
| `prompt-only` | after a normal interactive Claude Code or Codex session on the same task, run `python -m agent_arena.ledger grade TASK.md --repo PATH` | the newest session log whose cwd is PATH (`~/.claude/projects`, `~/.codex/sessions`, Codex sub-agents included) |

`grade` runs the same TASK.md checks as the harness, so "verified" means the same thing in both
arms. It stores numbers only (tokens, duration, human message count, model), never session text.
The CSV keeps only the repo's basename, so it is safe to commit.

Columns that answer "is it more efficient?": `verified`, `human_messages` (relay effort; 1 for a
harness run), `wall_time_s`, `turns`. Tokens and the two cost columns are tracked alongside.
`python -m agent_arena.ledger summary` prints per-task, per-arm medians.

For a fair comparison: use a fresh session per task in the prompt-only arm (its wall time runs
from first to last log event), give it the same instructions as the TASK.md body, reset the repo
between arms, and alternate which arm goes first.
