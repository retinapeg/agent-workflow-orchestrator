from __future__ import annotations

from pathlib import Path

import pytest

from agent_arena.config import ConfigError, load_config


@pytest.mark.parametrize("relative_path", ["team.example.toml", "configs/api.example.toml"])
def test_shipped_example_configs_parse(relative_path: str) -> None:
    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root / relative_path)
    assert set(config.modes) == {"hackathon", "engineering"}


def test_loads_exact_two_mode_profiles(arena_fixture: dict[str, Path]) -> None:
    config = load_config(arena_fixture["config"])
    assert set(config.modes) == {"hackathon", "engineering"}
    assert config.mode("hackathon").revision_rounds == 1
    assert config.mode("engineering").revision_rounds == 2


def test_default_provider_environment_preserves_posix_identity(
    arena_fixture: dict[str, Path],
) -> None:
    config = load_config(arena_fixture["config"])
    assert {"USER", "LOGNAME"} <= set(config.run.provider_env_passthrough)


def test_rejects_unknown_mode(arena_fixture: dict[str, Path]) -> None:
    config = load_config(arena_fixture["config"])
    with pytest.raises(ConfigError, match="exactly"):
        config.mode("fast-and-loose")


def test_rejects_unsafe_engineer_id(arena_fixture: dict[str, Path]) -> None:
    text = arena_fixture["config"].read_text(encoding="utf-8")
    text = text.replace("engineers.codex", 'engineers."../codex"')
    broken = arena_fixture["root"] / "broken.toml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid engineer id"):
        load_config(broken)


@pytest.mark.parametrize(
    ("old", "new", "expected_name"),
    [
        ("[run]\n", '[run]\nrequire_clean_source = "false"\n', "require_clean_source"),
        ("required = true", 'required = "false"', "required"),
        ("enabled = false", 'enabled = "false"', "judge.enabled"),
    ],
)
def test_rejects_non_boolean_values_for_boolean_fields(
    arena_fixture: dict[str, Path], old: str, new: str, expected_name: str
) -> None:
    text = arena_fixture["config"].read_text(encoding="utf-8").replace(old, new, 1)
    broken = arena_fixture["root"] / f"broken-{expected_name.replace('.', '-')}.toml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=expected_name.replace(".", r"\.")):
        load_config(broken)


def test_rejects_unknown_configuration_keys(arena_fixture: dict[str, Path]) -> None:
    text = arena_fixture["config"].read_text(encoding="utf-8")
    text = text.replace("max_workers = 2", "max_workers = 2\nmax_worker = 2")
    broken = arena_fixture["root"] / "unknown-key.toml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\[run\] contains unknown keys: \['max_worker'\]"):
        load_config(broken)


@pytest.mark.parametrize(
    ("insertion", "expected"),
    [
        ("trusted_overlay_destination = 7\n", "trusted_overlay_destination"),
        ('[judge]\nengineer = ["codex"]\n', "judge.engineer"),
    ],
)
def test_rejects_wrong_scalar_types(
    arena_fixture: dict[str, Path], insertion: str, expected: str
) -> None:
    text = arena_fixture["config"].read_text(encoding="utf-8")
    if insertion.startswith("[judge]"):
        text = text.replace("[judge]\n", insertion, 1)
    else:
        text = text.replace("[run]\n", "[run]\n" + insertion, 1)
    broken = arena_fixture["root"] / f"wrong-{expected.replace('.', '-')}.toml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=expected.replace(".", r"\.")):
        load_config(broken)
