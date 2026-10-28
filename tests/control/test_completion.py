"""Controller fault tests; provider results below are explicit test fixtures."""

import hashlib
import json
import time
from pathlib import Path

import pytest

from agent_arena.control.engine import ACTIVE, atomic_json, git
from agent_arena.control.publication import publish
from agent_arena.control.store import encode
from agent_arena.control.structured_build import apply_scoped_response
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


def candidate(setup):
    store, engine, project, source = setup
    git(source, "branch", "-M", "main")
    task_id = goal(store, engine, project)
    stage = Path(project["staging"])
    (stage / "src/a.txt").write_text("verified\n")
    git(stage, "add", "src/a.txt")
    git(stage, "commit", "-qm", "Unit test candidate")
    sha = git(stage, "rev-parse", "HEAD")
    artifact = store.home / "attempts" / "unit-build"
    artifact.mkdir(parents=True)
    log = artifact / "integration-0.txt"
    log.write_text("Explicit unit fixture: verification result supplied by test\n")
    checks = [
        {
            "name": "fixture",
            "status": "PASS",
            "artifact": log.name,
            "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        }
    ]
    atomic_json(artifact / "integration.json", {"candidate": sha, "checks": checks})
    with store.transaction() as db:
        project["head"] = sha
        db.execute(
            "UPDATE projects SET data=? WHERE id=?",
            (encode({k: v for k, v in project.items() if k not in {"id", "rev"}}), project["id"]),
        )
        task = store.get("tasks", task_id, db)
        store.update_task(
            db, task, "ready_for_approval", "Unit fixture", build_attempt="unit-build"
        )
        db.execute(
            "INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",
            (
                "unit-approval",
                task_id,
                "pending",
                encode(
                    {
                        "candidate": sha,
                        "base": project["base"],
                        "checks": checks,
                        "covered_tasks": [task_id],
                    }
                ),
            ),
        )
    client, headers = paired_client(store)
    return store, project, source, sha, client, headers


def authorize(store, client, headers):
    a = store.get("approvals", "unit-approval")
    body = {"revision": a["rev"], "candidate": a["candidate"], "base": a["base"]}
    result = client.post(
        "/api/v1/approvals/unit-approval/decision",
        json={**body, "decision": "accept"},
        headers={**headers, "Idempotency-Key": "accept"},
    )
    assert result.status_code == 200, result.text
    body["revision"] = store.get("approvals", "unit-approval")["rev"]
    result = client.post(
        "/api/v1/approvals/unit-approval/integrate",
        json=body,
        headers={**headers, "Idempotency-Key": "integrate"},
    )
    assert result.status_code == 200, result.text
    # Transport retry has exactly the original response.
    assert (
        client.post(
            "/api/v1/approvals/unit-approval/integrate",
            json=body,
            headers={**headers, "Idempotency-Key": "integrate"},
        ).json()
        == result.json()
    )
    return store.get("approvals", "unit-approval")


def test_approved_exact_commit_reaches_main_and_restart_reconciles(setup):
    store, project, source, sha, client, headers = candidate(setup)
    approval = authorize(store, client, headers)
    publish(store, approval)
    assert git(source, "rev-parse", "main") == sha
    assert (source / "src/a.txt").read_text() == "verified\n"
    assert store.get("projects", project["id"])["integrated_head"] == sha
    # Replay the persisted queued intent as if SQLite commit had been lost.
    publish(store, approval)
    a = store.get("approvals", approval["id"])
    assert a["publication"]["state"] == "integrated"
    assert a["publication"]["receipt"]["recovered"] is True
    assert git(source, "rev-list", "--count", project["base"] + "..main") == "1"


@pytest.mark.parametrize("obstruction", ["dirty", "advanced", "tampered"])
def test_integration_preserves_source_and_rejects_stale_or_tampered_work(setup, obstruction):
    store, project, source, sha, client, headers = candidate(setup)
    approval = authorize(store, client, headers)
    if obstruction == "dirty":
        (source / "src/initial.txt").write_text("valuable local edits\n")
    elif obstruction == "advanced":
        (source / "src/local.txt").write_text("valuable newer commit\n")
        git(source, "add", ".")
        git(source, "commit", "-qm", "Newer local change")
    else:
        (store.home / "attempts/unit-build/integration-0.txt").write_text("tampered")
    before = git(source, "rev-parse", "main")
    publish(store, approval)
    assert git(source, "rev-parse", "main") == before != sha
    assert store.get("approvals", approval["id"])["publication"]["state"] == "failed"
    if obstruction == "dirty":
        assert (source / "src/initial.txt").read_text() == "valuable local edits\n"


def test_no_main_integration_without_exact_acceptance(setup):
    store, _, _, sha, client, headers = candidate(setup)
    a = store.get("approvals", "unit-approval")
    response = client.post(
        "/api/v1/approvals/unit-approval/integrate",
        headers=headers,
        json={"revision": a["rev"], "candidate": sha, "base": a["base"]},
    )
    assert response.status_code == 409


def test_claude_operations_validate_every_path_before_writing(setup):
    _, _, project, source = setup
    task = {"scope": ["src/a.txt"]}
    data = {
        "summary": "test",
        "operations": [
            {"op": "write", "path": "src/a.txt", "content": "safe"},
            {"op": "write", "path": "acceptance/check.py", "content": "bypass"},
        ],
    }
    with pytest.raises(ValueError, match="scope"):
        apply_scoped_response(json.dumps(data), source, task, project)
    assert not (source / "src/a.txt").exists()
    data["operations"].pop()
    assert apply_scoped_response(json.dumps(data), source, task, project) == "test"
    assert (source / "src/a.txt").read_text() == "safe"


def test_new_instructions_fence_inflight_build_result(setup):
    store, engine, project, _ = setup
    ident = goal(store, engine, project)
    attempt = engine.claim()
    with store.transaction() as db:
        task = store.get("tasks", ident, db)
        store.update_task(db, task, "running", "Steering", instructions_pending=["Change output"])
    engine.finish(
        attempt,
        {
            "attempt_id": attempt["id"],
            "token": attempt["token"],
            "status": "passed",
            "candidate": "unit-fixture",
            "checks": [{"status": "PASS"}],
        },
    )
    assert store.get("tasks", ident)["state"] == "ready"
    assert not store.rows("approvals")


def test_uninspectable_worker_retains_slot_and_never_accepts_result(setup, monkeypatch):
    store, engine, project, _ = setup
    ident = goal(store, engine, project)
    attempt = engine.claim()
    path = Path(attempt["artifact_dir"])
    path.mkdir(parents=True)
    atomic_json(path / "result.json", {"status": "passed"})
    monkeypatch.setattr("agent_arena.control.engine.alive", lambda _: None)
    monkeypatch.setattr(engine, "claim", lambda: None)
    engine.tick()
    assert store.get("attempts", attempt["id"])["state"] in ACTIVE
    assert store.get("tasks", ident)["state"] == "recovering"
    assert not store.rows("approvals")


def test_crash_before_spawn_automatically_recovers_with_bound(setup):
    store, engine, project, _ = setup
    ident = goal(store, engine, project)
    for n in range(3):
        attempt = engine.claim()
        assert attempt
        with store.transaction() as db:
            data = {
                k: v
                for k, v in attempt.items()
                if k not in {"id", "state", "task_id", "account_id", "token"}
            }
            data["created"] = time.time() - 60
            db.execute("UPDATE attempts SET data=? WHERE id=?", (encode(data), attempt["id"]))
            db.execute("UPDATE meta SET value='true' WHERE key='paused'")
        engine.tick()
        t = store.get("tasks", ident)
        assert t["state"] == ("cooldown" if n < 2 else "interrupted")
        with store.transaction() as db:
            store.update_task(db, t, t["state"], "Unit test clock", cooldown_until=0)
            db.execute("UPDATE meta SET value='false' WHERE key='paused'")
    assert engine.claim() is None


def test_orphan_recovery_escalates_only_the_verified_owned_child(tmp_path, monkeypatch):
    import signal

    from agent_arena.control.recovery import orphan_stopped

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    child = {"pid": 12345, "created": 100, "boot": 50, "attempt_id": "owned", "token": 7}
    atomic_json(artifact / "child.json", child)
    attempt = {"id": "owned", "token": 7, "artifact_dir": str(artifact), "identity": {"pid": 999}}

    class OwnedProcess:
        def create_time(self):
            return 100

        def status(self):
            return "running"

    signals = []
    monkeypatch.setattr("agent_arena.control.recovery.psutil.Process", lambda _: OwnedProcess())
    monkeypatch.setattr("agent_arena.control.recovery.psutil.boot_time", lambda: 50)
    monkeypatch.setattr(
        "agent_arena.control.recovery.os.killpg", lambda pid, sig: signals.append((pid, sig))
    )
    assert not orphan_stopped(attempt)
    assert signals == [(12345, signal.SIGTERM)]
    atomic_json(artifact / "recovery-stop.json", {"child": child, "time": time.time() - 5})
    assert not orphan_stopped(attempt)
    assert signals[-1] == (12345, signal.SIGKILL)
    monkeypatch.setattr(OwnedProcess, "create_time", lambda _: 101)
    assert orphan_stopped(attempt)  # Reused PID is never signalled.
    assert len(signals) == 2
