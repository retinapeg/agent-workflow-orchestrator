from __future__ import annotations

import json
import shutil
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_arena.config import load_config
from agent_arena.errors import ProviderError
from agent_arena.models import AgentRequest, Phase, ProcessResult
from agent_arena.process import ProcessRunner
from agent_arena.prompting import JUDGE_SCHEMA, REVIEW_SCHEMA
from agent_arena.providers.api import (
    API_SYSTEM,
    IMPLEMENTATION_SCHEMA,
    AnthropicAPIProvider,
    OpenAIAPIProvider,
    _workspace_snapshot,
    apply_file_operations,
    validate_response_schema,
)
from agent_arena.providers.cli import ClaudeCLIProvider, CodexCLIProvider, _read_last_message


def request(workspace: Path, phase: Phase = Phase.JUDGE, **changes: Any) -> AgentRequest:
    result = AgentRequest(
        engineer_id="codex",
        phase=phase,
        model="test-model",
        prompt="Assess the candidate.",
        workspace=workspace,
        timeout_seconds=3,
        max_output_bytes=100000,
        max_output_tokens=1000,
        max_cost_usd=None,
        response_schema=JUDGE_SCHEMA,
    )
    return replace(result, **changes)


def setup_provider(arena_fixture: dict[str, Path], provider_class: Any) -> Any:
    config = load_config(arena_fixture["config"])
    engineer = replace(
        config.engineers[0],
        model="test-model",
        options={"allow_sdk_timeout_only": True},
    )
    return provider_class(engineer, config, ProcessRunner())


