---
name: hackathon
description: Start or inspect the lean Claude-planned, Codex-written adaptive Hackathon loop that preserves a last-green judge demo. Use only when the user explicitly invokes Hackathon mode.
---

# Hackathon

Use the installed `team` controller. Do not recreate its orchestration in chat.

Before starting, require one compact `DEMO.md` containing the outcome, one to three literal `Beat N`
entries, one surprising moment, `Not building`, the smoke command, and a labelled fallback. A
separate `NOT.md` may elaborate scope but is optional. Verify that `team.toml` contains the real demo
smoke and project checks. The adaptive path uses Claude as read-only planner and one isolated Codex
writer; do not add Devin, duplicate implementations, swarms, or a third QA mode.

Start one detached loop and return immediately with its run path and compact status:

```text
team hackathon start <outcome-or-markdown-file> --repo <absolute-repo> --config <absolute-config> --background
team hackathon status --config <absolute-config>
```

Every selected task must improve a named demo beat's runnability, reliability, visibility, truth, or
submission value. The controller runs one task at a time, accepts only config-owned check evidence,
and advances a private last-green SHA only on pass. A rejected or late slice falls back to the prior
green demo. For a 24-hour event, feature freeze is H12 and code freeze is H18; scale those freezes to
the event clock supplied in the goal. The default run is a 45-minute bounded build burst.

Use `team hackathon stop` when asked to stop. Never merge, push, deploy, publish, submit, or spend
money automatically. Materialize a chosen last-green result only into a new branch/worktree with:

```text
team hackathon integrate <run> --config <config> --source <repo> --branch <new-branch> --worktree <new-path>
```

Keep chat status short: clock/remaining, last-green SHA, accepted beats, active provider/task/deadline,
blocker, next action/freeze, and run/log paths. Ask only for authentication, money,
destructive/external actions, a missing demo contract, or a material product decision.
