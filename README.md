# agent-workflow-orchestrator

**Engineering decisions:** [docs/DECISIONS.md](docs/DECISIONS.md) records accepted policies for Codex-first compute routing, budgeted optional OpenRouter, Claude reserves, explicit Go Mad mode, and acceptance requirements. This is a policy record, not evidence the features have shipped. Check it before changing provider orchestration or billing.

Do two coding agents catch each other's mistakes? Codex and Claude build the same task in separate worktrees, review each other's diffs, and plain code decides what passes.

**Result:** In the one recorded live Codex-versus-Claude contest, Codex's review caught a Unicode lowercasing bug in Claude's first implementation, and Claude fixed it in revision. That is one observation, not a rate; it motivated the controlled experiment in [agent_reliability_lab](https://github.com/retinapeg/agent_reliability_lab).

**Why it matters:** A model's review can reject a candidate but can never rescue one that fails a required check. Scoring reads only deterministic check results, so the harness, not the models, owns the pass/fail decision.

**Status:** Prototype. Its design fed into [institutional-ai](https://github.com/retinapeg/institutional-ai). Later additions (a single-agent `run` mode, task queues, a local control panel and an Anthropic API adapter) have only smoke runs (recorded in `STATE.md` and `results/`; the control panel was exercised only with fake CLIs) and no controlled results.

- Each agent works in its own Git worktree cut from one frozen commit, and every candidate is re-checked in a fresh detached worktree of its snapshot.
- 197 offline tests with scripted providers and fake runners; no credentials or model calls needed.
- The contest record is prose in [VALIDATION.md](VALIDATION.md); its audit archive is kept outside the repository.

**Run mode (October 2026):** the harness runs Claude Code or Codex headless on a task file, checks each step itself, prices each call from a user-supplied pricing table (unpriced calls are recorded as null) and writes a build journal per queue. See [docs/RUN_MODE.md](docs/RUN_MODE.md) for `run` and the cost ledger, and [docs/GUIDE.md](docs/GUIDE.md) for the control panel; queues and build journals are so far described only in `STATE.md` (2026-10-04 entry).

```bash
pip install -e '.[dev]' && pytest -q      # 197 tests collected; all passed on 2026-10-06
```

[Technical details →](docs/GUIDE.md)

## AcquisitionOps: synthetic campaign orchestration

A separate, human-approved campaign workflow now demonstrates expected-value budget optimization, deterministic eligibility, durable SQLite approval and audit, an idempotent mock provider, restart recovery and delayed conversion outcomes. It uses frozen synthetic prospects and **scripted recommendations, with no live model calls or real delivery**.

```sh
PYTHONPATH=src python3 -m agent_arena.acquisition demo
```

This safe entry point does not invoke the coding-agent CLI, queues or worktrees. It starts a local dashboard with a fresh synthetic database. Dashboard browser verification remains pending because this recovery sandbox denies listener binding. The service/action tests and frozen evaluation run without a listener.

[Demo and limitations](docs/acquisition/README.md) · [Evaluation](docs/acquisition/evaluation/report.md) · [Architecture](docs/acquisition/architecture.md) · [Learning guide](LEARNING_GUIDE.md) · [Two-minute demo](docs/acquisition/interview-demo.md) · [Recovery report](RECOVERY_JOB_C_REPORT.md)
