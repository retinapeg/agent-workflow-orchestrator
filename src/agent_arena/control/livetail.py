"""What an agent is doing right now, read back from its transcript.

The worker streams the CLI's stdout to ``attempts/<id>/transcript.jsonl`` (Codex JSON lines
or Claude stream-json). ``tail`` reads only the last ``WINDOW`` bytes of that file, parses it
line by line, skips the leading partial line and anything that is not JSON, and maps each
event to one short phone line. It never raises on a growing, truncated or corrupt file.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from pathlib import Path
from typing import Any

from .worker import redact

WINDOW = 64 * 1024
LINES = 60
TEXT_CHARS = 300
KINDS = ("assistant", "tool", "command", "file", "test", "error", "system")
# A test runner invoked as a command (not merely mentioned inside a grep pattern).
_TEST_RUN = re.compile(
    r"(?:^|[\s;&]|&&|\|\|)\s*(?:\S*/)?(?:python[\d.]*\s+-m\s+)?"
    r"(?:pytest|unittest|vitest|jest|npm\s+(?:run\s+)?test|cargo\s+test|go\s+test)\b"
)
_SHELL_PREFIXES = ("/bin/zsh -lc ", "/bin/bash -lc ", "/bin/sh -c ", "zsh -lc ", "bash -lc ")
_FILE_TOOLS = {
    "Edit": "editing",
    "MultiEdit": "editing",
    "Write": "writing",
    "NotebookEdit": "editing",
    "Read": "reading",
}


def _text(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= TEXT_CHARS else text[: TEXT_CHARS - 1] + "…"


def _command_kind(command: str) -> str:
    return "test" if _TEST_RUN.search(command) else "command"


def _unwrap_shell(command: str) -> str:
    for prefix in _SHELL_PREFIXES:
        if command.startswith(prefix):
            rest = command[len(prefix) :].strip()
            try:
                parts = shlex.split(rest)
            except ValueError:
                return rest
            return parts[0] if len(parts) == 1 else rest
    return command


def _relative(path: str, workspace: str | None) -> str:
    if workspace and path.startswith(workspace.rstrip("/") + "/"):
        return path[len(workspace.rstrip("/")) + 1 :]
    return path


def _codex(event: dict[str, Any], workspace: str | None) -> tuple[str, str] | None:
    kind = event.get("type")
    item: dict[str, Any] = event.get("item") if isinstance(event.get("item"), dict) else {}
    if kind == "item.completed" and item.get("type") == "agent_message":
        return ("assistant", item.get("text") or "")
    if item.get("type") == "command_execution":
        command = _unwrap_shell(str(item.get("command") or ""))
        if kind == "item.started":
            return (_command_kind(command), "$ " + command)
        code = item.get("exit_code")
        if kind == "item.completed" and code not in (0, None):
            return ("error", f"exit {code} · {command}")
        return None
    if kind == "item.completed" and item.get("type") == "file_change":
        paths = [
            f"{c.get('kind', 'update')} {_relative(str(c.get('path', '')), workspace)}"
            for c in item.get("changes") or []
            if isinstance(c, dict)
        ]
        return ("file", ", ".join(paths) or "changed files")
    if kind == "item.completed" and item.get("type") == "error":
        return ("error", item.get("message") or item.get("text") or "error")
    if kind in {"turn.failed", "error"}:
        message = event.get("error") or event.get("message") or ""
        if isinstance(message, dict):
            message = message.get("message") or json.dumps(message)
        return ("error", str(message) or kind)
    if kind == "thread.started":
        return ("system", "Session started")
    if kind == "turn.started":
        return ("system", "Turn started")
    if kind == "turn.completed":
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        out = usage.get("output_tokens")
        return ("system", f"Turn completed · {out} output tokens" if out else "Turn completed")
    return None


def _claude_tool(block: dict[str, Any], workspace: str | None) -> tuple[str, str]:
    name = str(block.get("name") or "tool")
    inputs: dict[str, Any] = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name == "Bash" and isinstance(inputs.get("command"), str):
        return (_command_kind(inputs["command"]), "$ " + inputs["command"])
    if name in _FILE_TOOLS:
        path = inputs.get("file_path") or inputs.get("notebook_path") or inputs.get("path")
        if isinstance(path, str) and path:
            return ("file", f"{_FILE_TOOLS[name]} {_relative(path, workspace)}")
    if name in {"Grep", "Glob"}:
        target = inputs.get("pattern") or ""
        where = inputs.get("path") or ""
        return (
            "tool",
            f"{name} {target}" + (f" in {_relative(str(where), workspace)}" if where else ""),
        )
    detail = inputs.get("description") or inputs.get("query") or inputs.get("prompt") or ""
    return ("tool", f"{name} {detail}".strip())


def _claude(event: dict[str, Any], workspace: str | None) -> list[tuple[str, str]]:
    kind = event.get("type")
    if event.get("parent_tool_use_id"):
        return []
    if kind == "system":
        if event.get("subtype") == "init":
            model = event.get("model")
            return [("system", f"Session started · {model}" if model else "Session started")]
        return []
    message: dict[str, Any] = event.get("message") if isinstance(event.get("message"), dict) else {}
    blocks: list[Any] = message.get("content") if isinstance(message.get("content"), list) else []
    if kind == "assistant":
        out = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                out.append(("assistant", block["text"]))
            elif block.get("type") == "tool_use":
                out.append(_claude_tool(block, workspace))
        return out
    if kind == "user":
        out = []
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("is_error")
            ):
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                out.append(("error", str(content or "tool error")))
        return out
    if kind == "result":
        subtype = event.get("subtype") or "finished"
        cost = event.get("total_cost_usd")
        text = f"Finished · {subtype}" + (
            f" · ${cost:.2f}" if isinstance(cost, int | float) else ""
        )
        return [("error" if event.get("is_error") else "system", text)]
    if kind == "error":
        return [("error", str(event.get("message") or event.get("error") or "error"))]
    return []


def parse_lines(
    raw: str, *, workspace: str | None = None, limit: int = LINES
) -> list[dict[str, Any]]:
    """Map transcript lines (either provider) to the last ``limit`` phone lines."""
    out = []
    for line in raw.split("\n"):  # not splitlines(): JSON text may carry U+2028/U+2029
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        mapped = (
            _claude(event, workspace)
            if kind in {"system", "assistant", "user", "result", "stream_event", "rate_limit_event"}
            or "message" in event
            else [m for m in [_codex(event, workspace)] if m]
        )
        for kind, text in mapped:
            text = _text(redact(text))
            if text:
                out.append({"t": None, "kind": kind, "text": text})
    return out[-limit:]


def read_window(path: Path, window: int = WINDOW) -> tuple[str, int, float | None]:
    """The last ``window`` bytes of ``path`` as text (first partial line dropped), its size
    and mtime. A missing or unreadable file reads as empty."""
    try:
        size = path.stat().st_size
        mtime = path.stat().st_mtime
        with path.open("rb") as handle:
            if size > window:
                handle.seek(size - window)
                chunk = handle.read(window)
                chunk = chunk.split(b"\n", 1)[1] if b"\n" in chunk else b""
            else:
                chunk = handle.read(window)
    except OSError:
        return "", 0, None
    return chunk.decode("utf-8", errors="replace"), size, mtime


def tail(
    attempt: dict[str, Any], *, provider: str | None, done: bool, now: float | None = None
) -> dict[str, Any]:
    """The live view of one attempt: metadata plus the parsed transcript tail."""
    now = now or time.time()
    artifact_dir = attempt.get("artifact_dir")
    path = Path(artifact_dir) / "transcript.jsonl" if artifact_dir else None
    raw, size, mtime = read_window(path) if path else ("", 0, None)
    started = attempt.get("created")
    result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
    ended = attempt.get("ended") or result.get("provider_ended")
    if not isinstance(ended, int | float):
        ended = None
    if done and ended is None and isinstance(started, int | float):
        # An interrupted attempt records no end: use the worker's own wall time, else the
        # moment its transcript stopped growing, else report nothing rather than a clock
        # that keeps running forever.
        seconds = result.get("seconds")
        if isinstance(seconds, int | float):
            ended = started + seconds
        elif mtime:
            ended = max(mtime, started)
    elapsed = None
    if isinstance(started, int | float) and (not done or ended is not None):
        elapsed = round(((ended if done and ended else now) - started), 1)
    return {
        "state": attempt.get("state"),
        "phase": attempt.get("phase"),
        "model": attempt.get("model"),
        "provider": provider,
        "started": started,
        "elapsed": elapsed,
        "bytes": size,
        "updated": mtime,
        "done": done,
        "lines": parse_lines(raw, workspace=attempt.get("workspace")),
    }


__all__ = ["KINDS", "LINES", "WINDOW", "parse_lines", "read_window", "tail"]
