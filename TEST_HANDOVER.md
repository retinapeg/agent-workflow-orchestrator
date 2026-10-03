# Handover: test `run` mode live (Claude Code or Codex, on Leo's Mac)

Paste this file's path into a Claude Code or Codex session started in this repo:
`~/agent-reliability-workspace/agent-workflow-orchestrator`. Read `STATE.md` and
`docs/RUN_MODE.md` first.

**Goal:** prove `python -m agent_arena run` works against the real CLIs, then run the
`codex-smoke` dogfood. Report what happened, with evidence.
**Timebox:** 60 minutes of your work (the smoke batch's own runtime is extra). If you're not
done, add progress to STATE.md and stop.

## Rules
- Fix bugs only in `src/agent_arena/run_mode.py`, `tasks/` and `tests/test_run_mode.py`. Add a
  test for every fix. Do not touch the existing arena/review/adaptive modes or their tests.
- Do not edit `agent_reliability_lab` yourself. Only the harnessed agent does, via the task.
- Never write prices into `pricing.toml`. Leo fills it from official pages. If it's missing,
  the cost line saying `null` is correct behaviour, not a bug.
- No commit or push unless Leo says so. Don't fall back from `claude-sonnet-5` to `sonnet`.

## Steps
1. **Offline baseline.** Use a Python 3.11+ venv and run `pip install -e '.[dev]'`, then
   `pytest -q` (expect 159 passed) and `ruff check src tests tasks`.
2. **Flag preflight.** Confirm `claude --help` lists every flag `claude_argv()` uses:
   `--output-format stream-json`, `--verbose`, `--max-turns`, `--permission-mode dontAsk`,
   `--no-session-persistence`, `--strict-mcp-config`, `--mcp-config`, `--tools`,
   `--allowedTools`, `--disallowedTools`. If one is missing or renamed, fix `claude_argv()`
   and the docs.
3. **Cheap live run (pennies).**
   ```
   mkdir -p /tmp/arena-hello && git -C /tmp/arena-hello init -q
   python -m agent_arena run tasks/live-hello.md --repo /tmp/arena-hello
   ```
   Expect exit 0, 2 PASS, a usage table with real numbers, a CLI-reported cost, the model as
   reported, and a new entry in `/tmp/arena-hello/STATE.md`. Open the run dir and check that
   `transcript.jsonl` per-call usage matches the table, with no double counting of one
   message id. If the CLI reports the model under a different name, say so; don't hide it.
4. **Dogfood run (long: up to 60 min, nested Codex + Claude calls).** Run in the background
   (your Bash default timeout is too short) and poll:
   ```
   python -m agent_arena run tasks/codex-smoke.md \
     --repo ~/agent-reliability-workspace/agent_reliability_lab
   ```
   Known unknowns to watch for:
   - Does the permission rule `Bash(python3 -m agent_reliability run:*)` allow the
     `> /tmp/codex-smoke.log 2>&1` redirect? If it's denied, the transcript shows a permission
     denial and the log check is UNKNOWN. Propose the smallest allowlist change and ask Leo
     before rerunning.
   - The `models_used` check expects exactly `claude-sonnet-5`. Plain `sonnet` was reported as
     `claude-sonnet-5-5` before. A FAIL that shows the real name is information, not a bug.
5. **Report back** with: the full printed report from both runs; each FAIL/UNKNOWN with its
   cause (harness bug, task wording, permissions, or a real lab result); any fixes with
   their tests; and one next action. Append a dated `## ` entry to this repo's STATE.md.

## Done when
`live-hello` passes, `codex-smoke` has run end to end through the harness with a report
(checks marked PASS/FAIL/UNKNOWN, a token usage table, and a cost figure or `null` with the
reason), and Leo did no copy-pasting.