def mock_api(
    monkeypatch: pytest.MonkeyPatch, kind: str, text: str, **fields: Any
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    data = {"id": "response-1", "model": "actual-model", "usage": {"output_tokens": 12}}
    data.update(fields)

    class Response(SimpleNamespace):
        def model_dump(self, **kwargs: Any) -> dict[str, Any]:
            return {**data, "text": text}

    response = Response(
        **{key: value for key, value in data.items() if key != "usage"},
        usage=SimpleNamespace(model_dump=lambda: data["usage"]),
        output_text=text,
        content=[SimpleNamespace(type="text", text=text)],
    )

    def create(**kwargs: Any) -> Response:
        captured.update(kwargs)
        return response

    def client(**kwargs: Any) -> Any:
        return SimpleNamespace(
            responses=SimpleNamespace(create=create), messages=SimpleNamespace(create=create)
        )

    monkeypatch.setitem(
        sys.modules,
        kind,
        SimpleNamespace(**{"OpenAI" if kind == "openai" else "Anthropic": client}),
    )
    return captured


@pytest.mark.parametrize(
    ("provider_class", "kind", "completion"),
    [
        (OpenAIAPIProvider, "openai", {"status": "completed"}),
        (AnthropicAPIProvider, "anthropic", {"stop_reason": "end_turn"}),
    ],
)
def test_api_persists_exact_schema_prompt_and_failed_response(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_class: Any,
    kind: str,
    completion: dict[str, str],
) -> None:
    provider = setup_provider(arena_fixture, provider_class)
    captured = mock_api(monkeypatch, kind, "malformed paid output", **completion)
    artifacts = tmp_path / "api-audit"
    result = provider.run(request(arena_fixture["source"]), artifacts)
    assert not result.ok
    assert result.usage == {"output_tokens": 12}
    actual_input = captured.get("input") or captured["messages"][0]["content"]
    assert (artifacts / "api-input.txt").read_text() == actual_input
    assert json.loads((artifacts / "api-request.json").read_text()) == captured
    assert (artifacts / "api-system.txt").read_text() == API_SYSTEM
    assert '"required": ["winner", "reason", "confidence"]' in actual_input
    assert "calculator.py" in actual_input  # review/judge can inspect baseline context too
    metadata = json.loads((artifacts / "response-metadata.json").read_text())
    assert metadata["model"] == "actual-model"
    assert metadata["usage"] == result.usage
    assert "malformed paid output" in (artifacts / "raw-response.txt").read_text()


@pytest.mark.parametrize("provider_class", [OpenAIAPIProvider, AnthropicAPIProvider])
def test_api_refuses_unsupported_cost_and_requires_timeout_opt_in(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    provider_class: Any,
) -> None:
    provider = setup_provider(arena_fixture, provider_class)
    with pytest.raises(ProviderError, match="max_cost_usd is unsupported"):
        provider.run(request(arena_fixture["source"], max_cost_usd="0"), tmp_path / "no-call")
    provider.engineer = replace(provider.engineer, max_cost_usd="1")
    with pytest.raises(ProviderError, match="max_cost_usd is unsupported"):
        provider.preflight()
    provider.engineer = replace(provider.engineer, max_cost_usd=None, options={})
    with pytest.raises(ProviderError, match="allow_sdk_timeout_only"):
        provider.preflight()
    with pytest.raises(ProviderError, match="allow_sdk_timeout_only"):
        provider.run(request(arena_fixture["source"]), tmp_path / "no-call")


def test_api_rejects_expanded_prompt_before_call(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
) -> None:
    provider = setup_provider(arena_fixture, OpenAIAPIProvider)
    provider.config = replace(
        provider.config,
        limits=replace(
            provider.config.limits,
            max_prompt_bytes=len(API_SYSTEM.encode()) + 30,
        ),
    )
    with pytest.raises(ProviderError, match="expanded API input"):
        provider.run(request(arena_fixture["source"]), tmp_path / "no-call")


@pytest.mark.parametrize(
    ("status", "payload", "succeeds"),
    [
        ("completed", {"winner": "candidate-a", "reason": "clear", "confidence": 0.8}, True),
        ("incomplete", {"winner": "candidate-a", "reason": "clear", "confidence": 0.8}, False),
        ("completed", {"winner": "candidate-a"}, False),
    ],
)
def test_api_validates_schema_and_completion_before_success(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    payload: dict[str, Any],
    succeeds: bool,
) -> None:
    provider = setup_provider(arena_fixture, OpenAIAPIProvider)
    mock_api(monkeypatch, "openai", json.dumps(payload), status=status)
    result = provider.run(request(arena_fixture["source"]), tmp_path / "response")
    assert result.ok is succeeds
    assert result.usage["output_tokens"] == 12


def test_oversized_api_raw_response_is_bounded_and_usage_retained(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = setup_provider(arena_fixture, OpenAIAPIProvider)
    provider.config = replace(
        provider.config,
        limits=replace(
            provider.config.limits,
            max_api_response_bytes=300,
        ),
    )
    mock_api(monkeypatch, "openai", "x" * 1000, status="completed")
    artifacts = tmp_path / "bounded"
    result = provider.run(request(arena_fixture["source"]), artifacts)
    assert not result.ok
    assert result.usage["output_tokens"] == 12
    assert (artifacts / "raw-response.txt").stat().st_size == 300
    assert json.loads((artifacts / "response-metadata.json").read_text())["raw_response_truncated"]


@pytest.mark.parametrize(
    "payload",
    [
        {"winner": "candidate-a", "reason": "ok", "confidence": True},
        {"winner": "candidate-a", "reason": "ok", "confidence": float("nan")},
        {"winner": "candidate-a", "reason": "ok", "confidence": 1.1},
        {"winner": "candidate-a", "confidence": 0.5},
        {"winner": "candidate-a", "reason": "ok", "confidence": 0.5, "extra": True},
    ],
)
def test_schema_rejects_invalid_judge_values(payload: dict[str, Any]) -> None:
    with pytest.raises(ProviderError, match="schema"):
        validate_response_schema(payload, JUDGE_SCHEMA)


def test_schema_checks_nested_findings() -> None:
    with pytest.raises(ProviderError, match="missing fields"):
        validate_response_schema(
            {"target": "claude", "summary": "x", "findings": [{}], "attack_tests": []},
            REVIEW_SCHEMA,
        )


def test_file_updates_preserve_executable_mode(tmp_path: Path) -> None:
    executable = tmp_path / "start.sh"
    executable.write_text("old")
    executable.chmod(0o755)
    apply_file_operations(
        tmp_path,
        [{"op": "write", "path": "start.sh", "content": "new"}],
        max_operations=1,
        max_file_bytes=100,
        max_total_bytes=100,
    )
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755


class FakeRunner:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout

    def run(self, argv: list[str], **kwargs: Any) -> ProcessResult:
        return ProcessResult(
            argv=tuple(argv),
            cwd=str(kwargs["cwd"]),
            exit_code=0,
            timed_out=False,
            duration_seconds=0.01,
            stdout=self.stdout,
            stderr="",
            stdout_total_bytes=len(self.stdout.encode()),
            stderr_total_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )


class AuthStatusRunner:
    def __init__(
        self,
        stdout: str,
        *,
        exit_code: int = 0,
        timed_out: bool = False,
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.stdout_truncated = stdout_truncated
        self.stderr_truncated = stderr_truncated
        self.argv: tuple[str, ...] | None = None
        self.kwargs: dict[str, Any] = {}

    def run(self, argv: list[str], **kwargs: Any) -> ProcessResult:
        self.argv = tuple(argv)
        self.kwargs = kwargs
        return ProcessResult(
            argv=tuple(argv),
            cwd=str(kwargs["cwd"]),
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            duration_seconds=0.01,
            stdout=self.stdout,
            stderr="account-bearing output must not reach an exception",
            stdout_total_bytes=len(self.stdout.encode()),
            stderr_total_bytes=52,
            stdout_truncated=self.stdout_truncated,
            stderr_truncated=self.stderr_truncated,
        )


@pytest.mark.parametrize(
    ("provider_class", "kind", "executable", "arguments", "stdout"),
    [
        (CodexCLIProvider, "codex_cli", "custom-codex", ("login", "status"), ""),
        (
            ClaudeCLIProvider,
            "claude_cli",
            "custom-claude",
            ("auth", "status"),
            json.dumps({"loggedIn": True, "email": "private@example.test"}),
        ),
    ],
)
def test_cli_preflight_checks_auth_with_sanitized_provider_environment(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    provider_class: Any,
    kind: str,
    executable: str,
    arguments: tuple[str, ...],
    stdout: str,
) -> None:
    config = load_config(arena_fixture["config"])
    engineer = replace(
        config.engineers[0],
        kind=kind,
        timeout_seconds=17,
        options={"executable": executable},
    )
    runner = AuthStatusRunner(stdout)
    provider = provider_class(engineer, config, runner)
    monkeypatch.setenv("PATH", "/provider/bin")
    monkeypatch.setenv("HOME", "/private/home")
    monkeypatch.setenv("USER", "arena-user")
    monkeypatch.setenv("LOGNAME", "arena-logname")
    monkeypatch.setenv("UNSAFE_SECRET", "never-forward-this")

    def resolve(command: str, mode: int = 1, path: str | None = None) -> str | None:
        assert command == executable
        assert path == "/provider/bin"
        return f"/provider/bin/{command}"

    monkeypatch.setattr(shutil, "which", resolve)
    result = provider.preflight()

    assert runner.argv == (f"/provider/bin/{executable}", *arguments)
    assert runner.kwargs["cwd"] == config.path.parent
    assert runner.kwargs["timeout_seconds"] == 17
    assert runner.kwargs["max_output_bytes"] == 65_536
    assert runner.kwargs["env"]["USER"] == "arena-user"
    assert runner.kwargs["env"]["LOGNAME"] == "arena-logname"
    assert "UNSAFE_SECRET" not in runner.kwargs["env"]
    assert result["authentication"] == "authenticated"
    assert "private@example.test" not in json.dumps(result)


@pytest.mark.parametrize(
    ("provider_class", "kind", "executable", "stdout"),
    [
        (CodexCLIProvider, "codex_cli", "custom-codex", "private@example.test"),
        (
            ClaudeCLIProvider,
            "claude_cli",
            "custom-claude",
            json.dumps({"loggedIn": False, "email": "private@example.test"}),
        ),
    ],
)
def test_cli_preflight_fails_closed_without_leaking_auth_output(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    provider_class: Any,
    kind: str,
    executable: str,
    stdout: str,
) -> None:
    config = load_config(arena_fixture["config"])
    engineer = replace(config.engineers[0], kind=kind, options={"executable": executable})
    provider = provider_class(engineer, config, AuthStatusRunner(stdout, exit_code=1))
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: f"/bin/{executable}")

    with pytest.raises(ProviderError, match="not authenticated") as failure:
        provider.preflight()
    assert "private@example.test" not in str(failure.value)


@pytest.mark.parametrize(
    "stdout",
    ["not-json", "[]", json.dumps({"loggedIn": False})],
)
def test_claude_preflight_requires_explicit_logged_in_json(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    config = load_config(arena_fixture["config"])
    engineer = replace(config.engineers[0], kind="claude_cli", options={"executable": "claude"})
    provider = ClaudeCLIProvider(engineer, config, AuthStatusRunner(stdout))
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: "/bin/claude")

    with pytest.raises(ProviderError, match="authentication|confirm"):
        provider.preflight()


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (AuthStatusRunner("", timed_out=True), "timed out"),
        (AuthStatusRunner("", stdout_truncated=True), "byte limit"),
        (AuthStatusRunner("", stderr_truncated=True), "byte limit"),
    ],
)
def test_codex_preflight_rejects_timeout_or_truncated_status(
    arena_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    runner: AuthStatusRunner,
    message: str,
) -> None:
    config = load_config(arena_fixture["config"])
    engineer = replace(config.engineers[0], kind="codex_cli", options={})
    provider = CodexCLIProvider(engineer, config, runner)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: "/bin/codex")

    with pytest.raises(ProviderError, match=message):
        provider.preflight()


