from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_arena.control.api import make_app
from agent_arena.control.engine import Engine, atomic_json, git, overlap
from agent_arena.control.store import Conflict, Store, encode
from agent_arena.control.worker import auth_status, invocation
from agent_arena.process import ProcessRunner


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "state")
    source = tmp_path / "source"
    source.mkdir()
    (source / "src").mkdir()
    (source / "acceptance").mkdir()
    (source / "src/initial.txt").write_text("initial\n")
    (source / "acceptance/check.py").write_text(
        "from pathlib import Path\nfor p in Path('src').glob('*.txt'):"
        "\n assert p.read_text().strip() in {'initial','verified'}\n"
        "print('frozen checks passed')\n"
    )
    git(source, "init", "-q", "-b", "main")
    git(source, "add", ".")
    git(source, "commit", "-qm", "fixture")
    engine = Engine(store)
    project = engine.enroll(
        "Fixture",
        str(source),
        ["src"],
        [{"name": "frozen", "argv": ["{python}", "acceptance/check.py"], "timeout": 10}],
        ["acceptance"],
        True,
    )
    fake = tmp_path / "fake-codex"
    fake.write_text(f"""#!{sys.executable}
import sys,json,pathlib,re,time,os
if 'login' in sys.argv:
 print('Logged in using ChatGPT');sys.exit(0)
prompt=sys.stdin.read()
if 'FAKE_RATE_LIMIT' in prompt:
 print('rate limit exceeded',file=sys.stderr);sys.exit(1)
if 'FAKE_SLOW' in prompt:time.sleep(3)
if 'FAKE_CANCEL' in prompt:
 import subprocess
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
 pathlib.Path('descendant.pid').write_text(str(child.pid))
 time.sleep(60)
if 'Independently review' in prompt:
 final=json.dumps({{'approved':True,'findings':[]}})
else:
 scope=json.loads(re.search(r'declared paths: (\\[.*?\\])',prompt).group(1))
 for name in scope:
  p=pathlib.Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text('verified\\n')
 final='Implemented fixture'
print(json.dumps({{'type':'thread.started','thread_id':'test-only-thread'}}))
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':final}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10,'cached_input_tokens':0,'output_tokens':4}}}}))
""")
    fake.chmod(0o700)
    for n in range(3):
        data = dict(
            name=f"Test slot {n}",
            provider="codex",
            executable=str(fake),
            config_home=str(tmp_path / f"auth{n}"),
            model="test-only",
            version="fixture",
            enabled=True,
            health="ready",
            roles=["build", "review"],
            cooldown_until=0,
        )
        with store.transaction() as db:
            db.execute("INSERT INTO accounts(id,data) VALUES(?,?)", (f"slot{n}", encode(data)))
    return store, engine, project, source


def goal(
    store,
    engine,
    project,
    path="src/a.txt",
    instructions="Write the scoped fixture",
    review=False,
    deps=(),
):
    with store.transaction() as db:
        result = engine.add_goal(db, project["id"], path, instructions, [path], deps, review, 30)
    return result["task_id"]


