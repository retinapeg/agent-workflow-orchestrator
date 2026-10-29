"""Owner routing override. Auto (the default) is today's per-task routing, byte for byte;
the owner may pin Codex or Claude to one model and effort from the phone, and every attempt
routed to that provider then runs on exactly that pair. Only fake CLIs run here."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_arena.control import conversation
from agent_arena.control.routing import select_route
from agent_arena.control.status import arena_status
from agent_arena.control.store import encode
from agent_arena.control.worker import invocation
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client, run_until

# Routes of the matrix below as the routing before overrides chose them (commit 63199ee).
GOLDEN = Path(__file__).parent / "fixtures" / "auto-routes.json"
EVERY_EFFORT = ["low", "medium", "high", "xhigh", "max"]
CATALOGS = {
    "full": [
        ("gpt-6-luna", ["minimal", "low", "medium"]),
        ("gpt-6.1-sol", ["low", "medium", "high"]),
        ("gpt-6-sol", ["low", "medium", "high", "xhigh"]),
        ("gpt-6.1-terra", ["medium", "high", "xhigh"]),
        ("gpt-6-astra", EVERY_EFFORT),
    ],
    "narrow": [
        ("gpt-6-luna", ["low", "medium"]),
        ("gpt-6.1-sol", ["low", "medium", "high"]),
        ("gpt-6-astra", ["high", "max"]),
    ],
}
VALIDATED = {"claude-haiku-5-5": ["low"], "sonnet": ["medium"], "claude-fable-5-1": ["high", "max"]}
CODEX_PIN = {"model": "gpt-6-astra", "effort": "low"}
CLAUDE_PIN = {"model": "claude-opus-5-5", "effort": "max"}


def write_catalog(home: Path, catalog: str):
    home.mkdir(parents=True, exist_ok=True)
    models = [
        {
            "slug": slug,
            "visibility": "list",
            "supported_reasoning_levels": [{"effort": e} for e in levels],
        }
        for slug, levels in CATALOGS[catalog]
    ]
    # Never offered: a hidden model, and one with no effort level Arena knows.
    models += [
        {
            "slug": "gpt-6-internal",
            "visibility": "hide",
            "supported_reasoning_levels": [{"effort": "max"}],
        },
        {
            "slug": "gpt-6-nano",
            "visibility": "list",
            "supported_reasoning_levels": [{"effort": "minimal"}],
        },
    ]
    (home / "models_cache.json").write_text(json.dumps({"models": models}))


def codex(base: Path, ident="codex", catalog="full"):
    home = base / ident
    if catalog:
        write_catalog(home, catalog)
    return {
        "id": ident,
        "name": ident,
        "provider": "codex",
        "config_home": str(home),
        "model": "gpt-6-sol",
        "executable": "codex",
        "effort": "medium",
    }


def claude(base: Path, ident="claude", validated=None):
    account = {
        "id": ident,
        "name": ident,
        "provider": "claude",
        "config_home": str(base / ident),
        "model": "sonnet",
        "executable": "claude",
        "effort": "medium",
    }
    if validated:
        account["model_capabilities"] = validated
    return account


def task(**overrides):
    return {
        "id": "task",
        "title": "Add a filter",
        "scope": ["src/app.py"],
        "phase": "build",
        "repairs": 0,
        **overrides,
    }


TASKS = {
    "simple": task(title="Improve description", scope=["manifest.json"]),
    "moderate": task(),
    "complex": task(complexity="complex"),
    "high_risk": task(title="Fix auth token refresh", scope=["src/auth.py"]),
    "repair_1": task(repairs=1),
    "repair_2": task(repairs=2),
    "retry": task(retries=1),
    "review_of_codex": task(phase="review", author_provider="codex"),
    "review_of_claude": task(phase="review", author_provider="claude"),
    "plan_small": task(director=True, phase="plan", instructions="Rename the app"),
    "plan_broad": task(director=True, phase="plan", instructions="x" * 600),
    "replan_1": task(director=True, phase="replan", repairs=1),
    "replan_2": task(director=True, phase="replan", repairs=2),
    "direct": task(director=True, phase="direct"),
    "direct_repair": task(director=True, phase="direct", repairs=1),
}


def attempt(ident, account_id, model, state, *, task_id="task", provider="codex", seconds=None):
    return {
        "id": ident,
        "task_id": task_id,
        "account_id": account_id,
        "phase": "build",
        "state": state,
        "model": model,
        "routing": {"provider": provider, "risk": "medium", "complexity": "moderate"},
        "result": {} if seconds is None else {"seconds": seconds},
    }


HISTORIES = {
    "none": [],
    "codex_failed": [attempt("failed-1", "codex", "gpt-6.1-sol", "failed")],
    "mixed": [
        attempt("gone-1", "codex", "gpt-6.1-sol", "model_unavailable", task_id="old"),
        attempt(
            "gone-2",
            "claude",
            "claude-haiku-5-5",
            "model_unavailable",
            task_id="old",
            provider="claude",
        ),
        *(
            attempt(f"{model}-{i}", "codex", model, "passed", task_id=f"old-{i}", seconds=s)
            for model, s in [("gpt-6-sol", 40), ("gpt-6.1-terra", 10)]
            for i in range(3)
        ),
    ],
}


def account_sets(base: Path):
    sets = {
        "codex_full": lambda b: [codex(b, catalog="full")],
        "codex_narrow": lambda b: [codex(b, catalog="narrow")],
        "codex_no_catalog": lambda b: [codex(b, catalog=None)],
        "claude": lambda b: [claude(b)],
        "claude_validated": lambda b: [claude(b, validated=VALIDATED)],
        "codex_and_claude": lambda b: [codex(b, catalog="full"), claude(b)],
        "narrow_and_validated": lambda b: [
            codex(b, catalog="narrow"),
            claude(b, validated=VALIDATED),
        ],
    }
    return {name: make(base / name) for name, make in sets.items()}


def auto_routes(base: Path, route=select_route, **kwargs):
    """{"accounts/task/history": {"account", "route"} | None} over the whole matrix."""
    out = {}
    for set_name, accounts in account_sets(base).items():
        for task_name, spec in TASKS.items():
            for history_name, attempts in HISTORIES.items():
                selected = route(dict(spec), accounts, attempts, **kwargs)
                out[f"{set_name}/{task_name}/{history_name}"] = (
                    None
                    if selected is None
                    else {"account": selected[0]["id"], "route": selected[1]}
                )
    return out


def golden_text(routes):
    """One entry per line, so a changed route shows up as a one-line diff."""
    lines = [f"{json.dumps(key)}: {encode(value)}" for key, value in sorted(routes.items())]
    return "{\n" + ",\n".join(lines) + "\n}\n"


# -- Auto -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [None, {}, {"codex": None, "claude": None}, {"codex": {"model": "gpt-6-astra"}, "claude": 1}],
    ids=["none", "empty", "both_auto", "malformed"],
)
def test_auto_routes_are_byte_identical_to_routing_before_overrides(tmp_path, override):
    golden = json.loads(GOLDEN.read_text())
    routes = auto_routes(tmp_path, override=override)
    assert set(routes) == set(golden)
    for key, expected in golden.items():
        assert encode(routes[key]) == encode(expected), key
    assert golden_text(routes) == GOLDEN.read_text()
    # The matrix reaches every Auto branch, including declining to route at all.
    chosen = [v["route"] for v in golden.values() if v]
    assert {r["action"] for r in chosen} == {
        "initial",
        "provider_defaults",
        "increase_effort",
        "switch_provider",
        "retry_after_cooldown",
        "director_diagnosis",
    }
    assert {r["capability_source"] for r in chosen} == {
        "advertised_cli_catalog",
        "validated_probe",
        "enrolled_model_provider_defaults",
    }
    assert any(v is None for v in golden.values())


def test_pinning_one_provider_leaves_the_other_on_auto(tmp_path):
    golden = json.loads(GOLDEN.read_text())
    codex_pinned = auto_routes(tmp_path / "a", override={"codex": CODEX_PIN})
    claude_pinned = auto_routes(tmp_path / "b", override={"claude": CLAUDE_PIN})
    for key, expected in golden.items():
        if key.startswith(("claude/", "claude_validated/")):
            assert encode(codex_pinned[key]) == encode(expected), key
        if key.startswith(("codex_full/", "codex_narrow/", "codex_no_catalog/")):
            assert encode(claude_pinned[key]) == encode(expected), key
    # In a mixed pool, wherever Codex still takes the work it is on its Auto route.
    for key, value in claude_pinned.items():
        if value and value["route"]["provider"] == "codex":
            assert encode(value) == encode(golden[key]), key


# -- Pins -----------------------------------------------------------------------------------


def test_a_codex_pin_runs_every_phase_on_exactly_that_model_and_effort(tmp_path):
    accounts = [codex(tmp_path, catalog="full")]
    for name, spec in TASKS.items():
        _, route = select_route(dict(spec), accounts, [], {"codex": CODEX_PIN})
        assert (route["model"], route["effort"]) == ("gpt-6-astra", "low"), name
        assert route["reason"] == "Owner override: gpt-6-astra at low", name
        assert route["capability_source"] == "owner_override", name
        assert route["action"] == (
            "director_diagnosis"
            if spec["phase"] == "replan"
            else "retry_after_cooldown"
            if spec.get("retries")
            else "owner_override"
        ), name
    # Without a fresh catalog Codex still cannot direct: a pin changes the model, not
    # which slot may take which phase.
    bare = [codex(tmp_path / "bare", catalog=None)]
    assert select_route(TASKS["plan_small"], bare, [], {"codex": CODEX_PIN}) is None
    _, build = select_route(TASKS["moderate"], bare, [], {"codex": CODEX_PIN})
    assert (build["model"], build["effort"]) == ("gpt-6-astra", "low")


def test_a_claude_pin_runs_builds_reviews_and_stand_in_director_phases_on_it(tmp_path):
    accounts = [claude(tmp_path, validated=VALIDATED)]
    for name, spec in TASKS.items():
        _, route = select_route(dict(spec), accounts, [], {"claude": CLAUDE_PIN})
        assert (route["model"], route["effort"]) == ("claude-opus-5-5", "max"), name
        assert route["reason"] == "Owner override: claude-opus-5-5 at max", name
        assert route["capability_source"] == "owner_override", name
    # Claude stands in as director only with probe-validated Fable 5.1, pinned or not.
    plain = [claude(tmp_path / "plain")]
    assert select_route(TASKS["plan_small"], plain, [], {"claude": CLAUDE_PIN}) is None
    _, build = select_route(TASKS["simple"], plain, [], {"claude": CLAUDE_PIN})
    assert (build["model"], build["effort"]) == ("claude-opus-5-5", "max")


def test_pins_keep_reviews_cross_provider_and_the_director_on_codex(tmp_path):
    accounts = [codex(tmp_path), claude(tmp_path)]
    pins = {"codex": CODEX_PIN, "claude": CLAUDE_PIN}
    chosen, route = select_route(TASKS["review_of_codex"], accounts, [], pins)
    assert chosen["provider"] == "claude" and route["model"] == "claude-opus-5-5"
    chosen, route = select_route(TASKS["review_of_claude"], accounts, [], pins)
    assert chosen["provider"] == "codex" and route["model"] == "gpt-6-astra"
    for name in ("plan_small", "plan_broad", "replan_2", "direct"):
        chosen, route = select_route(TASKS[name], accounts, [], pins)
        assert chosen["provider"] == "codex" and route["effort"] == "low", name
    # A provider that failed still loses preference on the second repair.
    failed = attempt("failed", "codex", "gpt-6-astra", "failed")
    chosen, route = select_route(TASKS["repair_2"], accounts, [failed], pins)
    assert chosen["provider"] == "claude" and route["action"] == "switch_provider"
    assert route["reason"] == "Owner override: claude-opus-5-5 at max"


def test_pinned_model_and_effort_reach_each_cli_and_claude_stays_tool_free(tmp_path):
    pins = {"codex": CODEX_PIN, "claude": CLAUDE_PIN}
    workspace = tmp_path / "workspace"
    a = codex(tmp_path)
    for phase in ("build", "review"):
        _, route = select_route({**TASKS["moderate"], "phase": phase}, [a], [], pins)
        argv = invocation({**a, **route}, workspace, phase)
        assert argv[argv.index("--model") + 1] == "gpt-6-astra"
        assert 'model_reasoning_effort="low"' in argv
    _, route = select_route(TASKS["plan_broad"], [a], [], pins)
    argv = invocation({**a, **route}, workspace, "plan")
    assert argv[argv.index("--model") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="low"' in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    b = claude(tmp_path)
    for phase in ("build", "review"):
        _, route = select_route({**TASKS["moderate"], "phase": phase}, [b], [], pins)
        argv = invocation({**b, **route}, workspace, phase)
        assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
        assert argv[argv.index("--effort") + 1] == "max"
        # A pin changes the model, never the tool policy of a Claude worker.
        assert argv[argv.index("--tools") + 1] == ""
        assert argv[argv.index("--max-turns") + 1] == "2"
        assert "--fallback-model" not in argv


def test_a_pinned_model_reported_unavailable_falls_back_to_auto_on_that_slot(tmp_path):
    retry = {**TASKS["moderate"], "retries": 1}
    first, second = claude(tmp_path), claude(tmp_path, ident="claude-2")
    gone = attempt("gone", "claude", "claude-opus-5-5", "model_unavailable", provider="claude")
    _, route = select_route(retry, [first], [gone], {"claude": CLAUDE_PIN})
    _, auto = select_route(retry, [first], [gone])
    assert (route["model"], route["effort"]) == (auto["model"], auto["effort"]) == ("sonnet", None)
    assert route["override_unavailable"] == CLAUDE_PIN
    assert route["reason"] == (
        "Owner override unavailable (claude-opus-5-5 at max); Auto: " + auto["reason"]
    )
    assert {k: v for k, v in route.items() if k not in {"reason", "override_unavailable"}} == {
        k: v for k, v in auto.items() if k != "reason"
    }
    # Only the slot that reported it falls back; another Claude slot keeps the pin.
    _, route = select_route(retry, [second], [gone], {"claude": CLAUDE_PIN})
    assert route["model"] == "claude-opus-5-5" and "override_unavailable" not in route
    # Codex the same way: Auto picks for the slot whose CLI refused the pin.
    c = codex(tmp_path)
    refused = attempt("refused", "codex", "gpt-6-astra", "model_unavailable")
    _, route = select_route(TASKS["moderate"], [c], [refused], {"codex": CODEX_PIN})
    assert (route["model"], route["effort"]) == ("gpt-6.1-sol", "medium")
    assert route["override_unavailable"] == CODEX_PIN


def fake_claude(tmp_path: Path, log: Path) -> Path:
    """A Claude CLI that is signed in but whose account cannot use the requested model."""
    fake = tmp_path / "fake-claude"
    fake.write_text(
        f"""#!{sys.executable}
