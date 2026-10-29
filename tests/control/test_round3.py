"""Round 3, backend contract (docs/ROUND3_CONTRACT.md section A): reversible build archive,
the plain-English story of builds and blocks, read-only Mac activity discovery and the
director's context. No provider CLI is invoked and no real process is inspected."""

from __future__ import annotations

import json
import os
import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_arena.control import livetail, story
from agent_arena.control.store import Store, encode
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client
from tests.control.test_usability_api import FIXTURES, insert_attempt, real_state_home


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


def real_state_client(tmp_path, monkeypatch, **env):
    """The browser fixture (tests/control/real_state_server.py) over a copy of the
    sanitised real control database; returns the app's store, a paired client and headers."""
    from tests.control import real_state_server

    live = real_state_home(tmp_path)  # stands in for the live home: copied, never served
    monkeypatch.setenv("ARENA_UI_STATE", str(tmp_path / "scratch"))
    monkeypatch.setenv("ARENA_REAL_DB", str(live / "control.sqlite3"))
    monkeypatch.setenv("ARENA_UI_PORT", "8794")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = real_state_server.create_app()
    client = TestClient(app, base_url="http://127.0.0.1:8794")
    origin = {"Origin": "http://127.0.0.1:8794"}
    r = client.post("/api/v1/pair", json={"code": "real-state-one-use-code"}, headers=origin)
    assert r.status_code == 200, r.text
    return app.state.store, client, {**origin, "X-CSRF-Token": r.json()["csrf"]}


def set_state(store, task_id, state, reason="for the test", **extra):
    with store.transaction() as db:
        store.update_task(db, store.get("tasks", task_id, db), state, reason, **extra)


def post(client, headers, path, body, key):
    return client.post(path, json=body, headers={**headers, "Idempotency-Key": key})


# -- A1: build archive -----------------------------------------------------------------------


def test_goal_archive_is_reversible_and_never_touches_running_work(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    done = goal(store, engine, project, path="src/done.txt")
    stopped = goal(store, engine, project, path="src/stopped.txt")
    running = goal(store, engine, project, path="src/running.txt")
    queued = goal(store, engine, project, path="src/queued.txt")
    asking = goal(store, engine, project, path="src/asking.txt")
    merging = goal(store, engine, project, path="src/merging.txt")
    set_state(store, done, "completed")
    set_state(store, stopped, "blocked")
    set_state(store, running, "running")
    set_state(store, asking, "awaiting_input", question="Which colour?")
    set_state(store, merging, "integrating")
    goal_of = {t: store.get("tasks", t)["goal_id"] for t in (done, stopped, running, queued)}
    goal_of.update({t: store.get("tasks", t)["goal_id"] for t in (asking, merging)})
    before = {
        "tasks": store.rows("tasks"),
        "attempts": store.rows("attempts"),
        "approvals": store.rows("approvals"),
    }
    ids = [goal_of[t] for t in (done, stopped, running, queued, asking, merging)]
    r = post(client, headers, "/api/v1/goals/archive", {"ids": [*ids, "goal_missing"]}, "a1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] == [goal_of[done], goal_of[stopped]]
    assert body["skipped"] == [
        {"id": goal_of[running], "reason": "still running"},
        {"id": goal_of[queued], "reason": "still running"},
        {"id": goal_of[asking], "reason": "still running"},
        {"id": goal_of[merging], "reason": "still running"},
        {"id": "goal_missing", "reason": "not found"},
    ]
    # Idempotent replay returns the recorded answer; a reused key with another body is refused.
    again = post(client, headers, "/api/v1/goals/archive", {"ids": [*ids, "goal_missing"]}, "a1")
    assert again.json() == body
    assert post(client, headers, "/api/v1/goals/archive", {"ids": ids[:1]}, "a1").status_code == 409
    # CSRF and authentication still guard it.
    r = client.post(
        "/api/v1/goals/archive",
        json={"ids": ids},
        headers={"Origin": "http://127.0.0.1:8790", "Idempotency-Key": "a-no-csrf"},
    )
    assert r.status_code == 403
    empty = client.post("/api/v1/goals/archive", json={"ids": []}, headers=headers)
    assert empty.status_code == 422
    # The snapshot carries the flags; nothing underneath changed.
    goals = {g["id"]: g for g in client.get("/api/v1/overview").json()["goals"]}
    assert goals[goal_of[done]]["archived"] is True and goals[goal_of[done]]["archived_at"]
    assert goals[goal_of[running]]["archived"] is False
    assert goals[goal_of[running]]["archived_at"] is None
    assert client.get(f"/api/v1/goals/{goal_of[stopped]}").json()["goal"]["archived"] is True
    after = {
        "tasks": store.rows("tasks"),
        "attempts": store.rows("attempts"),
        "approvals": store.rows("approvals"),
    }
    assert after == before
    kinds = [e["kind"] for e in client.get("/api/v1/overview").json()["events"]]
    assert kinds.count("goal.archived") == 2
    # Archiving an archived build again changes nothing and emits nothing.
    stamp = store.get("goals", goal_of[done])["archived_at"]
    r = post(client, headers, "/api/v1/goals/archive", {"ids": [goal_of[done]]}, "a2")
    assert r.json() == {"archived": [goal_of[done]], "skipped": []}
    assert store.get("goals", goal_of[done])["archived_at"] == stamp
    # Restore.
    r = post(
        client,
        headers,
        "/api/v1/goals/unarchive",
        {"ids": [goal_of[done], goal_of[stopped], "goal_missing"], "revision": 3},
        "u1",
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "restored": [goal_of[done], goal_of[stopped]],
        "skipped": [{"id": "goal_missing", "reason": "not found"}],
    }
    goals = {g["id"]: g for g in client.get("/api/v1/overview").json()["goals"]}
    assert goals[goal_of[done]]["archived"] is False and goals[goal_of[done]]["archived_at"] is None
    events = client.get("/api/v1/overview").json()["events"]
    restored = [e["data"]["goal_id"] for e in events if e["kind"] == "goal.restored"]
    assert sorted(restored) == sorted([goal_of[done], goal_of[stopped]])


def test_retrying_work_of_an_archived_build_brings_it_back(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project, path="src/stopped.txt")
    set_state(store, task_id, "blocked")
    goal_id = store.get("tasks", task_id)["goal_id"]
    assert post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, "a1").json()[
        "archived"
    ] == [goal_id]
    task = store.get("tasks", task_id)
    r = post(client, headers, f"/api/v1/tasks/{task_id}/retry", {"revision": task["rev"]}, "r1")
    assert r.status_code == 200, r.text
    assert store.get("tasks", task_id)["state"] == "ready"
    assert store.get("goals", goal_id)["archived"] is False


def test_project_archive_is_refused_while_a_build_is_in_progress(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project, path="src/busy.txt")
    for n, state in enumerate(["ready", "running", "integrating", "cooldown", "verifying"]):
        set_state(store, task_id, state)
        p = store.get("projects", project["id"])
        r = post(
            client, headers, f"/api/v1/projects/{p['id']}/archive", {"revision": p["rev"]}, f"p{n}"
        )
        assert r.status_code == 409, state
        assert r.json()["detail"] == "This project has a build in progress"
        assert not store.get("projects", p["id"]).get("archived")
    # Waiting on the owner: neither the build nor its project can be archived (an archived
    # project leaves the needs-you lists, and a decision must never be hidden), with its own
    # message (lead decision, Round 3 review).
    set_state(store, task_id, "awaiting_input", question="Which colour?")
    goal_id = store.get("tasks", task_id)["goal_id"]
    r = post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, "g1")
    assert r.json()["skipped"] == [{"id": goal_id, "reason": "still running"}]
    p = store.get("projects", project["id"])
    r = post(client, headers, f"/api/v1/projects/{p['id']}/archive", {"revision": p["rev"]}, "q")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "This project has something waiting for you — answer or decide it first."
    )
    assert not store.get("projects", p["id"]).get("archived")
    # Once nothing waits, it can.
    set_state(store, task_id, "completed")
    p = store.get("projects", project["id"])
    r = post(client, headers, f"/api/v1/projects/{p['id']}/archive", {"revision": p["rev"]}, "ok")
    assert r.status_code == 200 and r.json()["archived"] is True
    assert time.time() - store.get("projects", p["id"])["archived_at"] < 60


def test_the_real_state_fixture_lets_the_acceptance_archive_and_restore(tmp_path, monkeypatch):
    store, client, headers = real_state_client(tmp_path, monkeypatch)
    finished = "goal_90ba46b911c02545"  # the integrated Pomodoro build
    r = post(client, headers, "/api/v1/goals/archive", {"ids": [finished]}, "ra1")
    assert r.status_code == 200 and r.json()["archived"] == [finished]
    goals = {g["id"]: g for g in client.get("/api/v1/overview").json()["goals"]}
    assert goals[finished]["archived"] is True
    r = post(client, headers, "/api/v1/goals/unarchive", {"ids": [finished]}, "ru1")
    assert r.status_code == 200 and r.json()["restored"] == [finished]
    smoke = next(p for p in store.rows("projects") if p["name"] == "Arena Smoke 20261028")
    path = f"/api/v1/projects/{smoke['id']}/archive"
    assert post(client, headers, path, {"revision": smoke["rev"]}, "rp1").status_code == 200
    # Everything that could reach a worker is still refused on the copy.
    for path in ("/api/v1/tasks/task_bd2af42e9557b61d/retry", "/api/v1/control/stop-all"):
        r = post(client, headers, path, {"revision": 0}, "no-" + path[-5:])
        assert r.status_code == 403 and "copy" in r.json()["detail"]