@pytest.mark.parametrize("terminal", ["", '{"type":"turn.failed"}', '{"type":"error"}'])
def test_codex_requires_successful_terminal_event(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    terminal: str,
) -> None:
    provider = setup_provider(arena_fixture, CodexCLIProvider)
    provider.runner = FakeRunner(
        '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n' + terminal
    )
    result = provider.run(request(arena_fixture["source"], response_schema=None), tmp_path)
    assert not result.ok


def test_codex_rejects_any_failure_even_with_completed_event(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
) -> None:
    provider = setup_provider(arena_fixture, CodexCLIProvider)
    provider.runner = FakeRunner(
        '{"type":"error"}\n{"type":"turn.completed"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}'
    )
    assert not provider.run(request(arena_fixture["source"], response_schema=None), tmp_path).ok


def test_codex_last_message_rejects_oversize_and_symlinks(tmp_path: Path) -> None:
    message = tmp_path / "message"
    message.write_bytes(b"x" * 101)
    with pytest.raises(ProviderError, match="byte limit"):
        _read_last_message(message, 100)
    link = tmp_path / "link"
    link.symlink_to(message)
    with pytest.raises(ProviderError, match="safely opened"):
        _read_last_message(link, 100)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "text",
        {"type": "result", "subtype": "error_max_budget_usd", "result": "done"},
        {"type": "result", "subtype": "success", "result": None},
        {"type": "result", "subtype": "success", "result": "ok", "total_cost_usd": "NaN"},
    ],
)
def test_claude_rejects_invalid_envelope(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    payload: Any,
) -> None:
    provider = setup_provider(arena_fixture, ClaudeCLIProvider)
    provider.runner = FakeRunner(json.dumps(payload))
    assert not provider.run(request(arena_fixture["source"], response_schema=None), tmp_path).ok


