# STATE

## 2026-10-03 — `run` mode built (Cowork session), not yet run live
- built: `python -m agent_arena run TASK.md --repo PATH` (src/agent_arena/run_mode.py), wired via a
  6-line intercept in cli.py; existing modes and their 150 tests untouched; 9 new tests; 159 pass,
  ruff clean, mypy --strict clean on run_mode.py.
- accounting: per-call tokens from Claude stream-json / Codex turn.completed; null never 0;
  cli_reported_cost kept separate; API-equivalent estimate only from pricing.toml (source+date);
  ledger = total incl. failed runs + cost per verified outcome; peak-context meter.
- context: every run is a fresh session; last 3 STATE.md entries injected as the resume point.
- dogfood: tasks/codex-smoke.md + tasks/codex_smoke_checks.py (9 checks). Checks verified
  against the lab's current state (correct FAIL/UNKNOWN before the run) and against the
  toolloop-smoke run (episode checks PASS).
- not verified: no live run yet (Cowork VM has no authenticated claude/codex). Unknowns for the
  first live run: (1) does `Bash(python3 -m agent_reliability run:*)` permit the
  `> /tmp/codex-smoke.log 2>&1` redirect; (2) does the CLI accept `claude-sonnet-5` and report it
  under exactly that name (`sonnet` currently reports as claude-sonnet-5-5).
- next: on the Mac, from this repo: `cp pricing.example.toml pricing.toml`, fill two models,
  then `python -m agent_arena run tasks/codex-smoke.md --repo ~/agent-reliability-workspace/agent_reliability_lab`

## 2026-10-03 — eval ledger added (Cowork session, in parallel with the live test session)
- new: src/agent_arena/ledger.py + tests/test_ledger.py (5 tests); cli.py `run` intercept now
  calls ledger.after_run() so experiments/harness_vs_prompting.csv rebuilds after every run.
- prompt-only arm: `python -m agent_arena.ledger grade TASK.md --repo PATH` right after a manual
  session; metrics come from the newest Claude Code/Codex session log for that cwd.