# -- A2: the plain-English story ---------------------------------------------------------------

PATHY = re.compile(r"[/\\]|\w\.\w")
HEXY = re.compile(r"(?i)\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b")
CODEY = re.compile(r"[`{}]|\$ |\(\)|==|=>|--\w|\w_\w")


def assert_plain(text, where=""):
    """No file paths or names, git hashes or code in owner-facing text."""
    if text is None:
        return
    assert isinstance(text, str), where
    for pattern in (PATHY, HEXY, CODEY):
        assert not pattern.search(text), (where, text)


def assert_plain_failure(failure, where=""):
    if failure is None:
        return
    assert set(failure) == {
        "task_id",
        "what_failed",
        "intended",
        "recoverable",
        "tried",
        "next",
        "needs_you",
        "action",
    }, where
    assert failure["action"] in {"retry", "answer", "sign_in", "permission", "none"}
    assert isinstance(failure["recoverable"], bool) and isinstance(failure["needs_you"], bool)
    for key in ("what_failed", "intended", "next"):
        assert failure[key], (where, key)
        assert_plain(failure[key], f"{where} failure.{key}")
    for text in failure["tried"]:
        assert_plain(text, f"{where} failure.tried")


def line(kind, text):
    return {"t": None, "kind": kind, "text": text}


def test_describe_line_maps_every_kind_of_live_line():
    D = story.describe_line
    expected = {
        "Running the tests": [line("test", "$ python -m pytest -q tests/test_quiet.py")],
        "Checking the project history": [
            line("command", "$ git status --short && git rev-parse HEAD"),
            line("command", "$ cd web && git log -3 --oneline"),
        ],
        "Reading the code": [
            line("command", f"$ {c}")
            for c in (
                "rg -n foo src",
                "grep -R bar .",
                "find . -name '*.py'",
                "ls -la",
                "cat README.md",
                "sed -n '1,80p' src/a.py",
                "head -5 a.txt",
                "tail -n 20 log.txt",
                "rg -n x web/src/style.css && sed -n '1,280p' scripts/self_check.py",
            )
        ]
        + [
            line("file", "reading /Users/x/ws/src/pomodoro/cli.py"),
            line("tool", "Grep def main in /Users/x/ws/src"),
            line("tool", "Glob **/*.py"),
        ],
        "Installing dependencies": [
            line("command", f"$ {c}")
            for c in (
                "npm install",
                "npm ci",
                "pnpm i",
                "pnpm install --frozen-lockfile",
                "yarn add left-pad",
                "pip install -e .",
                "pip3 install requests",
                "uv sync",
                "uv pip install httpx",
                "python -m pip install httpx",
                "FOO=1 npm install",
            )
        ],
        "Building the app": [
            line("command", f"$ {c}")
            for c in ("npm run build", "pnpm build", "yarn build", "tsc -p .", "npx tsc --noEmit")
        ]
        + [line("command", "$ vite build")],
        "Running a script": [
            line("command", f"$ {c}")
            for c in (
                "python scripts/self_check.py",
                "python3 -c 'print(1)'",
                "node scripts/build.mjs",
                "node --input-type=module <<'NODE' import assert from 'node:assert/strict';",
            )
        ],
        "Running a command": [
            line("command", f"$ {c}")
            for c in ("curl -s https://example.invalid", "make", "echo 'unterminated", "")
        ],
        "Editing code": [
            line("file", "update /Users/x/ws/web/src/screens/Devices.tsx"),
            line("file", "editing src/pomodoro/cli.py"),
            line("file", "writing tests/test_quiet.py"),
        ],
        "Updating its to-do list": [line("tool", "TodoWrite")],
        "Looking something up online": [line("tool", "WebSearch pomodoro timers")],
        "Handing part of the work to a helper agent": [line("tool", "Task explore the repo")],
        "Using a tool": [line("tool", "mcp__thing do it")],
        "Hit an error and is working around it": [
            line("error", "exit 1 · sed -n '710,790p' tests/control/test_control.py"),
            line("error", "curl: (6) Could not resolve host: example.invalid"),
        ],
    }
    for text, lines in expected.items():
        for item in lines:
            assert D(item) == text, item
    for item in (line("system", "Session started"), line("system", "Turn completed")):
        assert D(item) is None
    # The agent's own words: first sentence, code spans, paths and hashes removed, ≤ 140.
    assert D(
        line(
            "assistant",
            "I’ll inspect the device screen and wrapping styles, add the Copy behavior, then run "
            "the unchanged self-check if the workspace permits it. I’ll limit scope.",
        )
    ) == (
        "I’ll inspect the device screen and wrapping styles, add the Copy behavior, then run "
        "the unchanged self-check if the workspace permits it."
    )
    assert D(
        line(
            "assistant",
            "Implemented Copy in `Devices.tsx` with accessible feedback, stale-operation guards, "
            "retry support, and existing wrapping.",
        )
    ) == (
        "Implemented Copy with accessible feedback, stale-operation guards, retry support, "
        "and existing wrapping."
    )
    assert D(line("assistant", "Committed the fix as 7d5ef60f14 after editing src/app.py.")) == (
        "Committed the fix after editing."
    )
    assert D(line("assistant", "**Done.** Everything passes.")) == "Done."
    long = D(line("assistant", "This sentence keeps going " * 20 + "."))
    assert len(long) <= 140 and long.endswith("…")
    assert D(line("assistant", "`npm test`")) is None
    assert D(line("assistant", "")) is None
    # Structured answers speak through their own summary; a review is "finishing".
    built = json.dumps({"summary": "Added the Copy button to Devices.tsx. Tests pass.", "x": []})
    assert D(line("assistant", built)) == "Added the Copy button."
    cut = '{"summary": "Rebuilt the dialog so the code copies with one tap. More", "operations": ['
    assert D(line("assistant", cut)) == "Rebuilt the dialog so the code copies with one tap."
    assert D(line("assistant", '{"approved": true, "findings": []}')) == "Finishing its review"
    assert D(line("assistant", '{"approved": false, "findings": ["a')) == "Finishing its review"
    assert D(line("assistant", '{"operations": [1, 2]}')) is None
    # Every line of the two recorded transcripts maps to plain English (or is skipped).
    for name in ("transcript-codex.jsonl", "transcript-claude.jsonl"):
        raw = (FIXTURES / name).read_text()
        described = [D(x) for x in livetail.parse_lines(raw, limit=400)]
        assert any(described)
        for text in described:
            assert_plain(text, name)
            assert text is None or len(text) <= 140


def test_plain_text_drops_paths_code_hashes_and_secrets():
    plain = story.plain
    assert plain("Add descriptive metadata to the phone app manifest") == (
        "Add descriptive metadata to the phone app manifest"
    )
    assert plain(
        "Applied the exact replacement to `web/src/screens/Devices.tsx`, including Copy."
    ) == ("Applied the exact replacement, including Copy.")
    assert plain(
        "web/src/screens/Devices.tsx: Copy feedback can label a stale code as copied."
    ) == ("Copy feedback can label a stale code as copied.")
    assert plain("Committed 7d5ef60f1472fed to staging; deadbeef and defaced stay.") == (
        "Committed to staging; deadbeef and defaced stay."
    )
    assert plain("Changes only README.md and package.json.") == "Changes only."
    messy = plain("Use `--no-bell` or --quiet with candidate_fingerprint() and {x: 1}.")
    assert_plain(messy)
    secret = plain("Token sk-abcdefghijklmnopqrstu and Bearer abc.def.ghi leaked")
    assert "sk-" not in secret and "abc.def" not in secret
    assert plain("") == plain(None) == plain("`only code`") == plain("src/a.py") == ""
    clipped = plain("word " * 100, 50)
    assert len(clipped) <= 50 and clipped.endswith("…")
    assert story.first_sentence("## Heading\n- First item. Second.") == "Heading"
    assert story.first_sentence("\n\n1. Do this first. Then that.") == "Do this first."


def real_records(tmp_path):
    store = Store(real_state_home(tmp_path))
    tasks = {t["id"]: t for t in store.rows("tasks")}
    attempts = {a["id"]: a for a in store.rows("attempts")}
    accounts = {a["id"]: a for a in store.rows("accounts")}

    def attempts_of(task_id):
        return sorted(
            (a for a in attempts.values() if a["task_id"] == task_id), key=lambda a: a["created"]
        )

    return store, tasks, attempts, accounts, attempts_of


