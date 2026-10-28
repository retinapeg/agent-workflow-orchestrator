from __future__ import annotations

from test_control import paired_client, setup  # noqa: F401  (fixture re-export)

from agent_arena.control.engine import git
from agent_arena.control.intent import resolve_project, score_project
from agent_arena.control.store import encode

ARENA = {"id": "p_arena", "name": "Arena", "repo": "/x/agent-workflow-orchestrator", "self": True}
TRACKER = {"id": "p_tracker", "name": "Application tracker", "repo": "/x/job-tracker"}
SHOP = {"id": "p_shop", "name": "Shop", "repo": "/x/shop-site", "keywords": ["checkout", "cart"]}


def test_self_phrases_pick_the_project_flagged_self():
    for text in [
        "Improve Arena: make the review screen easier to read",
        "Fix a bug in yourself: the overview flickers",
        "Add a dark mode to this dashboard",
        "autobuild should show the build stepper",
    ]:
        result = resolve_project(text, [ARENA, TRACKER, SHOP])
        assert result["project"] is ARENA, (text, result)
        assert "Arena itself" in result["reason"]
    # Without the self flag the word "arena" is just a name match, still explainable.
    plain = {**ARENA, "self": False}
    result = resolve_project("Improve Arena's review screen", [plain, TRACKER, SHOP])
    assert result["project"] is plain
    assert "named 'Arena'" in result["candidates"][0]["why"]


def test_exact_name_repository_basename_and_keywords():
    result = resolve_project("Add CSV export to the application tracker", [ARENA, TRACKER, SHOP])
    assert result["project"] is TRACKER
    assert result["candidates"][0]["why"] == "named 'Application tracker'"

    result = resolve_project("job-tracker: show the offer date on each row", [ARENA, TRACKER, SHOP])
    assert result["project"] is TRACKER
    assert "repository 'job-tracker'" in result["candidates"][0]["why"]

    result = resolve_project("Fix a bug: the checkout total is wrong", [ARENA, TRACKER, SHOP])
    assert result["project"] is SHOP
    assert "keyword 'checkout'" in result["candidates"][0]["why"]

    # Token-wise, case-insensitive: one name word alone is a hint, not a decision.
    score, why = score_project("make the TRACKER list taller", TRACKER)
    assert score == 1 and why == ["name word 'tracker'"]


def test_ambiguous_requests_return_no_project_with_sorted_candidates():
    result = resolve_project("Make the buttons bigger", [ARENA, TRACKER, SHOP])
    assert result["project"] is None
    assert result["reason"] == "The request does not name a project"
    assert [c["score"] for c in result["candidates"]] == [0, 0, 0]
    assert {c["id"] for c in result["candidates"]} == {"p_arena", "p_tracker", "p_shop"}
    assert all(c["why"] == "no match in the request" for c in result["candidates"])

    result = resolve_project("Sync the application tracker with the shop", [ARENA, TRACKER, SHOP])
    assert result["project"] is None
    assert result["reason"].startswith("Several projects match")
    scores = [c["score"] for c in result["candidates"]]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0

    # Generic words shared with a project name do not count.
    generic = {"id": "p_app", "name": "App", "repo": "/x/app"}
    result = resolve_project("Add a new feature to the app", [generic, TRACKER])
    assert result["project"] is None


def test_single_enrolled_project_always_wins_and_none_is_honest():
    result = resolve_project("Anything at all", [TRACKER])
    assert result["project"] is TRACKER
    assert result["reason"] == "Only one project is enrolled: Application tracker"
    assert resolve_project("Anything", []) == {
        "project": None,
        "candidates": [],
        "reason": "No project is enrolled",
    }


def enroll_second(store, engine, tmp_path, name, dirname):
    source = tmp_path / dirname
    (source / "src").mkdir(parents=True)
    (source / "acceptance").mkdir()
    (source / "src/initial.txt").write_text("initial\n")
    (source / "acceptance/check.py").write_text("print('ok')\n")
    git(source, "init", "-q", "-b", "main")
    git(source, "add", ".")
    git(source, "commit", "-qm", "fixture")
    return engine.enroll(
        name,
        str(source),
        ["src"],
        [{"name": "frozen", "argv": ["{python}", "acceptance/check.py"], "timeout": 10}],
        ["acceptance"],
        True,
    )


