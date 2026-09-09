from __future__ import annotations

import json
import os
import shutil
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..errors import ProviderError
from ..models import AgentRequest, AgentResult, Phase, ProcessResult
from ..process import sanitized_environment
from .api import extract_json_object, validate_response_schema
from .base import Provider

_AUTH_STATUS_TIMEOUT_SECONDS = 30
_AUTH_STATUS_MAX_OUTPUT_BYTES = 65_536


def _resolved_executable(executable: str, env: dict[str, str], label: str) -> str:
    resolved = shutil.which(executable, path=env.get("PATH"))
    if not resolved:
        raise ProviderError(
            f"{label} CLI executable not found in the provider environment: {executable}"
        )
    return resolved


def _auth_status_process(
    provider: Provider,
    executable: str,
    arguments: list[str],
    label: str,
) -> tuple[str, ProcessResult]:
    """Run a local, non-model auth probe without retaining credential-adjacent output."""

    env = sanitized_environment(provider.config.run.provider_env_passthrough)
    resolved = _resolved_executable(executable, env, label)
    configured_timeout = (
        provider.engineer.timeout_seconds or provider.config.limits.provider_timeout_seconds
    )
    process = provider.runner.run(
        [resolved, *arguments],
        cwd=provider.config.path.parent,
        timeout_seconds=min(
            _AUTH_STATUS_TIMEOUT_SECONDS,
            configured_timeout,
            provider.config.run.max_run_seconds,
        ),
        max_output_bytes=min(
            _AUTH_STATUS_MAX_OUTPUT_BYTES, provider.config.limits.max_output_bytes
        ),
        env=env,
    )
    if process.timed_out:
        raise ProviderError(f"{label} CLI authentication check timed out")
    if process.stdout_truncated or process.stderr_truncated:
        raise ProviderError(f"{label} CLI authentication check output exceeded its byte limit")
    if process.exit_code != 0:
        raise ProviderError(
            f"{label} CLI is not authenticated in the configured provider environment"
        )
    return resolved, process


def _last_codex_message(jsonl: str) -> tuple[str, dict[str, Any], str | None]:
    text = ""
    usage: dict[str, Any] = {}
    completed = False
    failure: str | None = None
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") in {"turn.failed", "error"}:
            failure = "Codex emitted a failure/error event"
        if event.get("type") == "turn.started":
            completed = False
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                text = item["text"]
        if event.get("type") == "turn.completed":
            completed = True
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
    if not completed:
        failure = failure or "Codex returned no successful terminal turn.completed event"
    return text, usage, failure


def _read_last_message(path: Path, limit: int) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProviderError("Codex last-message artifact could not be safely opened") from exc
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ProviderError("Codex last-message artifact must be a regular file")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ProviderError("Codex last-message artifact exceeds the configured byte limit")
    return raw.decode("utf-8", errors="replace")


class CodexCLIProvider(Provider):
    def preflight(self) -> dict[str, Any]:
        executable = str(self.engineer.options.get("executable", "codex"))
        resolved, _ = _auth_status_process(self, executable, ["login", "status"], "Codex")
        return {
            "kind": self.engineer.kind,
            "executable": resolved,
            "model": self.engineer.model,
            "authentication": "authenticated",
            "authentication_check": "codex login status",
            "budget_enforcement": "timeout and observed token usage; no hard CLI dollar cap",
            "token_limit_enforcement": "observed only; max_output_tokens is not a hard CLI cap",
            "sandbox": "workspace-write for implementation/revision, read-only otherwise",
        }

    def run(self, request: AgentRequest, artifact_dir: Path) -> AgentResult:
        self._check_prompt(request)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        executable = str(self.engineer.options.get("executable", "codex"))
        final_message = artifact_dir / "last_message.txt"
        sandbox = (
            "workspace-write" if request.phase in {Phase.IMPLEMENT, Phase.REVISE} else "read-only"
        )
        argv = [
            executable,
            "exec",
            "--cd",
            str(request.workspace),
            "--sandbox",
            sandbox,
            "-c",
            'approval_policy="never"',
            "--ignore-user-config",
            "--ephemeral",
            "--json",
            "--output-last-message",
            str(final_message),
        ]
        if request.model:
            argv.extend(["--model", request.model])
        if request.response_schema:
            schema_path = artifact_dir / "response_schema.json"
            schema_path.write_text(
                json.dumps(request.response_schema, indent=2, sort_keys=True), encoding="utf-8"
            )
            argv.extend(["--output-schema", str(schema_path)])
        argv.append("-")
        env = sanitized_environment(self.config.run.provider_env_passthrough)
        process = self.runner.run(
            argv,
            cwd=request.workspace,
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            stdin_text=request.prompt,
            env=env,
        )
        parsed_text, usage, error = _last_codex_message(process.stdout)
        try:
            message = _read_last_message(final_message, self.config.limits.max_api_response_bytes)
            if message is not None:
                parsed_text = message
            if request.response_schema and not error:
                validate_response_schema(extract_json_object(parsed_text), request.response_schema)
        except ProviderError as exc:
            error = str(exc)
        if process.stdout_truncated:
            error = "Codex event stream exceeded the capture limit; terminal state is unverifiable"
        if process.timed_out:
            error = "Codex CLI invocation timed out"
        elif process.exit_code != 0:
            error = f"Codex CLI exited with status {process.exit_code}"
        elif not parsed_text.strip():
            error = "Codex CLI returned no final agent message"
        status = "completed" if process.ok and parsed_text.strip() and not error else "failed"
        return AgentResult(
            engineer_id=request.engineer_id,
            phase=request.phase,
            provider_kind=self.engineer.kind,
            model=request.model,
            status=status,
            text=parsed_text,
            usage=usage,
            process=process,
            error=error,
        )