def run_until(engine, store, predicate, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        engine.tick()
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError([(t["state"], t["reason"]) for t in store.rows("tasks")])


def paired_client(store):
    app = make_app(store.home, run_scheduler=False)
    client = TestClient(app, base_url="http://127.0.0.1:8790")
    code = "test-pairing-one-use"
    atomic_json(
        store.home / "pairing.json",
        {"hash": hashlib.sha256(code.encode()).hexdigest(), "expires": time.time() + 60},
    )
    r = client.post(
        "/api/v1/pair", json={"code": code}, headers={"Origin": "http://127.0.0.1:8790"}
    )
    assert r.status_code == 200
    headers = {
        "Origin": "http://127.0.0.1:8790",
        "X-CSRF-Token": r.json()["csrf"],
        "Idempotency-Key": "command-1",
    }
    return client, headers


def test_phone_mutation_idempotency_revision_and_security(setup):
    store, engine, project, _ = setup
    app = make_app(store.home, run_scheduler=False)
    public = TestClient(app, base_url="http://127.0.0.1:8790")
    assert public.get("/api/v1/overview").status_code == 401
    assert public.get("/api/v1/overview", headers={"Host": "evil.example"}).status_code == 403
    client, headers = paired_client(store)
    body = dict(
        title="Goal",
        instructions="Implement",
        scope=["src/a.txt"],
        revision=project["rev"],
        review=False,
    )
    url = f"/api/v1/projects/{project['id']}/goals"
    assert (
        client.post(
            url, json=body, headers={**headers, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(url, json=body, headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
    )
    first = client.post(url, json=body, headers=headers)
    assert first.status_code == 200
    assert client.post(url, json=body, headers=headers).json() == first.json()
    assert len(store.rows("tasks")) == 1
    assert client.post(url, json={**body, "title": "different"}, headers=headers).status_code == 409
    assert (
        client.post(
            url, json={**body, "revision": 999}, headers={**headers, "Idempotency-Key": "stale"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            url,
            json={**body, "scope": ["../escape"]},
            headers={**headers, "Idempotency-Key": "escape"},
        ).status_code
        == 400
    )
    client.post("/api/v1/logout", json={}, headers=headers)
    assert client.get("/api/v1/overview").status_code == 401


def test_concurrent_claims_have_three_slot_ceiling_and_conflicting_scopes_wait(setup):
    store, engine, project, source = setup
    ids = [
        goal(store, engine, project, p)
        for p in ["src/a.txt", "src/b.txt", "src/a.txt", "src/c.txt"]
    ]
    other = source.parent / "other"
    git(source.parent, "clone", str(source), str(other))
    second = engine.enroll("Other", str(other), ["src"], project["checks"], ["acceptance"], True)
    goal(store, engine, second, "src/z.txt")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: engine.claim(), range(8)))
    claimed = [a for a in claimed if a]
    assert len(claimed) == 3
    assert len({a["account_id"] for a in claimed}) == 3
    assert len({a["workspace"] for a in claimed}) == 3
    assert len([a for a in claimed if a["task_id"] in ids]) == 2
    assert store.get("tasks", ids[2])["state"] == "waiting_capacity"


def test_stale_token_cannot_complete_and_crash_before_spawn_recovers(setup):
    store, engine, project, _ = setup
    task_id = goal(store, engine, project)
    a = engine.claim()
    with store.transaction() as db:
        data = {
            k: v for k, v in a.items() if k not in {"id", "task_id", "account_id", "token", "state"}
        }
        data["created"] = time.time() - 30
        db.execute("UPDATE attempts SET data=? WHERE id=?", (encode(data), a["id"]))
        db.execute("UPDATE meta SET value='true' WHERE key='paused'")
    engine.tick()
    assert store.get("tasks", task_id)["state"] == "cooldown"
    assert not engine.finish(
        a,
        dict(
            attempt_id=a["id"],
            token=a["token"],
            status="passed",
            candidate="forged",
            checks=[{"status": "PASS"}],
        ),
    )
    assert len(store.rows("attempts")) == 1


def test_singleton_scheduler_owner(setup):
    store, engine, _, _ = setup
    engine.start()
    try:
        with pytest.raises(RuntimeError, match="Another scheduler"):
            Engine(store).start(wait_seconds=0)
    finally:
        engine.close()


def test_restart_after_spawn_and_artifact_before_commit_does_not_duplicate(setup):
    store, engine, project, _ = setup
    task_id = goal(store, engine, project, instructions="FAKE_SLOW write fixture")
    engine.tick()
    first = store.rows("attempts")[0]
    # Drop scheduler instance while independent guardian keeps running.
    replacement = Engine(Store(store.home))
    run_until(
        replacement, store, lambda: store.get("tasks", task_id)["state"] == "ready_for_approval"
    )
    assert len(store.rows("attempts")) == 1
    assert store.get("attempts", first["id"])["state"] == "passed"
    assert len(store.rows("approvals")) == 1
    replacement.tick()
    assert len(store.rows("approvals")) == 1


def test_parallel_candidates_combine_before_dependent_and_review_is_independent(setup):
    store, engine, project, source = setup
    a = goal(store, engine, project, "src/a.txt", "FAKE_SLOW build A", True)
    b = goal(store, engine, project, "src/b.txt", "FAKE_SLOW build B", True)
    c = goal(store, engine, project, "src/c.txt", "Build dependent", False, [a, b])
    run_until(
        engine, store, lambda: store.get("tasks", c)["state"] == "ready_for_approval", seconds=50
    )
    stage = Path(store.get("projects", project["id"])["staging"])
    assert all((stage / "src" / f"{n}.txt").read_text() == "verified\n" for n in "abc")
    assert not (source / "src/a.txt").exists()
    tasks = {t["id"]: t for t in store.rows("tasks")}
    attempts = {a["id"]: a for a in store.rows("attempts")}
    for t in [tasks[a], tasks[b]]:
        assert (
            attempts[t["build_attempt"]]["account_id"]
            != attempts[t["review_attempt"]]["account_id"]
        )
    dependent = attempts[tasks[c]["build_attempt"]]
    assert dependent["base"] == tasks[b].get("staged") or dependent["base"] == tasks[a].get(
        "staged"
    )
    receipt = json.loads((Path(dependent["artifact_dir"]) / "integration.json").read_text())
    assert receipt["checks"][0]["status"] == "PASS"
    assert len([x for x in store.rows("approvals") if x["state"] == "pending"]) == 1


def test_approval_bound_to_actual_candidate_and_base(setup):
    store, engine, project, _ = setup
    task_id = goal(store, engine, project)
    run_until(engine, store, lambda: store.get("tasks", task_id)["state"] == "ready_for_approval")
    a = store.rows("approvals")[0]
    client, headers = paired_client(store)
    body = dict(revision=a["rev"], candidate="wrong", base=a["base"], decision="accept")
    url = f"/api/v1/approvals/{a['id']}/decision"
    assert client.post(url, json=body, headers=headers).status_code == 409
    response = client.post(
        url,
        json={**body, "candidate": a["candidate"]},
        headers={**headers, "Idempotency-Key": "accept"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["published"] is False
    assert store.get("tasks", task_id)["state"] == "completed"


def test_rate_limit_cools_one_slot_without_stopping_other_project(setup):
    store, engine, project, _ = setup
    task = goal(store, engine, project, instructions="FAKE_RATE_LIMIT")
    run_until(engine, store, lambda: store.get("tasks", task)["state"] == "cooldown")
    assert any(a.get("cooldown_until", 0) > time.time() for a in store.rows("accounts"))
    other = goal(store, engine, project, "src/other.txt")
    run_until(engine, store, lambda: store.get("tasks", other)["state"] == "ready_for_approval")
    assert store.get("tasks", task)["retries"] == 1


def test_cancel_terminates_owned_descendants(setup):
    import psutil

    store, engine, project, _ = setup
    ident = goal(store, engine, project, instructions="FAKE_CANCEL")
    run_until(
        engine,
        store,
        lambda: (
            bool(store.rows("attempts"))
            and (Path(store.rows("attempts")[0]["workspace"]) / "descendant.pid").exists()
        ),
    )
    attempt = store.rows("attempts")[0]
    pid = int((Path(attempt["workspace"]) / "descendant.pid").read_text())
    with store.transaction() as db:
        t = store.get("tasks", ident, db)
        store.update_task(db, t, t["state"], "cancel requested", cancel_requested=True)
    engine.cancel(ident)
    run_until(engine, store, lambda: store.get("tasks", ident)["state"] == "cancelled")
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_missing_subscription_never_invokes_exec_or_api(setup, tmp_path):
    store, _, _, _ = setup
    account = store.rows("accounts")[0]
    fake = tmp_path / "no-auth"
    fake.write_text(
        f'#!{sys.executable}\nimport sys\nprint("Logged in using an API key")\nsys.exit(0)\n'
    )
    fake.chmod(0o700)
    account["executable"] = str(fake)
    assert not auth_status(account)[0]
    assert "danger-full-access" not in invocation(account, tmp_path, "build")


def test_streaming_callback_arrives_before_process_completion(tmp_path):
    seen = []
    runner = ProcessRunner()
    start = time.monotonic()
    result = runner.run(
        [
            sys.executable,
            "-u",
            "-c",
            'import time;print("first",flush=True);time.sleep(.5);print("last")',
        ],
        cwd=tmp_path,
        timeout_seconds=3,
        max_output_bytes=1000,
        on_output=lambda channel, data: seen.append((time.monotonic() - start, data)),
    )
    assert result.exit_code == 0 and seen[0][0] < 0.4 and b"first" in seen[0][1]


def test_online_backup_and_scope_validation(setup, tmp_path):
    store, engine, project, _ = setup
    goal(store, engine, project)
    store.backup(tmp_path / "backup.sqlite")
    import sqlite3

    with sqlite3.connect(tmp_path / "backup.sqlite") as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert overlap(["src"], ["src/a.py"])
    with pytest.raises(ValueError):
        goal(store, engine, project, "acceptance/check.py")


def test_planning_expansion_preserves_authority_and_waits_for_builds(setup):
    from agent_arena.control.planning import validate_plan

    store, engine, project, _ = setup
    plan = {
        "tasks": [
            {"title": "A", "instructions": "Build A", "scope": ["src/a.txt"]},
            {
                "title": "B",
                "instructions": "Build B after A",
                "scope": ["src/b.txt"],
                "dependencies": [0],
            },
        ]
    }
    for bad in [
        {"tasks": [{**plan["tasks"][0], "scope": ["acceptance/check.py"]}]},
        {"tasks": [{**plan["tasks"][0], "dependencies": [0]}]},
        {"tasks": [{**plan["tasks"][0], "dependencies": [1]}, plan["tasks"][1]]},
    ]:
        with pytest.raises(ValueError):
            validate_plan(bad, ["src"])
    with store.transaction() as db:
        parent = engine.add_goal(
            db, project["id"], "Plan", "Build A then B", ["src"], review=True, planning=True
        )["task_id"]
        for account in store.rows("accounts", db):
            account["roles"].append("plan")
            db.execute(
                "UPDATE accounts SET data=? WHERE id=?",
                (
                    encode({k: v for k, v in account.items() if k not in {"id", "rev"}}),
                    account["id"],
                ),
            )
    attempt = engine.claim()
    assert attempt["phase"] == "plan"
    result = {
        "attempt_id": attempt["id"],
        "token": attempt["token"],
        "status": "passed",
        "reason": "Valid fixture plan",
        "plan": plan,
    }
    assert engine.finish(attempt, result)
    assert not engine.finish(attempt, result)  # replay cannot duplicate children
    children = store.get("tasks", parent)["planned_tasks"]
    assert len(children) == 2
    assert all(store.get("tasks", t)["review"] for t in children)
    assert engine.claim()["task_id"] == children[0]
    assert engine.claim() is None
    assert store.get("tasks", children[1])["state"] == "waiting_dependency"
    with pytest.raises(ValueError, match="planner's build tasks"):
        goal(store, engine, project, "src/c.txt", deps=[parent])


def test_remote_origin_secure_pairing_health_and_unknown_api(tmp_path):
    from agent_arena.control.configuration import validate_origin

    for origin in [
        "http://public.example",
        "https://user:pass@host",
        "https://host/path",
        "https://host?query=yes",
        "ftp://host",
        "https://host:invalid",
    ]:
        with pytest.raises(ValueError):
            validate_origin(origin)
    store = Store(tmp_path / "remote")
    origin = "https://mac.example.ts.net"
    code = "remote-one-time-code"
    atomic_json(
        store.home / "pairing.json",
        {
            "hash": hashlib.sha256(code.encode()).hexdigest(),
            "expires": time.time() + 60,
        },
    )
    app = make_app(store.home, origin=origin, run_scheduler=False)
    with TestClient(app, base_url=origin) as client:
        assert client.get("/api/v1/health").status_code == 401
        response = client.post("/api/v1/pair", json={"code": code}, headers={"Origin": origin})
        assert response.status_code == 200
        assert all(
            flag in response.headers["set-cookie"].lower()
            for flag in ["secure", "httponly", "samesite=strict"]
        )
        assert client.get("/api/v1/overview").status_code == 200
        assert client.get("/api/v1/health").status_code == 503
        with store.transaction() as db:
            db.execute("INSERT INTO meta VALUES('heartbeat',?)", (encode(time.time()),))
        assert client.get("/api/v1/health").json()["scheduler_ready"] is True
        assert client.get("/api/v1/unknown").status_code == 404
        assert client.get("/api/v1/overview", headers={"Host": "evil.invalid"}).status_code == 403


def test_service_configuration_preserves_remote_origin_and_port(tmp_path):
    import plistlib

    from agent_arena.control.__main__ import main

    destination = tmp_path / "control.plist"
    main(
        [
            "--home",
            str(tmp_path / "state"),
            "service-plist",
            str(destination),
            "--port",
            "8791",
            "--origin",
            "https://mac.example.ts.net",
            "--keep-awake",
        ]
    )
    config = plistlib.loads(destination.read_bytes())
    args = config["ProgramArguments"]
    assert args[:2] == ["/usr/bin/caffeinate", "-i"]
    assert args[-4:] == ["--port", "8791", "--origin", "https://mac.example.ts.net"]
    assert config["KeepAlive"] and config["RunAtLoad"]
    with pytest.raises(ValueError, match="exists"):
        main(["--home", str(tmp_path / "state"), "service-plist", str(destination)])


@pytest.mark.parametrize("decision", ["accept", "reject", "request_changes"])
def test_combined_approval_diff_and_decision_cover_all_changes(setup, monkeypatch, decision):
    store, engine, project, source = setup
    # Exercise controller staging with synthetic candidate evidence. No provider invocation.
    # OS sandbox/check execution is covered separately by the worker integration suite.
    monkeypatch.setattr(
        "agent_arena.control.worker.checks_run",
        lambda *args: [{"name": "synthetic", "status": "PASS"}],
    )
    base = project["head"]
    for name in ["a", "b"]:
        task_id = goal(store, engine, project, f"src/{name}.txt")
        attempt = engine.claim()
        workspace = Path(attempt["workspace"])
        workspace.parent.mkdir(exist_ok=True)
        git(workspace.parent, "clone", "--no-hardlinks", project["staging"], str(workspace))
        # Trailing blank lines make the patch end in blank context; integration must keep them.
        (workspace / f"src/{name}.txt").write_text("verified\n\n" if name == "b" else "verified\n")
        git(workspace, "add", ".")
        git(workspace, "commit", "-qm", name)
        candidate = git(workspace, "rev-parse", "HEAD")
        Path(attempt["artifact_dir"]).mkdir(parents=True)
        assert engine.finish(
            attempt,
            {
                "attempt_id": attempt["id"],
                "token": attempt["token"],
                "status": "passed",
                "candidate": candidate,
                "checks": [{"status": "PASS"}],
            },
        )
        engine.integrate(store.get("tasks", task_id))
    pending = [a for a in store.rows("approvals") if a["state"] == "pending"]
    assert len(pending) == 1
    approval = pending[0]
    assert approval["review_base"] == base
    client, headers = paired_client(store)
    diff = client.get(f"/api/v1/approvals/{approval['id']}/diff").json()
    assert "src/a.txt" in diff["text"] and "src/b.txt" in diff["text"]
    assert diff["candidate"] == approval["candidate"] and not diff["truncated"]
    response = client.post(
        f"/api/v1/approvals/{approval['id']}/decision",
        headers=headers,
        json={
            "revision": approval["rev"],
            "candidate": approval["candidate"],
            "base": approval["base"],
            "decision": decision,
        },
    )
    assert response.status_code == 200, response.text
    expected = "completed" if decision == "accept" else "blocked"
    assert {t["state"] for t in store.rows("tasks")} == {expected}
    current = store.get("projects", project["id"])
    assert current["enabled"] is (decision == "accept")
    if decision == "accept":
        assert current["accepted_head"] == approval["candidate"]
    assert git(source, "rev-parse", "HEAD") == base
    assert not (source / "src/a.txt").exists()


def test_host_permission_failure_never_invokes_provider(setup, monkeypatch):
    from agent_arena.control import worker

    store, engine, project, _ = setup
    task_id = goal(store, engine, project)
    attempt = engine.claim()

    def denied(_pid):
        raise PermissionError("process identity unavailable")

    def forbidden(_account):
        pytest.fail("Provider must not run without verified process ownership")

    monkeypatch.setattr(worker, "identity", denied)
    monkeypatch.setattr(worker, "auth_status", forbidden)
    assert worker.main(store.home, attempt["id"]) == 1
    result = json.loads((Path(attempt["artifact_dir"]) / "result.json").read_text())
    assert result["status"] == "needs_permission"
    assert engine.finish(attempt, result)
    assert store.get("tasks", task_id)["state"] == "needs_permission"
    assert not Path(attempt["workspace"]).exists()


def test_auto_local_main_policy_integrates_verified_work_without_a_decision(setup, monkeypatch):
    store, engine, project, source = setup

    def synthetic_checks(project, workspace, artifact, prefix="checks", runner=None):
        log = Path(artifact) / f"{prefix}-0.txt"
        log.write_text("Explicit unit fixture: verification result supplied by test\n")
        return [
            {
                "name": "synthetic",
                "status": "PASS",
                "artifact": log.name,
                "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            }
        ]

    monkeypatch.setattr("agent_arena.control.worker.checks_run", synthetic_checks)
    git(source, "branch", "-M", "main")
    with store.transaction() as db:
        current = store.get("projects", project["id"], db)
        current["integration_policy"] = "auto_local_main"
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (encode({k: v for k, v in current.items() if k not in {"id", "rev"}}), project["id"]),
        )
    base = project["head"]
    task_id = goal(store, engine, project, "src/a.txt")
    attempt = engine.claim()
    workspace = Path(attempt["workspace"])
    workspace.parent.mkdir(exist_ok=True)
    git(workspace.parent, "clone", "--no-hardlinks", project["staging"], str(workspace))
    (workspace / "src/a.txt").write_text("verified\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-qm", "a")
    candidate = git(workspace, "rev-parse", "HEAD")
    Path(attempt["artifact_dir"]).mkdir(parents=True)
    assert engine.finish(
        attempt,
        {
            "attempt_id": attempt["id"],
            "token": attempt["token"],
            "status": "passed",
            "candidate": candidate,
            "checks": [{"status": "PASS"}],
        },
    )
    engine.integrate(store.get("tasks", task_id))
    approvals = store.rows("approvals")
    assert len(approvals) == 1 and approvals[0]["state"] == "accept"
    approval = approvals[0]
    assert approval["decision"]["by"] == "integration_policy"
    assert approval["publication"]["state"] == "queued"
    assert approval["publication"]["expected_source"] == base
    assert store.get("projects", project["id"])["accepted_head"] == approval["candidate"]
    assert git(source, "rev-parse", "main") == base  # Nothing reaches the source before publish.
    from agent_arena.control.publication import publish

    publish(store, approval)
    assert git(source, "rev-parse", "main") == approval["candidate"]
    task = store.get("tasks", task_id)
    assert task["state"] == "completed" and task["integrated_commit"] == approval["candidate"]
    assert store.get("approvals", approval["id"])["publication"]["state"] == "integrated"
    assert store.get("approvals", approval["id"])["publication"]["receipt"]["pushed"] is False


def test_device_sessions_persist_renew_and_revoke(setup):
    store, engine, project, source = setup
    client, headers = paired_client(store)
    cookie = client.cookies.get("arena_session")
    with contextlib.closing(store.connect()) as db:
        row = dict(db.execute("SELECT * FROM sessions").fetchone())
    assert row["expires"] - time.time() > 29 * 86400 and row["id"] and row["created"]
    # Fresh session: no renewal needed, overview works, and no cookie churn on every poll.
    fresh = client.get("/api/v1/overview")
    assert fresh.status_code == 200 and "set-cookie" not in fresh.headers
    # Rows written before device columns existed are listed by hash prefix and revocable.
    with store.transaction() as db:
        db.execute(
            "INSERT INTO sessions(hash,expires,csrf) VALUES(?,?,?)",
            ("a" * 64, time.time() + 86400, "legacy-csrf"),
        )
    legacy = next(d for d in client.get("/api/v1/devices").json() if d["id"] == "a" * 12)
    assert "before device labels" in legacy["label"]
    r = client.post(
        f"/api/v1/devices/{legacy['id']}/revoke",
        json={"revision": 0},
        headers={**headers, "Idempotency-Key": "revoke-legacy"},
    )
    assert r.status_code == 200 and r.json()["revoked"] is True
    r = client.post(
        f"/api/v1/devices/{legacy['id']}/revoke",
        json={"revision": 0},
        headers={**headers, "Idempotency-Key": "revoke-legacy-again"},
    )
    assert r.status_code == 200 and r.json()["revoked"] is False
    # Past the absolute cap, use no longer renews: the session will lapse.
    with store.transaction() as db:
        db.execute(
            "UPDATE sessions SET created=?, expires=?",
            (time.time() - 181 * 86400, time.time() + 20 * 86400),
        )
    capped = client.get("/api/v1/session")
    assert capped.status_code == 200 and "set-cookie" not in capped.headers
    with store.transaction() as db:
        db.execute("UPDATE sessions SET created=?", (time.time() - 86400,))
    # Age the session past the renewal interval; the next request extends it and re-sets the cookie.
    with store.transaction() as db:
        db.execute("UPDATE sessions SET expires=?", (time.time() + 20 * 86400,))
    response = client.get("/api/v1/session")
    assert response.status_code == 200
    assert "arena_session" in response.headers.get("set-cookie", "")
    assert client.cookies.get("arena_session") == cookie  # Same token, longer life.
    with contextlib.closing(store.connect()) as db:
        expires = db.execute("SELECT expires FROM sessions").fetchone()[0]
    assert expires - time.time() > 29 * 86400
    # A second device pairs with its own code; both are listed; one can revoke the other.
    other = TestClient(make_app(store.home, run_scheduler=False), base_url="http://127.0.0.1:8790")
    atomic_json(
        store.home / "pairing.json",
        {"hash": hashlib.sha256(b"second-device-code").hexdigest(), "expires": time.time() + 60},
    )
    paired = other.post(
        "/api/v1/pair",
        json={"code": "second-device-code"},
        headers={"Origin": "http://127.0.0.1:8790"},
    )
    assert paired.status_code == 200, paired.text
    devices = client.get("/api/v1/devices").json()
    assert len(devices) == 2 and sum(d["current"] for d in devices) == 1
    victim = next(d for d in devices if not d["current"])
    r = client.post(
        f"/api/v1/devices/{victim['id']}/revoke",
        json={"revision": 0},
        headers={**headers, "Idempotency-Key": "revoke-1"},
    )
    assert r.status_code == 200, r.text
    assert other.get("/api/v1/overview").status_code == 401
    assert client.get("/api/v1/overview").status_code == 200
    # Explicit logout ends this device's session.
    assert client.post("/api/v1/logout", json={}, headers=headers).status_code == 200
    assert client.get("/api/v1/overview").status_code == 401


def test_multiple_pairing_codes_coexist_and_paired_devices_can_invite(setup):
    from agent_arena.control.store import issue_pairing_code

    store, engine, project, source = setup
    first, _ = issue_pairing_code(store.home, "host")
    second, _ = issue_pairing_code(store.home, "host")  # Must not cancel the first.
    app = make_app(store.home, run_scheduler=False)
    origin = {"Origin": "http://127.0.0.1:8790"}
    a = TestClient(app, base_url="http://127.0.0.1:8790")
    assert a.post("/api/v1/pair", json={"code": first}, headers=origin).status_code == 200
    assert a.post("/api/v1/pair", json={"code": first}, headers=origin).status_code == 403  # once
    b = TestClient(app, base_url="http://127.0.0.1:8790")
    assert b.post("/api/v1/pair", json={"code": second}, headers=origin).status_code == 200
    # Only a paired device may mint a code for another device.
    anon = TestClient(app, base_url="http://127.0.0.1:8790")
    denied = anon.post("/api/v1/devices/invite", json={"revision": 0}, headers=origin)
    assert denied.status_code == 401
    csrf = a.get("/api/v1/session").json()["csrf"]
    invite = a.post(
        "/api/v1/devices/invite",
        json={"revision": 0},
        headers={**origin, "X-CSRF-Token": csrf, "Idempotency-Key": "invite-1"},
    )
    assert invite.status_code == 200, invite.text
    c = TestClient(app, base_url="http://127.0.0.1:8790")
    paired = c.post("/api/v1/pair", json={"code": invite.json()["code"]}, headers=origin)
    assert paired.status_code == 200
    assert len(a.get("/api/v1/devices").json()) == 3


def test_self_enrolment_is_explicit_and_one_self_build_at_a_time(setup, tmp_path):
    from agent_arena.control.__main__ import inferred_manifest

    store, engine, project, source = setup
    controller = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="self_target"):
        engine.enroll("Arena", str(controller), ["src"], [{"argv": ["x"]}], [], True)
    with pytest.raises(ValueError, match="only valid for the controller"):
        engine.enroll("Other", str(source), ["src"], [{"argv": ["x"]}], [], True, self_target=True)
    # A project flagged as Arena itself accepts one request at a time.
    with store.transaction() as db:
        current = store.get("projects", project["id"], db)
        current["self"] = True
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (encode({k: v for k, v in current.items() if k not in {"id", "rev"}}), project["id"]),
        )
    goal(store, engine, project)
    with pytest.raises(Conflict, match="already working on itself"):
        goal(store, engine, project)
    # Inferred manifests skip build junk and point checks at the generic repo check.
    repo = tmp_path / "theirs"
    (repo / ".git").mkdir(parents=True)
    (repo / "node_modules").mkdir()
    (repo / "app").mkdir()
    (repo / "README.md").write_text("x")
    manifest = inferred_manifest(repo, keywords="billing, invoices")
    assert manifest["scope"] == ["README.md", "app"]
    assert manifest["integration_policy"] == "auto_local_main"
    assert "repo_check.py" in manifest["checks"][0]["argv"][1]
    assert manifest["keywords"] == ["billing", "invoices", "theirs"]