def mark_self(store, project_id):
    with store.transaction() as db:
        p = store.get("projects", project_id, db)
        p["self"] = True
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (encode({k: v for k, v in p.items() if k not in {"id", "rev"}}), project_id),
        )


def test_auto_request_resolves_single_project_and_creates_director_request(setup):  # noqa: F811
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    r = client.post("/api/v1/requests", json={"instructions": "Make it nicer"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] is True and body["project_id"] == project["id"]
    assert body["why"] == "Only one project is enrolled: Fixture"
    task = store.get("tasks", body["task_id"])
    assert task["director"] and task["phase"] == "plan" and task["timeout"] == 900
    assert task["project_id"] == project["id"]
    goal = store.get("goals", body["goal_id"])
    assert goal["request"] == "Make it nicer"
    # Idempotent replay returns the same answer without a second goal.
    again = client.post("/api/v1/requests", json={"instructions": "Make it nicer"}, headers=headers)
    assert again.json() == body
    assert len(store.rows("goals")) == 1


def test_auto_request_asks_which_project_when_ambiguous(setup, tmp_path):  # noqa: F811
    store, engine, project, _ = setup
    second = enroll_second(store, engine, tmp_path, "Tracker", "job-tracker")
    client, headers = paired_client(store)
    r = client.post(
        "/api/v1/requests", json={"instructions": "Make the buttons bigger"}, headers=headers
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["detail"] == "Which project?"
    assert {c["id"] for c in body["candidates"]} == {project["id"], second["id"]}
    assert all({"id", "name", "score", "why"} <= set(c) for c in body["candidates"])
    assert store.rows("goals") == [] and store.rows("tasks") == []

    # A clear name wins; a disabled project is never a candidate.
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Add CSV export to the tracker"},
        headers={**headers, "Idempotency-Key": "named"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == second["id"]
    assert r.json()["why"].startswith("Tracker: named 'Tracker'")

    with store.transaction() as db:
        p = store.get("projects", second["id"], db)
        p["enabled"] = False
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (encode({k: v for k, v in p.items() if k not in {"id", "rev"}}), second["id"]),
        )
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Make the buttons bigger"},
        headers={**headers, "Idempotency-Key": "only-enabled"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == project["id"]


def test_auto_request_self_phrases_pick_the_project_flagged_self(setup, tmp_path):  # noqa: F811
    store, engine, project, _ = setup
    enroll_second(store, engine, tmp_path, "Tracker", "job-tracker")
    mark_self(store, project["id"])
    client, headers = paired_client(store)
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Improve Arena: add a stepper to the overview"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == project["id"] and r.json()["resolved"] is True
    assert "Arena itself" in r.json()["why"]
    assert store.get("tasks", r.json()["task_id"])["project_id"] == project["id"]


def test_auto_request_with_explicit_project_matches_per_project_route(setup, tmp_path):  # noqa: F811
    store, engine, project, _ = setup
    second = enroll_second(store, engine, tmp_path, "Tracker", "job-tracker")
    client, headers = paired_client(store)
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Make the buttons bigger", "project_id": second["id"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == second["id"] and r.json()["resolved"] is False
    assert store.get("tasks", r.json()["task_id"])["project_id"] == second["id"]

    # A stale revision is still refused when one is supplied, as on the per-project route.
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Again", "project_id": second["id"], "revision": 999},
        headers={**headers, "Idempotency-Key": "stale"},
    )
    assert r.status_code == 409
    r = client.post(
        "/api/v1/requests",
        json={"instructions": "Nope", "project_id": "project_missing"},
        headers={**headers, "Idempotency-Key": "missing"},
    )
    assert r.status_code == 404

    # The per-project route is unchanged: revision required and checked.
    r = client.post(
        f"/api/v1/projects/{project['id']}/requests",
        json={"instructions": "Direct"},
        headers={**headers, "Idempotency-Key": "direct-no-rev"},
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/v1/projects/{project['id']}/requests",
        json={"instructions": "Direct", "revision": project["rev"]},
        headers={**headers, "Idempotency-Key": "direct"},
    )
    assert r.status_code == 200 and "task_id" in r.json()
    # Unauthenticated and CSRF-less calls are refused like every other mutation.
    assert client.post("/api/v1/requests", json={"instructions": "x"}).status_code == 403
