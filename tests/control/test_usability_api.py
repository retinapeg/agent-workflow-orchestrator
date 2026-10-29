"""Usability release, backend contract: slim overview, detail endpoints, live attempt
tail, project archive (+ migration) and display titles. Fixtures: the small fake-CLI
project from test_control, two recorded CLI transcripts and a sanitised copy of a real
control database (sessions, commands and pairing records removed)."""

from __future__ import annotations

import builtins
import gzip
import json
import pathlib
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_arena.control import livetail, views
from agent_arena.control.conversation import Catalog
from agent_arena.control.store import (
    ARCHIVE_PROOF_MIGRATION,
    Store,
    archive_proof_projects,
    encode,
    is_proof_project,
)
from agent_arena.control.titles import display_title
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client

FIXTURES = Path(__file__).parent / "fixtures"
CODEX_WORKSPACE = (
    "/Users/leonardaarons-ditson/Projects/agent-workflow-orchestrator/.arena-control/"
    "workspaces/attempt_1cb512d44c882c44"
)
CLAUDE_WORKSPACE = (
    "/Users/leonardaarons-ditson/Projects/agent-workflow-orchestrator/.arena-control/"
    "workspaces/attempt_0709601ef8b90698"
)


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


def insert_attempt(store, task_id, account_id="slot0", state="passed", **data):
    ident = "attempt_" + task_id.split("_")[1]
    with store.transaction() as db:
        task = store.get("tasks", task_id, db)
        payload = dict(
            created=time.time() - 30,
            phase="build",
            base="0" * 40,
            artifact_dir=str(store.home / "attempts" / ident),
            workspace=str(store.home / "workspaces" / ident),
            identity={"pid": 1, "start": 1.0},
            reason="Routed to the only slot",
            model="test-only",
            effort="medium",
            routing={
                "provider": "codex",
                "model": "test-only",
                "effort": "medium",
                "action": "initial",
                "reason": "x" * 500,
                "complexity": "simple",
            },
            instruction_count=0,
        )
        payload.update(data)
        db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?)",
            (ident, task_id, account_id, task["token"], state, encode(payload)),
        )
        store.event(db, "attempt.claimed", attempt_id=ident, task_id=task_id, token=task["token"])
    return ident


def big_result(ident, token, heavy=True):
    """A > 20 KB result. ``heavy`` maxes every clipped field (to test the clipping); the
    realistic variant has a review of four findings and a short summary, with the bulk in
    the plan, check output and session ids that the list never shows."""
    return {
        "status": "passed",
        "reason": "Candidate verified",
        "seconds": 12.5,
        "cost": 0.01,
        "usage": [
            {
                "call": 1,
                "provider": "codex_cli",
                "model": "test-only",
                "model_source": "declared",
                "input": 100,
                "output": 10,
                "not_applicable": [],
                "single_request": False,
                "wall_time_s": None,
            }
        ],
        "invocation": {"exit_code": 0, "timed_out": False},
        "provider_started": 1.0,
        "provider_ended": 2.0,
        "attempt_id": ident,
        "token": token,
        "candidate": "a" * 40,
        "changed": ["src/a.txt"],
        "checks": [
            {
                "name": "frozen",
                "status": "PASS",
                "seconds": 0.1,
                "argv": ["python", "check.py"],
                "sha256": "b" * 64,
                "artifact": "checks-0.txt",
                "output": "x" * 4000,
            }
        ],
        "review": {
            "approved": True,
            "summary": "s" * (2000 if heavy else 240),
            "findings": ["f" * 1000 for _ in range(20)] if heavy else ["f" * 180] * 4,
        },
        "summary": "y" * (5000 if heavy else 360),
        "plan": {"milestones": ["z" * 5000] * (1 if heavy else 3)},
        "session_ids": ["sess"] * 10,
        "artifacts": {"transcript.jsonl": "c" * 64, "diff.patch": "d" * 64, "child.json": "e" * 64},
    }


# -- titles ---------------------------------------------------------------------------