import json, sys
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}}))
    sys.exit(0)
sys.stdin.read()
with open({str(log)!r}, "a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
model = sys.argv[sys.argv.index("--model") + 1]
print(json.dumps({{"type": "system", "subtype": "init", "model": model}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": True,
    "result": "There's an issue with the selected model (" + model + "). It may not exist "
    "or you may not have access to it. Run --model to pick a different model."}}))
sys.exit(1)
"""
    )
    fake.chmod(0o700)
    return fake


def test_cli_refusing_a_pinned_model_cools_down_then_the_slot_routes_on_auto(tmp_path):
    store, engine, project, _ = support.setup.__wrapped__(tmp_path)
    log = tmp_path / "claude-argv.jsonl"
    with store.transaction() as db:
        for account in store.rows("accounts", db):  # only the Claude slot takes this build
            data = {k: v for k, v in account.items() if k not in {"id", "rev"}}
            db.execute(
                "UPDATE accounts SET data=? WHERE id=?",
                (encode({**data, "enabled": False}), account["id"]),
            )
        slot = dict(
            name="Claude slot",
            provider="claude",
            executable=str(fake_claude(tmp_path, log)),
            config_home=str(tmp_path / "claude-home"),
            model="sonnet",
            version="fixture",
            enabled=True,
            health="ready",
            roles=["build", "review"],
            cooldown_until=0,
        )
        db.execute("INSERT INTO accounts(id,data) VALUES(?,?)", ("claude0", encode(slot)))
    client, headers = paired_client(store)
    body = {**CLAUDE_PIN, "provider": "claude", "revision": 0}
    assert client.post("/api/v1/control/routing", json=body, headers=headers).status_code == 200
    task_id = goal(store, engine, project)
    run_until(engine, store, lambda: store.get("tasks", task_id)["state"] == "cooldown")
    (first,) = store.rows("attempts")
    assert first["state"] == "model_unavailable"
    assert (first["model"], first["effort"]) == ("claude-opus-5-5", "max")
    assert first["routing"]["capability_source"] == "owner_override"
    argv = json.loads(log.read_text().splitlines()[0])
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--effort") + 1] == "max"
    # After the cooldown the same slot takes the task again, on Auto, and says why.
    with store.transaction() as db:
        cooling = store.get("tasks", task_id, db)
        store.update_task(db, cooling, "cooldown", cooling["reason"], cooldown_until=0)
        slot = store.get("accounts", "claude0", db)
        slot["cooldown_until"] = 0
        db.execute(
            "UPDATE accounts SET data=? WHERE id=?",
            (encode({k: v for k, v in slot.items() if k not in {"id", "rev"}}), "claude0"),
        )
    second = engine.claim()
    assert second["account_id"] == "claude0"
    assert (second["model"], second["effort"]) == ("sonnet", None)
    assert second["routing"]["override_unavailable"] == CLAUDE_PIN
    assert second["routing"]["action"] == "retry_after_cooldown"
    assert second["reason"].startswith(
        "Owner override unavailable (claude-opus-5-5 at max); Auto: "
    )
    # The owner's choice itself stands; only this slot's attempt fell back.
    assert client.get("/api/v1/overview").json()["routing_override"]["claude"] == CLAUDE_PIN


# -- Phone API ------------------------------------------------------------------------------


def test_options_list_the_advertised_codex_catalog_and_claude_choices(tmp_path):
    store, _, _, _ = support.setup.__wrapped__(tmp_path)
    write_catalog(tmp_path / "auth0", "narrow")  # slot0's catalog
    write_catalog(tmp_path / "auth1", "full")  # slot1's adds models and levels
    client, _ = paired_client(store)
    options = client.get("/api/v1/routing/options").json()
    assert options == {
        "codex": {
            "models": [
                {"slug": "gpt-6-luna", "efforts": ["low", "medium"]},
                {"slug": "gpt-6.1-sol", "efforts": ["low", "medium", "high"]},
                {"slug": "gpt-6-astra", "efforts": EVERY_EFFORT},
                {"slug": "gpt-6-sol", "efforts": ["low", "medium", "high", "xhigh"]},
                {"slug": "gpt-6.1-terra", "efforts": ["medium", "high", "xhigh"]},
            ]
        },
        "claude": {
            "models": [
                {"slug": slug, "efforts": EVERY_EFFORT}
                for slug in ("sonnet", "claude-opus-5-5", "claude-fable-5-1")
            ]
        },
        "override": {"codex": None, "claude": None},
    }
    for home in ("auth0", "auth1"):
        (tmp_path / home / "models_cache.json").unlink()
    assert client.get("/api/v1/routing/options").json()["codex"] == {"models": []}


def test_phone_pins_and_unpins_a_provider_through_the_control_api(tmp_path):
    store, engine, project, _ = support.setup.__wrapped__(tmp_path)
    write_catalog(tmp_path / "auth0", "narrow")
    client, headers = paired_client(store)

    def post(body, key, **extra):
        return client.post(
            "/api/v1/control/routing",
            json=body,
            headers={**headers, "Idempotency-Key": key, **extra},
        )

    def routing_events():
        events = client.get("/api/v1/overview").json()["events"]
        return [e["data"] for e in events if e["kind"] == "control.routing"]

    overview = client.get("/api/v1/overview").json()
    assert overview["routing_override"] == {"codex": None, "claude": None}
    revision = overview["revision"]
    pin = {"provider": "codex", "model": "gpt-6-astra", "effort": "high", "revision": revision}
    first = post(pin, "pin-1")
    assert first.status_code == 200
    pinned = {"model": "gpt-6-astra", "effort": "high"}
    assert first.json() == {"ok": True, "override": {"codex": pinned, "claude": None}}
    assert post(pin, "pin-1").json() == first.json()  # an idempotent replay changes nothing
    overview = client.get("/api/v1/overview").json()
    assert overview["routing_override"] == {"codex": pinned, "claude": None}
    assert overview["revision"] == revision + 1
    assert [(e["provider"], e["model"], e["effort"]) for e in routing_events()] == [
        ("codex", "gpt-6-astra", "high")
    ]
    revision += 1

    choice = {k: v for k, v in pin.items() if k != "revision"}
    choices = "Choose one of: gpt-6-luna, gpt-6.1-sol, gpt-6-astra."
    refusals = {
        "stale": ({**choice, "revision": revision - 1}, "Control view is stale"),
        "provider": (
            {**choice, "provider": "gemini"},
            "Arena routes work to Codex or Claude; choose one of them.",
        ),
        "model": ({**choice, "model": "gpt-9"}, f"Codex can't be pinned to gpt-9. {choices}"),
        "hidden": (
            {**choice, "model": "gpt-6-internal"},
            f"Codex can't be pinned to gpt-6-internal. {choices}",
        ),
        "no_known_effort": (
            {**choice, "model": "gpt-6-nano", "effort": "minimal"},
            f"Codex can't be pinned to gpt-6-nano. {choices}",
        ),
        "effort": (
            {**choice, "effort": "low"},
            "gpt-6-astra can't run at low effort. Choose one of: high, max.",
        ),
        "no_effort": (
            {**choice, "effort": None},
            "Choose an effort for gpt-6-astra: high, max.",
        ),
        "claude_model": (
            {"provider": "claude", "model": "claude-haiku-5-5", "effort": "low"},
            "Claude can't be pinned to claude-haiku-5-5. Choose one of: sonnet, "
            "claude-opus-5-5, claude-fable-5-1.",
        ),
        "claude_effort": (
            {"provider": "claude", "model": "sonnet", "effort": "fast"},
            "sonnet can't run at fast effort. Choose one of: low, medium, high, xhigh, max.",
        ),
    }
    for key, (body, detail) in refusals.items():
        r = post({"revision": revision, **body}, "refused-" + key)
        assert (r.status_code, r.json()["detail"]) == (409, detail), key
    assert post(pin, "csrf", **{"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/api/v1/overview").json()["revision"] == revision  # nothing changed

    # Builds now run on the pin; the route says so.
    goal(store, engine, project)
    claimed = engine.claim()
    assert (claimed["model"], claimed["effort"]) == ("gpt-6-astra", "high")
    assert claimed["reason"] == "Owner override: gpt-6-astra at high"
    assert claimed["routing"]["capability_source"] == "owner_override"

    both = {"codex": pinned, "claude": CLAUDE_PIN}
    r = post({**CLAUDE_PIN, "provider": "claude", "revision": revision}, "pin-2")
    assert r.json()["override"] == both
    revision += 1
    assert client.get("/api/v1/routing/options").json()["override"] == both
    assert arena_status(store)["routing_override"] == both  # what the director reads

    # A null model returns that provider to Auto; the other keeps its pin.
    r = post({"provider": "codex", "model": None, "revision": revision}, "auto-1")
    assert r.json()["override"] == {"codex": None, "claude": CLAUDE_PIN}
    revision += 1
    assert routing_events()[0] == {
        "schema": 1,
        "provider": "codex",
        "model": None,
        "effort": None,
    }
    assert arena_status(store)["routing_override"] == {"codex": None, "claude": CLAUDE_PIN}
    # Pins share the control revision with pause and resume.
    stale = client.post(
        "/api/v1/control/pause-dispatch",
        json={"revision": revision - 1},
        headers={**headers, "Idempotency-Key": "pause-stale"},
    )
    assert stale.status_code == 409

    # Without a catalog on the Mac, Codex cannot be pinned, and the owner is told why.
    (tmp_path / "auth0" / "models_cache.json").unlink()
    r = post({**choice, "revision": revision}, "no-catalog")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Codex can't be pinned right now: its model catalog on the Mac is missing or more "
        "than a week old. Run codex once on the Mac, then try again."
    )


def test_director_chat_turns_ignore_pins(tmp_path):
    store, engine, _, _ = support.setup.__wrapped__(tmp_path)
    with store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES('routing_override',?)",
            (encode({"codex": CODEX_PIN, "claude": CLAUDE_PIN}),),
        )
    director = conversation.Director(
        store, engine, projects_dir=tmp_path / "none", mac_activity=lambda: None
    )
    account = director._account()
    assert (account["model"], account["effort"]) == conversation.CHAT_MODELS["codex"]
