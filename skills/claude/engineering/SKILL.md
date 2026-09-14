---
name: engineering
description: Start or inspect the lean Claude-planned, Codex-written adaptive engineering loop for a real repository. Use only when the user explicitly invokes engineering mode.
disable-model-invocation: true
---

# Engineering

Use the installed `team` controller. Do not recreate its orchestration in chat.

Before starting, read the repository instructions and verify that `team.toml` names only the real
project-owned checks needed for acceptance. The adaptive path requires engineers named `claude`
(read-only planner) and `codex` (sole isolated writer). Do not add Devin, a second writer, a swarm,
or model-written shell commands.

Start one detached loop and return immediately with its run path and compact status:

```text
team engineering start <goal-or-markdown-file> --repo <absolute-repo> --config <absolute-config> --background
team engineering status --config <absolute-config>
```

The controller must choose one bounded task from current last-green evidence, dispatch Codex in its
private worktree, run only config-owned checks in fresh validation worktrees, advance last-green on
pass, and repeat. Prefer correctness, primary evidence, the smallest root-cause change, and refusal
of speculative features. A provider return or exit zero is not acceptance.

Use `team engineering stop` when asked to stop; it takes effect at the bounded phase boundary. Never
merge, push, deploy, publish, or replace the source branch automatically. When the user explicitly
chooses a result, materialize it only in a new branch/worktree:

```text
team engineering integrate <run> --config <config> --source <repo> --branch <new-branch> --worktree <new-path>
```

Keep chat status short: clock/remaining, last-green SHA, accepted tasks, active provider/task/deadline,
blocker, next action, and run/log paths. Ask only for authentication, money, destructive/external
actions, missing acceptance, or a material product decision.