def test_display_title_cases():
    assert display_title("OUTCOME:\nAdd a Copy button. Keep the rest.") == "Add a Copy button"
    assert display_title("Outcome: Stop malformed addresses getting through") == (
        "Stop malformed addresses getting through"
    )
    assert display_title("Request: **Outcome:** Stop it\n\nmore") == "Stop it"
    assert display_title("# Pomodoro\nBuild a timer.") == "Pomodoro"
    assert display_title("- [Fix](http://x) the *flaky* __test__ now. Then more.") == (
        "Fix the flaky test now"
    )
    assert display_title("Wrap here\nand continue. Second.") == "Wrap here and continue"
    assert display_title("src/a.txt") == "src/a.txt"
    # A hyphenated word that starts with a label word is a word, not a label.
    assert display_title("Build-time caching for the CLI") == "Build-time caching for the CLI"
    assert display_title("Task-runner retries. More.") == "Task-runner retries"
    assert display_title("Outcome - Add a bell") == "Add a bell"
    assert display_title("Outcome — Add a bell") == "Add a bell"
    assert display_title("") == "" and display_title(None) == "" and display_title("OUTCOME:") == ""
    long = "word " * 40
    cut = display_title(long)
    assert len(cut) <= 72 and cut.endswith("…") and not cut.endswith(" …")
    assert len(display_title("A" * 100)) == 72


