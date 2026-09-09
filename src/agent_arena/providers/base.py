from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..config import ArenaConfig, EngineerConfig
from ..models import AgentRequest, AgentResult
from ..process import ProcessRunner


class Provider(ABC):
    def __init__(
        self, engineer: EngineerConfig, config: ArenaConfig, runner: ProcessRunner
    ) -> None:
        self.engineer = engineer
        self.config = config
        self.runner = runner

    @abstractmethod
    def preflight(self) -> dict[str, Any]:
        """Validate local availability without making a paid model call."""

    @abstractmethod
    def run(self, request: AgentRequest, artifact_dir: Path) -> AgentResult:
        """Run one bounded phase and return normalized evidence."""

    def _check_prompt(self, request: AgentRequest) -> None:
        prompt_bytes = len(request.prompt.encode("utf-8"))
        if prompt_bytes > self.config.limits.max_prompt_bytes:
            raise ValueError(
                f"prompt is {prompt_bytes} bytes; limit is {self.config.limits.max_prompt_bytes}"
            )
