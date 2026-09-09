from __future__ import annotations

from ..config import ArenaConfig, EngineerConfig
from ..process import ProcessRunner
from .api import AnthropicAPIProvider, OpenAIAPIProvider
from .base import Provider
from .cli import ClaudeCLIProvider, CodexCLIProvider, GenericCLIProvider
from .scripted import ScriptedProvider


def create_provider(
    engineer: EngineerConfig, config: ArenaConfig, runner: ProcessRunner
) -> Provider:
    kinds: dict[str, type[Provider]] = {
        "codex_cli": CodexCLIProvider,
        "claude_cli": ClaudeCLIProvider,
        "openai_api": OpenAIAPIProvider,
        "anthropic_api": AnthropicAPIProvider,
        "generic_cli": GenericCLIProvider,
        "scripted": ScriptedProvider,
    }
    return kinds[engineer.kind](engineer, config, runner)


__all__ = ["Provider", "create_provider"]