def test_new_goal_titles_are_display_titles(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    r = client.post(
        f"/api/v1/projects/{project['id']}/requests",
        json={
            "instructions": "OUTCOME:\nMake the *label* clearer on the phone. Keep tests green.",
            "revision": project["rev"],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    goal_row = store.get("goals", r.json()["goal_id"])
    assert goal_row["title"] == "Make the label clearer on the phone"
    assert store.get("tasks", r.json()["task_id"])["title"] == goal_row["title"]


# -- slim overview ---------------------------------------------------------------------


def test_overview_is_slim_and_small_with_forty_heavy_attempts(setup, monkeypatch):
    store, engine, project, _ = setup
    task_ids = []
    for n in range(40):
        task_ids.append(goal(store, engine, project, path=f"src/t{n}.txt", instructions="Z" * 3000))
    with store.transaction() as db:
        for task_id in task_ids:
            t = store.get("tasks", task_id, db)
            store.update_task(
                db,
                t,
                "completed",
                "R" * 2000,
                specification={"outcome": "S" * 8000},
                repair_evidence={"reason": "E" * 8000},
                instructions_pending=["one", "two"],
            )
        gdb = store.rows("goals", db)
        for g in gdb:
            g["specification"] = {"outcome": "S" * 8000}
            g["request"] = "Q" * 2000
            db.execute(
                "UPDATE goals SET data=? WHERE id=?",
                (encode({k: v for k, v in g.items() if k not in {"id", "project_id"}}), g["id"]),
            )
    for n, task_id in enumerate(task_ids):
        ident = "attempt_" + task_id.split("_")[1]
        insert_attempt(store, task_id, result=big_result(ident, 0, heavy=n == 0), ended=time.time())
    assert all(len(json.dumps(a["result"])) > 20000 for a in store.rows("attempts"))

    opened = []
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_path_open(self, *a, **k):
        opened.append(str(self))
        return real_path_open(self, *a, **k)

    client, headers = paired_client(store)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "open", spy_path_open)
    r = client.get("/api/v1/overview")
    monkeypatch.undo()
    assert r.status_code == 200
    assert len(r.content) < 200_000, len(r.content)
    assert not [p for p in opened if "/attempts/" in p], opened  # contract 7: no artifact reads
    data = r.json()
    task = next(t for t in data["tasks"] if t["id"] == task_ids[0])
    assert "specification" not in task and "repair_evidence" not in task
    assert "instructions_pending" not in task and task["instructions_pending_count"] == 2
    assert len(task["instructions"]) == 400 and task["instructions_truncated"] is True
    assert len(task["reason"]) == 300 and task["dependencies"] == []
    g = data["goals"][0]
    assert "specification" not in g and len(g["request"]) == 300 and g["request_truncated"]
    # Round 3: a build with an agreed specification is named after its outcome.
    assert g["display_title"] == "S" * 71 + "…"
    a = next(x for x in data["attempts"] if x["task_id"] == task_ids[0])  # the heavy one
    for gone in ("workspace", "artifact_dir", "identity"):
        assert gone not in a
    assert set(a["routing"]) == {"provider", "model", "effort", "action", "reason"}
    assert len(a["routing"]["reason"]) == 200
    result = a["result"]
    assert "plan" not in result and "session_ids" not in result
    assert result["checks"] == [{"name": "frozen", "status": "PASS", "seconds": 0.1}]
    assert result["review"]["approved"] is True and len(result["review"]["summary"]) == 600
    assert len(result["review"]["findings"]) == 10 and len(result["review"]["findings"][0]) == 300
    assert result["review"]["findings_total"] == 20
    assert len(result["summary"]) <= 600
    assert result["artifacts"] == ["child.json", "diff.patch", "transcript.jsonl"]
    assert result["usage"] == [{"call": 1, "input": 100, "output": 10}]
    assert result["candidate"] == "a" * 40 and result["changed"] == ["src/a.txt"]
    for key in ("status", "reason", "seconds", "cost", "invocation", "attempt_id", "token"):
        assert key in result
    assert data["projects"][0]["archived"] is False
    # The detail endpoints still carry the full records.
    full = client.get(f"/api/v1/tasks/{task_ids[0]}").json()
    assert full["specification"]["outcome"] == "S" * 8000 and len(full["instructions"]) == 3000
    detail = client.get(f"/api/v1/goals/{full['goal_id']}").json()
    assert detail["goal"]["specification"]["outcome"] == "S" * 8000
    assert detail["attempts"][0]["result"]["plan"] == {"milestones": ["z" * 5000]}
    assert "identity" not in detail["attempts"][0]


def test_planner_json_summary_is_unpacked_for_the_list():
    assert views.plain_summary('{"outcome": "O", "summary": "I will do X."}') == "I will do X."
    assert views.plain_summary('{"outcome": "Only outcome"}') == "Only outcome"
    assert views.plain_summary("{not json") == "{not json"
    assert views.plain_summary("plain text") == "plain text"
    assert views.plain_summary(None) == ""


# -- real state snapshot ----------------------------------------------------------------


def real_state_home(tmp_path):
    home = tmp_path / "real-state"
    home.mkdir(mode=0o700)
    with (
        gzip.open(FIXTURES / "real-state.sqlite3.gz", "rb") as src,
        (home / "control.sqlite3").open("wb") as dst,
    ):
        shutil.copyfileobj(src, dst)
    return home


def test_real_state_overview_under_200kb_and_proof_projects_archived_once(tmp_path):
    home = real_state_home(tmp_path)
    store = Store(home)  # opening the store runs the one-time migration
    projects = {p["name"]: p for p in store.rows("projects")}
    proof = [n for n in projects if n.startswith("Orchestrator live proof")]
    assert len(proof) == 4 and all(projects[n]["archived"] for n in proof)
    assert not projects["Arena"].get("archived")
    assert not projects["Arena Smoke 20261028"].get("archived")
    marker = store.setting(ARCHIVE_PROOF_MIGRATION)
    assert sorted(marker["archived"]) == sorted(projects[n]["id"] for n in proof)
    # Idempotent: an owner's unarchive survives every later store open.
    client, headers = paired_client(store)
    victim = projects[proof[0]]
    r = client.post(
        f"/api/v1/projects/{victim['id']}/unarchive",
        json={"revision": victim["rev"]},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["archived"] is False
    assert archive_proof_projects(Store(home)) == []
    assert Store(home).get("projects", victim["id"])["archived"] is False
    assert len([e for e in store.rows("projects") if e.get("archived")]) == 3
    # The slim overview of the real state is phone-sized.
    r = client.get("/api/v1/overview")
    assert r.status_code == 200
    assert len(r.content) < 200_000, len(r.content)
    data = r.json()
    assert len(data["attempts"]) == 76 and len(data["tasks"]) == 28 and len(data["goals"]) == 13
    assert sum(1 for p in data["projects"] if p["archived"]) == 3
    assert all(g["display_title"] and len(g["display_title"]) <= 72 for g in data["goals"])
    assert all("plan" not in (a.get("result") or {}) for a in data["attempts"])
    # Goal detail and its event history work on real records.
    g = data["goals"][-1]["id"]
    detail = client.get(f"/api/v1/goals/{g}").json()
    assert detail["goal"]["id"] == g and detail["tasks"] and detail["attempts"]
    kinds = {e["kind"] for e in detail["events"]}
    assert "goal.created" in kinds and "task.changed" in kinds
    assert [e["id"] for e in detail["events"]] == sorted(e["id"] for e in detail["events"])
    # A finished real attempt answers the live endpoint without its artifact directory.
    a = detail["attempts"][0]["id"]
    # The fixture records absolute paths from the owner's Mac, where the directory exists.
    with store.transaction() as db:
        db.execute(
            "UPDATE attempts SET data=json_set(data,'$.artifact_dir',?) WHERE id=?",
            (str(tmp_path / "gone"), a),
        )
    live = client.get(f"/api/v1/attempts/{a}/live").json()
    assert live["done"] is True and live["bytes"] == 0 and live["lines"] == []
    assert live["provider"] in {"codex", "claude"} and live["elapsed"] is not None


def test_proof_project_rule(tmp_path):
    home = tmp_path / "home"
    assert is_proof_project({"repo": "/Users/x/p/.arena-control/proof-source-final"}, home)
    assert is_proof_project({"repo": str(home / "anything" / "repo")}, home)
    assert not is_proof_project({"repo": str(tmp_path / "project")}, home)
    assert not is_proof_project({"repo": ""}, home)


# -- detail endpoints ---------------------------------------------------------------------


def test_goal_detail_and_incremental_events(setup):
    store, engine, project, _ = setup
    first = goal(store, engine, project, path="src/one.txt")
    second = goal(store, engine, project, path="src/two.txt", deps=[first])
    other = goal(store, engine, project, path="src/other.txt")
    client, headers = paired_client(store)
    goal_id = store.get("tasks", first)["goal_id"]
    with store.transaction() as db:
        store.update_task(db, store.get("tasks", other, db), "blocked", "unrelated noise")
        store.update_task(db, store.get("tasks", first, db), "running", "worker started")
    detail = client.get(f"/api/v1/goals/{goal_id}").json()
    assert set(detail) == {"goal", "tasks", "attempts", "events"}
    assert detail["goal"]["display_title"] == "src/one.txt"
    assert [t["id"] for t in detail["tasks"]] == [first]
    assert detail["tasks"][0]["dependencies"] == []
    assert {e["kind"] for e in detail["events"]} == {"goal.created", "task.changed"}
    assert all(
        e["data"].get("goal_id") == goal_id or e["data"].get("task_id") == first
        for e in detail["events"]
    )
    assert "unrelated noise" not in json.dumps(detail["events"])
    cursor = detail["events"][-1]["id"]
    feed = client.get(f"/api/v1/goals/{goal_id}/events?after={cursor}").json()
    assert feed == {"events": [], "cursor": cursor}
    with store.transaction() as db:
        store.update_task(db, store.get("tasks", first, db), "completed", "done")
    feed = client.get(f"/api/v1/goals/{goal_id}/events?after={cursor}").json()
    assert len(feed["events"]) == 1 and feed["events"][0]["data"]["reason"] == "done"
    assert feed["cursor"] == feed["events"][0]["id"] > cursor
    assert client.get("/api/v1/goals/goal_missing").status_code == 404
    assert client.get(f"/api/v1/tasks/{second}").json()["dependencies"] == [first]
    assert client.get("/api/v1/tasks/task_missing").status_code == 404


# -- live tail ---------------------------------------------------------------------------


def test_live_tail_parses_codex_and_claude_transcripts(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    cases = {
        "codex": ("transcript-codex.jsonl", CODEX_WORKSPACE, "slot0"),
        "claude": ("transcript-claude.jsonl", CLAUDE_WORKSPACE, "slot1"),
    }
    with store.transaction() as db:
        a = store.get("accounts", "slot1", db)
        a["provider"] = "claude"
        db.execute(
            "UPDATE accounts SET data=? WHERE id=?",
            (encode({k: v for k, v in a.items() if k not in {"id", "rev"}}), "slot1"),
        )
    for provider, (name, workspace, account) in cases.items():
        task_id = goal(store, engine, project, path=f"src/{provider}.txt")
        ident = insert_attempt(
            store,
            task_id,
            account_id=account,
            state="passed",
            workspace=workspace,
            ended=time.time(),
        )
        artifact = store.home / "attempts" / ident
        artifact.mkdir(parents=True)
        shutil.copy(FIXTURES / name, artifact / "transcript.jsonl")
        r = client.get(f"/api/v1/attempts/{ident}/live")
        assert r.status_code == 200, r.text
        live = r.json()
        assert set(live) >= {
            "state",
            "phase",
            "model",
            "provider",
            "started",
            "elapsed",
            "bytes",
            "done",
            "lines",
        }
        assert live["provider"] == provider and live["done"] is True and live["state"] == "passed"
        assert live["bytes"] == (FIXTURES / name).stat().st_size
        assert 0 < live["elapsed"] < 120
        kinds = [line["kind"] for line in live["lines"]]
        assert set(kinds) <= set(livetail.KINDS) and len(kinds) <= livetail.LINES
        assert all(len(line["text"]) <= 300 and line["t"] is None for line in live["lines"])
        texts = "\n".join(line["text"] for line in live["lines"])
        assert workspace not in texts  # paths are shown relative to the workspace
        if provider == "codex":
            assert kinds[:3] == ["system", "system", "assistant"]
            assert "command" in kinds and "file" in kinds and "error" in kinds
            assert "update web/src/screens/Devices.tsx" in texts
            assert "$ sed -n '1,280p' web/src/screens/Devices.tsx" in texts
            assert "exit 1 ·" in texts and kinds[-1] == "system"
        else:
            assert kinds[0] == "system" and "claude-fable-5-1" in live["lines"][0]["text"]
            assert "reading src/pomodoro/cli.py" in texts
            assert "editing src/pomodoro/cli.py" in texts
            assert "writing tests/test_quiet.py" in texts
            assert "test" in kinds and "$ python -m pytest -q tests/test_quiet.py" in texts
            assert "error" in kinds and "Could not resolve host" in texts
            assert "sk-live-" not in texts and "[REDACTED]" in texts
            assert "Bearer [REDACTED]" in texts
            assert "Grep def main in src" in texts and "TodoWrite" in texts
            assert live["lines"][-1]["text"].startswith("Finished · success")
            assert "thinking" not in texts.lower() and "SECRET-PLAN" not in texts


def test_live_tail_tolerates_missing_partial_and_corrupt_transcripts(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project, path="src/live.txt")
    ident = insert_attempt(store, task_id, state="running")
    # No artifact directory yet: a running attempt that has not produced output.
    live = client.get(f"/api/v1/attempts/{ident}/live").json()
    assert live == {
        **live,
        "done": False,
        "bytes": 0,
        "updated": None,
        "lines": [],
        "state": "running",
    }
    assert live["elapsed"] >= 29
    # A transcript larger than the window whose first line is cut and whose last line is
    # still being written, plus junk in the middle.
    artifact = store.home / "attempts" / ident
    artifact.mkdir(parents=True)
    filler = [
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": f"F{n:03d}" * 250}}
        )
        for n in range(80)
    ]
    lines = list(filler)  # ~80 KB, more than the 64 KB window
    lines.append("this is not json")
    lines.append('{"type": "item.started", "item": {"type": "command_execution", "command": 42}}')
    lines.append('{"type": "item.completed", "item": {"type": "file_change", "changes": "nope"}}')
    lines.append('{"type": "turn.completed", "usage": "weird"}')
    lines.append('{"type": "item.completed", "item": {"type": "agent_message", "text": "tail')
    (artifact / "transcript.jsonl").write_text("\n".join(lines))
    live = client.get(f"/api/v1/attempts/{ident}/live").json()
    assert live["done"] is False and live["bytes"] > 64 * 1024 and live["updated"]
    kinds = [line["kind"] for line in live["lines"]]
    assert len(kinds) == livetail.LINES and kinds[-1] == "system"
    assert live["lines"][-1]["text"] == "Turn completed"
    assert live["lines"][-2]["text"] == "changed files" and live["lines"][-3]["text"] == "$ 42"
    assert "tail" not in json.dumps(live["lines"][-4:])  # the cut trailing line is ignored
    # Only the last 64 KB were read: the earliest surviving filler line is well past the start.
    assert len(livetail.read_window(artifact / "transcript.jsonl")[0]) <= livetail.WINDOW
    earliest = min(int(line["text"][1:4]) for line in live["lines"] if line["text"].startswith("F"))
    assert earliest >= 15, earliest
    # Direct reader: an unreadable path and an empty file never raise.
    assert livetail.read_window(Path("/nonexistent/transcript.jsonl")) == ("", 0, None)
    (artifact / "transcript.jsonl").write_bytes(b"")
    assert client.get(f"/api/v1/attempts/{ident}/live").json()["lines"] == []
    (artifact / "transcript.jsonl").write_bytes(b"\xff\xfe{garbage\n" + b"\x00" * 10)
    assert client.get(f"/api/v1/attempts/{ident}/live").json()["lines"] == []
    assert client.get("/api/v1/attempts/attempt_missing/live").status_code == 404


def test_live_tail_elapsed_stops_for_interrupted_attempts_and_lines_split_on_newlines(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    # An attempt interrupted by recovery records no `ended`: its clock must not keep running.
    task_id = goal(store, engine, project, path="src/stopped.txt")
    ident = insert_attempt(store, task_id, state="interrupted", created=time.time() - 3600)
    live = client.get(f"/api/v1/attempts/{ident}/live").json()
    assert live["done"] is True and live["elapsed"] is None  # nothing recorded, nothing claimed
    artifact = store.home / "attempts" / ident
    artifact.mkdir(parents=True)
    (artifact / "transcript.jsonl").write_text('{"type": "turn.started"}\n')
    live = client.get(f"/api/v1/attempts/{ident}/live").json()
    assert 3595 <= live["elapsed"] <= 3605  # the moment the transcript stopped growing
    with store.transaction() as db:
        db.execute(
            "UPDATE attempts SET data=json_set(data,'$.result',json(?)) WHERE id=?",
            (json.dumps({"status": "failed", "seconds": 42.5}), ident),
        )
    assert client.get(f"/api/v1/attempts/{ident}/live").json()["elapsed"] == 42.5
    # A JSON line whose text carries a Unicode line separator is still one line.
    lines = livetail.parse_lines(
        '{"type": "item.completed", "item": {"type": "agent_message", '
        '"text": "first\u2028second\u0085third"}}\n'
    )
    assert [line["kind"] for line in lines] == ["assistant"]
    assert "first" in lines[0]["text"] and "third" in lines[0]["text"]


# -- archive ------------------------------------------------------------------------------


def test_archive_blocks_requests_catalog_and_dispatch_then_unarchive_restores(setup):
    store, engine, project, source = setup
    client, headers = paired_client(store)
    queued = goal(store, engine, project, path="src/queued.txt")
    p = store.get("projects", project["id"])
    # Round 3: a project with a build in progress is never archived; a stopped one can be.
    r = client.post(
        f"/api/v1/projects/{p['id']}/archive",
        json={"revision": p["rev"]},
        headers={**headers, "Idempotency-Key": "arch-0"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "This project has a build in progress"
    with store.transaction() as db:
        store.update_task(db, store.get("tasks", queued, db), "blocked", "Stopped for the test")
    r = client.post(
        f"/api/v1/projects/{p['id']}/archive",
        json={"revision": p["rev"]},
        headers={**headers, "Idempotency-Key": "arch-1"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True, "archived": True}
    r = client.post(
        f"/api/v1/projects/{p['id']}/archive",
        json={"revision": p["rev"]},
        headers={**headers, "Idempotency-Key": "arch-1"},
    )
    assert r.status_code == 200  # idempotent replay
    assert (
        client.post(
            f"/api/v1/projects/{p['id']}/archive",
            json={"revision": p["rev"]},
            headers={**headers, "Idempotency-Key": "arch-2"},
        ).status_code
        == 409
    )  # stale revision
    archived = store.get("projects", p["id"])
    assert archived["archived"] is True and archived["archived_at"]
    overview = client.get("/api/v1/overview").json()
    assert overview["projects"][0]["archived"] is True
    assert [t["id"] for t in overview["tasks"]] == [queued]  # history stays visible
    # New requests are refused on every path.
    r = client.post(
        f"/api/v1/projects/{p['id']}/requests",
        json={"instructions": "Add a thing", "revision": archived["rev"]},
        headers={**headers, "Idempotency-Key": "req-1"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "Project is archived"
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Add a thing", "project_id": p["id"]},
        headers={**headers, "Idempotency-Key": "req-2"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "Project is archived"
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Fixture: add a thing to the fixture project"},
        headers={**headers, "Idempotency-Key": "req-3"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "Which project?"
    assert r.json()["candidates"] == []
    r = client.post(
        f"/api/v1/projects/{p['id']}/goals",
        json={
            "title": "explicit",
            "instructions": "x",
            "scope": ["src"],
            "revision": archived["rev"],
        },
        headers={**headers, "Idempotency-Key": "req-4"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "Project is archived"
    # The director no longer sees it, and the scheduler will not dispatch its queued task
    # (queued again after archiving, as a retry of the stopped task would be).
    with store.transaction() as db:
        store.update_task(db, store.get("tasks", queued, db), "ready", "Explicit retry queued")
    assert Catalog(store, projects_dir=source.parent / "none").enrolled() == []
    assert engine.claim() is None
    assert store.get("tasks", queued)["state"] == "ready"
    # Unarchive puts it back everywhere.
    archived = store.get("projects", p["id"])
    r = client.post(
        f"/api/v1/projects/{p['id']}/unarchive",
        json={"revision": archived["rev"]},
        headers={**headers, "Idempotency-Key": "unarch-1"},
    )
    assert r.status_code == 200 and r.json()["archived"] is False
    assert [e["name"] for e in Catalog(store, projects_dir=source.parent / "none").enrolled()] == [
        "Fixture"
    ]
    assert client.get("/api/v1/overview").json()["projects"][0]["archived"] is False
    claimed = engine.claim()
    assert claimed and claimed["task_id"] == queued
    r = client.post(
        f"/api/v1/projects/{p['id']}/requests",
        json={"instructions": "Add a thing", "revision": archived["rev"] + 1},
        headers={**headers, "Idempotency-Key": "req-5"},
    )
    assert r.status_code == 200, r.text


def test_migration_archives_proof_snapshots_inside_the_home_once(setup):
    store, engine, project, source = setup
    # Two proof snapshots: one under this home, one under a home that has since moved.
    with store.transaction() as db:
        for n, repo in enumerate(
            [str(store.home / "proof-source-final"), "/elsewhere/.arena-control/proof-source"]
        ):
            db.execute(
                "INSERT INTO projects(id,data) VALUES(?,?)",
                (
                    f"project_proof{n}",
                    encode({**{k: v for k, v in project.items() if k != "id"}, "repo": repo}),
                ),
            )
        db.execute("DELETE FROM meta WHERE key=?", (ARCHIVE_PROOF_MIGRATION,))
    assert sorted(archive_proof_projects(store)) == ["project_proof0", "project_proof1"]
    assert store.get("projects", project["id"]).get("archived") is None
    assert archive_proof_projects(store) == []  # a no-op from now on
    assert archive_proof_projects(Store(store.home)) == []
    with store.transaction() as db:
        db.execute(
            "INSERT INTO projects(id,data) VALUES(?,?)",
            (
                "project_proof_late",
                encode(
                    {
                        **{k: v for k, v in project.items() if k != "id"},
                        "repo": str(store.home / "proof-source-late"),
                    }
                ),
            ),
        )
    assert Store(store.home).get("projects", "project_proof_late").get("archived") is None


# -- real-state fixture safety ------------------------------------------------------------


def test_real_state_fixture_cannot_reach_live_workers_or_mutate(tmp_path, monkeypatch):
    from tests.control import real_state_server

    # A "live" database whose attempt carries a worker identity, and a scratch home.
    live_home = tmp_path / "live" / ".arena-control"
    live_store = Store(live_home)
    with live_store.transaction() as db:
        db.execute(
            "INSERT INTO projects(id,data) VALUES(?,?)",
            ("project_live", encode({"name": "Live", "repo": "/tmp/x", "scope": ["src"]})),
        )
        db.execute(
            "INSERT INTO accounts(id,data) VALUES(?,?)",
            ("slot_live", encode({"name": "Codex", "provider": "codex"})),
        )
        db.execute(
            "INSERT INTO tasks(id,project_id,goal_id,state,rev,token,data) VALUES(?,?,?,?,?,?,?)",
            ("task_live", "project_live", None, "running", 0, 1, encode({"title": "t"})),
        )
        db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?)",
            (
                "attempt_live",
                "task_live",
                "slot_live",
                1,
                "running",
                encode({"identity": {"pid": 1, "created": 1.0, "boot": 1.0}, "created": 1.0}),
            ),
        )
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("ARENA_UI_STATE", str(scratch))
    monkeypatch.setenv("ARENA_REAL_DB", str(live_home / "control.sqlite3"))
    monkeypatch.setenv("ARENA_UI_PORT", "8794")
    app = real_state_server.create_app()
    # The copy carries no worker identity, so nothing in it can resolve to a live process.
    assert Store(scratch).get("attempts", "attempt_live")["identity"] is None
    assert live_store.get("attempts", "attempt_live")["identity"]["pid"] == 1  # untouched
    client = TestClient(app, base_url="http://127.0.0.1:8794")
    origin = {"Origin": "http://127.0.0.1:8794"}
    r = client.post("/api/v1/pair", json={"code": "real-state-one-use-code"}, headers=origin)
    assert r.status_code == 200
    headers = {**origin, "X-CSRF-Token": r.json()["csrf"], "Idempotency-Key": "fixture-1"}
    assert client.get("/api/v1/overview").status_code == 200
    for path in ("/api/v1/tasks/task_live/cancel", "/api/v1/control/stop-all"):
        r = client.post(path, json={"revision": 0}, headers=headers)
        assert r.status_code == 403 and "copy" in r.json()["detail"], (path, r.text)
    assert live_store.get("tasks", "task_live")["state"] == "running"
    # Conversations stay writable (a read-only director turn on the copy).
    r = client.post("/api/v1/conversations", json={"revision": 0, "text": ""}, headers=headers)
    assert r.status_code == 200
    # Scratch directories that could touch the live state are refused.
    for bad in (live_home, live_home / "sub", real_state_server.ROOT):
        with pytest.raises(SystemExit):
            real_state_server.check_home(bad, live_home / "control.sqlite3")
