"""New-project provisioning with a fake GitHub CLI; git operations are real in tmp_path."""

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_arena.control import provisioning
from agent_arena.control.engine import Engine, git
from agent_arena.control.store import Store
from tests.control.test_control import paired_client


class FakeGh:
    """Answers `gh` calls; `git` calls pass through to the real binary."""

    def __init__(self, login="octo", existing=()):
        self.login, self.existing, self.created = login, set(existing), []

    def __call__(self, argv, **kw):
        if argv[0] != "gh":
            return subprocess.run(argv, **kw)
        if argv[1:4] == ["api", "user", "-q"]:
            return SimpleNamespace(returncode=0, stdout=self.login + "\n", stderr="")
        if argv[1:3] == ["repo", "view"]:
            name = argv[3]
            ok = name in self.existing or name in self.created
            return SimpleNamespace(
                returncode=0 if ok else 1,
                stdout="",
                stderr="" if ok else "GraphQL: Could not resolve to a Repository",
            )
        if argv[1:3] == ["repo", "create"]:
            name = argv[3]
            self.created.append(name)
            # Simulate what gh does: add the origin remote and push (to a bare repo in tmp).
            source = Path(argv[argv.index("--source") + 1])
            bare = source.parent / (source.name + ".git")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
            git(source, "remote", "add", "origin", str(bare))
            git(source, "push", "-q", "origin", "main")
            return SimpleNamespace(returncode=0, stdout=f"https://github.com/{name}\n", stderr="")
        raise AssertionError(argv)


def test_names_are_sensible_and_collisions_get_a_suffix(tmp_path):
    name, slug = provisioning.derive_name(
        "Build me a simple habit tracker app that reminds me daily"
    )
    assert (name, slug) == ("Habit Tracker Reminds", "habit-tracker-reminds")
    store = Store(tmp_path / "home")
    projects = tmp_path / "Projects"
    (projects / "habit-tracker-reminds").mkdir(parents=True)
    (projects / "habit-tracker-reminds" / "x").write_text("taken")
    gh = FakeGh(existing={"octo/habit-tracker-reminds-2"})
    proposal = provisioning.propose(
        store,
        "Build me a simple habit tracker app that reminds me daily.",
        projects_dir=projects,
        run=gh,
    )
    assert proposal["slug"] == "habit-tracker-reminds-3"
    assert proposal["github"] == {
        "owner": "octo",
        "full_name": "octo/habit-tracker-reminds-3",
        "url": "https://github.com/octo/habit-tracker-reminds-3",
        "private": True,
    }
    assert len(proposal["notes"]) == 2
    assert provisioning.load_proposal(store, proposal["id"])["slug"] == proposal["slug"]
    with pytest.raises(KeyError):
        provisioning.load_proposal(store, "proposal_000000000000")


def test_provisioning_creates_connected_repos_enrols_and_requests(tmp_path, monkeypatch):
    store = Store(tmp_path / "home")
    engine = Engine(store)
    projects = tmp_path / "Projects"
    projects.mkdir()
    gh = FakeGh()
    proposal = provisioning.propose(
        store,
        "Build a tiny pomodoro timer web app with a start and stop button.",
        projects_dir=projects,
        run=gh,
    )
    pv = provisioning.Provisioner(store, engine, run=gh)
    monkeypatch.setattr(
        pv,
        "_create_remote",
        lambda local, full_name, record: gh(
            ["gh", "repo", "create", full_name, "--source", str(local)]
        ).stdout.strip(),
    )
    record = pv.start(proposal, approved_by="test")
    for _ in range(200):
        current = provisioning.provisions(store)[0]
        if current["state"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert current["state"] == "done", current
    assert [s["name"] for s in current["steps"]] == [
        "local repository",
        "github repository",
        "verify connection",
        "enrol in arena",
        "first request",
    ]
    local = Path(current["local_path"])
    assert (local / "README.md").read_text().startswith("# Tiny Pomodoro Timer")
    assert git(local, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    project = store.get("projects", current["project_id"])
    assert project["push_remote"] is True and project["github_url"].endswith(proposal["slug"])
    assert project["integration_policy"] == "auto_local_main"
    task = store.get("tasks", current["task_id"])
    assert task["director"] is True and task["phase"] == "plan" and task["state"] == "ready"
    assert record["id"] == current["id"]
    # Pushing main after integration uses the project's origin.
    assert provisioning.push_main(local, run=gh) is not None
    # A second attempt at the same name never overwrites.
    again = provisioning.Provisioner(store, engine, run=gh)
    with pytest.raises(ValueError, match="not empty"):
        again._create_local(local, current)


def test_phone_endpoints_require_a_session_and_approval(tmp_path, monkeypatch):
    from tests.control import test_control as support

    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    gh = FakeGh()
    original = provisioning.propose
    monkeypatch.setattr(
        provisioning,
        "propose",
        lambda s, d, **kw: original(s, d, projects_dir=tmp_path / "P", run=gh),
    )
    client, headers = paired_client(store)
    r = client.post(
        "/api/v1/projects/proposals",
        json={"description": "Build a recipe box app for my family"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert proposal["github"]["private"] is True and "local_path" in proposal
    anon = client.post("/api/v1/projects/proposals", json={"description": "x" * 20})
    assert anon.status_code in (401, 403)
    started = []
    monkeypatch.setattr(
        provisioning.Provisioner,
        "start",
        lambda self, p, approved_by="phone": (
            started.append(p["id"]) or {"id": "provision_test", "state": "queued", "steps": []}
        ),
    )
    r = client.post(
        "/api/v1/projects/provision",
        json={"proposal_id": proposal["id"], "revision": 0},
        headers={**headers, "Idempotency-Key": "approve-1"},
    )
    assert r.status_code == 202 and started == [proposal["id"]]
    r = client.post(
        "/api/v1/projects/provision",
        json={"proposal_id": "proposal_000000000000", "revision": 0},
        headers={**headers, "Idempotency-Key": "approve-2"},
    )
    assert r.status_code == 404
    assert json.loads(client.get("/api/v1/overview").text)["provisions"] == []
