# agent-workflow-orchestrator

I wanted to see whether two coding agents catch each other's mistakes, so Codex and Claude build the same task, review each other's diffs, and plain code decides what passes.

**Result:** In the one recorded live run, Codex's review caught a Unicode lowercasing bug in Claude's first implementation, and Claude fixed it in revision.

**Status:** Prototype. Its design fed into [institutional-ai](https://github.com/retinapeg/institutional-ai), and the Unicode catch motivated `agent_reliability_lab` (not public yet).

- Each agent works in its own Git worktree cut from one frozen commit, and every candidate is re-checked in a fresh checkout.
- A model's review can reject a candidate but can never rescue one that fails a required check.
- The live run (n=1) is one observation, not a rate. Everything else, including 196 tests, runs offline with scripted agents.

**New in October 2026:** a single-agent `run` mode, task queues and a local control panel. The harness runs Claude Code or Codex headless, checks each step itself, prices every call, and writes a build journal per queue. See [docs/RUN_MODE.md](docs/RUN_MODE.md).

[Technical details →](docs/GUIDE.md)
