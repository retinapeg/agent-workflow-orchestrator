"""The plain-English story of a build (goal) and of each of its blocks (tasks).

The functions below are pure: they read plain dicts (the goal, its project, tasks with
their ``dependencies``, attempts, approvals, accounts, events and live transcript tails) and
return what the phone shows, so they unit-test without a server. ``load_goal_story`` and
``load_task_story`` read one goal's records in a single read transaction and call them.

Default text is for the owner, not for engineers: no source code, terminal output, git
hashes or file names (``plain`` removes them). Technical evidence appears only under
``advanced``. Nothing is invented: every sentence comes from a recorded state, event,
attempt outcome, review or transcript line, and a field whose record is missing is null.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import time
from collections import defaultdict
from typing import Any

from .engine import ACTIVE, QUEUED
from .titles import display_title, goal_title
from .worker import redact

STAGE_LABELS = ("Design", "Implement", "Test", "Review", "Merge")
# A live agent that has not written to its transcript for this long is shown as stalled.
STALL_SECONDS = 600
NOW_CHARS = 140
PURPOSE_CHARS = 300
UPDATES_LIMIT = 20
DECISIONS_LIMIT = 5
TRIED_LIMIT = 6
TAIL_LINES = 40
FAILED = {"blocked", "interrupted", "needs_auth", "needs_permission"}
RETRYABLE = FAILED
PROVIDERS = {"codex": "Codex", "claude": "Claude"}
STATUS_LABELS = {
    "running": "Building",
    "needs_you": "Needs you",
    "queued": "Queued",
    "completed": "Finished",
    "blocked": "Blocked",
    "cancelled": "Stopped",
}

# -- plain text -------------------------------------------------------------------------

_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
_CODE_SPAN = re.compile(r"`[^`\n]*`?")
_HEX = re.compile(r"(?i)^(?:[0-9a-f]{12,}|(?=[0-9a-f]*\d)[0-9a-f]{7,})$")
_PATHISH = re.compile(r"[/\\]|\w\.\w{1,5}\b|^\.\w")
_CODEISH = re.compile(r"[{}<>=$|#@]|\(\)|::|->|^--?\w|\w_\w|\w\(")
_EDGE = "\"'“”‘’()[]<>,;:!?…*"
_SENTENCE_PUNCT = ".,;:!?"
_GAP = "\x00"  # where technical text was removed, until the sentence is tidied
_PREP = r"(?:in|at|to|from|of|for|on|with|into|inside|under|via|by|as)"
# Little words left pointing at removed text: "code in <gap>." → "code.", "Copy in <gap>
# with feedback" → "Copy with feedback". Only words next to a gap are touched.
_DANGLING = re.compile(
    rf"(?:\s+\b(?:{_PREP}|the|a|an|this|that|its|and|or)\b)+(?:\s*{_GAP})+(?=\s*(?:[.,;:!?]|$))",
    re.I,
)
_NEXT = (
    rf"{_PREP}|and|or|but|so|then|after|before|while|when|because|since|including|"
    r"without|which|that"
)
_DOUBLED = re.compile(rf"\b{_PREP}(?:\s*{_GAP})+\s*(?=(?:{_NEXT})\b)", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[\"'“‘(]?[A-Z0-9])")
_EMPHASIS = re.compile(r"\*\*|__|(?<![\w*])\*(?=\w)|(?<=[\w.!?])\*(?![\w*])")


def _unmark(text: str) -> str:
    """Markdown emphasis off ("**Done.**" → "Done."); snake_case stays code."""
    return _EMPHASIS.sub("", text)


def _technical(token: str) -> bool:
    core = token.strip(_EDGE + ".")
    if not core:
        return False
    return bool(_PATHISH.search(core) or _HEX.match(core) or _CODEISH.search(core))


def clip_words(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a word boundary with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-–—(") + "…"


def plain(text: Any, limit: int = 300) -> str:
    """Owner-facing text: code blocks and spans, file paths and names, hashes and
    code-looking tokens removed, whitespace and punctuation tidied, at most ``limit``
    characters. Secrets are redacted first."""
    value = _unmark(redact(str(text or "")).replace(_GAP, " "))
    value = _FENCE.sub(f" {_GAP} ", value)
    value = _CODE_SPAN.sub(f" {_GAP} ", value)
    words = []
    for token in value.split():
        if token != _GAP and _technical(token):
            stripped = token.rstrip(_EDGE + ".")
            trail = token[len(stripped) :]
            words.append(_GAP)
            kept = [c for c in trail if c in _SENTENCE_PUNCT]
            if kept:
                words.append(kept[-1])
            continue
        words.append(token)
    out = " ".join(words)
    out = _DOUBLED.sub("", out)
    out = _DANGLING.sub("", out)
    out = out.replace(_GAP, " ")
    out = re.sub(r"\(\s*\)|\[\s*\]|“\s*”|\"\s*\"|‘\s*’", "", out)
    previous = None
    while out != previous:
        previous = out
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
        out = re.sub(r"([,;:])(?=[,.;:!?])", "", out)
        out = re.sub(r"([.!?])[.]+", r"\1", out)
        out = re.sub(r"\s{2,}", " ", out)
    out = out.strip().lstrip(",;:.-–—) ").strip()
    if not re.search(r"[A-Za-z]{2}", out):
        return ""
    return clip_words(out, limit)


def first_sentence(text: Any) -> str:
    """The first sentence of the first line that has words in it (markdown markers off)."""
    for line in _FENCE.sub("\n", str(text or "")).split("\n"):
        line = re.sub(r"^\s{0,3}#{1,6}\s+|^\s*(?:[-*+>]|\d+[.)])\s+", "", line)
        line = _unmark(line).strip()
        if re.search(r"[A-Za-z]", line):
            return _SENTENCE.split(line, maxsplit=1)[0].strip()
    return ""


def plain_title(text: Any, fallback: str = "") -> str:
    return plain(display_title(str(text or "")), 80) or fallback


# -- what an agent is doing now ---------------------------------------------------------

READERS = {
    "rg",
    "grep",
    "egrep",
    "fgrep",
    "find",
    "fd",
    "ls",
    "cat",
    "sed",
    "head",
    "tail",
    "wc",
    "nl",
    "less",
    "more",
    "tree",
    "awk",
    "stat",
    "file",
    "bat",
    "du",
    "ag",
}
NODE_TOOLS = {"npm", "pnpm", "yarn", "bun"}
BUILDERS = {"tsc", "vite", "webpack", "esbuild", "rollup", "parcel"}
SCRIPTS = re.compile(r"^(?:python[\d.]*|node|nodejs|deno|bun|tsx|ts-node|ruby|perl|php)$")
WRAPPERS = {"sudo", "env", "time", "nohup", "command", "exec", "nice", "caffeinate"}
SKIPPED = {"cd", "pushd", "popd", "export", "set", "source", ".", "true", ":", "unset"}
_SEGMENTS = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _describe_program(prog: str, args: list[str]) -> str:
    if prog in {"npx", "pnpx", "bunx"} and args:
        return _describe_program(os.path.basename(args[0]), args[1:])
    if prog in NODE_TOOLS and args[:1] in (["exec"], ["dlx"]) and len(args) > 1:
        return _describe_program(os.path.basename(args[1]), args[2:])
    if prog == "git":
        return "Checking the project history"
    if prog in READERS:
        return "Reading the code"
    first = args[0] if args else ""
    if (
        (prog in NODE_TOOLS and first in {"install", "i", "ci", "add"})
        or (re.match(r"^pip[\d.]*$", prog) and first == "install")
        or (prog == "uv" and (first in {"sync", "add"} or args[:2] == ["pip", "install"]))
        or (prog in {"poetry", "bundle", "brew"} and first in {"install", "add"})
        or (SCRIPTS.match(prog) and args[:3] == ["-m", "pip", "install"])
    ):
        return "Installing dependencies"
    if (
        prog in BUILDERS
        or (prog in NODE_TOOLS and (first == "build" or args[:2] == ["run", "build"]))
        or (prog in {"next", "cargo", "go"} and first == "build")
    ):
        return "Building the app"
    if SCRIPTS.match(prog):
        return "Running a script"
    return "Running a command"


def _describe_command(command: str) -> str:
    for segment in _SEGMENTS.split(command.strip()):
        words = _words(segment)
        while words and (re.match(r"^[A-Za-z_]\w*=", words[0]) or words[0] in WRAPPERS):
            words = words[1:]
        if words and words[0] == "timeout" and len(words) > 1:
            words = words[2:]
        if not words:
            continue
        prog = os.path.basename(words[0])
        if prog in SKIPPED:
            continue
        return _describe_program(prog, words[1:])
    return "Running a command"


def _describe_tool(text: str) -> str:
    name = text.split()[0] if text.split() else ""
    if name in {"Grep", "Glob", "LS", "Read", "NotebookRead"}:
        return "Reading the code"
    if name == "TodoWrite":
        return "Updating its to-do list"
    if name in {"WebSearch", "WebFetch"}:
        return "Looking something up online"
    if name in {"Task", "Agent"}:
        return "Handing part of the work to a helper agent"
    return "Using a tool"


_JSON_TEXT = re.compile(r'"(?:summary|reply|outcome)"\s*:\s*"((?:[^"\\]|\\.)*)')


def _agent_sentence(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        # A structured answer (the build, plan or review JSON): its own summary, if any.
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            spoken = next(
                (
                    parsed[k]
                    for k in ("summary", "reply", "outcome")
                    if isinstance(parsed.get(k), str)
                ),
                None,
            )
            if spoken is None:
                return "Finishing its review" if "approved" in parsed else None
            stripped = spoken
        else:
            match = _JSON_TEXT.search(stripped)
            if not match:
                return "Finishing its review" if '"approved"' in stripped else None
            stripped = match.group(1).replace('\\"', '"').replace("\\n", "\n")
    sentence = plain(first_sentence(stripped), NOW_CHARS)
    return sentence or None


def describe_line(line: dict[str, Any]) -> str | None:
    """One live-tail line ({kind, text}, see livetail) in plain English, or None for a line
    that says nothing to the owner (system lines, empty agent text)."""
    kind = line.get("kind")
    text = str(line.get("text") or "")
    if kind == "test":
        return "Running the tests"
    if kind == "command":
        return _describe_command(text.removeprefix("$ "))
    if kind == "file":
        return "Reading the code" if text.lower().startswith("reading") else "Editing code"
    if kind == "tool":
        return _describe_tool(text)
    if kind == "assistant":
        return _agent_sentence(text)
    if kind == "error":
        return "Hit an error and is working around it"
    return None


# -- stages and states ------------------------------------------------------------------


def is_plan_task(task: dict[str, Any]) -> bool:
    """The director's (or planner's) own task: the goal's root, which plans the work and
    creates the build tasks. Build tasks carry the planner attempt that created them."""
    return not task.get("planner_attempt") and (
        task.get("phase") in {"plan", "replan"} or "planned_tasks" in task
    )


def stage_index(task: dict[str, Any]) -> int:
    """Mirror of web/src/types.ts stageIndex: 0 plan · 1 build · 2 verify · 3 review ·
    4 approve · 5 integrate · 6 done · -1 cancelled."""
    state, phase = task.get("state"), task.get("phase")
    if state == "completed":
        return 6
    if state == "cancelled":
        return -1
    if phase in {"plan", "replan"}:
        return 0
    if state == "integrating":
        return 5
    if state == "ready_for_approval":
        return 4
    if state in {"reviewing", "recovering"} or phase in {"review", "direct"}:
        return 3
    if state == "verifying":
        return 2
    return 1


_RAIL = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 4}
_PHASE_RAIL = {"plan": 0, "replan": 0, "build": 1, "review": 3, "direct": 3}


def _approval_of(task: dict[str, Any], approvals) -> dict[str, Any] | None:
    mine = [
        a
        for a in approvals
        if a.get("task_id") == task["id"] or task["id"] in (a.get("covered_tasks") or [])
    ]
    return max(mine, key=lambda a: a.get("created") or 0) if mine else None


def block_state(task: dict[str, Any], approvals=(), policy="manual") -> str:
    """running | queued | waiting | needs_you | completed | blocked | cancelled."""
    state = task.get("state")
    if state in ACTIVE or state == "integrating":
        return "running"
    if state == "waiting_dependency":
        return "waiting"
    if state in QUEUED:
        return "queued"
    if state in {"awaiting_input", "needs_auth", "needs_permission"}:
        return "needs_you"
    if state == "ready_for_approval":
        approval = _approval_of(task, approvals)
        if approval and approval.get("state") == "pending":
            return "needs_you"
        if approval and (approval.get("publication") or {}).get("state") == "failed":
            return "blocked"
        if approval is None and policy != "auto_local_main":
            return "needs_you"
        return "running"  # accepted under the project's policy and merging now
    if state == "completed":
        return "completed"
    if state in {"blocked", "interrupted"}:
        return "blocked"
    if state == "cancelled":
        return "cancelled"
    return "queued"


# Work an agent is doing right now, or a change under review or being merged (progress).
IN_FLIGHT = ACTIVE | {"integrating"}
# Task states in which an attempt's agent is really at work (agents_working).
WORKING = ACTIVE - {"leased", "recovering"}
# A dependency in one of these states will not finish without the owner (archive guards).
HALTED = {"blocked", "interrupted", "cancelled", "needs_auth", "needs_permission"}
MERGING = {"queued", "running"}  # publication states of a change on its way into main


def stuck_waits(tasks) -> set[str]:
    """Ids of tasks waiting on a dependency that stopped, directly or through another
    such task: nothing starts them until the owner restarts what they wait for."""
    states = {t["id"]: t.get("state") for t in tasks}
    stuck: set[str] = set()
    grew = True
    while grew:
        grew = False
        for t in tasks:
            if (
                t.get("state") == "waiting_dependency"
                and t["id"] not in stuck
                and any(states.get(d) in HALTED or d in stuck for d in t.get("dependencies") or [])
            ):
                stuck.add(t["id"])
                grew = True
    return stuck


def still_going(tasks, approvals=(), policy="manual", *, owner_waits=False) -> set[str]:
    """Ids of a project's tasks whose work is still going, so their build is not archived
    and (without ``owner_waits``) neither is their project: an agent on it, queued (unless
    it waits on a stopped dependency), integrating, or its verified change being merged into
    main (a publication queued or running, whatever the policy). With ``owner_waits`` work
    waiting on the owner counts too: a question, or a verified change awaiting approval
    (the story says "Needs you"). ``tasks`` carry their ``dependencies``."""
    stuck = stuck_waits(tasks)
    merging: set[str] = set()
    for approval in approvals:
        if (approval.get("publication") or {}).get("state") in MERGING:
            merging.add(approval.get("task_id"))
            merging.update(approval.get("covered_tasks") or [])
    going = set()
    for t in tasks:
        state = t.get("state")
        if state == "ready_for_approval":
            block = block_state(t, approvals, policy)
            busy = block == "running" or (owner_waits and block == "needs_you")
        else:
            busy = (
                state in IN_FLIGHT
                or (state in QUEUED and t["id"] not in stuck)
                or (owner_waits and state == "awaiting_input")
            )
        if busy or t["id"] in merging:
            going.add(t["id"])
    return going


OWNER_STATES = {"awaiting_input", "needs_auth", "needs_permission"}


def waiting_for_owner(tasks, approvals=(), policy="manual") -> set[str]:
    """Ids of tasks waiting for the owner: a question, an agent account to sign in again, a
    permission, or a verified change awaiting approval (the story's "Needs you"). Their
    project is not archived: archived projects leave the needs-you lists, and a decision
    must never be hidden."""
    return {
        t["id"]
        for t in tasks
        if t.get("state") in OWNER_STATES
        or (
            t.get("state") == "ready_for_approval"
            and block_state(t, approvals, policy) == "needs_you"
        )
    }


def state_label(task: dict[str, Any], block: str) -> str:
    if block == "running":
        state, phase = task.get("state"), task.get("phase")
        if phase in {"plan", "replan"}:
            return "Planning"
        if state == "verifying":
            return "Testing"
        if state in {"integrating", "ready_for_approval"}:
            return "Integrating"
        if state == "reviewing" or phase in {"review", "direct"}:
            return "Reviewing"
        return "Implementing"
    return {
        "queued": "Queued",
        "waiting": "Waiting",
        "needs_you": "Needs you",
        "completed": "Done",
        "blocked": "Blocked",
        "cancelled": "Stopped",
    }.get(block, "Queued")


# -- why work stopped ---------------------------------------------------------------------

WHAT_FAILED = {
    "timeout": "The agent ran out of time before it finished.",
    "checks": "The project's automatic checks did not pass.",
    "review_rejected": "An independent reviewer found problems and rejected the change.",
    "director_rejected": "The director checked the result and did not accept it.",
    "needs_auth": "An agent account on the Mac needs to be signed in again.",
    "needs_permission": "The Mac refused a permission the agent needed.",
    "rate_limited": "The agent hit its usage limit.",
    "connection": "The agent lost its connection to its AI service.",
    "model_unavailable": "The AI model this step needed was not available.",
    "interrupted": "The agent was interrupted before it finished.",
    "no_agent": "No agent was available to take this step.",
    "no_changes": "The agent finished without changing anything.",
    "bad_response": "The agent's answer was not in a form Arena could use.",
    "scope": "The agent changed files it was not allowed to touch.",
    "merge_conflict": "Arena could not merge the verified change into the project.",
    "owner_rejected": "You did not accept the staged change.",
    "unknown": "Stopped with an error Arena could not classify.",
}
CLAUSES = {
    "timeout": "it ran out of time",
    "checks": "the project's checks did not pass",
    "review_rejected": "the reviewer asked for changes",
    "director_rejected": "the director asked for changes",
    "needs_auth": "its account needed signing in",
    "needs_permission": "the Mac refused a permission it needed",
    "rate_limited": "it hit its usage limit",
    "connection": "it lost its connection",
    "model_unavailable": "its AI model was not available",
    "interrupted": "it was interrupted",
    "no_agent": "no agent was available",
    "no_changes": "it did not change anything",
    "bad_response": "its answer could not be used",
    "scope": "it touched files it was not allowed to",
    "merge_conflict": "Arena could not merge the change",
    "owner_rejected": "you did not accept the change",
    "unknown": "it stopped with an error Arena could not classify",
}
# The same classes as a short phrase for the updates list ("Stopped: …").
STOPPED = {
    "timeout": "the agent ran out of time",
    "checks": "the checks did not pass",
    "review_rejected": "the reviewer asked for changes",
    "director_rejected": "the director asked for changes",
    "needs_auth": "an agent account needs signing in",
    "needs_permission": "the Mac refused a permission",
    "rate_limited": "the agent hit its usage limit",
    "connection": "the agent lost its connection",
    "model_unavailable": "the AI model was not available",
    "interrupted": "the agent was interrupted",
    "no_agent": "no agent was available",
    "no_changes": "the agent changed nothing",
    "bad_response": "the agent's answer could not be used",
    "scope": "the agent touched files it was not allowed to",
    "merge_conflict": "the change could not be merged",
    "owner_rejected": "you did not accept the change",
    "unknown": "an error Arena could not classify",
}
OWNER_REJECTED = {"Change rejected; staging and evidence preserved", "Combined change not accepted"}


def classify(
    reason: str | None,
    *,
    state: str | None = None,
    status: str | None = None,
    phase: str | None = None,
    timed_out: bool = False,
) -> str:
    """The class of a recorded stop: a task's or attempt's reason, its state, the attempt's
    result status and phase. Unknown reasons are "unknown" (shown only under Advanced)."""
    low = (reason or "").lower()
    if state == "needs_auth" or status == "needs_auth":
        return "needs_auth"
    if state == "needs_permission" or status == "needs_permission":
        return "needs_permission"
    if status == "rate_limited" or state == "cooldown":
        return "rate_limited"
    if status == "transient":
        return "connection"
    if status == "model_unavailable" or "no director model is available" in low:
        return "model_unavailable"
    if "no supported model route" in low or "no healthy eligible slot" in low:
        return "no_agent"
    if (
        state == "interrupted"
        or status == "interrupted"
        or any(
            s in low
            for s in (
                "interrupted worker",
                "recovery budget exhausted",
                "could not confirm its process",
            )
        )
    ):
        return "interrupted"
    if timed_out or "timeout=true" in low or "took longer than" in low:
        return "timeout"
    if "checks did not all pass" in low or "staging checks failed" in low:
        return "checks"
    if "review rejected" in low:
        return "director_rejected" if phase == "direct" else "review_rejected"
    if "no candidate changes" in low:
        return "no_changes"
    if any(
        s in low
        for s in (
            "valid json object",
            "nonempty file operations",
            "does not match the required schema",
            "lacks an explicit structured approval",
        )
    ):
        return "bad_response"
    if "exceeded its declared file scope" in low:
        return "scope"
    if low.startswith("integration preserved for inspection"):
        return "merge_conflict"
    if (reason or "").strip() in OWNER_REJECTED:
        return "owner_rejected"
    return "unknown"


def attempt_class(attempt: dict[str, Any]) -> str:
    result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
    invocation = result.get("invocation") if isinstance(result.get("invocation"), dict) else {}
    return classify(
        result.get("reason") or attempt.get("reason"),
        status=result.get("status") or attempt.get("state"),
        phase=attempt.get("phase"),
        timed_out=bool(invocation.get("timed_out")),
    )


def _agent_name(attempt: dict[str, Any], accounts: dict[str, Any]) -> str:
    account = accounts.get(attempt.get("account_id")) or {}
    routing = attempt.get("routing") if isinstance(attempt.get("routing"), dict) else {}
    provider = account.get("provider") or routing.get("provider")
    return PROVIDERS.get(provider, "An agent")


def tried_sentence(attempt: dict[str, Any], accounts: dict[str, Any]) -> str | None:
    """What one finished attempt tried, in a sentence (None for live attempts)."""
    state = attempt.get("state")
    if state in ACTIVE:
        return None
    phase = attempt.get("phase")
    passed = state == "passed"
    stopped = state in {"cancelled", "interrupted"}
    clause = CLAUSES[attempt_class(attempt)]
    agent = _agent_name(attempt, accounts)
    routing = attempt.get("routing") if isinstance(attempt.get("routing"), dict) else {}
    action = routing.get("action")
    if phase == "plan":
        if passed:
            return "The director planned the work."
        return f"The director tried to plan the work, but {clause}."
    if phase == "replan":
        if passed:
            return "The director diagnosed the failure and rewrote the instructions."
        return f"The director tried to work out a fix, but {clause}."
    if phase == "direct":
        if passed:
            return "The director checked the result and approved it."
        if not stopped and attempt_class(attempt) == "director_rejected":
            return "The director checked the result and asked for changes."
        return f"The director tried to check the result, but {clause}."
    if phase == "review":
        if passed:
            return f"{agent} reviewed it independently and approved it."
        if not stopped and attempt_class(attempt) == "review_rejected":
            return f"{agent} reviewed it independently and asked for changes."
        return f"{agent} tried to review it, but {clause}."
    opening = {
        "switch_provider": f"Handed to {agent} after a failure; it rebuilt it",
        "increase_effort": f"{agent} tried again with more effort",
        "retry_after_cooldown": f"{agent} tried again after a pause",
    }.get(action, f"{agent} built it")
    if passed:
        return f"{opening} and the checks passed."
    if state == "cancelled":
        return f"{opening}, but it was stopped."
    return f"{opening}, but {clause}."


def _failing_checks(attempts) -> list[str]:
    for attempt in reversed(attempts):
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        checks = result.get("checks")
        if attempt.get("phase") == "build" and isinstance(checks, list) and checks:
            return [
                str(c.get("name") or "check")
                for c in checks
                if isinstance(c, dict) and c.get("status") != "PASS"
            ]
    return []


def failure_class(task: dict[str, Any], attempts, approvals=()) -> str | None:
    """The class of this block's stop (see WHAT_FAILED), or None while it has not stopped.
    ``attempts`` are this task's attempts, oldest first."""
    approval = _approval_of(task, approvals)
    published = (approval or {}).get("publication") or {}
    if task.get("state") == "ready_for_approval" and published.get("state") == "failed":
        return "merge_conflict"
    if task.get("state") not in FAILED:
        return None
    finished = [a for a in attempts if a.get("state") not in ACTIVE]
    last = finished[-1] if finished else {}
    result = last.get("result") if isinstance(last.get("result"), dict) else {}
    invocation = result.get("invocation") if isinstance(result.get("invocation"), dict) else {}
    cls = classify(
        task.get("reason"),
        state=task.get("state"),
        status=result.get("status") or last.get("state"),
        phase=last.get("phase"),
        timed_out=bool(invocation.get("timed_out")),
    )
    if cls == "unknown" and approval and approval.get("state") in {"reject", "request_changes"}:
        return "owner_rejected"
    return cls


def failure(
    task: dict[str, Any],
    *,
    attempts,
    accounts: dict[str, Any],
    approvals=(),
    title: str,
    intended: str,
) -> dict[str, Any] | None:
    """Why this block stopped, in plain English, from recorded state only; None when it
    has not stopped. ``attempts`` are this task's attempts, oldest first."""
    cls = failure_class(task, attempts, approvals)
    if cls is None:
        return None
    what = WHAT_FAILED[cls]
    if cls == "checks":
        names = [n for n in (plain(n, 60) for n in _failing_checks(attempts)) if n]
        if names:
            what = what[:-1] + " (" + ", ".join(names[:3]) + ")."
    live = any(a.get("state") in ACTIVE for a in attempts)
    retryable = (
        task.get("state") in RETRYABLE
        and (task.get("repairs") or 0) < (task.get("max_repairs") or 0)
        and not live
    )
    if cls == "needs_auth":
        action = "sign_in"
        fix = "Sign the agent's account in again on the Mac"
    elif cls == "needs_permission":
        action = "permission"
        fix = "Allow the permission on the Mac"
    else:
        action = "retry" if retryable else "none"
        fix = ""
    if fix:
        follow = (
            ", then tap Retry."
            if retryable
            else ", then ask the director to start this build again."
        )
        step = fix + follow
    elif cls == "merge_conflict" and task.get("state") == "ready_for_approval":
        step = (
            "Arena will not merge it on its own. Make sure the project has no uncommitted "
            "changes on its main branch, then ask the director to try the merge again."
        )
    elif cls == "owner_rejected":
        step = "Nothing happens until you ask the director for a new build that uses your feedback."
    elif retryable:
        step = (
            "Nothing more happens until you tap Retry or ask the director for a different approach."
        )
    else:
        step = (
            "Arena has used all of its automatic repair attempts for this step, so it will not "
            "try again on its own. Ask the director to start a new build with a different "
            "approach."
        )
    if cls == "interrupted" and "could not confirm" in (task.get("reason") or "").lower():
        step = "Check the Mac first: Arena could not confirm the agent stopped. " + step
    if cls == "unknown":
        step += " The technical details are under Advanced."
    tried = [s for s in (tried_sentence(a, accounts) for a in attempts) if s][-TRIED_LIMIT:]
    return {
        "task_id": task["id"],
        "what_failed": f"{title}: {what}",
        "intended": intended,
        "recoverable": retryable or cls == "merge_conflict",
        "tried": tried,
        "next": step,
        "needs_you": True,
        "action": action,
    }


# -- blocks ---------------------------------------------------------------------------


def _created(record: dict[str, Any]) -> float:
    value = record.get("created")
    return float(value) if isinstance(value, int | float) else 0.0


class Records:
    """One goal's recorded data, indexed (still pure: no I/O)."""

    def __init__(
        self,
        goal: dict[str, Any],
        project: dict[str, Any] | None,
        tasks,
        attempts,
        approvals=(),
        accounts: dict[str, Any] | None = None,
        tails: dict[str, Any] | None = None,
        now: float | None = None,
    ):
        self.goal, self.project = goal, project
        self.tasks = sorted(tasks, key=_created)
        self.by_id = {t["id"]: t for t in self.tasks}
        self.attempts_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for attempt in sorted(attempts, key=_created):
            self.attempts_of[attempt["task_id"]].append(attempt)
        self.approvals = list(approvals)
        self.accounts = accounts or {}
        self.tails = tails or {}
        self.now = time.time() if now is None else now
        self.policy = (project or {}).get("integration_policy") or "manual"
        self.dependents: dict[str, list[str]] = defaultdict(list)
        for t in self.tasks:
            for dep in t.get("dependencies") or []:
                self.dependents[dep].append(t["id"])
        self.plan = next((t for t in self.tasks if is_plan_task(t)), None)
        spec = goal.get("specification") if isinstance(goal.get("specification"), dict) else {}
        plan_spec = (self.plan or {}).get("specification")
        plan_spec = plan_spec if isinstance(plan_spec, dict) else {}
        outcome = (
            spec.get("outcome")
            or plan_spec.get("outcome")
            or goal_title({"request": goal.get("request")}, 200)  # the owner's own words
            or goal_title(goal, 200)
        )
        self.outcome = plain(first_sentence(outcome), 200)
        if self.outcome and not self.outcome.endswith(("…", ".", "!", "?")):
            self.outcome += "."
        self.plan_summary = plain(plan_spec.get("summary") or spec.get("summary"), 400)
        # The build's name: its agreed outcome, else the request without legacy chips.
        self.build_title = plain_title(goal_title(goal)) or "Build"

    def work(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks if t is not self.plan]

    def shown(self) -> list[dict[str, Any]]:
        """The blocks: work tasks in dependency order, then by creation; the plan task only
        while planning is in progress or when it is the goal's only task."""
        work = self.work()
        ids = {t["id"] for t in work}
        waiting = {
            t["id"]: sum(1 for d in t.get("dependencies") or [] if d in ids and d != t["id"])
            for t in work
        }
        ready = [t for t in work if not waiting[t["id"]]]
        ordered: list[dict[str, Any]] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for child in self.dependents.get(current["id"], []):
                if child in waiting:
                    waiting[child] -= 1
                    if waiting[child] == 0:
                        ready.append(self.by_id[child])
                        ready.sort(key=_created)
        ordered += [t for t in work if t not in ordered]  # a cycle: keep creation order
        if self.plan and (not work or self.plan.get("state") != "completed"):
            ordered.insert(0, self.plan)
        return ordered

    def title(self, task: dict[str, Any]) -> str:
        if task is self.plan:
            return "Planning"
        return plain_title(task.get("title"), "Build step")

    def purpose(self, task: dict[str, Any]) -> str:
        if task is self.plan:
            return "The director reads the project and splits the work into parts."
        own = plain(task.get("purpose"), PURPOSE_CHARS)
        if own:
            return own
        return f"Part of: {self.outcome}" if self.outcome else "Part of this build."

    def intended(self, task: dict[str, Any]) -> str:
        """What a stopped block was meant to achieve: the director's purpose for it, else
        the build's outcome (no "Part of:" prefix), else its title."""
        own = plain(task.get("purpose"), PURPOSE_CHARS)
        if own:
            return own
        if self.outcome:
            return self.outcome
        title = self.title(task)
        return title if title.endswith(("…", ".", "!", "?")) else title + "."

    def live_attempt(self, task: dict[str, Any]) -> dict[str, Any] | None:
        live = [a for a in self.attempts_of.get(task["id"], []) if a.get("state") in ACTIVE]
        return live[-1] if live else None

    def agent(self, task: dict[str, Any]) -> dict[str, Any] | None:
        attempts = self.attempts_of.get(task["id"], [])
        attempt = self.live_attempt(task) or (attempts[-1] if attempts else None)
        if not attempt:
            return None
        account = self.accounts.get(attempt.get("account_id")) or {}
        routing = attempt.get("routing") if isinstance(attempt.get("routing"), dict) else {}
        return {
            "provider": account.get("provider") or routing.get("provider"),
            "model": attempt.get("model") or routing.get("model"),
            "name": account.get("name"),
        }

    def now_line(self, task: dict[str, Any]) -> tuple[str | None, float | None, bool]:
        """(now, last_activity, stalled) from the live attempt and its transcript tail."""
        attempt = self.live_attempt(task)
        if not attempt:
            return None, None, False
        tail = self.tails.get(attempt["id"]) or {}
        updated = tail.get("updated") if isinstance(tail.get("updated"), int | float) else None
        state = attempt.get("state")
        if state == "verifying" or task.get("state") == "verifying":
            now_text = "Running the project's checks"
        elif state == "leased":
            now_text = "Starting an agent"
        else:
            now_text = next(
                (d for d in (describe_line(x) for x in reversed(tail.get("lines") or [])) if d),
                None,
            )
        return now_text, updated, is_stalled(attempt, updated, self.now)

    def stage(self, task: dict[str, Any]) -> int:
        """The rail position (0 Design … 4 Merge) this block has reached; a stopped block
        sits where it stopped (the phase of its last attempt; the checks are Test, a
        refused or failed merge is Merge)."""
        if task is self.plan:
            return 0
        attempts = self.attempts_of.get(task["id"], [])
        cls = failure_class(task, attempts, self.approvals)
        if cls is not None:
            if cls in {"merge_conflict", "owner_rejected"}:
                return 4
            if cls == "checks":
                return 2
            finished = [a for a in attempts if a.get("state") not in ACTIVE]
            if finished:
                return _PHASE_RAIL.get(finished[-1].get("phase"), 1)
        if task.get("state") == "recovering":
            return _PHASE_RAIL.get(task.get("phase"), 1)
        index = stage_index(task)
        if index < 0:
            attempts = self.attempts_of.get(task["id"], [])
            phase = attempts[-1].get("phase") if attempts else task.get("phase")
            return _PHASE_RAIL.get(phase, 1)
        return _RAIL[index]

    def connects(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        if task is self.plan:
            return []
        out = []
        for dep in task.get("dependencies") or []:
            if dep in self.by_id and self.by_id[dep] is not self.plan:
                out.append({"id": dep, "title": self.title(self.by_id[dep]), "relation": "needs"})
        for child in self.dependents.get(task["id"], []):
            if child in self.by_id:
                out.append(
                    {"id": child, "title": self.title(self.by_id[child]), "relation": "unblocks"}
                )
        return out

    def block(self, task: dict[str, Any]) -> dict[str, Any]:
        state = block_state(task, self.approvals, self.policy)
        position = self.stage(task)
        if task.get("state") == "recovering":
            label = {0: "Planning", 3: "Reviewing"}.get(position, "Implementing")
        else:
            label = state_label(task, state)
        now_text, last_activity, stalled = self.now_line(task)
        return {
            "id": task["id"],
            "title": self.title(task),
            "purpose": self.purpose(task),
            "connects": self.connects(task),
            "agent": self.agent(task),
            "stage": {"index": position, "label": STAGE_LABELS[position]},
            "state": state,
            "state_label": label,
            "now": now_text,
            "last_activity": last_activity,
            "stalled": stalled,
            "updated": task.get("updated"),
        }

    def failure(self, task: dict[str, Any]) -> dict[str, Any] | None:
        return failure(
            task,
            attempts=self.attempts_of.get(task["id"], []),
            accounts=self.accounts,
            approvals=self.approvals,
            title=self.title(task),
            intended=self.intended(task),
        )


def is_stalled(attempt: dict[str, Any], updated: float | None, now: float) -> bool:
    """A live agent with no transcript write for STALL_SECONDS (never while the controller
    runs the checks, when the transcript is complete, or before the agent started)."""
    if attempt.get("state") not in ACTIVE or attempt.get("state") in {"verifying", "leased"}:
        return False
    since = updated if updated is not None else attempt.get("created")
    return isinstance(since, int | float) and now - since >= STALL_SECONDS


# -- the build story ------------------------------------------------------------------


def goal_status(blocks) -> str:
    states = [b["state"] for b in blocks]
    if "running" in states:
        return "running"
    if "needs_you" in states:
        return "needs_you"
    if "blocked" in states:
        return "blocked"
    if "queued" in states or "waiting" in states:
        return "queued"
    if "completed" in states:
        return "completed"
    return "cancelled" if states else "queued"


FALLBACK_SUMMARY = {
    "running": "Arena is working on this build.",
    "needs_you": "This build is waiting for you.",
    "queued": "This build is waiting for an agent to start.",
    "completed": "This build is finished.",
    "blocked": "This build stopped before it finished.",
    "cancelled": "This build was stopped.",
}


def needs_you(records: Records) -> list[dict[str, Any]]:
    out = []
    for task in records.tasks:
        state = task.get("state")
        if state == "awaiting_input":
            text = (task.get("question") or "").strip()[:500] or "The director has a question."
            out.append({"kind": "answer", "text": text, "task_id": task["id"]})
        elif state == "needs_auth":
            out.append(
                {
                    "kind": "sign_in",
                    "text": "An agent account needs to be signed in again on the Mac.",
                    "task_id": task["id"],
                }
            )
        elif state == "needs_permission":
            out.append(
                {
                    "kind": "permission",
                    "text": "The Mac refused a permission an agent needed.",
                    "task_id": task["id"],
                }
            )
        elif (
            state == "ready_for_approval"
            and block_state(task, records.approvals, records.policy) == "needs_you"
        ):
            out.append(
                {
                    "kind": "approve",
                    "text": "A verified change is waiting for your approval.",
                    "task_id": task["id"],
                }
            )
    return out


def progress(records: Records, counted: list[dict[str, Any]]) -> dict[str, Any]:
    """{done, total, in_progress, fraction} over the counted (not cancelled) blocks.
    ``in_progress``: work tasks (not the plan) an agent is working on right now or that are
    under review or being merged (leased, running, verifying, reviewing, recovering,
    integrating, or a verified change on its way into main); queued and waiting work is not
    in progress. ``fraction``: (done + each other block's reached stage on the five-step
    rail / 5) / total, so a build moves as its blocks move from Design towards Merge; a
    stopped block counts the stage it reached."""
    done = sum(1 for b in counted if b["state"] == "completed")

    def in_flight(block: dict[str, Any]) -> bool:
        task = records.by_id[block["id"]]
        if task is records.plan:
            return False
        state = task.get("state")
        return state in IN_FLIGHT or (state == "ready_for_approval" and block["state"] == "running")

    in_progress = sum(1 for b in counted if in_flight(b))
    partial = sum(
        b["stage"]["index"] / len(STAGE_LABELS) for b in counted if b["state"] != "completed"
    )
    fraction = min(1.0, (done + partial) / len(counted)) if counted else 0.0
    return {
        "done": done,
        "total": len(counted),
        "in_progress": in_progress,
        "fraction": round(fraction, 3),
    }


def goal_story(
    goal: dict[str, Any],
    project: dict[str, Any] | None,
    tasks,
    attempts,
    approvals=(),
    accounts: dict[str, Any] | None = None,
    tails: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """GET /api/v1/goals/{id}/story (docs/ROUND3_CONTRACT.md A2)."""
    records = Records(goal, project, tasks, attempts, approvals, accounts, tails, now)
    blocks = [records.block(t) for t in records.shown()]
    status = goal_status(blocks)
    label = STATUS_LABELS[status]
    if status == "running" and all(
        b["state"] != "running" or b["id"] == (records.plan or {}).get("id") for b in blocks
    ):
        label = "Planning"
    counted = [b for b in blocks if b["state"] != "cancelled"]
    failed = [t for t in records.tasks if records.failure(t)]
    failed.sort(key=lambda t: t.get("updated") or 0)
    # Agents working right now: a started agent (running) or its checks (verifying) on a
    # task that is live; not one still starting (leased) or left without its guardian
    # (the task is recovering).
    agents = sum(
        1
        for t in records.tasks
        if t.get("state") in WORKING
        for a in records.attempts_of.get(t["id"], [])
        if a["state"] in {"running", "verifying"}
    )
    updated = max([goal.get("created") or 0] + [t.get("updated") or 0 for t in records.tasks])
    return {
        "goal_id": goal.get("id"),
        "title": records.build_title,
        "project": {"id": project["id"], "name": project.get("name")} if project else None,
        "status": status,
        "status_label": label,
        "started": goal.get("created"),
        "updated": updated,
        "agents_working": agents,
        "progress": progress(records, counted),
        "summary": records.plan_summary or FALLBACK_SUMMARY[status],
        "blocks": blocks,
        "failure": records.failure(failed[-1]) if failed else None,
        "needs_you": needs_you(records),
    }


# -- the block story ------------------------------------------------------------------


def _quoted(titles: list[str]) -> str:
    quoted = [f"“{t}”" for t in titles]
    if len(quoted) <= 2:
        return " and ".join(quoted)
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def what_sentence(records: Records, task: dict[str, Any], block: dict[str, Any]) -> str:
    agent = PROVIDERS.get((block.get("agent") or {}).get("provider"), "An agent")
    state, label = block["state"], block["state_label"]
    if task is records.plan:
        opening = "The director is planning this build."
        if state == "needs_you":
            return opening[:-1] + " and needs your answer before it can continue."
        if state == "completed":
            return "The director planned this build and split it into parts."
        if state in {"blocked", "cancelled"}:
            return "Planning this build stopped before it finished."
        return opening
    title = block["title"]
    title = title if title.endswith(("…", ".", "!", "?")) else title + "."
    if state == "running":
        doing = {
            "Planning": "The director is working out how to fix it now.",
            "Implementing": f"{agent} is building it now.",
            "Testing": "Arena is running the project's checks on it now.",
            "Reviewing": (
                "The director is checking the result now."
                if task.get("phase") == "direct"
                else f"{agent} is reviewing it independently now."
            ),
            "Integrating": "Arena is merging it into the project now.",
        }.get(label, f"{agent} is working on it now.")
    elif state == "queued":
        doing = (
            "It is waiting a little before trying again."
            if task.get("state") == "cooldown"
            else "It is queued and starts when an agent is free."
        )
    elif state == "waiting":
        needed = [c["title"] for c in block["connects"] if c["relation"] == "needs"]
        doing = (
            f"It starts when {_quoted(needed)} {'is' if len(needed) == 1 else 'are'} finished."
            if needed
            else "It is waiting for another part of this build."
        )
    elif state == "needs_you":
        doing = {
            "awaiting_input": "The director needs your answer before it can continue.",
            "ready_for_approval": "It is verified and waiting for your approval.",
            "needs_auth": "It is waiting for an agent account to be signed in on the Mac.",
            "needs_permission": "It is waiting for a permission on the Mac.",
        }.get(task.get("state"), "It is waiting for you.")
    elif state == "completed":
        doing = (
            "It is finished and merged into the project."
            if task.get("integrated_commit")
            else "It is finished."
        )
    elif state == "blocked":
        doing = "It stopped before it was finished."
    else:
        doing = "It was stopped before it finished."
    return f"{title} {doing}"


def connects_sentence(records: Records, task: dict[str, Any], block: dict[str, Any]) -> str:
    if task is records.plan:
        return "Every other part of this build comes from this plan."
    needs = [c for c in block["connects"] if c["relation"] == "needs"]
    unblocks = [c for c in block["connects"] if c["relation"] == "unblocks"]
    parts = []
    if needs:
        finished = all(
            records.by_id[c["id"]].get("state") == "completed"
            for c in needs
            if c["id"] in records.by_id
        )
        verb = "is" if len(needs) == 1 else "are"
        parts.append(
            f"It builds on {_quoted([c['title'] for c in needs])}, which {verb} "
            + ("finished." if finished else "not finished yet.")
        )
    if unblocks:
        verb = "starts" if len(unblocks) == 1 else "start"
        parts.append(f"When it is done, {_quoted([c['title'] for c in unblocks])} {verb}.")
    if parts:
        return " ".join(parts)
    if len(records.work()) <= 1:
        return "It is the only part of this build."
    return "It does not depend on any other part of this build."


def rail(records: Records, task: dict[str, Any], block: dict[str, Any]) -> list[dict[str, str]]:
    position = block["stage"]["index"]
    state = block["state"]
    out = []
    for index, label in enumerate(STAGE_LABELS):
        if task is records.plan:  # the plan itself only designs; its parts do the rest
            first = {"completed": "done", "blocked": "failed", "cancelled": "todo"}
            status = first.get(state, "current") if index == 0 else "todo"
        elif state == "completed" or index < position:
            status = "done"
        elif index == position:
            status = {"blocked": "failed", "cancelled": "todo"}.get(state, "current")
        else:
            status = "todo"
        out.append({"label": label, "state": status})
    return out


_RUNNING_START = {
    "plan": "Started planning",
    "replan": "The director started working out a fix",
    "direct": "The director started checking the result",
    "review": "Under independent review",
}


def updates(
    records: Records, task: dict[str, Any], events, attempts_by_id: dict[str, Any]
) -> list[dict[str, Any]]:
    """What happened to this block, newest first, from recorded events only."""
    plan = task is records.plan
    phase = None
    finished_phase = None
    out: list[dict[str, Any]] = []

    def add(t, text, tone="neutral"):
        if out and out[-1]["text"] == text:
            return
        if out and isinstance(t, int | float) and isinstance(out[-1]["t"], int | float):
            t = max(t, out[-1]["t"])  # a reordered pair keeps the list in time order
        out.append({"t": t, "text": text, "tone": tone})

    # The engine records the task's new state just before "attempt.finished" in the same
    # transaction; told in order, the attempt's outcome comes first.
    events = list(events)
    for i in range(len(events) - 1):
        first, second = events[i], events[i + 1]
        if (
            first.get("kind") == "task.changed"
            and second.get("kind") == "attempt.finished"
            and abs((second.get("time") or 0) - (first.get("time") or 0)) < 1
        ):
            events[i], events[i + 1] = second, first
    for event in events:
        data = event.get("data") or {}
        kind, t = event.get("kind"), event.get("time")
        attempt = attempts_by_id.get(data.get("attempt_id")) or {}
        if kind == "attempt.claimed":
            phase = attempt.get("phase") or phase
        elif kind == "routing.selected" and data.get("action") == "switch_provider":
            add(
                t,
                f"Handed to {PROVIDERS.get(data.get('provider'), 'another agent')} after a failure",
            )
        elif kind == "task.changed":
            state, reason = data.get("state"), data.get("reason") or ""
            if state == "running":
                add(t, _RUNNING_START.get(phase or task.get("phase"), "Started building"))
            elif state == "reviewing":
                add(t, "Under independent review")
            elif state == "verifying":
                add(t, "Running the project's checks")
            elif state == "ready_for_approval":
                add(t, "Verified and staged", "good")
            elif state == "integrating":
                add(t, "Merging")
            elif state == "completed":
                add(t, "Plan ready" if plan else "Finished", "good")
            elif state == "blocked":
                cls = classify(reason, state=state, phase=finished_phase)
                add(t, "Stopped: " + STOPPED[cls], "bad")
            elif state == "repair_queued":
                cls = classify(reason, phase=finished_phase)
                add(
                    t,
                    {
                        "review_rejected": "Fixing what the review found",
                        "director_rejected": "Fixing what the director found",
                        "checks": "Fixing what the checks found",
                    }.get(cls, "Fixing the problem and trying again"),
                )
            elif state in {"cooldown", "waiting_capacity"}:
                add(t, "Waiting for an agent to become available")
            elif state == "waiting_dependency":
                add(t, "Waiting for another part to finish")
            elif state == "awaiting_input":
                add(t, "The director asked you a question")
            elif state == "needs_auth":
                add(t, "Stopped: an agent account needs signing in", "bad")
            elif state == "needs_permission":
                add(t, "Stopped: the Mac refused a permission", "bad")
            elif state == "interrupted":
                add(t, "Interrupted", "bad")
            elif state == "cancelled":
                add(t, "Stopped")
            elif state == "recovering":
                add(t, "Checking on an agent that stopped responding")
            elif state == "ready" and reason == "Explicit retry queued":
                add(t, "Retry requested")
            elif state == "ready" and reason.startswith("Thanks."):
                add(t, "Your answer went to the director")
        elif kind == "attempt.finished":
            status, which = data.get("status"), attempt.get("phase")
            finished_phase = which
            cls = attempt_class(attempt) if attempt else "unknown"
            if status == "passed":
                text = {
                    "build": ("Checks passed", "good"),
                    "review": ("Review approved", "good"),
                    "direct": ("Director approved the result", "good"),
                }.get(which)
                if text:
                    add(t, *text)
            elif status not in {"cancelled"}:
                if which == "review" and cls == "review_rejected":
                    add(t, "Review asked for changes", "bad")
                elif which == "direct" and cls == "director_rejected":
                    add(t, "Director asked for changes", "bad")
                elif which == "build" and cls == "checks":
                    add(t, "Checks failed", "bad")
                elif cls == "timeout":
                    add(t, "Ran out of time", "bad")
                elif which in {"plan", "replan"}:
                    add(
                        t,
                        "Planning failed" if which == "plan" else "The fix could not be planned",
                        "bad",
                    )
                else:
                    add(t, "Attempt failed: " + STOPPED[cls], "bad")
        elif kind == "director.replanned":
            add(t, "Director adjusted the plan")
        elif kind == "approval.decided":
            accepted = data.get("decision") == "accept"
            add(
                t,
                "You accepted the change" if accepted else "You did not accept the change",
                "good" if accepted else "bad",
            )
        elif kind == "integration.integrated":
            add(t, "Merged into main", "good")
        elif kind == "integration.failed":
            add(t, "Merging into main failed", "bad")
        elif kind == "integration.pushed":
            add(t, "Published to GitHub", "good")
    return out[::-1][:UPDATES_LIMIT]


def decisions(records: Records, task: dict[str, Any], events) -> list[str]:
    """Director replans and review/director verdicts, plain English, newest first."""
    found: list[tuple[float, str]] = []
    for event in events:
        if event.get("kind") == "director.replanned":
            said = plain((event.get("data") or {}).get("reason"), 240)
            found.append(
                (
                    event.get("time") or 0,
                    "The director adjusted the plan" + (f": {said}" if said else "."),
                )
            )
    for attempt in records.attempts_of.get(task["id"], []):
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        review = result.get("review") if isinstance(result.get("review"), dict) else None
        if not review or attempt.get("phase") not in {"review", "direct"}:
            continue
        when = attempt.get("ended") or attempt.get("created") or 0
        findings = [f for f in review.get("findings") or [] if isinstance(f, str)]
        finding = plain(findings[0], 240) if findings else ""
        if attempt.get("phase") == "review":
            if review.get("approved"):
                text = "The independent reviewer approved the change."
            else:
                text = "The independent reviewer asked for changes"
                text += f": {finding}" if finding else "."
        else:
            said = plain(review.get("summary"), 240) or finding
            text = (
                "The director approved the result"
                if review.get("approved")
                else "The director asked for changes"
            )
            text += f": {said}" if said else "."
        found.append((when, text))
    found.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in found[:DECISIONS_LIMIT]]


def advanced(records: Records, task: dict[str, Any]) -> dict[str, Any]:
    attempts = records.attempts_of.get(task["id"], [])
    rows = []
    for attempt in attempts:
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        seconds = result.get("seconds")
        if not isinstance(seconds, int | float) and isinstance(attempt.get("ended"), int | float):
            seconds = round(attempt["ended"] - _created(attempt), 1)
        account = records.accounts.get(attempt.get("account_id")) or {}
        rows.append(
            {
                "id": attempt["id"],
                "phase": attempt.get("phase"),
                "provider": account.get("provider"),
                "model": attempt.get("model"),
                "state": attempt.get("state"),
                "seconds": seconds if isinstance(seconds, int | float) else None,
            }
        )
    checks: list[dict[str, Any]] = []
    for attempt in reversed(attempts):
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        recorded = result.get("checks")
        if attempt.get("phase") == "build" and isinstance(recorded, list) and recorded:
            checks = [
                {"name": c.get("name"), "status": c.get("status")}
                for c in recorded
                if isinstance(c, dict)
            ]
            break
    return {
        "attempts": rows,
        "checks": checks,
        "candidate": task.get("staged") or task.get("candidate"),
        "integrated_commit": task.get("integrated_commit"),
        "scope": list(task.get("scope") or []),
        "reason": redact(task.get("reason") or "")[:2000],
    }


def task_story(
    task_id: str,
    goal: dict[str, Any],
    project: dict[str, Any] | None,
    tasks,
    attempts,
    approvals=(),
    accounts: dict[str, Any] | None = None,
    events=(),
    tails: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """GET /api/v1/tasks/{id}/story (docs/ROUND3_CONTRACT.md A2). ``events`` are this
    task's events (its own, its attempts' and its approvals'), oldest first."""
    records = Records(goal, project, tasks, attempts, approvals, accounts, tails, now)
    task = records.by_id[task_id]
    block = records.block(task)
    by_id = {a["id"]: a for a in attempts}
    return {
        "goal_id": goal.get("id"),
        "block": block,
        "what": what_sentence(records, task, block),
        "why": block["purpose"],
        "connects_text": connects_sentence(records, task, block),
        "stage_rail": rail(records, task, block),
        "updates": updates(records, task, events, by_id),
        "now": block["now"],
        "decisions": decisions(records, task, events),
        "failure": records.failure(task),
        "advanced": advanced(records, task),
    }


# -- loading one goal's records ---------------------------------------------------------


def _goal_records(store, db, goal_id: str) -> dict[str, Any]:
    goal = store.get("goals", goal_id, db)
    tasks = [store.unpack(r) for r in db.execute("SELECT * FROM tasks WHERE goal_id=?", (goal_id,))]
    return _records_for(store, db, goal, tasks)


def _records_for(store, db, goal, tasks) -> dict[str, Any]:
    ids = [t["id"] for t in tasks]
    marks = ",".join("?" * len(ids))
    dependencies: dict[str, list[str]] = defaultdict(list)
    attempts: list[dict[str, Any]] = []
    if ids:
        for task_id, dependency in db.execute(
            f"SELECT task_id, dependency_id FROM task_dependencies WHERE task_id IN ({marks})", ids
        ):
            dependencies[task_id].append(dependency)
        attempts = [
            store.unpack(r)
            for r in db.execute(f"SELECT * FROM attempts WHERE task_id IN ({marks})", ids)
        ]
    for t in tasks:
        t["dependencies"] = dependencies.get(t["id"], [])
    wanted = set(ids)
    approvals = [
        a
        for a in store.rows("approvals", db)
        if a.get("task_id") in wanted or wanted & set(a.get("covered_tasks") or [])
    ]
    accounts = {
        a["id"]: {k: a.get(k) for k in ("name", "provider", "model")}
        for a in store.rows("accounts", db)
    }
    project = None
    with contextlib.suppress(KeyError):
        project = store.get("projects", goal["project_id"], db)
    return {
        "goal": goal,
        "project": project,
        "tasks": tasks,
        "attempts": attempts,
        "approvals": approvals,
        "accounts": accounts,
    }


def live_tails(attempts, accounts: dict[str, Any], now: float) -> dict[str, Any]:
    """The transcript tail of every live attempt (the last 64 KB, read-only)."""
    from .livetail import tail

    return {
        a["id"]: tail(
            a,
            provider=(accounts.get(a.get("account_id")) or {}).get("provider"),
            done=False,
            now=now,
            lines=TAIL_LINES,
        )
        for a in attempts
        if a.get("state") in ACTIVE
    }


def load_goal_story(store, goal_id: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    with contextlib.closing(store.connect()) as db:
        db.execute("BEGIN")
        records = _goal_records(store, db, goal_id)
        db.commit()
    tails = live_tails(records["attempts"], records["accounts"], now)
    return goal_story(**records, tails=tails, now=now)


def load_task_story(store, task_id: str, now: float | None = None) -> dict[str, Any]:
    from .views import goal_events

    now = time.time() if now is None else now
    with contextlib.closing(store.connect()) as db:
        db.execute("BEGIN")
        task = store.get("tasks", task_id, db)
        if task.get("goal_id"):
            records = _goal_records(store, db, task["goal_id"])
        else:  # a task recorded without a goal: its own one-block story
            goal = {
                "id": None,
                "project_id": task["project_id"],
                "title": task.get("title"),
                "request": task.get("instructions"),
                "created": task.get("created"),
            }
            records = _records_for(store, db, goal, [task])
        mine = [a["id"] for a in records["attempts"] if a["task_id"] == task_id]
        approvals = [a["id"] for a in records["approvals"]]
        raw = goal_events(db, task.get("goal_id") or task_id, [task_id], mine, approvals, limit=400)
        db.commit()
    own = set(mine)
    covering = {
        a["id"]
        for a in records["approvals"]
        if a.get("task_id") == task_id or task_id in (a.get("covered_tasks") or [])
    }
    events = [
        e
        for e in raw
        if e["data"].get("task_id") == task_id
        or e["data"].get("attempt_id") in own
        or e["data"].get("approval_id") in covering
    ]
    tails = live_tails(
        [a for a in records["attempts"] if a["task_id"] == task_id], records["accounts"], now
    )
    return task_story(task_id, **records, events=events, tails=tails, now=now)


# -- what finished and what went wrong, for the director ------------------------------------

RECENT_SECONDS = 24 * 3600
FAILURE_SECONDS = 48 * 3600
OUTCOMES_LIMIT = 20
FAILURES_LIMIT = 10
FINDINGS_LIMIT = 3


def _findings(attempts) -> list[str]:
    """The newest rejecting review's (or director check's) top findings, clipped."""
    for attempt in reversed(attempts):
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        review = result.get("review") if isinstance(result.get("review"), dict) else None
        if attempt.get("phase") in {"review", "direct"} and review and not review.get("approved"):
            return [redact(f)[:300] for f in (review.get("findings") or []) if isinstance(f, str)][
                :FINDINGS_LIMIT
            ]
    return []


def outcomes(store, now: float | None = None) -> dict[str, list[dict[str, Any]]]:
    """``recent_outcomes``: builds that finished or stopped in the last 24 hours;
    ``failures``: why builds that stopped or need the owner did so (last 48 hours), with
    the top review findings and failing check names. Archived builds and projects are
    left out; both lists are bounded and newest first."""
    from .status import ago

    now = time.time() if now is None else now
    with contextlib.closing(store.connect()) as db:
        db.execute("BEGIN")
        projects = {p["id"]: p for p in store.rows("projects", db)}
        goals = {g["id"]: g for g in store.rows("goals", db)}
        by_goal: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for task in store.rows("tasks", db):
            if task.get("goal_id") in goals:
                by_goal[task["goal_id"]].append(task)
        loaded = []
        for goal_id, tasks in by_goal.items():
            goal = goals[goal_id]
            if goal.get("archived") or (projects.get(goal["project_id"]) or {}).get("archived"):
                continue
            updated = max(t.get("updated") or 0 for t in tasks)
            if now - updated <= FAILURE_SECONDS:
                loaded.append((updated, _records_for(store, db, goal, tasks)))
        db.commit()
    recent, failures = [], []
    for updated, found in sorted(loaded, key=lambda x: x[0], reverse=True):
        records = Records(**found, now=now)
        status = goal_status([records.block(t) for t in records.shown()])
        title = records.build_title
        project = (found["project"] or {}).get("name")
        when = ago(now - updated)
        if status in {"completed", "blocked", "cancelled"} and now - updated <= RECENT_SECONDS:
            recent.append(
                {"title": title, "project": project, "outcome": STATUS_LABELS[status], "when": when}
            )
        if status not in {"blocked", "needs_you"}:
            continue
        stopped = [t for t in records.tasks if records.failure(t)]
        if not stopped:
            continue
        task = max(stopped, key=lambda t: t.get("updated") or 0)
        attempts = records.attempts_of.get(task["id"], [])
        failures.append(
            {
                "build": title,
                "project": project,
                "when": when,
                "goal_id": found["goal"]["id"],
                "failure": records.failure(task),
                "review_findings": _findings(attempts),
                "failing_checks": _failing_checks(attempts),
                # For "can you investigate that failure?"; never shown to the owner as is.
                "technical_reason": redact(task.get("reason") or "")[:300],
            }
        )
    return {
        "recent_outcomes": recent[:OUTCOMES_LIMIT],
        "failures": failures[:FAILURES_LIMIT],
    }