def test_failures_are_classified_from_recorded_reasons(tmp_path):
    store, tasks, attempts, accounts, attempts_of = real_records(tmp_path)
    # The five builds the real state recorded as blocked.
    for task_id, cls in {
        "task_bd2af42e9557b61d": "timeout",  # CLI did not complete (exit=-15, timeout=True)
        "task_ff42efad905f2850": "review_rejected",
        "task_1887295cff070056": "checks",
        "task_ff1a669e96c5ae03": "checks",
        "task_ed3154d28c5e8492": "unknown",  # Candidate contains a non-regular untracked path
    }.items():
        assert tasks[task_id]["state"] == "blocked"
        assert story.failure_class(tasks[task_id], attempts_of(task_id)) == cls, task_id
    # Real attempt outcomes, including the director's rejection (same reason text as an
    # independent review, told apart by the phase that produced it).
    for attempt_id, cls in {
        "attempt_34897aca0dfce1f0": "director_rejected",
        "attempt_8a3e7020ab3b396e": "review_rejected",
        "attempt_80d917ef9a8f04f9": "timeout",
        "attempt_28f20e22ae4238d0": "checks",
        "attempt_fec4c853c689edaa": "no_changes",
        "attempt_73265c2c96dbb497": "bad_response",
        "attempt_2f2bd72710f05017": "bad_response",
        "attempt_893a6a446c6d3774": "unknown",
    }.items():
        assert story.attempt_class(attempts[attempt_id]) == cls, attempt_id
    # The timed-out overnight-mode build, in full.
    task = tasks["task_bd2af42e9557b61d"]
    f = story.failure(
        task,
        attempts=attempts_of(task["id"]),
        accounts=accounts,
        title="Overnight mode",
        intended="Run builds overnight.",
    )
    assert_plain_failure(f, "timeout")
    assert f["what_failed"] == "Overnight mode: The agent ran out of time before it finished."
    assert f["intended"] == "Run builds overnight."
    assert f["recoverable"] is False and f["action"] == "none" and f["needs_you"] is True
    assert f["tried"] == [
        "Claude built it, but the project's checks did not pass.",
        "The director diagnosed the failure and rewrote the instructions.",
        "Claude tried again with more effort, but its answer could not be used.",
        "The director diagnosed the failure and rewrote the instructions.",
        "Handed to Codex after a failure; it rebuilt it, but it ran out of time.",
    ]
    assert "will not try again on its own" in f["next"]
    # With repair budget left, the owner can retry.
    f = story.failure(
        {**task, "repairs": 1},
        attempts=attempts_of(task["id"]),
        accounts=accounts,
        title="Overnight mode",
        intended="Run builds overnight.",
    )
    assert f["action"] == "retry" and f["recoverable"] is True and "tap Retry" in f["next"]
    # Checks: the failing check is named; review: the reviewer; unknown: under Advanced.
    checks = story.failure(
        tasks["task_1887295cff070056"],
        attempts=attempts_of("task_1887295cff070056"),
        accounts=accounts,
        title="Copy button",
        intended="Copy the code with one tap.",
    )
    assert checks["what_failed"] == (
        "Copy button: The project's automatic checks did not pass (Arena self-check)."
    )
    unknown = story.failure(
        tasks["task_ed3154d28c5e8492"],
        attempts=attempts_of("task_ed3154d28c5e8492"),
        accounts=accounts,
        title="Copy button",
        intended="Copy the code with one tap.",
    )
    assert unknown["what_failed"] == "Copy button: Stopped with an error Arena could not classify."
    assert "Advanced" in unknown["next"]
    assert "untracked" not in json.dumps(unknown)
    # Classes the real state has not recorded yet, from the engine's and worker's own words.
    base = {**tasks["task_1887295cff070056"], "repairs": 0}
    rate_limited = {
        **attempts["attempt_28f20e22ae4238d0"],
        "state": "rate_limited",
        "result": {
            "status": "rate_limited",
            "reason": "CLI did not complete successfully (exit=1, timeout=False); see "
            "preserved transcript",
        },
    }
    cases = [
        (
            {
                "state": "needs_auth",
                "reason": "Subscription login missing or unsupported; paid API "
                "fallback is disabled",
            },
            [],
            "needs_auth",
            "sign_in",
        ),
        (
            {
                "state": "needs_permission",
                "reason": "Host denied process identity verification; "
                "no provider invoked: AccessDenied",
            },
            [],
            "needs_permission",
            "permission",
        ),
        (
            {"state": "interrupted", "reason": "Recovery budget exhausted; evidence preserved"},
            [],
            "interrupted",
            "retry",
        ),
        (
            {
                "state": "blocked",
                "reason": "No director model is available: the Codex model "
                "catalog (~/.codex/models_cache.json) is missing or older than 7 days and no "
                "validated Fable 5.1 slot is enrolled. Run `codex` once on the Mac or "
                "`check-accounts --probe`, then Retry.",
            },
            [],
            "model_unavailable",
            "retry",
        ),
        (
            {
                "state": "blocked",
                "reason": "No supported model route remains; failed approaches are preserved",
            },
            [],
            "no_agent",
            "retry",
        ),
        (
            {"state": "blocked", "reason": rate_limited["result"]["reason"]},
            [rate_limited],
            "rate_limited",
            "retry",
        ),
    ]
    for change, own, cls, action in cases:
        recorded = {**base, **change}
        assert story.failure_class(recorded, own) == cls, cls
        f = story.failure(
            recorded, attempts=own, accounts=accounts, title="Copy button", intended="Copy it."
        )
        assert_plain_failure(f, cls)
        assert f["what_failed"] == "Copy button: " + story.WHAT_FAILED[cls]
        assert f["action"] == action and f["recoverable"] is True, cls
    assert story.classify(None, state="cooldown") == "rate_limited"
    assert story.classify("anything", status="transient") == "connection"
    # Nothing has stopped: no failure.
    assert story.failure_class({**base, "state": "running"}, []) is None
    assert story.failure_class({**base, "state": "completed"}, []) is None


GOAL_KEYS = {
    "goal_id",
    "title",
    "project",
    "status",
    "status_label",
    "started",
    "updated",
    "agents_working",
    "progress",
    "summary",
    "blocks",
    "failure",
    "needs_you",
}
BLOCK_KEYS = {
    "id",
    "title",
    "purpose",
    "connects",
    "agent",
    "stage",
    "state",
    "state_label",
    "now",
    "last_activity",
    "stalled",
    "updated",
}
STATE_LABELS = {
    "Planning",
    "Implementing",
    "Testing",
    "Reviewing",
    "Integrating",
    "Waiting",
    "Queued",
    "Done",
    "Blocked",
    "Needs you",
    "Stopped",
}
BLOCK_STATES = {"running", "queued", "waiting", "needs_you", "completed", "blocked", "cancelled"}
RAIL = ["Design", "Implement", "Test", "Review", "Merge"]


def check_goal_story(s, where=""):
    assert set(s) == GOAL_KEYS, where
    assert s["status"] in {"running", "needs_you", "queued", "completed", "blocked", "cancelled"}
    assert_plain(s["title"], where)
    assert_plain(s["summary"], where)
    assert_plain_failure(s["failure"], where)
    assert 0 <= s["progress"]["done"] <= s["progress"]["total"]
    for need in s["needs_you"]:
        assert set(need) == {"kind", "text", "task_id"}
        assert need["kind"] in {"answer", "approve", "sign_in", "permission"}
    for block in s["blocks"]:
        check_block(block, where)


def check_block(block, where=""):
    assert set(block) == BLOCK_KEYS, where
    assert block["state"] in BLOCK_STATES and block["state_label"] in STATE_LABELS
    assert block["stage"]["label"] == RAIL[block["stage"]["index"]]
    for key in ("title", "purpose", "now"):
        assert_plain(block[key], f"{where} block.{key}")
    for link in block["connects"]:
        assert link["relation"] in {"needs", "unblocks"}
        assert_plain(link["title"], where)


def check_task_story(s, where=""):
    assert set(s) == {
        "goal_id",
        "block",
        "what",
        "why",
        "connects_text",
        "stage_rail",
        "updates",
        "now",
        "decisions",
        "failure",
        "advanced",
    }, where
    check_block(s["block"], where)
    for key in ("what", "why", "now", "connects_text"):
        assert_plain(s[key], f"{where} {key}")
    assert [r["label"] for r in s["stage_rail"]] == RAIL
    assert {r["state"] for r in s["stage_rail"]} <= {"done", "current", "todo", "failed"}
    assert len(s["updates"]) <= 20 and len(s["decisions"]) <= 5
    times = [u["t"] for u in s["updates"]]
    assert times == sorted(times, reverse=True), where
    for update in s["updates"]:
        assert update["tone"] in {"good", "bad", "neutral"} and update["text"]
        assert_plain(update["text"], f"{where} update")
    for decision in s["decisions"]:
        assert_plain(decision, f"{where} decision")
    assert_plain_failure(s["failure"], where)
    assert set(s["advanced"]) == {
        "attempts",
        "checks",
        "candidate",
        "integrated_commit",
        "scope",
        "reason",
    }
    for row in s["advanced"]["attempts"]:
        assert set(row) == {"id", "phase", "provider", "model", "state", "seconds"}


