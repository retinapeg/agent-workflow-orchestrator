"""Director policy tests with explicitly synthetic model responses."""

import json
from pathlib import Path

import pytest

from agent_arena.control.planning import director_context, validate_director_spec
from agent_arena.control.store import encode
from tests.control import test_control as support


@pytest.fixture
def setup(tmp_path):
    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    with store.transaction() as db:
        for account in store.rows("accounts", db):
            config = Path(account["config_home"])
            config.mkdir(exist_ok=True)
            (config / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-6-astra",
                                "visibility": "list",
                                "supported_reasoning_levels": [{"effort": "max"}],
                            }
                        ]
                    }
                )
            )
            account["roles"].append("plan")
            db.execute(
                "UPDATE accounts SET data=? WHERE id=?",
                (
                    encode({k: v for k, v in account.items() if k not in {"id", "rev"}}),
                    account["id"],
                ),
            )
    return store, engine, project, source


def spec(strategy="single"):
    return {
        "outcome": "Make the product clearer",
        "summary": "I found unclear labels and will improve them.",
        "observations": ["src/initial.txt contains the existing label"],
        "acceptance": ["The new label is understandable"],
        "constraints": ["Keep frozen checks"],
        "strategy": strategy,
        "question": "",
        "tasks": [
            {
                "title": "Improve label",
                "instructions": "Replace the label in src/initial.txt with verified.",
                "scope": ["src/initial.txt"],
                "dependencies": [],
                "acceptance": ["Label reads verified"],
            }
        ],
    }


def request(setup):
    store, engine, project, _ = setup
    client, headers = support.paired_client(store)
    result = client.post(
        f"/api/v1/projects/{project['id']}/requests",
        headers=headers,
        json={"revision": project["rev"], "instructions": "Make this easier to use."},
    )
    assert result.status_code == 200, result.text
    return result.json()["task_id"], client, headers


def finish(engine, attempt, **result):
    return engine.finish(
        attempt, {"attempt_id": attempt["id"], "token": attempt["token"], **result}
    )


def test_casual_request_always_gets_astra_director_before_workers(setup):
    store, engine, project, source = setup
    ident, _, _ = request(setup)
    task = store.get("tasks", ident)
    assert task["director"] and task["phase"] == "plan" and task["review"]
    assert task["scope"] == project["scope"]
    context = director_context(store, project, task, source)
    assert context["repository"]["files"]["src/initial.txt"] == "initial\n"
    assert context["recent_commits"] and context["tasks"][0]["id"] == ident
    attempt = engine.claim()
    assert (attempt["model"], attempt["effort"]) == ("gpt-6-astra", "max")
    assert finish(engine, attempt, status="passed", plan=spec())
    children = store.get("tasks", ident)["planned_tasks"]
    child = store.get("tasks", children[0])
    assert child["director"] and child["phase"] == "build"
    assert child["instructions"] != task["instructions"]
    assert "Label reads verified" in child["instructions"]
    assert store.get("goals", child["goal_id"])["specification"]["outcome"]


def test_consequential_question_dispatches_nothing_until_answer(setup):
    store, engine, _, _ = setup
    ident, client, headers = request(setup)
    attempt = engine.claim()
    clarification = {
        **spec("clarify"),
        "question": "Should archived items be hidden by default?",
        "tasks": [],
    }
    finish(engine, attempt, status="passed", plan=clarification)
    assert engine.claim() is None
    task = store.get("tasks", ident)
    assert task["state"] == "awaiting_input" and len(store.rows("tasks")) == 1
    response = client.post(
        f"/api/v1/tasks/{ident}/answer",
        headers={**headers, "Idempotency-Key": "answer"},
        json={"revision": task["rev"], "text": "Hide them initially."},
    )
    assert response.status_code == 200
    next_attempt = engine.claim()
    assert next_attempt["phase"] == "plan"
    assert store.get("tasks", ident)["instructions_pending"] == ["Hide them initially."]


def test_director_cannot_expand_scope_skip_criteria_or_dispatch_while_asking():
    with pytest.raises(ValueError):
        validate_director_spec(spec(), ["src/other.txt"])
    with pytest.raises(ValueError):
        validate_director_spec(
            {**spec(), "strategy": "clarify", "question": "Choose behavior"}, ["src"]
        )
    missing = spec()
    missing["tasks"][0]["acceptance"] = []
    with pytest.raises(ValueError):
        validate_director_spec(missing, ["src"])


def test_evidence_returns_to_director_and_repair_is_bounded(setup):
    store, engine, _, _ = setup
    ident, _, _ = request(setup)
    finish(engine, engine.claim(), status="passed", plan=spec())
    child_id = store.get("tasks", ident)["planned_tasks"][0]
    build = engine.claim()
    finish(
        engine,
        build,
        status="passed",
        candidate="synthetic-unit-candidate",
        checks=[{"status": "PASS"}],
    )
    review = engine.claim()
    assert review["phase"] == "review" and review["account_id"] != build["account_id"]
    finish(engine, review, status="passed", review={"approved": True, "findings": []})
    director = engine.claim()
    assert director["phase"] == "direct" and director["model"] == "gpt-6-astra"
    finish(
        engine,
        director,
        status="failed",
        reason="Outcome not met",
        review={"approved": False, "findings": ["Label still unclear"]},
    )
    repair = engine.claim()
    assert repair["phase"] == "replan"
    assert store.get("tasks", child_id)["repairs"] == 1
    finish(engine, repair, status="passed", plan=spec())
    assert store.get("tasks", child_id)["phase"] == "build"
    assert len(store.rows("tasks")) == 2  # Replanning does not create a second framework or fanout.
    assert not store.rows("approvals")