def test_claude_null_structured_output_falls_back_to_result(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
) -> None:
    provider = setup_provider(arena_fixture, ClaudeCLIProvider)
    provider.runner = FakeRunner(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "structured_output": None,
                "result": "done",
            }
        )
    )
    result = provider.run(request(arena_fixture["source"], response_schema=None), tmp_path)
    assert result.ok and result.text == "done"


@pytest.mark.parametrize("phase", [Phase.IMPLEMENT, Phase.REVISE])
def test_api_coding_schema_is_always_sent_and_valid_operations_work(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: Phase,
) -> None:
    provider = setup_provider(arena_fixture, OpenAIAPIProvider)
    captured = mock_api(
        monkeypatch,
        "openai",
        json.dumps(
            {
                "summary": "Add a small file",
                "operations": [{"op": "write", "path": "new.txt", "content": "verified"}],
            }
        ),
        status="completed",
    )
    result = provider.run(
        request(arena_fixture["source"], phase=phase, response_schema=None),
        tmp_path / "coding",
    )
    assert result.ok
    assert (arena_fixture["source"] / "new.txt").read_text() == "verified"
    sent_schema = captured["input"].split("AUTHORITATIVE RESPONSE JSON SCHEMA:\n", 1)[1]
    assert json.loads(sent_schema) == IMPLEMENTATION_SCHEMA