def test_story_endpoints_tell_the_real_state_in_plain_english(tmp_path):
    store = Store(real_state_home(tmp_path))
    client, headers = paired_client(store)
    statuses = {}
    for g in store.rows("goals"):
        r = client.get(f"/api/v1/goals/{g['id']}/story")
        assert r.status_code == 200, r.text
        s = r.json()
        check_goal_story(s, g["id"])
        statuses[g["id"]] = s["status"]
        assert all(b["title"] != "Planning" for b in s["blocks"])  # every plan is finished
    assert set(statuses.values()) == {"completed", "blocked", "cancelled"}
    for t in store.rows("tasks"):
        r = client.get(f"/api/v1/tasks/{t['id']}/story")
        assert r.status_code == 200, r.text
        check_task_story(r.json(), t["id"])
    # The Pomodoro options build: two parts in dependency order, both merged.
    s = client.get("/api/v1/goals/goal_a6faaa785f084d70/story").json()
    assert s["status"] == "completed" and s["status_label"] == "Finished"
    assert s["progress"] == {"done": 2, "total": 2, "in_progress": 0, "fraction": 1.0}
    assert s["agents_working"] == 0
    assert [b["id"] for b in s["blocks"]] == ["task_613472a2ca0d2adb", "task_4e54fb6538a128da"]
    assert s["blocks"][0]["connects"] == [
        {
            "id": "task_4e54fb6538a128da",
            "title": "Document bell control, prefixed output and quiet mode",
            "relation": "unblocks",
        }
    ]
    assert s["blocks"][1]["connects"][0]["relation"] == "needs"
    assert s["project"] == {"id": "project_3c47ef383f5bbb62", "name": "Arena Smoke 20261028"}
    assert s["summary"].startswith("Preserve the agreed two-step plan")
    # A stopped build: the timeout, what was tried, what happens next.
    s = client.get("/api/v1/goals/goal_815db45a71e3a62d/story").json()
    assert s["status"] == "blocked" and s["failure"]["task_id"] == "task_bd2af42e9557b61d"
    assert "ran out of time" in s["failure"]["what_failed"]
    assert s["failure"]["tried"][-1].startswith("Handed to Codex")
    block = s["blocks"][0]
    assert block["agent"] == {"provider": "codex", "model": "gpt-6-astra", "name": "Codex"}
    assert (
        block["stage"] == {"index": 1, "label": "Implement"} and block["state_label"] == "Blocked"
    )
    # A cancelled build is stopped, not finished, though its plan completed.
    s = client.get("/api/v1/goals/goal_7a5db32a3a62aeba/story").json()
    assert s["status"] == "cancelled" and s["status_label"] == "Stopped"
    # The block story of a rejected change.
    s = client.get("/api/v1/tasks/task_ff42efad905f2850/story").json()
    assert s["block"]["stage"] == {"index": 3, "label": "Review"}
    assert [r["state"] for r in s["stage_rail"]] == ["done", "done", "done", "failed", "todo"]
    assert s["updates"][0] == {
        "t": s["updates"][0]["t"],
        "text": "Stopped: the reviewer asked for changes",
        "tone": "bad",
    }
    assert s["updates"][1]["text"] == "Review asked for changes"
    texts = [u["text"] for u in s["updates"]]
    assert "Director asked for changes" in texts and "Fixing what the director found" in texts
    assert texts.index("Fixing what the director found") < texts.index("Director asked for changes")
    assert s["decisions"][0].startswith("The independent reviewer asked for changes")
    assert s["what"] == (
        "Add descriptive metadata to the phone app manifest. It stopped before it was finished."
    )
    # Technical evidence lives under Advanced only.
    s = client.get("/api/v1/tasks/task_4e54fb6538a128da/story").json()
    assert re.fullmatch(r"[0-9a-f]{40}", s["advanced"]["integrated_commit"])
    assert "README.md" in s["advanced"]["scope"]
    assert s["what"].endswith("It is finished and merged into the project.")
    assert s["connects_text"] == (
        "It builds on “Implement bell control, prefixed countdown and quiet mode with…”, "
        "which is finished."
    )
    assert [u["text"] for u in s["updates"][:2]] == ["Published to GitHub", "Merged into main"]
    assert [r["state"] for r in s["stage_rail"]] == ["done"] * 5
    unknown = client.get("/api/v1/tasks/task_ed3154d28c5e8492/story").json()
    assert unknown["advanced"]["reason"] == "Candidate contains a non-regular untracked path"
    assert unknown["failure"]["what_failed"].endswith(
        "Stopped with an error Arena could not classify."
    )
    plan = client.get("/api/v1/tasks/task_5477cfaf06a2cd56/story").json()
    assert plan["block"]["title"] == "Planning" and plan["block"]["state_label"] == "Done"
    assert [r["state"] for r in plan["stage_rail"]] == ["done", "todo", "todo", "todo", "todo"]
    assert [u["text"] for u in plan["updates"]] == ["Plan ready", "Started planning"]
    assert client.get("/api/v1/goals/goal_missing/story").status_code == 404
    assert client.get("/api/v1/tasks/task_missing/story").status_code == 404


def add_part(store, goal_id, project_id, title, state, *, deps=(), planner="attempt_plan", **data):
    """A build task the director planned for ``goal_id`` (as engine.finish records it)."""
    ident = "task_" + os.urandom(8).hex()
    record = dict(
        title=title,
        instructions=title + "\nAcceptance: []",
        scope=["src"],
        review=True,
        timeout=900,
        created=time.time(),
        updated=time.time(),
        reason="Validated plan; frozen project checks and goal authority inherited",
        repairs=0,
        max_repairs=2,
        phase="build",
        director=True,
        cooldown_until=0,
        retries=0,
        instructions_pending=[],
        planner_attempt=planner,
    )
    record.update(data)
    with store.transaction() as db:
        db.execute(
            "INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",
            (ident, project_id, goal_id, state, encode(record)),
        )
        for dep in deps:
            db.execute("INSERT INTO task_dependencies VALUES(?,?)", (ident, dep))
    return ident


def write_transcript(store, attempt_id, *events, age=0.0):
    folder = store.home / "attempts" / attempt_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "transcript.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return stamp


def codex_command(command):
    return {"type": "item.started", "item": {"type": "command_execution", "command": command}}


def test_a_live_build_story_from_planning_to_parallel_blocks(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    with store.transaction() as db:
        created = engine.add_goal(
            db,
            project["id"],
            "Add a greeting",
            "OUTCOME: Add a greeting file for visitors. Keep the rest.",
            ["src"],
            planning=True,
            director=True,
        )
    goal_id, plan_id = created["goal_id"], created["task_id"]
    # Planning: one "Planning" block saying what the director is reading right now.
    set_state(store, plan_id, "running")
    plan_attempt = insert_attempt(store, plan_id, state="running", phase="plan")
    stamp = write_transcript(
        store,
        plan_attempt,
        {"type": "thread.started"},
        codex_command("/bin/zsh -lc 'sed -n 1,80p src/initial.txt'"),
    )
    s = client.get(f"/api/v1/goals/{goal_id}/story").json()
    check_goal_story(s, "planning")
    assert s["status"] == "running" and s["status_label"] == "Planning"
    assert [b["title"] for b in s["blocks"]] == ["Planning"]
    planning = s["blocks"][0]
    assert planning["state_label"] == "Planning" and planning["stage"]["label"] == "Design"
    assert planning["now"] == "Reading the code" and planning["stalled"] is False
    assert abs(planning["last_activity"] - stamp) < 1 and s["agents_working"] == 1
    assert s["progress"] == {"done": 0, "total": 1, "in_progress": 0, "fraction": 0.0}
    assert s["summary"] == "Arena is working on this build."
    # No transcript write for ten minutes: stalled.
    write_transcript(store, plan_attempt, codex_command("git log -3"), age=700)
    s = client.get(f"/api/v1/goals/{goal_id}/story").json()
    assert s["blocks"][0]["stalled"] is True
    assert s["blocks"][0]["now"] == "Checking the project history"
    # The plan lands: three parts, two in a chain, Planning leaves the story.
    with store.transaction() as db:
        db.execute("UPDATE attempts SET state='passed' WHERE id=?", (plan_attempt,))
    first = add_part(
        store,
        goal_id,
        project["id"],
        "Create the greeting text",
        "completed",
        purpose="So visitors are welcomed by name.",
        integrated_commit="a" * 40,
    )
    second = add_part(store, goal_id, project["id"], "Show the greeting", "verifying", deps=[first])
    third = add_part(
        store, goal_id, project["id"], "Document the greeting", "waiting_dependency", deps=[second]
    )
    set_state(store, plan_id, "completed", planned_tasks=[first, second, third])
    live = insert_attempt(store, second, state="verifying", phase="build")
    write_transcript(store, live, {"type": "turn.completed"}, age=900)
    s = client.get(f"/api/v1/goals/{goal_id}/story").json()
    check_goal_story(s, "building")
    assert [b["id"] for b in s["blocks"]] == [first, second, third]
    assert s["status"] == "running" and s["status_label"] == "Building"
    # One merged; one at Test (2/5) and one designed but waiting (1/5). Only the one being
    # checked is in progress: waiting work is not (owner's lead engineer, Round 3 review).
    assert s["progress"] == {"done": 1, "total": 3, "in_progress": 1, "fraction": 0.533}
    assert s["agents_working"] == 1
    done, checking, waiting = s["blocks"]
    assert done["state_label"] == "Done" and done["stage"]["label"] == "Merge"
    assert done["purpose"] == "So visitors are welcomed by name."
    assert checking["purpose"] == "Part of: Add a greeting file for visitors."
    assert checking["state_label"] == "Testing" and checking["stage"]["label"] == "Test"
    assert checking["now"] == "Running the project's checks"
    assert checking["stalled"] is False  # checks run while the transcript is complete
    assert [(c["title"], c["relation"]) for c in checking["connects"]] == [
        ("Create the greeting text", "needs"),
        ("Document the greeting", "unblocks"),
    ]
    assert waiting["state"] == "waiting" and waiting["state_label"] == "Waiting"
    story3 = client.get(f"/api/v1/tasks/{third}/story").json()
    check_task_story(story3, "waiting")
    assert (
        story3["what"] == "Document the greeting. It starts when “Show the greeting” is finished."
    )
    assert story3["connects_text"] == "It builds on “Show the greeting”, which is not finished yet."
    assert [r["state"] for r in story3["stage_rail"]] == ["done", "current", "todo", "todo", "todo"]
    story2 = client.get(f"/api/v1/tasks/{second}/story").json()
    assert story2["what"] == "Show the greeting. Arena is running the project's checks on it now."
    assert story2["now"] == "Running the project's checks"
    assert story2["connects_text"] == (
        "It builds on “Create the greeting text”, which is finished. When it is done, "
        "“Document the greeting” starts."
    )


def test_what_needs_the_owner_and_a_failed_merge(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    # The director asks a question while planning.
    with store.transaction() as db:
        asked = engine.add_goal(
            db,
            project["id"],
            "Colours",
            "Make the button nicer",
            ["src"],
            planning=True,
            director=True,
        )
    set_state(store, asked["task_id"], "awaiting_input", question="Which colour should it be?")
    s = client.get(f"/api/v1/goals/{asked['goal_id']}/story").json()
    check_goal_story(s, "asking")
    assert s["status"] == "needs_you" and s["status_label"] == "Needs you"
    assert s["needs_you"] == [
        {"kind": "answer", "text": "Which colour should it be?", "task_id": asked["task_id"]}
    ]
    assert [b["title"] for b in s["blocks"]] == ["Planning"] and s["failure"] is None
    # A signed-out agent: sign in, then retry.
    signed_out = goal(store, engine, project, path="src/auth.txt")
    set_state(store, signed_out, "needs_auth", "Subscription login missing or unsupported")
    s = client.get(f"/api/v1/goals/{store.get('tasks', signed_out)['goal_id']}/story").json()
    check_goal_story(s, "auth")
    assert s["needs_you"][0]["kind"] == "sign_in" and s["status"] == "needs_you"
    assert s["failure"]["action"] == "sign_in" and s["failure"]["recoverable"] is True
    assert s["failure"]["next"] == "Sign the agent's account in again on the Mac, then tap Retry."
    # A verified change waiting for approval (manual project).
    staged = goal(store, engine, project, path="src/staged.txt")
    set_state(store, staged, "ready_for_approval")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",
            ("approval_wait", staged, "pending", encode({"candidate": "b" * 40, "created": 1.0})),
        )
    s = client.get(f"/api/v1/goals/{store.get('tasks', staged)['goal_id']}/story").json()
    assert s["needs_you"] == [
        {
            "kind": "approve",
            "text": "A verified change is waiting for your approval.",
            "task_id": staged,
        }
    ]
    assert s["blocks"][0]["stage"]["label"] == "Merge"
    # Pure functions: an automatic merge in flight, then one the source refused.
    task = {**store.get("tasks", staged), "dependencies": []}
    approval = {
        "id": "approval_auto",
        "task_id": staged,
        "state": "accept",
        "created": 2.0,
        "publication": {"state": "queued"},
    }
    auto = {**project, "integration_policy": "auto_local_main"}
    g = {"id": "goal_x", "title": "Staged", "created": 1.0}
    s = story.goal_story(g, auto, [task], [], [approval])
    assert s["status"] == "running" and s["blocks"][0]["state_label"] == "Integrating"
    refused = {**approval, "publication": {"state": "failed", "reason": "Source main moved"}}
    s = story.goal_story(g, auto, [task], [], [refused])
    assert s["status"] == "blocked" and s["blocks"][0]["state"] == "blocked"
    assert s["failure"]["what_failed"].endswith(story.WHAT_FAILED["merge_conflict"])
    assert s["failure"]["recoverable"] is True and "merge again" in s["failure"]["next"]


# -- A3: read-only Mac activity discovery --------------------------------------------------


class FakeProcess:
    """A psutil.Process stand-in: only what discovery may read. Anything else (signals,
    environment, attaching) fails the test."""

    def __init__(
        self,
        pid,
        ppid,
        argv,
        *,
        cwd=None,
        started=None,
        user="leo",
        name=None,
        ports=(),
        cwd_error=None,
        ports_error=None,
        status="running",
    ):
        self.info = {
            "pid": pid,
            "ppid": ppid,
            "name": name if name is not None else os.path.basename(argv[0]) if argv else "",
            "username": user,
            "status": status,
            "cmdline": list(argv) if argv is not None else None,
            "create_time": started if started is not None else 1_000.0 + pid,
        }
        self._cwd, self._cwd_error = cwd, cwd_error
        self._ports, self._ports_error = ports, ports_error

    def cwd(self):
        if self._cwd_error:
            raise self._cwd_error
        return str(self._cwd) if self._cwd else ""

    def net_connections(self, kind="inet"):
        assert kind == "inet"
        if self._ports_error:
            raise self._ports_error
        listening = [
            SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(ip="127.0.0.1", port=p))
            for p in self._ports
        ]
        talking = SimpleNamespace(status="ESTABLISHED", laddr=SimpleNamespace(port=40000))
        return [*listening, talking]

    def __getattr__(self, name):
        raise AssertionError(f"discovery must not call Process.{name}")


