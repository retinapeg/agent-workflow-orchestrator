from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_arena import cli
from agent_arena.config import ArenaConfig, load_config, with_model_overrides
from agent_arena.errors import ConfigError
from agent_arena.providers import create_provider


def provider_config(config_path: Path, codex_kind: str, claude_kind: str) -> ArenaConfig:
    config = load_config(config_path)
    return replace(
        config,
        engineers=(
            replace(config.engineers[0], kind=codex_kind, model="original-openai"),
            replace(config.engineers[1], kind=claude_kind, model="original-anthropic"),
        ),
    )


@pytest.mark.parametrize("codex_kind", ["codex_cli", "openai_api"])
@pytest.mark.parametrize("claude_kind", ["claude_cli", "anthropic_api"])
def test_overrides_match_provider_family_and_preserve_defaults(
    arena_fixture: dict[str, Path], codex_kind: str, claude_kind: str
) -> None:
    original = provider_config(arena_fixture["config"], codex_kind, claude_kind)
    original = replace(
        original,
        engineers=tuple(
            replace(engineer, engineer_id=f"custom-{index}")
            for index, engineer in enumerate(original.engineers)
        ),
    )
    assert with_model_overrides(original) is original
    changed = with_model_overrides(original, codex_model="new-openai")
    assert changed.engineers[0].model == "new-openai"
    assert changed.engineers[1] is original.engineers[1]
    assert original.engineers[0].model == "original-openai"
    both = with_model_overrides(original, codex_model="openai-choice", claude_model="claude-choice")
    assert [engineer.model for engineer in both.engineers] == ["openai-choice", "claude-choice"]
    assert [engineer.model for engineer in original.engineers] == [
        "original-openai",
        "original-anthropic",
    ]


@pytest.mark.parametrize("option", ["codex_model", "claude_model"])
def test_missing_provider_is_rejected_even_when_engineer_name_matches(
    arena_fixture: dict[str, Path], option: str
) -> None:
    config = load_config(arena_fixture["config"])
    with pytest.raises(ConfigError, match="requires one configured provider"):
        with_model_overrides(config, **{option: "model-choice"})


@pytest.mark.parametrize(
    ("option", "extra_kind"), [("codex_model", "openai_api"), ("claude_model", "anthropic_api")]
)
def test_ambiguous_provider_is_rejected(
    arena_fixture: dict[str, Path], option: str, extra_kind: str
) -> None:
    config = provider_config(arena_fixture["config"], "codex_cli", "claude_cli")
    config = replace(
        config,
        engineers=config.engineers
        + (replace(config.engineers[0], engineer_id="extra-engineer", kind=extra_kind),),
    )
    with pytest.raises(ConfigError, match="ambiguous"):
        with_model_overrides(config, **{option: "model-choice"})


@pytest.mark.parametrize("model", ["", " ", "model\nchoice", "model\x00choice"])
def test_invalid_model_override_is_rejected(arena_fixture: dict[str, Path], model: str) -> None:
    config = provider_config(arena_fixture["config"], "codex_cli", "claude_cli")
    with pytest.raises(ConfigError, match="nonempty model ID"):
        with_model_overrides(config, codex_model=model)


@pytest.mark.parametrize("command", ["hack", "engineer"])
@pytest.mark.parametrize(
    ("codex_kind", "claude_kind"),
    [("codex_cli", "claude_cli"), ("openai_api", "anthropic_api")],
)
def test_run_cli_records_effective_models_without_rewriting_config(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    codex_kind: str,
    claude_kind: str,
) -> None:
    config = provider_config(arena_fixture["config"], codex_kind, claude_kind)
    original_bytes = arena_fixture["config"].read_bytes()
    monkeypatch.setattr(cli, "load_config", lambda _: config)

    def offline_provider(engineer: Any, config: Any, runner: Any) -> Any:
        return create_provider(replace(engineer, kind="scripted"), config, runner)

    monkeypatch.setattr("agent_arena.orchestrator.create_provider", offline_provider)
    result = cli.main(
        [
            command,
            str(arena_fixture["task"]),
            "--repo",
            str(arena_fixture["source"]),
            "--config",
            str(arena_fixture["config"]),
            "--codex-model",
            "openai-choice",
            "--claude-model",
            "claude-choice",
            "--json",
        ]
    )
    assert result == 0
    run_dir = Path(json.loads(capsys.readouterr().out)["run_dir"])
    effective = json.loads((run_dir / "effective-config.json").read_text())["config"]
    assert {engineer["engineer_id"]: engineer["model"] for engineer in effective["engineers"]} == {
        "codex": "openai-choice",
        "claude": "claude-choice",
    }
    for engineer_id, expected in (("codex", "openai-choice"), ("claude", "claude-choice")):
        record = json.loads((run_dir / f"engineers/{engineer_id}/initial/result.json").read_text())
        assert record["model"] == expected
    workspaces = json.loads((run_dir / "workspaces.json").read_text())
    assert {record["engineer_id"] for record in workspaces["engineers"]} == {
        "codex",
        "claude",
    }
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert "workspaces.json" in {item["path"] for item in manifest["files"]}
    assert arena_fixture["config"].read_bytes() == original_bytes
    assert config.engineers[0].model == "original-openai"


def test_doctor_receives_model_overrides(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = provider_config(arena_fixture["config"], "codex_cli", "claude_cli")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    captured: list[ArenaConfig] = []

    class Doctor:
        def __init__(self, effective: ArenaConfig, mode: str) -> None:
            captured.append(effective)

        def doctor(self) -> dict[str, Any]:
            return {"ok": True, "engineers": {}}

    monkeypatch.setattr(cli, "ArenaOrchestrator", Doctor)
    assert (
        cli.main(["doctor", "--codex-model", "chosen-openai", "--claude-model", "chosen-claude"])
        == 0
    )
    assert [engineer.model for engineer in captured[0].engineers] == [
        "chosen-openai",
        "chosen-claude",
    ]


def test_invalid_override_fails_before_orchestrator_construction(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda _: load_config(arena_fixture["config"]))

    def unexpected_orchestrator(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid overrides must not construct an orchestrator")

    monkeypatch.setattr(cli, "ArenaOrchestrator", unexpected_orchestrator)
    assert cli.main(["hack", "task", "--codex-model", "choice"]) == 2
    assert "requires one configured provider" in capsys.readouterr().err
