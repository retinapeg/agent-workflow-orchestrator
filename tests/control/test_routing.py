"""Routing policy uses synthetic catalog metadata; no provider calls here."""

import json
from pathlib import Path

from agent_arena.control.routing import select_route
from agent_arena.control.worker import invocation


def account(tmp_path, provider="codex"):
    home = tmp_path / provider
    home.mkdir(exist_ok=True)
    if provider == "codex":
        (home / "models_cache.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": model,
                            "visibility": "list",
                            "supported_reasoning_levels": [{"effort": e} for e in levels],
                        }
                        for model, levels in [
                            ("gpt-6-luna", ["low", "medium"]),
                            ("gpt-6.1-sol", ["low", "medium", "high"]),
                            ("gpt-6-astra", ["high", "max"]),
                        ]
                    ]
                }
            )
        )
    return {
        "id": provider,
        "provider": provider,
        "config_home": str(home),
        "model": "gpt-6.1-sol" if provider == "codex" else "sonnet",
        "executable": provider,
        "effort": "medium",
    }


def task(**overrides):
    return {
        "id": "task",
        "title": "Improve description",
        "scope": ["manifest.json"],
        "phase": "build",
        "repairs": 0,
        **overrides,
    }


def test_small_work_uses_advertised_light_model_and_legal_effort(tmp_path):
    a = account(tmp_path)
    _, route = select_route(task(), [a], [])
    assert (route["model"], route["effort"]) == ("gpt-6-luna", "low")
    _, complex_route = select_route(task(complexity="complex", risk="high"), [a], [])
    assert (complex_route["model"], complex_route["effort"]) == ("gpt-6-astra", "high")
    _, raised = select_route(task(repairs=2), [a], [])
    assert raised["effort"] == "max"  # xhigh is not supported by this fixture model.


def test_claude_effort_is_not_invented_from_generic_cli_help(tmp_path):
    a = account(tmp_path, "claude")
    _, route = select_route(task(), [a], [])
    assert route["model"] == "sonnet" and route["effort"] is None
    argv = invocation({**a, **route}, Path(tmp_path), "build")
    assert "--effort" not in argv and "--fallback-model" not in argv


def test_repeated_failed_provider_loses_preference(tmp_path):
    codex, claude = account(tmp_path), account(tmp_path, "claude")
    failure = {
        "id": "failed",
        "task_id": "task",
        "account_id": "codex",
        "phase": "build",
        "state": "failed",
        "model": "gpt-6.1-sol",
        "routing": {"provider": "codex", "risk": "medium"},
    }
    chosen, route = select_route(task(repairs=2), [codex, claude], [failure])
    assert chosen["provider"] == "claude" and route["action"] == "switch_provider"
    assert route["failure_attempt"] == "failed"


def test_successful_measured_outcomes_can_replace_nominal_speed_preference(tmp_path):
    a = account(tmp_path)
    history = [
        {
            "id": f"{model}-{i}",
            "task_id": f"old-{i}",
            "account_id": "codex",
            "phase": "build",
            "state": "passed",
            "model": model,
            "routing": {"risk": "low", "complexity": "simple"},
            "result": {"seconds": seconds},
        }
        for model, seconds in [("gpt-6-luna", 40), ("gpt-6.1-sol", 10)]
        for i in range(3)
    ]
    _, route = select_route(task(), [a], history)
    assert route["model"] == "gpt-6.1-sol" and route["median_seconds"] == 10
    assert route["history_samples"] == 3


def test_director_model_is_not_silently_substituted(tmp_path):
    a = account(tmp_path)
    chosen, route = select_route(task(director=True, phase="plan"), [a], [])
    assert route["model"] == "gpt-6-astra" and route["effort"] == "max"
    (Path(a["config_home"]) / "models_cache.json").unlink()
    assert select_route(task(director=True, phase="plan"), [a], []) is None


def test_validated_fable_slot_stands_in_as_director_only_without_codex(tmp_path):
    codex, claude = account(tmp_path), account(tmp_path, "claude")
    director = task(phase="plan", director=True)
    assert select_route(director, [claude], []) is None  # No validated capability: no guess.
    claude["model_capabilities"] = {"claude-fable-5-1": ["max"]}
    chosen, route = select_route(director, [codex, claude], [])
    assert chosen is codex and (route["model"], route["effort"]) == ("gpt-6-astra", "max")
    chosen, route = select_route(director, [claude], [])
    assert chosen is claude and (route["model"], route["effort"]) == ("claude-fable-5-1", "max")
    assert route["capability_source"] == "validated_probe"
    argv = invocation({**claude, **route}, Path(tmp_path), "plan")
    assert argv[argv.index("--model") + 1] == "claude-fable-5-1"
    assert argv[argv.index("--effort") + 1] == "max"
    _, build = select_route(task(), [claude], [])
    assert (build["model"], build["effort"]) == ("sonnet", None)
