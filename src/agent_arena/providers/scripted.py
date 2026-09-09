from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..errors import ProviderError
from ..models import AgentRequest, AgentResult, Phase
from .api import apply_file_operations
from .base import Provider


class ScriptedProvider(Provider):
    """Deterministic offline provider for demos and end-to-end harness tests."""

    def _script_path(self) -> Path:
        value = self.engineer.options.get("script")
        if not isinstance(value, str):
            raise ProviderError("scripted provider requires options.script")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.config.path.parent / path).resolve()
        return path

    def _load(self) -> dict[str, Any]:
        path = self._script_path()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise ProviderError(f"invalid scripted provider fixture: {path}") from exc
        if not isinstance(value, dict):
            raise ProviderError("scripted provider fixture must be a JSON object")
        return value

    def preflight(self) -> dict[str, Any]:
        self._load()
        return {
            "kind": self.engineer.kind,
            "model": self.engineer.model,
            "script": str(self._script_path()),
            "mode": "offline deterministic fixture",
        }

    def run(self, request: AgentRequest, artifact_dir: Path) -> AgentResult:
        self._check_prompt(request)
        script = self._load()
        phase_value = script.get(request.phase.value)
        if isinstance(phase_value, list):
            index_file = artifact_dir.parent / f".{request.phase.value}-index"
            index = int(index_file.read_text()) if index_file.exists() else 0
            if index >= len(phase_value):
                raise ProviderError(f"scripted phase {request.phase.value} has no response {index}")
            payload = phase_value[index]
            index_file.write_text(str(index + 1), encoding="utf-8")
            os.chmod(index_file, 0o600)
        else:
            payload = phase_value
        if not isinstance(payload, dict):
            raise ProviderError(f"scripted fixture has no object for phase {request.phase.value}")
        if payload.get("status", "completed") != "completed":
            return AgentResult(
                engineer_id=request.engineer_id,
                phase=request.phase,
                provider_kind=self.engineer.kind,
                model=request.model,
                status="failed",
                text=str(payload.get("text", "")),
                error=str(payload.get("error", "scripted failure")),
            )
        operations = payload.get("operations", [])
        applied = 0
        if request.phase in {Phase.IMPLEMENT, Phase.REVISE}:
            applied = apply_file_operations(
                request.workspace,
                operations,
                max_operations=self.config.limits.max_api_operations,
                max_file_bytes=self.config.limits.max_api_file_bytes,
                max_total_bytes=self.config.limits.max_api_response_bytes,
            )
        elif operations:
            raise ProviderError("scripted review/judge responses may not mutate files")
        text_value = payload.get("text", payload.get("response", payload))
        text = text_value if isinstance(text_value, str) else json.dumps(text_value, sort_keys=True)
        return AgentResult(
            engineer_id=request.engineer_id,
            phase=request.phase,
            provider_kind=self.engineer.kind,
            model=request.model,
            status="completed",
            text=text,
            usage={"scripted": True},
            operations_applied=applied,
        )
