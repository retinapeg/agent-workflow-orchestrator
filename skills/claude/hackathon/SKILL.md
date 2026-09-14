---
name: hackathon
description: Start or inspect the lean Claude-written adaptive Hackathon MVP loop that preserves a last-green judge demo and a user-controlled feature boundary. Use only when the user explicitly invokes Hackathon mode.
disable-model-invocation: true
---

# Hackathon

Use the installed `team` controller. Do not recreate its orchestration in chat.

Before starting, require one compact `DEMO.md` containing the outcome, one to three literal `Beat N`
entries, one surprising moment, `Not building`, the smoke command, and a labelled fallback. A
separate `NOT.md` may elaborate scope but is optional. Together they are the frozen MVP feature
contract for that run. Verify that `team.toml` contains the real demo smoke, project checks, and a
narrow, non-empty `run.allowed_paths`. The adaptive path uses Codex as the read-only planner/reviewer and Claude as the sole isolated MVP
writer; do not add Devin, duplicate implementations, swarms, or a third QA mode.

Start one detached loop and return immediately with its run path and compact status:

```text
team hackathon start <outcome-or-markdown-file> --repo <absolute-repo> --config <absolute-config> --background
team hackathon status --config <absolute-config>
```

Every selected task must improve a named demo beat's runnability, reliability, visibility, truth, or
submission value. The controller runs one task at a time, accepts only config-owned check evidence,
and advances a private last-green SHA only on pass. A rejected or late slice falls back to the prior
green demo. Claude must not edit `DEMO.md`/`NOT.md` or add a capability they do not name. Keep ideas
outside that contract out of the build; when the user explicitly asks for a new feature, update the
contract before a new run. The feature boundary is frozen from the first step; the default run is a
45-minute bounded build burst.

Use `team hackathon stop` when asked to stop. Never merge, push, deploy, publish, submit, or spend
money automatically. Materialize a chosen last-green result only into a new branch/worktree with:

```text
team hackathon integrate <run> --config <config> --source <repo> --branch <new-branch> --worktree <new-path>
```

Keep chat status short: clock/remaining, last-green SHA, accepted beats, active provider/task/deadline,
blocker, next action/scope state, and run/log paths. Ask only for authentication, money,
destructive/external actions, a missing demo contract, or a material product decision.
