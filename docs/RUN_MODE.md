# `run` mode

```
python -m agent_arena run TASK.md --repo PATH [--runs-dir ~/.agent-arena/runs] [--pricing ~/.agent-arena/pricing.toml] [--excluded experiments/excluded_runs.toml]
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
| cache_write | `cache_creation_input_tokens`, plus the 5m/1h split from `cache_creation` | `cache_write_input_tokens` (billable) |
| output | `output_tokens` from the `message_delta` stream event (needs `--include-partial-messages`) | `output_tokens` |
| reasoning | n/a (billed as output) | `reasoning_output_tokens` if present |

* A field the CLI did not report is `null`, never 0. Totals are `null` if any call is `null`.
* The `usage` on a Claude `assistant` event is the snapshot from `message_start`: its
  `output_tokens` is a few tokens, not the final count, and it repeats unchanged for every
  content block of the message. Only `message_delta` has the final count, so a call without
  one has `output: null`.
* The per-call sums are compared with the `usage` totals in the CLI's `result` event
  (`usage.cli_result_totals` in `result.json`). Any difference is a warning in the report.
* `cli_reported_cost` is Claude's `total_cost_usd`, kept as reported as a cross-check and
  never merged.

### Cost

Raw tokens per category per model are the ground truth and are always stored. Dollars are
**derived** from them and a dated price table, so sealed runs can be re-priced and are never
edited. Every cost is **API-equivalent (subscription: no marginal $)**.

* **Price table:** `~/.agent-arena/pricing.toml` (outside git), or `$AGENT_PRICING_FILE`, or
  `--pricing`. It has a `version`; each model entry needs `source` and `retrieved`. The
  version and the file's sha256 are recorded with every computed cost. `pricing.example.toml`
  shows the keys. The lab reads the same file with an identical copy of `pricing.py`.
* **Never $0 for unknown:** a call with a missing price or a missing token count is unpriced
  (`null`), with the reason named. Totals are the sum of the priced calls plus coverage, e.g.
  `$0.3066 (20/20 calls priced)`; with nothing priced the figure is `null`.
* **Cache writes:** Claude's 5-minute and 1-hour cache writes are priced separately
  (`cache_write_per_mtok`, `cache_write_1h_per_mtok`). Without the split the 5m rate is used
  and a note says so.
* **Rate modifiers** are data in the model entry: `service_tier_multipliers` (OpenAI fast 2x,
  ultrafast 6x on every category) and `long_context_over_input_tokens` with its input and
  output multipliers (more than 272K input tokens: 2x input/cache, 1.5x output for the whole
  record). Codex reports usage per turn, not per request, so the long-context rule is applied
  to the turn's totals.
* **Metadata per run:** `effort` (TASK.md key, passed to the CLI), `service_tier` (TASK.md
  key, default `standard`; used for pricing when a call reports none; not passed to the CLI),
  `ultracode` (TASK.md key) and, for Codex, the change in `rate_limits.primary.used_percent`
  with `window_minutes` when the events expose them. Effort and ultracode never change rates.
* **Nested usage:** `usage_import = { argv = [...] }` in TASK.md runs after the checks and
  prints `{"calls": [{model, input, cache_read, cache_write, output, reasoning,
  input_includes_cache_read, provider, cli_reported_cost}]}` (optionally `cache_write_5m`,
  `cache_write_1h`, `service_tier`, `not_applicable`) for calls made by tools the agent ran.
  They are priced by the same code. The report shows agent + nested = total, each with
  coverage. A failed import is a warning and the nested cost is `null`.
  `python -m agent_arena.ledger attach-usage RUN_ID TASK.md --repo PATH` does the import for
  a run that is already sealed and writes a write-once `RUN_ID.nested.json` next to it.
* **Re-pricing:** the ledger re-parses each harness run's `transcript.jsonl` and recomputes
  every cost on each rebuild, so the CSV and the Ledger line always reflect the current
  price table and parser.
* **Ledger:** this covers every run in the runs dir, failed ones included. It reports the total
  cost with coverage and the **cost per verified outcome** (total ÷ runs where every check
  passed; `null` unless every call is priced). `ledger summary` flags runs that are not fully
  priced and, per task, gives harness ÷ prompt-only ratios for tokens, cost, wall time and
  messages when both arms used the same model (otherwise `n/a (different models)`).
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
