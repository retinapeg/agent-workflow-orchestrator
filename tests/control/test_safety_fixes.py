"""Regressions for defects confirmed by the 28 Oct 2026 adversarial safety review.

Provider results here are explicit fixtures; no CLI is invoked.
"""

import contextlib
import time

import pytest

from agent_arena.control.recovery import orphan_stopped
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


def test_dead_guardian_without_registered_child_is_released_after_grace(tmp_path):
    attempt = {
        "id": "a",
        "token": 1,
        "artifact_dir": str(tmp_path),
        "identity": {"pid": 1},
        "created": time.time(),
    }
    assert orphan_stopped(attempt) is False  # Fresh: a CLI child may still register.
    attempt["created"] = time.time() - 121
    assert orphan_stopped(attempt) is True  # No registration after the grace period.
    attempt.update(identity=None, created=time.time())
    assert orphan_stopped(attempt) is True  # Never registered itself: nothing to stop.


def test_unconfirmed_termination_is_reported_as_interrupted_not_cancelled(setup):
    store, engine, project, source = setup
    task_id = goal(store, engine, project)
    attempt = engine.claim()
    with store.transaction() as db:
        store.update_task(
            db, store.get("tasks", task_id, db), "running", "fixture", cancel_requested=True
        )
    assert engine.finish(
        attempt,
        {
            "attempt_id": attempt["id"],
            "token": attempt["token"],
            "status": "failed",
            "reason": "Termination unconfirmed for owned pid/pgid=4242",
            "termination_unconfirmed": True,
            "checks": [],
        },
    )
    task = store.get("tasks", task_id)
    assert task["state"] == "interrupted"
    assert "could not confirm" in task["reason"]


def test_failed_integration_future_does_not_stall_dispatch(setup):
    store, engine, project, source = setup

    def boom():
        raise RuntimeError("publication exploded")

    engine.submit_integration(project["id"], boom)
    assert engine.integrations[project["id"]].exception(timeout=5) is not None
    engine.submit_integration(project["id"], lambda: "ok")  # Must not raise the old failure.
    assert engine.integrations[project["id"]].result(timeout=5) == "ok"
    with contextlib.closing(store.connect()) as db:
        kinds = [r["kind"] for r in db.execute("SELECT kind FROM events")]
    assert "integration.error" in kinds


def test_non_ascii_csrf_header_is_rejected_not_a_server_error(setup):
    store, engine, project, source = setup
    client, headers = paired_client(store)
    response = client.post(
        f"/api/v1/projects/{project['id']}/pause",
        json={"revision": project["rev"]},
        headers={
            **{k: v for k, v in headers.items() if k != "X-CSRF-Token"},
            b"X-CSRF-Token": b"t\xf8ken",
        },
    )
    assert response.status_code == 403


def test_instructions_clear_director_question_and_refuse_cancelled_tasks(setup):
    store, engine, project, source = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project)
    with store.transaction() as db:
        store.update_task(
            db,
            store.get("tasks", task_id, db),
            "awaiting_input",
            "fixture",
            director=True,
            phase="plan",
            question="Which colour?",
        )
    task = store.get("tasks", task_id)
    response = client.post(
        f"/api/v1/tasks/{task_id}/instructions",
        json={"revision": task["rev"], "text": "Blue, and keep it simple"},
        headers={**headers, "Idempotency-Key": "steer-1"},
    )
    assert response.status_code == 200, response.text
    task = store.get("tasks", task_id)
    assert task["state"] == "ready" and task["question"] == ""
    with store.transaction() as db:
        store.update_task(db, store.get("tasks", task_id, db), "cancelled", "fixture")
    task = store.get("tasks", task_id)
    response = client.post(
        f"/api/v1/tasks/{task_id}/instructions",
        json={"revision": task["rev"], "text": "more"},
        headers={**headers, "Idempotency-Key": "steer-2"},
    )
    assert response.status_code == 409


def test_scheduler_loop_survives_a_store_failure(setup, monkeypatch):
    import threading

    store, engine, project, source = setup
    calls = {"n": 0}

    def flaky_tick():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("unable to open database file")

    monkeypatch.setattr(engine, "tick", flaky_tick)
    monkeypatch.setattr(engine.halt, "wait", lambda seconds: time.sleep(0.01) or False)
    thread = threading.Thread(target=engine.loop, daemon=True)
    thread.start()
    time.sleep(0.5)
    assert thread.is_alive() and calls["n"] > 2  # kept ticking after the failures
    engine.halt.set()
    thread.join(timeout=2)
