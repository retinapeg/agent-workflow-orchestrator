# agent-workflow-orchestrator

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