class ClaudeCLIProvider(Provider):
    def preflight(self) -> dict[str, Any]:
        executable = str(self.engineer.options.get("executable", "claude"))
        resolved, process = _auth_status_process(self, executable, ["auth", "status"], "Claude")
        try:
            status = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "Claude CLI authentication check did not return valid JSON"
            ) from exc
        if not isinstance(status, dict) or status.get("loggedIn") is not True:
            raise ProviderError(
                "Claude CLI did not confirm authentication in the configured provider environment"
            )
        return {
            "kind": self.engineer.kind,
            "executable": resolved,
            "model": self.engineer.model,
            "authentication": "authenticated",
            "authentication_check": "claude auth status",
            "budget_enforcement": "Claude --max-budget-usd plus parent timeout",
            "token_limit_enforcement": "observed only; max_output_tokens is not a hard CLI cap",
            "sandbox": "safe-mode and restricted file tools; no Bash by default",
        }

    def run(self, request: AgentRequest, artifact_dir: Path) -> AgentResult:
        self._check_prompt(request)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        executable = str(self.engineer.options.get("executable", "claude"))
        writable = request.phase in {Phase.IMPLEMENT, Phase.REVISE}
        tools = "Read,Glob,Grep,Edit,Write" if writable else "Read,Glob,Grep"
        argv = [
            executable,
            "-p",
            "--safe-mode",
            "--restricted",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            tools,
            "--allowedTools",
            tools,
            "--permission-mode",
            "dontAsk",
            "--output-format",
            "json",
            "--no-session-persistence",
        ]
        if request.model:
            argv.extend(["--model", request.model])
        if request.max_cost_usd:
            argv.extend(["--max-budget-usd", request.max_cost_usd])
        if request.response_schema:
            argv.extend(
                ["--json-schema", json.dumps(request.response_schema, separators=(",", ":"))]
            )
        env = sanitized_environment(self.config.run.provider_env_passthrough)
        process = self.runner.run(
            argv,
            cwd=request.workspace,
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            stdin_text=request.prompt,
            env=env,
        )
        text = ""
        usage: dict[str, Any] = {}
        cost: str | None = None
        parse_error: str | None = None
        try:
            payload = json.loads(process.stdout)
            if not isinstance(payload, dict) or payload.get("type") != "result":
                raise ProviderError("Claude output must be a result object")
            candidate_text = payload.get("structured_output")
            if candidate_text is None:
                candidate_text = payload.get("result", "")
            if not isinstance(candidate_text, (str, dict)):
                raise ProviderError("Claude result/structured_output has an invalid type")
            text = (
                json.dumps(candidate_text, sort_keys=True)
                if not isinstance(candidate_text, str)
                else candidate_text
            )
            if isinstance(payload.get("usage"), dict):
                usage = payload["usage"]
            if payload.get("total_cost_usd") is not None:
                numeric_cost = Decimal(str(payload["total_cost_usd"]))
                if not numeric_cost.is_finite() or numeric_cost < 0:
                    raise ProviderError("Claude returned an invalid cost")
                cost = str(numeric_cost)
            if payload.get("is_error") or payload.get("subtype") != "success":
                parse_error = "Claude reported an error result"
            elif request.response_schema:
                validate_response_schema(extract_json_object(text), request.response_schema)
        except json.JSONDecodeError as exc:
            parse_error = f"Claude output was not valid JSON: {exc}"
        except (ProviderError, InvalidOperation) as exc:
            parse_error = str(exc)
        status = "completed" if process.ok and text.strip() and not parse_error else "failed"
        error = parse_error
        if process.timed_out:
            error = "Claude CLI invocation timed out"
        elif process.exit_code != 0:
            error = f"Claude CLI exited with status {process.exit_code}"
        elif not text.strip() and not error:
            error = "Claude CLI returned no result"
        return AgentResult(
            engineer_id=request.engineer_id,
            phase=request.phase,
            provider_kind=self.engineer.kind,
            model=request.model,
            status=status,
            text=text,
            usage=usage,
            cost_usd=cost,
            process=process,
            error=error,
        )


class GenericCLIProvider(Provider):
    """A future-provider escape hatch using argv arrays and stdin, never a shell."""

    def _command(self, request: AgentRequest | None = None) -> list[str]:
        value = self.engineer.options.get("command")
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) for item in value)
        ):
            raise ProviderError("generic_cli options.command must be a non-empty string array")
        replacements = {
            "{model}": self.engineer.model or "",
            "{phase}": request.phase.value if request else "preflight",
        }
        command: list[str] = []
        for item in value:
            rendered = replacements.get(item, item)
            if rendered:
                command.append(rendered)
        return command

    def preflight(self) -> dict[str, Any]:
        command = self._command()
        resolved = shutil.which(command[0])
        if not resolved:
            raise ProviderError(f"generic CLI executable not found: {command[0]}")
        return {
            "kind": self.engineer.kind,
            "executable": resolved,
            "model": self.engineer.model,
            "warning": (
                "generic_cli relies on the external command's own sandbox and budget controls"
            ),
        }

    def run(self, request: AgentRequest, artifact_dir: Path) -> AgentResult:
        self._check_prompt(request)
        process = self.runner.run(
            self._command(request),
            cwd=request.workspace,
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            stdin_text=request.prompt,
            env=sanitized_environment(self.config.run.provider_env_passthrough),
        )
        return AgentResult(
            engineer_id=request.engineer_id,
            phase=request.phase,
            provider_kind=self.engineer.kind,
            model=request.model,
            status="completed" if process.ok else "failed",
            text=process.stdout,
            process=process,
            error=None if process.ok else f"generic CLI exited with status {process.exit_code}",
        )