def git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    return path


def fake_mac(tmp_path, store, project, now):
    """A Mac with Claude Code and Codex sessions, Arena work (live, matched by identity and
    by workspace, recorded live without a process, a director chat turn), a dev server, a
    test run, an agent whose folder macOS withholds and processes discovery must ignore."""
    import psutil

    home = tmp_path / "mac-home"
    leo = git_repo(home / "Projects" / "LeoOS")
    continuoum = git_repo(home / "Projects" / "Continuoum")
    exchange = git_repo(home / "Projects" / "AgentExchange")
    (leo / "src").mkdir()
    (home / "Downloads").mkdir(parents=True)
    # Claude Code session files for LeoOS (metadata only is read): the session itself two
    # minutes ago, one subagent active within ten minutes and one an hour ago.
    sessions = home / ".claude" / "projects" / str(leo).replace("/", "-").replace(".", "-")
    (sessions / "s1" / "subagents").mkdir(parents=True)
    for path, age in (
        (sessions / "s1.jsonl", 120),
        (sessions / "s1" / "subagents" / "agent-a.jsonl", 300),
        (sessions / "agent-b.jsonl", 3600),
    ):
        path.write_text('{"secret": "never read"}\n')
        os.utime(path, (now - age, now - age))
    # Arena's records: a live build whose worker is running (identity), a live build seen
    # only through its workspace, a live build with no process left, a director turn.
    with store.transaction() as db:
        created = engine_goal(db, store, project, "Add the greeting")
    workspaces = store.home / "workspaces"
    records = {
        "attempt_live": dict(identity={"pid": 400, "created": 1400.0, "boot": 1.0}),
        "attempt_ws": dict(identity={"pid": 9999, "created": 5.0, "boot": 1.0}),
        "attempt_ghost": dict(identity={"pid": 7777, "created": 7.0, "boot": 1.0}),
    }
    for ident, extra in records.items():
        with store.transaction() as db:
            db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?)",
                (
                    ident,
                    created["task_id"],
                    "slot0",
                    1,
                    "running",
                    encode(
                        {
                            "created": now - 600,
                            "phase": "build",
                            "model": "test-only",
                            "artifact_dir": str(store.home / "attempts" / ident),
                            "workspace": str(workspaces / ident),
                            **extra,
                        }
                    ),
                ),
            )
    (workspaces / "attempt_ws" / "src").mkdir(parents=True)
    controller = 50
    denied = psutil.AccessDenied(pid=800)
    table = [
        FakeProcess(1, 0, ["/sbin/launchd"], user="root"),
        FakeProcess(60, 1, ["codex"], user="root", cwd=continuoum),  # someone else's
        FakeProcess(
            controller, 1, ["/usr/bin/python3", "-m", "agent_arena.control", "serve"], cwd=leo
        ),
        FakeProcess(
            51,
            controller,
            ["codex", "exec", "--sandbox", "read-only", "--cd", str(store.home / "chat-scratch")],
            cwd=store.home / "chat-scratch",
        ),
        FakeProcess(100, 1, ["-zsh"], cwd=home),
        # Claude Code (node) in LeoOS, running the tests through its shell.
        FakeProcess(
            200,
            100,
            ["node", "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js"],
            cwd=leo,
            name="node",
            started=now - 3600,
        ),
        FakeProcess(202, 200, ["/bin/zsh", "-c", "pytest -q"], cwd=leo),
        FakeProcess(201, 202, ["/usr/bin/python3", "/usr/local/bin/pytest", "-q"], cwd=leo),
        # Codex (npm wrapper and its native binary) in Continuoum.
        FakeProcess(300, 100, ["node", "/opt/homebrew/bin/codex"], cwd=continuoum, name="node"),
        FakeProcess(
            301,
            300,
            ["/opt/homebrew/lib/node_modules/@openai/codex/vendor/codex/codex"],
            cwd=continuoum,
        ),
        # Arena's worker for attempt_live (matched by identity) and its agent CLI.
        FakeProcess(
            400,
            controller,
            [
                "/usr/bin/python3",
                "-m",
                "agent_arena.control.worker",
                str(store.home),
                "attempt_live",
            ],
            cwd=leo,
            started=1400.0,
        ),
        FakeProcess(
            401,
            400,
            ["codex", "exec", "--cd", str(workspaces / "attempt_live")],
            cwd=workspaces / "attempt_live",
        ),
        # The controller checking the combined change in its integration folder.
        FakeProcess(
            450,
            controller,
            ["/usr/bin/python3", "-m", "pytest", "-q"],
            cwd=store.home
            / "projects"
            / project["id"]
            / f"integrate-{created['task_id']}-work_0a1b2c3d4e5f6a7b",
        ),
        # A Claude CLI inside attempt_ws's workspace (its worker is not visible).
        FakeProcess(500, 1, ["claude", "-p", "--model", "sonnet"], cwd=workspaces / "attempt_ws"),
        # npm run dev → sh → vite listening on 5173.
        FakeProcess(600, 100, ["npm", "run", "dev"], cwd=exchange),
        FakeProcess(601, 600, ["sh", "-c", "vite"], cwd=exchange),
        FakeProcess(
            602,
            601,
            ["node", str(exchange / "node_modules/.bin/vite")],
            cwd=exchange,
            ports=[5173],
        ),
        # Tests run from a terminal in the enrolled project.
        FakeProcess(700, 100, ["python3", "-m", "pytest", "tests", "-q"], cwd=project["repo"]),
        # Aider with an API key on its command line; macOS withholds its folder.
        FakeProcess(
            800,
            100,
            ["aider", "--openai-api-key", "sk-abcdefghijklmnopqrstu", "--model", "gpt"],
            cwd_error=denied,
        ),
        # A server whose ports cannot be read, outside any repository.
        FakeProcess(
            900,
            100,
            ["python3", "-m", "http.server", "8000"],
            cwd=home / "Downloads",
            ports_error=psutil.AccessDenied(pid=900),
        ),
        # Claude Code started in the home folder: not a repository.
        FakeProcess(950, 100, ["claude"], cwd=home),
        # Not development work.
        FakeProcess(990, 1, ["/Applications/Claude.app/Contents/MacOS/Claude"], cwd=home),
        FakeProcess(991, 1, ["vim", str(home / "claude-code-notes.md")], cwd=home),
    ]
    return home, controller, table, created