- verified: Codex log parser against real ~/.codex/sessions files (113 calls, sub-agents
  merged, dedup by response_id). NOT verified: Claude Code log parser (built from the known
  transcript format; Cowork can't read ~/.claude) — check it on the first prompt-only grade.
- note: a Cowork-side `ledger csv` emptied the CSV once (no runs dir in the VM); now guarded,
  and the next `run` or `python -m agent_arena.ledger csv` on the Mac regenerates it.
- next: first prompt-only grade on live-hello to verify the Claude log parser.

## 2026-10-03 — `run` mode tested live (claude 2.1.288); dogfood blocked on a permission
- ran: baseline 159 passed, ruff clean. Flag preflight: all flags in `claude --help` except
  `--max-turns`, which is accepted and enforced (probe with max_turns=1 ended `error_max_turns`).
  live-hello twice (run 20261003T203915Z-…-21107c before the fix, 20261003T204027Z-…-75920b
  after): exit 0, 2 PASS, model reported as `claude-sonnet-5`, manifest ok.
  codex-smoke once (run 20261003T204130Z-codex-smoke-49dac0): exit 2, 3 PASS, 0 FAIL, 6 UNKNOWN in
  9s. The agent edited the v2 config and wrote the lab STATE.md, then its Bash call was denied, so
  the batch never ran and no Codex or nested Claude call was made.
- cause: permissions. `Bash(python3 -m agent_reliability run:*)` allows the bare command only.
  Probes in /tmp/arena-hello: any `> file` redirect is denied, also with plain Write/Edit allowed;
  an appended `; echo "exit=$?"` (which the agent added) is denied too; adding
  `Edit(//tmp/arena-hello/probe5-out.log)` allowed exactly that redirect and no other.
- fixed (run_mode.py, 4 new tests, 168 pass incl. 5 in tests/test_ledger.py from another session):
  (1) output tokens were the `message_start` snapshot on assistant events (10 reported vs 356 in
  the CLI result); now read from `message_delta` via `--include-partial-messages`, null without
  one, and per-call sums are reconciled with the CLI result totals (equal in every run since);
  (2) permission denials and CLI errors (e.g. `error_max_turns`) are now report warnings.
- not verified: the smoke batch itself, the `models_used` name, whether nested `claude-sonnet-5`
  is accepted inside the lab, the Codex path of `run` mode, pricing (no pricing.toml).
- note: `src/agent_arena/ledger.py`, `tests/test_ledger.py`, `experiments/` and a longer `run`
  intercept in cli.py (9 lines, calls `ledger.after_run`) appeared at 21:41 from another session.
  Not touched here. The 6 probe runs are in ~/.agent-arena/runs and so in the ledger counts.
- next: Leo approves adding `"Edit(//tmp/codex-smoke.log)"` to allowed_tools in
  tasks/codex-smoke.md plus one sentence telling the agent to append nothing to the command;
  then rerun codex-smoke once.

## 2026-10-03 — codex-smoke verified through the harness; ledger exclusions added
- ran: codex-smoke rerun (run 20261003T211118Z-codex-smoke-824939) after Leo approved adding
  `Edit(//tmp/codex-smoke.log)` and the "append nothing" sentence to tasks/codex-smoke.md.
- result: exit 0, 9 PASS, 0 FAIL, 0 UNKNOWN in 199s; no permission denials; per-call sums equal
  the CLI result totals; manifest ok. Lab batch: 6/6 episodes complete, 15 calls with usage,
  models_used = claude-sonnet-5 + gpt-6-sol exactly, so `claude-sonnet-5` is accepted and
  reported under that name. Config and lab STATE.md edits date from the first run; the rerun
  agent found them done and changed nothing.
- added (ledger.py, 1 new test in tests/test_ledger.py): `experiments/excluded_runs.toml`
  (run_id + reason; 7 runs listed), an `excluded` CSV column, `ledger summary` leaves excluded
  runs out, and a `cli_result_totals_match` CSV column (true/false, empty when the run recorded
  no CLI totals).
- not verified: the Codex path of `run` mode (agent = "codex"), pricing (no pricing.toml), the
  prompt-only `grade` arm. The "Ledger, N runs" line inside each run report (run_mode.ledger)
  still counts excluded runs; only the CSV and `ledger summary` use the exclusion list.
- next: Leo fills pricing.toml, then run the full v2 Codex batch and compare coders against v1.

## 2026-10-03 — report Ledger line honours exclusions; codex-cost-null task written, not run
- changed: `load_exclusions` now lives in run_mode.py and ledger.py imports it; `run` takes
  `--excluded` (default experiments/excluded_runs.toml) and the report prints
  "Ledger, N runs incl. failed (M excluded)". 1 new test; 170 pass, ruff clean.
- added: tasks/codex-cost-null.md + tasks/codex_cost_null_checks.py (4 checks) for the lab bug
  where a role with no reported cost is summarised as $0.0000. The call records already store
  `list_cost_usd: null`; the 0.0 comes from `compute_metrics()` in the lab's summarize.py, so the
  checks target the summary. Dry run of the checks on the unfixed lab: pytest PASS (63), the
  other three FAIL for the expected reasons.
- not verified: the task itself has not been run (Leo runs it as the first harness-vs-prompting
  pair); the Codex path of `run` mode; pricing; the prompt-only `grade` arm.
- next: Leo runs codex-cost-null through the harness and by prompting, then `ledger grade`.

## 2026-10-03 — complete cost accounting: derived, re-priceable, agent + nested
- replaces the codex-cost-null plan: that task was not run and is left in place unused; the lab
  summary bug is fixed directly (see the lab's STATE.md).
- pricing: `~/.agent-arena/pricing.toml` (outside git, version 2026-10-03, 5 models, Leo's
  verified figures). New `src/agent_arena/pricing.py` (identical copy in the lab) prices raw
  tokens: 5m/1h Claude cache writes separately, OpenAI service-tier and long-context
  multipliers as data in the model entry, unpriced = null with the reason, totals with coverage.
- harness: default pricing path + `$AGENT_PRICING_FILE`; pricing version and sha256 recorded
  with every cost; Codex `cache_write_input_tokens` read and billed; TASK.md keys `effort`,
  `service_tier`, `ultracode`, `usage_import`; Codex quota delta when exposed; report shows
  agent + nested = total. `tasks/codex_smoke_usage.py` wired into codex-smoke.md.
- ledger: re-prices every run from transcript.jsonl on each rebuild; 10 new CSV columns
  (`api_estimate_usd` replaced by agent/nested/total); summary flags partly priced runs and has
  harness ÷ prompt-only ratios; `ledger attach-usage` writes a write-once sidecar for a sealed run.
- verified offline: 177 tests pass, ruff clean, mypy --strict clean on run_mode, ledger, pricing.
  Re-priced codex-smoke run …824939: agent $0.0490 + nested $0.2576 = $0.3066 (20/20 calls
  priced). Cross-check: the API estimate equals the CLI-reported cost on all 9 post-fix Claude
  runs and on the 6 nested Claude calls (gap 0.0%). Ledger: 3 runs, total $0.3851 (26/26),
  cost per verified outcome $0.1925.
- not verified: `effort` passed live to either CLI; a `usage_import` during a live run (only
  attached after the fact); the Codex path of `run` mode; Codex quota events (none seen in
  `codex exec --json` output); service-tier and long-context rates against a real bill; whether
  Codex `input_tokens` includes cache writes (all observed values are 0); the prompt-only arm.
- next: run one task both ways (harness and prompting) to get the first ratio row.

## 2026-10-03 — review fixes before commit
- long context: the >272K rates apply only to a record marked `single_request` (a Claude
  message id). Codex turn totals and imported nested records are aggregates: priced at base
  rates, marked `long_context = "unknown"` with a coverage note, never surcharged by guess.
- failed `usage_import` is unknown spend: it counts as 1 unpriced call, so coverage is below
  100%, the run is flagged in `ledger summary`, and cost per verified outcome is null.
- `tasks/codex_smoke_usage.py` takes the harness run dir and selects the one `*-codex-smoke`
  lab run whose `started_at` lies inside that run's agent_started..agent_finished window;
  zero or several matches is an error. Checked on run …824939 (selects 20261003T211124Z-…)
  and on the first, failed run …49dac0 (no match, exit 2).
- deleted tasks/codex-cost-null.md and tasks/codex_cost_null_checks.py (superseded).
- 179 tests pass (2 new, 1 extended for the failed import), ruff clean, mypy --strict clean on run_mode, ledger, pricing. No
  re-priced figure changed.
- known, pre-existing, not fixed: tests/test_adaptive.py::
  test_background_start_returns_while_controller_finishes is flaky (reported by Leo's review;
  it passed in every full run of this session).

## 2026-10-03 — control panel (GUI) added (Cowork session)
- new: src/agent_arena/gui.py + gui.html (stdlib only), `agent-arena gui`, double-click launcher
  `Agent Arena.command` (creates .venv on first run). Run / Grade / New task / live log /
  recent runs / ledger summary. Panel only launches the CLI command it displays.
- run mode gained `--model` and `--effort` overrides (per run, TASK.md unchanged).
- verified: 5 tests in tests/test_gui.py (validation, task writer, jobs, HTTP host/CSRF guards);
  end-to-end in a browser with a fake claude CLI (run → checks 2/2 → report → CSV row).
- not verified: a real claude/codex run launched from the panel (do one live-hello from it).
- next: open the panel, run live-hello, then live-hello-codex.
- 2026-10-03 later: panel gained an Agent selector (Claude/Codex per run; run mode `--agent`
  override, default Claude allowlist when a Codex task is run with Claude). Verified in a browser
  with fake claude/codex CLIs: Codex run priced $0.0058 (1/1 calls) and the table/ledger updated.