@pytest.mark.parametrize(
    "payload",
    [
        {"operations": []},
        {"summary": None, "operations": []},
        {"summary": "ok", "operations": [], "shell": "echo escaped"},
        {"summary": "ok", "operations": [{"op": "delete", "path": "calculator.py", "content": ""}]},
        {"summary": "ok", "operations": [{"op": "write", "path": "calculator.py"}]},
        {
            "summary": "ok",
            "operations": [
                {"op": "write", "path": "calculator.py", "content": "changed", "mode": "0777"},
            ],
        },
    ],
)
def test_api_coding_rejects_nonexact_payload_before_mutation(
    arena_fixture: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    provider = setup_provider(arena_fixture, OpenAIAPIProvider)
    before = (arena_fixture["source"] / "calculator.py").read_bytes()
    mock_api(monkeypatch, "openai", json.dumps(payload), status="completed")
    result = provider.run(
        request(arena_fixture["source"], phase=Phase.IMPLEMENT, response_schema=None),
        tmp_path,
    )
    assert not result.ok
    assert (arena_fixture["source"] / "calculator.py").read_bytes() == before


def test_file_operations_reject_extra_fields_even_without_provider(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="unexpected fields"):
        apply_file_operations(
            tmp_path,
            [{"op": "delete", "path": "missing.txt", "content": "extra"}],
            max_operations=1,
            max_file_bytes=100,
            max_total_bytes=100,
        )


def test_api_snapshot_caps_file_listing(arena_fixture: dict[str, Path]) -> None:
    workspace = arena_fixture["source"]
    for index in range(100):
        (workspace / f"long-untracked-filename-{index:03d}.txt").write_text("x")
    with pytest.raises(ProviderError, match="file list exceeds"):
        _workspace_snapshot(workspace, max_bytes=1000, max_file_bytes=100)


def test_api_snapshot_caps_omission_names_within_context_budget(
    arena_fixture: dict[str, Path],
) -> None:
    workspace = arena_fixture["source"]
    for index in range(70):
        (workspace / f"f{index:03d}").write_text("too large")
    snapshot = _workspace_snapshot(workspace, max_bytes=1000, max_file_bytes=1)
    assert len(snapshot.encode("utf-8")) <= 1000
    assert "OMITTED FILE:" in snapshot
    assert "CONTEXT LIMIT REACHED" in snapshot