def engine_goal(db, store, project, title):
    from agent_arena.control.engine import Engine

    return Engine(store).add_goal(db, project["id"], title, title, ["src"], (), True, 300)


def by_pid(result):
    return {
        (group, a["pid"]): a
        for group in ("managed", "external", "unknown")
        for a in result["groups"][group]
    }


def test_discovery_reconciles_the_mac_with_arenas_records(setup, tmp_path):
    from agent_arena.control import discovery

    store, engine, project, source = setup
    now = time.time()
    home, controller, table, created = fake_mac(tmp_path, store, project, now)
    result = discovery.discover(
        store, processes=table, home=home, user="leo", controller_pid=controller, now=now
    )
    assert set(result) == {"generated", "groups", "summary", "limitations", "partial", "stale"}
    assert result["generated"] == now
    assert result["partial"] is False and result["stale"] is False
    found = by_pid(result)
    keys = {
        "kind",
        "label",
        "repo",
        "project",
        "folder",
        "pid",
        "started",
        "last_activity",
        "headless",
        "ports",
        "evidence",
        "arena",
        "subagents_recent",
    }
    assert all(set(a) == keys for a in found.values())
    assert {(g, pid) for g, pid in found} == {
        ("managed", 400),
        ("managed", 450),
        ("managed", 500),
        ("managed", None),
        ("managed", 51),
        ("external", 200),
        ("external", 201),
        ("external", 300),
        ("external", 600),
        ("external", 700),
        ("external", 900),
        ("unknown", 800),
        ("unknown", 950),
    }
    arena_ids = {"goal_id": created["goal_id"], "task_id": created["task_id"]}
    live = found[("managed", 400)]
    assert live["kind"] == "arena_build" and live["arena"] == {
        **arena_ids,
        "attempt_id": "attempt_live",
    }
    assert live["label"] == "Arena is building “Add the greeting”" and live["headless"] is True
    assert live["project"] == "Fixture" and live["folder"].endswith("workspaces/attempt_live")
    assert "Matches Arena's record of this attempt (process id and start time)" in live["evidence"]
    assert "Agent process: Codex (pid 401)" in live["evidence"]
    by_workspace = found[("managed", 500)]
    assert by_workspace["arena"]["attempt_id"] == "attempt_ws"
    assert "Runs inside the workspace of this Arena attempt" in by_workspace["evidence"]
    ghost = found[("managed", None)]
    assert ghost["label"] == "Arena records this as running, but no agent process was found"
    assert ghost["arena"]["attempt_id"] == "attempt_ghost"
    assert "No running process has its recorded id 7777" in ghost["evidence"]
    checking = found[("managed", 450)]
    assert checking["label"] == "Arena is checking the combined change for “Add the greeting”"
    assert checking["arena"] == {**arena_ids, "attempt_id": None}
    assert "Running the project's checks: pytest (pid 450)" in checking["evidence"]
    chat = found[("managed", 51)]
    assert chat["kind"] == "arena_director" and chat["arena"] is None
    assert chat["label"] == "Arena's director is answering a chat message"
    claude = found[("external", 200)]
    assert claude["kind"] == "claude" and claude["repo"] == "~/Projects/LeoOS"
    assert claude["project"] is None and claude["headless"] is False
    assert abs(claude["last_activity"] - (now - 120)) < 1 and claude["subagents_recent"] == 1
    assert (
        "1 subagent session file changed in the last 10 minutes (inferred from session files)"
        in (claude["evidence"])
    )
    tests = found[("external", 201)]
    assert tests["kind"] == "tests" and "Started from Claude Code (pid 200)" in tests["evidence"]
    codex = found[("external", 300)]
    assert codex["kind"] == "codex" and codex["repo"] == "~/Projects/Continuoum"
    assert "1 related child process" in codex["evidence"]  # the native binary, folded in
    server = found[("external", 600)]
    assert server["kind"] == "dev_server" and server["ports"] == [5173]
    assert server["label"] == "Development server (npm run dev)"
    enrolled = found[("external", 700)]
    assert enrolled["project"] == "Fixture" and enrolled["label"] == "Test run (pytest)"
    withheld = found[("unknown", 800)]
    assert (
        withheld["folder"] is None and "macOS withheld its working folder" in withheld["evidence"]
    )
    assert "Command: aider --openai-api-key [REDACTED] --model gpt" in withheld["evidence"]
    assert "sk-abc" not in json.dumps(result)
    assert found[("unknown", 950)]["folder"] == "~"
    assert found[("external", 900)]["ports"] == [] and found[("external", 900)]["folder"] == (
        "~/Downloads"
    )
    assert result["summary"] == [
        "Arena is building Fixture: 2 agents.",
        "Arena is checking the combined change for Fixture.",
        "Arena records a build of Fixture as running, but no agent process was found.",
        "Arena's director is answering a chat message.",
        "Claude is working in LeoOS (active 2 min ago).",
        "A Codex session is open in Continuoum (started " + discovery.ago(now - 1300.0) + ").",
        "Tests are running for Fixture (pytest).",
        "Claude is running tests for LeoOS.",
        "A development server is running for AgentExchange on port 5173.",
        # ~/Downloads is a macOS privacy folder: named, never inspected for a repository.
        "A development server is running in Downloads.",
        "A Claude Code session is open in a folder outside any project (~).",
        "An Aider session is running, but macOS did not reveal its working folder.",
    ]
    limitations = result["limitations"]
    assert limitations[0].startswith("Only your own processes were inspected")
    assert discovery.PRIVACY_NOTE in limitations
    assert "macOS withheld the working folder of 1 process, so its repository is unknown." in (
        limitations
    )
    assert "Listening ports could not be read for 1 development server(s)." in limitations
    assert any("inferred from its session files" in x for x in limitations)
    assert "Arena only looked: it did not stop, attach to or change anything." in limitations
    # The director's compact view: no process ids, command lines or folders anywhere.
    compact = discovery.compact(result)
    assert len(compact["activities"]) == 13 and compact["omitted"] == 0
    assert "pid" not in json.dumps(compact) and "Command" not in json.dumps(compact)
    assert "A Claude Code session is open in a folder outside any project." in compact["summary"]
    assert compact["activities"][0]["group"] == "managed by Arena"
    assert discovery.compact(result, limit=3)["omitted"] == 10


def test_discovery_cache_endpoint_and_quiet_machine(setup, tmp_path):
    from agent_arena.control import discovery

    store, engine, project, source = setup
    now = time.time()
    home, controller, table, created = fake_mac(tmp_path, store, project, now)
    reads, clock = [], [now]

    def processes():
        reads.append(1)
        return table

    cached = discovery.Discovery(
        store,
        processes=processes,
        home=home,
        user="leo",
        controller_pid=controller,
        clock=lambda: clock[0],
    )
    first = cached.get()
    clock[0] += 9
    assert cached.get() is first and len(reads) == 1  # cached for ten seconds
    clock[0] += 2
    assert cached.get() is not first and len(reads) == 2
    client, headers = paired_client(store)
    client.app.state.discovery = cached
    r = client.get("/api/v1/mac-activity")
    assert r.status_code == 200 and r.json()["groups"]["external"]
    assert (
        TestClient(client.app, base_url="http://127.0.0.1:8790")
        .get("/api/v1/mac-activity")
        .status_code
        == 401
    )
    assert "mac" not in json.dumps(client.get("/api/v1/overview").json()["goals"])
    # A machine with nothing on it says so, and still states its limits.
    quiet = discovery.discover(store, processes=[], home=home, user="leo", controller_pid=1)
    assert (
        quiet["summary"]
        == ["Arena records a build of Fixture as running, but no agent process was found."] * 3
    )
    assert quiet["limitations"][0].startswith("Only your own processes")
    # Classification never mistakes a file name for an agent.
    assert discovery.classify(["vim", "/Users/x/claude-code-notes.md"]) is None
    assert discovery.classify(["/Applications/Claude.app/Contents/MacOS/Claude"]) is None
    assert discovery.classify(["vite", "build"]) is None
    assert discovery.classify(["node", "/x/node_modules/.bin/vite"]) == ("dev_server", "vite")
    assert discovery.classify(["python3", "-m", "uvicorn", "app:app"]) == ("dev_server", "uvicorn")
    assert discovery.classify(["pnpm", "dev"]) == ("dev_server", "pnpm run dev")
    assert discovery.classify(["npm", "test"]) == ("tests", "npm test")
    assert discovery.classify(["npx", "playwright", "test"]) == ("tests", "playwright test")
    assert discovery.classify(["go", "test", "./..."]) == ("tests", "go test")
    assert discovery.classify(["cargo", "test"]) == ("tests", "cargo test")
    assert discovery.classify(["node", "/x/bin/gemini"]) == ("agent", "Gemini CLI")
    assert discovery.classify(["cursor-agent"]) == ("agent", "Cursor Agent")
    assert discovery.classify(["flask", "run"]) == ("dev_server", "flask run")
    assert discovery.classify(["jupyter-lab"]) == ("dev_server", "jupyter")
    assert discovery.classify(["next", "dev"]) == ("dev_server", "next dev")
    assert discovery.classify(["webpack", "serve"]) == ("dev_server", "webpack serve")
    assert discovery.classify(["streamlit", "run", "app.py"]) == ("dev_server", "streamlit")
    assert discovery.classify(["jest", "--watch"]) == ("tests", "jest")


