"""Conversational director with a fake CLI and fake gh: no provider is invoked."""

import json
import subprocess
import time
from types import SimpleNamespace

from agent_arena.control import conversation
from agent_arena.control.store import encode
from tests.control import test_control as support
from tests.control.test_control import paired_client


class FakeRunner:
    """Stands in for ProcessRunner: returns a scripted director answer per prompt."""

    def __init__(self, answers):
        self.answers, self.prompts, self.cwds = list(answers), [], []

    def run(self, argv, *, cwd, env, stdin_text, timeout_seconds, max_output_bytes, **kw):
        self.prompts.append(stdin_text)
        self.cwds.append(cwd)
        answer = self.answers.pop(0)
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(answer)},
            }
        )
        done = json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
        return SimpleNamespace(
            stdout=line + "\n" + done + "\n", stderr="", exit_code=0, timed_out=False
        )


def fake_gh_run(argv, **kw):
    if argv[0] != "gh":
        return subprocess.run(argv, **kw)
    if argv[1:3] == ["repo", "list"]:
        repos = [
            {
                "name": "leo-os",
                "nameWithOwner": "octo/leo-os",
                "description": "Personal OS",
                "updatedAt": "2026-10-01T00:00:00Z",
                "isPrivate": True,
                "primaryLanguage": {"name": "Python"},
                "url": "https://github.com/octo/leo-os",
            },
        ]
        return SimpleNamespace(returncode=0, stdout=json.dumps(repos), stderr="")
    if argv[1:3] == ["repo", "view"]:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "description": "Personal OS",
                    "updatedAt": "2026-10-01T00:00:00Z",
                    "defaultBranchRef": {"name": "main"},
                    "url": "https://github.com/octo/leo-os",
                }
            ),
            stderr="",
        )
    if argv[1] == "api":
        return SimpleNamespace(
            returncode=0, stdout="# LeoOS\nA personal operating system.", stderr=""
        )
    raise AssertionError(argv)


def wait_idle(store, conv_id, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if store.get("conversations", conv_id)["status"] == "idle":
            return
        time.sleep(0.02)
    raise AssertionError("director never finished")


def ready_accounts(store):
    with store.transaction() as db:
        for a in store.rows("accounts", db):
            a.update(health="ready", enabled=True)
            db.execute(
                "UPDATE accounts SET data=? WHERE id=?",
                (encode({k: v for k, v in a.items() if k not in {"id", "rev"}}), a["id"]),
            )


def test_chat_answers_selects_projects_and_only_builds_on_explicit_words(tmp_path, monkeypatch):
    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    runner = FakeRunner(
        [
            {
                "reply": "No project is selected yet. Enrolled: Fixture.",
                "project_hint": None,
                "action": "chat",
                "spec": None,
            },
            {
                "reply": "Fixture is a small sample repository with src/initial.txt.",
                "project_hint": "Fixture",
                "action": "chat",
                "spec": None,
            },
            {
                "reply": "Agreed: add a greeting file. Shall I build it?",
                "project_hint": "Fixture",
                "action": "build",
                "spec": {
                    "outcome": "Add src/hello.txt containing a greeting",
                    "acceptance": ["src/hello.txt exists"],
                    "constraints": ["Only src"],
                    "notes": "",
                },
            },
            {
                "reply": "Starting the build now.",
                "project_hint": "Fixture",
                "action": "build",
                "spec": {
                    "outcome": "Add src/hello.txt containing a greeting",
                    "acceptance": ["src/hello.txt exists"],
                    "constraints": ["Only src"],
                    "notes": "",
                },
            },
        ]
    )
    director = conversation.Director(
        store, engine, run=fake_gh_run, runner=runner, projects_dir=tmp_path / "none"
    )
    conv = conversation.new_conversation(store)
    director.submit(conv["id"], "What repo are we in?")
    wait_idle(store, conv["id"])
    history = conversation.messages(store, conv["id"])
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert "No project" in history[-1]["text"] and history[-1]["model"] == "gpt-6.1-sol"
    assert "enrolled_projects" in runner.prompts[0] and "Fixture" in runner.prompts[0]
    # Naming an enrolled project selects it and later turns inspect its repository read-only.
    director.submit(conv["id"], "What's going on with Fixture?")
    wait_idle(store, conv["id"])
    assert store.get("conversations", conv["id"])["project_id"] == project["id"]
    assert conversation.messages(store, conv["id"])[-1]["project_selected"] == "Fixture"
    # The director wanting to build is not enough: the owner has not said so.
    director.submit(conv["id"], "I want a greeting file in src.")
    wait_idle(store, conv["id"])
    last = conversation.messages(store, conv["id"])[-1]
    assert last.get("offer_build") is True and not last.get("build_started")
    assert runner.cwds[-1] == source.resolve() or str(runner.cwds[-1]) == project["repo"]
    assert not [t for t in store.rows("tasks")]
    assert store.get("conversations", conv["id"])["spec"]["outcome"].startswith("Add src/hello.txt")
    # Explicit words launch the existing request path with the compiled specification.
    director.submit(conv["id"], "Right, build it.")
    wait_idle(store, conv["id"])
    last = conversation.messages(store, conv["id"])[-1]
    assert last["build_started"] is True and last["project"] == "Fixture"
    tasks = store.rows("tasks")
    assert len(tasks) == 1 and tasks[0]["director"] is True and tasks[0]["phase"] == "plan"
    assert (
        "ACCEPTANCE CRITERIA" in tasks[0]["instructions"]
        and "src/hello.txt" in tasks[0]["instructions"]
    )
    assert store.get("conversations", conv["id"])["builds"][0]["goal_id"] == tasks[0]["goal_id"]


def test_catalog_lists_github_read_only_and_api_round_trip(tmp_path, monkeypatch):
    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    catalog = conversation.Catalog(store, run=fake_gh_run, projects_dir=tmp_path)
    repos = catalog.github_repos()
    assert repos[0]["full_name"] == "octo/leo-os" and repos[0]["private"] is True
    assert catalog.github_summary("octo/leo-os")["readme"].startswith("# LeoOS")
    assert any(r["name"] == "source" for r in catalog.local_repos())
    client, headers = paired_client(store)
    submitted = []
    monkeypatch.setattr(
        conversation.Director, "submit", lambda self, ident, text: submitted.append((ident, text))
    )
    created = client.post(
        "/api/v1/conversations",
        json={"revision": 0, "text": ""},
        headers={**headers, "Idempotency-Key": "c1"},
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    r = client.post(
        f"/api/v1/conversations/{cid}/messages",
        json={"text": "Hello Arena", "revision": 0},
        headers={**headers, "Idempotency-Key": "m1"},
    )
    assert r.status_code == 200 and submitted == [(cid, "Hello Arena")]
    r = client.post(
        f"/api/v1/conversations/{cid}/messages",
        json={"text": "Hello Arena", "revision": 0},
        headers={**headers, "Idempotency-Key": "m1"},
    )
    assert r.status_code == 200 and len(submitted) == 1  # Transport retry does not resend.
    r = client.post(
        f"/api/v1/conversations/{cid}/project",
        json={"project_id": project["id"], "revision": 0},
        headers={**headers, "Idempotency-Key": "p1"},
    )
    assert r.status_code == 200
    detail = client.get(f"/api/v1/conversations/{cid}").json()
    assert detail["project"]["name"] == "Fixture" and detail["messages"] == []
    assert client.get("/api/v1/overview").json()["conversations"][0]["id"] == cid
