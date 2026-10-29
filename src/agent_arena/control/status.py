"""What Arena is doing right now, as facts for the director (and anyone else).

``arena_status`` answers "what is running?", "what needs me?" and "what just finished?"
from the store alone, so the director can say so whether or not a project is selected.
"""

from __future__ import annotations

import time
from pathlib import Path

from .engine import ACTIVE, TERMINAL
from .story import is_plan_task, is_stalled
from .titles import display_title, goal_title

NEEDS_YOU = {
    "awaiting_input": "Waiting for your answer",
    "ready_for_approval": "A staged change is waiting for your decision",
    "blocked": "Stopped and needs a decision",
    "needs_auth": "A CLI account needs signing in on the Mac",
    "needs_permission": "The host denied a permission",
    "interrupted": "Interrupted; retry or close it",
}
RECENT_LIMIT = 5
NEEDS_YOU_LIMIT = 10  # the newest; needs_you_total says how many there are in all


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def arena_status(store, now: float | None = None) -> dict:
    now = now or time.time()
    projects = {p["id"]: p for p in store.rows("projects")}
    accounts = {a["id"]: a for a in store.rows("accounts")}
    tasks = {t["id"]: t for t in store.rows("tasks")}
    goals = {g["id"]: g for g in store.rows("goals")}

    def name(task):
        project = projects.get(task["project_id"]) or {}
        return project.get("name") or task["project_id"]

    def title(task):
        goal = goals.get(task.get("goal_id"))
        if goal and is_plan_task(task):  # the director's plan is named after its build
            named = goal_title(goal)
            if named:
                return named
        return display_title(task.get("title")) or display_title(task.get("instructions")) or "?"

    def live(task):
        """Not archived, as a project or a build (story.outcomes leaves both out too)."""
        project = projects.get(task["project_id"]) or {}
        goal = goals.get(task.get("goal_id")) or {}
        return not project.get("archived") and not goal.get("archived")

    running = []
    for attempt in store.rows("attempts"):
        if attempt["state"] not in ACTIVE:
            continue
        task = tasks.get(attempt["task_id"])
        if not task or not live(task):
            continue
        account = accounts.get(attempt["account_id"]) or {}
        written = None  # when the agent last wrote to its transcript
        if attempt.get("artifact_dir"):
            try:
                written = (Path(attempt["artifact_dir"]) / "transcript.jsonl").stat().st_mtime
            except OSError:
                written = None
        running.append(
            {
                "project": name(task),
                "title": title(task),
                "phase": attempt.get("phase") or task.get("phase"),
                "state": attempt["state"],
                "agent": account.get("name") or account.get("provider") or attempt["account_id"],
                "model": attempt.get("model"),
                "elapsed_seconds": int(now - attempt.get("created", now)),
                "last_activity_seconds": int(now - written) if written else None,
                "stalled": is_stalled(attempt, written, now),
            }
        )
    waiting = sorted(
        (t for t in tasks.values() if t["state"] in NEEDS_YOU and live(t)),
        key=lambda t: t.get("updated") or 0,
        reverse=True,
    )
    needs_you = []
    for task in waiting[:NEEDS_YOU_LIMIT]:
        why = NEEDS_YOU[task["state"]]
        detail = task.get("question") if task["state"] == "awaiting_input" else task.get("reason")
        if detail:
            why = f"{why}: {display_title(detail, 140) or detail[:140]}"
        needs_you.append({"project": name(task), "title": title(task), "why": why})
    recent = sorted(
        (
            t
            for t in tasks.values()
            if t["state"] in TERMINAL and live(t) and t["state"] not in NEEDS_YOU
        ),
        key=lambda t: t.get("updated", 0),
        reverse=True,
    )[:RECENT_LIMIT]
    return {
        "running": running,
        "needs_you": needs_you,
        "needs_you_total": len(waiting),
        "recent": [
            {
                "project": name(t),
                "title": title(t),
                "outcome": t["state"],
                "when": ago(now - t.get("updated", now)),
            }
            for t in recent
        ],
        "paused": bool(store.setting("paused")),
    }