def test_the_real_state_fixture_serves_a_recorded_discovery(tmp_path, monkeypatch):
    from agent_arena.control import discovery

    recorded = json.loads((FIXTURES / "mac-activity.json").read_text())
    store, client, headers = real_state_client(
        tmp_path, monkeypatch, ARENA_DISCOVERY_FIXTURE=str(FIXTURES / "mac-activity.json")
    )
    served = client.get("/api/v1/mac-activity").json()
    assert served["summary"] == recorded["summary"]
    assert abs(served["generated"] - time.time()) < 5
    first = recorded["groups"]["external"][0]
    moved = served["groups"]["external"][0]
    assert round(served["generated"] - moved["last_activity"]) == round(
        recorded["generated"] - first["last_activity"]
    )
    # The shape is exactly what discovery produces.
    live = discovery.discover(store, processes=[], home=tmp_path, user="leo", controller_pid=1)
    assert set(served) == set(live)
    keys = set(discovery._activity("claude", "x"))
    assert all(set(a) == keys for group in served["groups"].values() for a in group)
    # One real (read-only) discovery through the same server: here the fake, empty table.
    real = client.get("/api/v1/mac-activity", headers={"X-Arena-Discovery": "real"}).json()
    assert real["groups"] == {"managed": [], "external": [], "unknown": []}
    assert real["summary"] == ["No coding agents, development servers or test runs were found."]


# -- A4: the director's context ----------------------------------------------------------------


def director_on(store, engine, tmp_path, monkeypatch, answers, *, activity=None, now=None):
    from agent_arena.control import conversation
    from tests.control.test_conversation import FakeRunner, fake_gh_run, ready_accounts

    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    runner = FakeRunner(
        [{"reply": a, "project_hint": None, "action": "chat", "spec": None} for a in answers]
    )
    director = conversation.Director(
        store,
        engine,
        run=fake_gh_run,
        runner=runner,
        projects_dir=tmp_path / "none",
        mac_activity=(lambda: activity),
        clock=(lambda: now) if now else time.time,
    )
    return director, runner


def test_director_context_holds_the_mac_recent_outcomes_and_failures(tmp_path, monkeypatch):
    from agent_arena.control import conversation, discovery
    from agent_arena.control.engine import Engine
    from tests.control.test_conversation import wait_idle

    store = Store(real_state_home(tmp_path))  # proof projects archived on open
    recorded = json.loads((FIXTURES / "mac-activity.json").read_text())
    now = 1793189210.0 + 3600  # an hour after the real state's last event
    director, runner = director_on(
        store,
        Engine(store),
        tmp_path,
        monkeypatch,
        ["Arena is building Arena Smoke 20261028; Claude is working in LeoOS.", "Noted."],
        activity=recorded,
        now=now,
    )
    conv = conversation.new_conversation(store)
    context, selected = director._context(conv, "What's running on my laptop?")
    mac = context["mac_activity"]
    assert mac["summary"] == [discovery.pathless(s) for s in recorded["summary"]]
    assert mac["omitted"] == 0 and "(~" not in json.dumps(mac["summary"])
    assert len(mac["activities"]) == 6 and "pid" not in json.dumps(mac)
    assert "Command" not in json.dumps(mac) and "evidence" not in json.dumps(mac)
    assert [a["group"] for a in mac["activities"]][:2] == [
        "managed by Arena",
        "discovered on your Mac",
    ]
    assert mac["limitations"] == recorded["limitations"]
    # Builds that finished or stopped in the last 24 hours (archived projects left out).
    outcomes = context["recent_outcomes"]
    assert len(outcomes) == 7 and all(
        set(o) == {"title", "project", "outcome", "when"} for o in outcomes
    )
    assert outcomes[0] == {
        "title": "Arena can run its queued builds unattended overnight in a bounded…",
        "project": "Arena",
        "outcome": "Blocked",
        "when": "1h ago",
    }
    assert {o["outcome"] for o in outcomes} == {"Blocked", "Finished", "Stopped"}
    assert "Orchestrator live proof" not in json.dumps(context["recent_outcomes"])
    # Why builds stopped: plain Failure records with failing checks and findings.
    failures = context["failures"]
    assert len(failures) == 4 and "Orchestrator" not in json.dumps(failures)
    assert "ran out of time" in failures[0]["failure"]["what_failed"]
    checks = [f for f in failures if f["failing_checks"]]
    assert {tuple(f["failing_checks"]) for f in checks} == {("Arena self-check",)}
    for f in failures:
        assert set(f) == {
            "build",
            "project",
            "when",
            "goal_id",
            "failure",
            "review_findings",
            "failing_checks",
            "technical_reason",
        }
        assert_plain_failure(f["failure"], f["goal_id"])
    assert failures[0]["technical_reason"].startswith("CLI did not complete successfully")
    assert context["arena_status"]["running"] == []
    # A real turn: the prompt carries the new facts and rules; the reply carries the card.
    director.submit(conv["id"], "What's running on my laptop?")
    wait_idle(store, conv["id"])
    prompt = runner.prompts[0]
    for key in ('"mac_activity"', '"recent_outcomes"', '"failures"', '"arena_status"'):
        assert key in prompt
    assert "answer ONLY from mac_activity" in prompt and "Never invent" in prompt
    assert "Claude is working in LeoOS (active 2 min ago)." in prompt
    reply = conversation.messages(store, conv["id"])[-1]
    assert reply["role"] == "assistant" and reply["mac_activity"] == discovery.capped(recorded)
    card = reply["mac_activity"]
    assert card["summary"] == [discovery.pathless(s) for s in recorded["summary"]]
    assert set(card) == set(recorded)
    # A stored card keeps what the phone shows: no evidence, command lines, pids or folders.
    for activity in (a for g in card["groups"].values() for a in g):
        assert set(activity) == set(discovery.CARD_KEYS)
    # A question that is not about what is running gets no card.
    director.submit(conv["id"], "Let's start improving the Pomodoro timer.")
    wait_idle(store, conv["id"])
    assert "mac_activity" not in conversation.messages(store, conv["id"])[-1]


def test_asking_about_a_block_or_build_sets_the_project_and_brings_its_story(
    setup, tmp_path, monkeypatch
):
    from agent_arena.control import conversation
    from tests.control.test_conversation import wait_idle

    store, engine, project, _ = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project, path="src/copy.txt")
    set_state(store, task_id, "blocked", "Frozen acceptance checks did not all pass")
    goal_id = store.get("tasks", task_id)["goal_id"]
    cid = post(
        client, headers, "/api/v1/conversations", {"revision": 0, "project_id": None}, "c1"
    ).json()["id"]
    assert store.get("conversations", cid)["project_id"] is None
    submitted = []
    monkeypatch.setattr(
        conversation.Director,
        "submit",
        lambda self, ident, text, about=None: submitted.append((ident, text, about)),
    )
    path = f"/api/v1/conversations/{cid}/messages"
    for n, about in enumerate([{"task_id": "task_missing"}, {"goal_id": "goal_missing"}], start=1):
        r = post(client, headers, path, {"text": "Why?", "about": about}, f"bad{n}")
        assert r.status_code == 404, r.text
    for n, about in enumerate([{}, {"task_id": task_id, "goal_id": goal_id}], start=1):
        r = post(client, headers, path, {"text": "Why?", "about": about}, f"invalid{n}")
        assert r.status_code == 422, r.text
    assert submitted == [] and store.get("conversations", cid)["project_id"] is None
    r = post(
        client, headers, path, {"text": "What went wrong?", "about": {"task_id": task_id}}, "m1"
    )
    assert r.status_code == 200, r.text
    assert submitted == [(cid, "What went wrong?", {"task_id": task_id})]
    assert store.get("conversations", cid)["project_id"] == project["id"]
    # A plain message still goes through unchanged.
    r = post(client, headers, path, {"text": "Thanks", "revision": 0}, "m2")
    assert r.status_code == 200 and submitted[-1] == (cid, "Thanks", None)
    monkeypatch.undo()
    # The turn: the block's story and failure are in the director's context.
    director, runner = director_on(
        store, engine, tmp_path, monkeypatch, ["The checks failed.", "It stopped."]
    )
    director.submit(cid, "What went wrong?", about={"task_id": task_id})
    wait_idle(store, cid)
    prompt = runner.prompts[0]
    assert '"about":{"kind":"block"' in prompt
    assert "The project's automatic checks did not pass." in prompt
    messages = conversation.messages(store, cid)
    assert messages[-2]["about"] == {"task_id": task_id}  # the owner's question
    assert messages[-1]["about"] == {"task_id": task_id}  # the director's answer
    assert "mac_activity" not in messages[-1]
    director.submit(cid, "And the whole build?", about={"goal_id": goal_id})
    wait_idle(store, cid)
    assert '"about":{"goal_id"' in runner.prompts[1] or '"kind":"build"' in runner.prompts[1]
    assert f'"goal_id":"{goal_id}"' in runner.prompts[1]


