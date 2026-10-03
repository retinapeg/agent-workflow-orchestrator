"""Nested usage for tasks/codex-smoke.md. Run with cwd = agent_reliability_lab.

    codex_smoke_usage.py HARNESS_RUN_DIR

Prints {"source": ..., "calls": [...]} for the lab run that THIS harness run produced: the one
results/runs/*-codex-smoke run whose started_at lies between the harness run's agent_started
and agent_finished events. No match, or more than one, is an error (exit 2): usage is never
taken from some other run. One entry per model call the lab made, as raw token counts; every
entry is the total of one CLI invocation, so none is marked as a single request. The harness
prices them; this script never computes a cost. Stdlib only; reads files, never writes.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

LABEL = os.environ.get("LAB_LABEL", "codex-smoke")  # set via TASK.md env


def _claude_split(run: Path, call: dict) -> dict:
    """5m/1h cache-write split from the raw trace, when the gitignored raw file is present."""

    trace = call.get("raw_trace")
    path = run / trace if isinstance(trace, str) else None
    if path is None or not path.is_file():
        return {}
    try:
        events = json.loads(path.read_text()).get("events") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    for event in reversed(events):
        if isinstance(event, dict) and event.get("type") == "result":
            split = (event.get("usage") or {}).get("cache_creation")
            if isinstance(split, dict):
                return {
                    "cache_write_5m": split.get("ephemeral_5m_input_tokens"),
                    "cache_write_1h": split.get("ephemeral_1h_input_tokens"),
                }
    return {}


def convert(run: Path, call: dict) -> dict:
    usage = call.get("usage") or {}
    models = call.get("models_used") or []
    out = {
        "provider": call.get("provider"),
        "role": call.get("role"),
        "model": models[0] if len(models) == 1 else call.get("model"),
        "output": usage.get("output_tokens"),
        "cli_reported_cost": call.get("list_cost_usd"),
    }
    if call.get("provider") == "codex_cli":
        out.update(
            input=usage.get("input_tokens"),  # includes cached tokens
            cache_read=usage.get("cached_input_tokens"),
            cache_write=usage.get("cache_write_input_tokens"),
            reasoning=usage.get("reasoning_output_tokens"),
            input_includes_cache_read=True,
        )
    else:
        out.update(
            input=usage.get("input_tokens"),
            cache_read=usage.get("cache_read_input_tokens"),
            cache_write=usage.get("cache_creation_input_tokens"),
            reasoning=None,
            not_applicable=["reasoning"],  # Claude bills thinking as output tokens
            input_includes_cache_read=False,
            **_claude_split(run, call),
        )
    return out


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def harness_window(harness_run: Path) -> tuple[datetime, datetime]:
    """When the harnessed agent ran, from the harness run's own events.jsonl."""

    stamps: dict[str, datetime] = {}
    for line in (harness_run / "events.jsonl").read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = _stamp(event.get("timestamp")) if isinstance(event, dict) else None
        if stamp and event.get("event") in {"agent_started", "agent_finished"}:
            stamps[event["event"]] = stamp
    if set(stamps) != {"agent_started", "agent_finished"}:
        raise ValueError(f"{harness_run}/events.jsonl lacks agent_started/agent_finished")
    # the lab records whole seconds, so compare from the start of the harness's first second
    return stamps["agent_started"].replace(microsecond=0), stamps["agent_finished"]


def select_run(runs_root: Path, start: datetime, end: datetime) -> Path:
    """The one lab run with this label that started inside the window."""

    matches = []
    for run in sorted(runs_root.glob(f"*-{LABEL}")):
        try:
            started = _stamp(json.loads((run / "run.json").read_text()).get("started_at"))
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        if started is not None and start <= started <= end:
            matches.append(run)
    if len(matches) != 1:
        found = ", ".join(m.name for m in matches) or "none"
        raise ValueError(
            f"expected exactly one *-{LABEL} lab run started between {start.isoformat()} and "
            f"{end.isoformat()}; found {len(matches)} ({found})"
        )
    return matches[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} HARNESS_RUN_DIR", file=sys.stderr)
        return 2
    try:
        run = select_run(Path("results/runs"), *harness_window(Path(argv[1])))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    episodes = run / "episodes.jsonl"
    if not episodes.is_file():
        print(f"{episodes} missing", file=sys.stderr)
        return 2
    calls = [
        convert(run, call)
        for line in episodes.read_text().splitlines()
        if line.strip()
        for call in json.loads(line).get("calls") or []
    ]
    print(json.dumps({"source": str(run), "calls": calls}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