def test_arena_status_says_when_an_agent_went_quiet(setup):
    from agent_arena.control.status import arena_status

    store, engine, project, _ = setup
    task_id = goal(store, engine, project, path="src/quiet.txt")
    set_state(store, task_id, "running")
    attempt = insert_attempt(store, task_id, state="running", created=time.time() - 3600)
    write_transcript(store, attempt, {"type": "turn.started"}, age=700)
    running = arena_status(store)["running"][0]
    assert running["stalled"] is True and 695 <= running["last_activity_seconds"] <= 710
    write_transcript(store, attempt, {"type": "turn.started"}, age=5)
    running = arena_status(store)["running"][0]
    assert running["stalled"] is False and running["last_activity_seconds"] <= 10
    with store.transaction() as db:
        db.execute("UPDATE attempts SET state='verifying' WHERE id=?", (attempt,))
    write_transcript(store, attempt, {"type": "turn.completed"}, age=900)
    assert arena_status(store)["running"][0]["stalled"] is False  # checks are running


# -- A5: a purpose for every planned task -------------------------------------------------------


def test_director_plans_give_each_task_a_purpose(tmp_path):
    from agent_arena.control.planning import DIRECTOR_PROMPT, validate_director_spec
    from tests.control import test_director

    assert "purpose" in DIRECTOR_PROMPT and "no file names, code or jargon" in DIRECTOR_PROMPT
    plan = test_director.spec()
    plan["tasks"][0]["purpose"] = "So you can see at a glance which label is current."
    assert validate_director_spec(plan, ["src"])["tasks"][0]["purpose"] == (
        "So you can see at a glance which label is current."
    )
    long = {**plan, "tasks": [{**plan["tasks"][0], "purpose": "word " * 100}]}
    clipped = validate_director_spec(long, ["src"])["tasks"][0]["purpose"]
    assert len(clipped) == 300 and clipped.endswith("…")
    absent = test_director.spec()
    assert validate_director_spec(absent, ["src"])["tasks"][0]["purpose"] == ""
    with pytest.raises(ValueError):  # unknown fields are still refused
        validate_director_spec({**plan, "tasks": [{**plan["tasks"][0], "why": "x"}]}, ["src"])
    # The engine stores it on the task it creates; the story shows it.
    setup = test_director.setup.__wrapped__(tmp_path)
    store, engine = setup[0], setup[1]
    task_id, client, headers = test_director.request(setup)
    attempt = engine.claim()
    assert test_director.finish(engine, attempt, status="passed", plan=plan)
    child = store.get("tasks", store.get("tasks", task_id)["planned_tasks"][0])
    assert child["purpose"] == "So you can see at a glance which label is current."
    s = client.get(f"/api/v1/goals/{child['goal_id']}/story").json()
    assert s["blocks"][0]["purpose"] == "So you can see at a glance which label is current."
    assert s["summary"] == "I found unclear labels and will improve them."


# -- A2 refinements (lead review of the real-state screens) ----------------------------------


def test_build_titles_prefer_the_agreed_outcome_and_drop_legacy_chips(tmp_path):
    from agent_arena.control import views
    from agent_arena.control.titles import goal_title

    for request, title in {
        "Add to an existing project: What repo are we in? whatever": "What repo are we in?",
        "Build a new feature: a dark mode toggle. Then more.": "A dark mode toggle",
        "FIX A BUG: crash on start": "Crash on start",
        "fix a bug:crash on start": "Crash on start",
        "Improve Arena: faster chat": "Faster chat",
        "Build: the importer": "The importer",
        "Fix: the importer": "The importer",
        "Improve: the importer": "The importer",
        "Improve the speed of the importer": "Improve the speed of the importer",
        "Building blocks for later": "Building blocks for later",
        "OUTCOME:\nFix a bug: crash": "Crash",
    }.items():
        assert goal_title({"request": request}) == title, request
    legacy = {
        "title": "Add to an existing project: What repo are we in?",
        "request": "Add to an existing project: What repo are we in? whatever you want",
    }
    assert goal_title(legacy) == "What repo are we in?"
    agreed = {**legacy, "specification": {"outcome": "Arena runs builds overnight. Safely."}}
    assert goal_title(agreed) == "Arena runs builds overnight"
    assert goal_title({"title": "", "request": "Build:"}) == ""
    assert views.slim_goal(agreed)["display_title"] == "Arena runs builds overnight"
    # The real blocked overnight-mode build is named after its agreed outcome everywhere.
    store = Store(real_state_home(tmp_path))
    client, headers = paired_client(store)
    expected = "Arena can run its queued builds unattended overnight in a bounded…"
    s = client.get("/api/v1/goals/goal_815db45a71e3a62d/story").json()
    assert s["title"] == expected
    goals = {g["id"]: g for g in client.get("/api/v1/overview").json()["goals"]}
    assert goals["goal_815db45a71e3a62d"]["display_title"] == expected
    outcome = (
        "Arena can run its queued builds unattended overnight in a bounded, lower-compute mode, "
        "preserving every verification and approval gate."
    )
    # What it was meant to do: the outcome itself, never "Part of: …" (that stays for blocks).
    assert s["failure"]["intended"] == outcome
    assert s["blocks"][0]["purpose"] == "Part of: " + outcome
    task = client.get("/api/v1/tasks/task_bd2af42e9557b61d/story").json()
    assert task["failure"]["intended"] == outcome and task["why"] == "Part of: " + outcome


def test_a_director_plan_is_named_after_its_build_in_status_and_on_the_mac(setup, tmp_path):
    from agent_arena.control import discovery
    from agent_arena.control.status import arena_status

    store, engine, project, _ = setup
    with store.transaction() as db:
        created = engine.add_goal(
            db,
            project["id"],
            "Add to an existing project: Make the importer faster",
            "Add to an existing project: Make the importer faster. Keep the format.",
            ["src"],
            planning=True,
            director=True,
        )
    plan_id = created["task_id"]
    set_state(store, plan_id, "awaiting_input", question="Which files are slow?")
    assert arena_status(store)["needs_you"][0]["title"] == "Make the importer faster"
    set_state(store, plan_id, "running")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?)",
            (
                "attempt_plan1",
                plan_id,
                "slot0",
                1,
                "running",
                encode(
                    {
                        "created": time.time(),
                        "phase": "plan",
                        "workspace": str(store.home / "workspaces" / "attempt_plan1"),
                        "identity": {"pid": 4000, "created": 4000.0, "boot": 1.0},
                    }
                ),
            ),
        )
    assert arena_status(store)["running"][0]["title"] == "Make the importer faster"
    worker = FakeProcess(
        4000,
        1,
        ["python3", "-m", "agent_arena.control.worker", str(store.home), "attempt_plan1"],
        cwd=tmp_path,
        started=4000.0,
    )
    found = discovery.discover(
        store, processes=[worker], home=tmp_path, user="leo", controller_pid=1
    )
    activity = found["groups"]["managed"][0]
    assert activity["kind"] == "arena_director"
    assert activity["label"] == "Arena director is planning “Make the importer faster”"


def test_progress_counts_parts_in_flight_and_how_far_each_got():
    def task(ident, state, phase="build", **extra):
        return {
            "id": ident,
            "state": state,
            "phase": phase,
            "title": ident,
            "created": float(len(ident)),
            "updated": 2.0,
            "planner_attempt": "attempt_plan",
            "dependencies": [],
            **extra,
        }

    goal_record = {"id": "goal_p", "title": "Progress", "created": 1.0}
    plan = {
        **task("plan", "completed", "plan", planned_tasks=["a"]),
        "planner_attempt": None,
    }
    tasks = [
        plan,
        task("a", "completed"),
        task("bb", "verifying"),  # Test: 2/5
        task("ccc", "waiting_dependency"),  # Implement: 1/5 (designed, not started)
        task("dddd", "blocked", "review", reason="Independent review rejected: []"),
        task("eeeee", "cancelled"),
    ]
    review = {
        "id": "attempt_r",
        "task_id": "dddd",
        "account_id": "slot0",
        "state": "failed",
        "phase": "review",
        "created": 3.0,
        "result": {"status": "failed", "reason": "Independent review rejected: []"},
    }
    s = story.goal_story(goal_record, None, tasks, [review])
    # done + (Test 0.4 + Implement 0.2 + Review reached 0.6) over the four counted blocks;
    # in progress: only the block being checked (waiting and stopped blocks are not).
    assert s["progress"] == {"done": 1, "total": 4, "in_progress": 1, "fraction": 0.55}
    assert [b["stage"]["index"] for b in s["blocks"]] == [4, 2, 1, 3, 1]
    planning = story.goal_story(goal_record, None, [{**plan, "state": "running"}], [])
    assert planning["progress"] == {"done": 0, "total": 1, "in_progress": 0, "fraction": 0.0}
    finished = story.goal_story(goal_record, None, [plan, task("a", "completed")], [])
    assert finished["progress"] == {"done": 1, "total": 1, "in_progress": 0, "fraction": 1.0}
    nothing = story.goal_story(goal_record, None, [plan, task("e", "cancelled")], [])
    assert nothing["progress"] == {"done": 0, "total": 0, "in_progress": 0, "fraction": 0.0}
