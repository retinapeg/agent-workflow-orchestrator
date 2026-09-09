from __future__ import annotations

from pathlib import Path

import pytest

from agent_arena.config import load_config
from agent_arena.prompting import PromptBook


def test_review_prompt_names_required_adversarial_attack_dimensions(
    arena_fixture: dict[str, Path],
) -> None:
    prompt = PromptBook(load_config(arena_fixture["config"])).template("review").lower()
    for phrase in (
        "incorrect assumptions",
        "hidden bugs",
        "edge cases",
        "security problems",
        "invalid apis",
        "race conditions",
        "bad ux",
        "brittle behavior",
        "unnecessary complexity",
        "missing tests",
        "ways to make the implementation",
    ):
        assert phrase in prompt


def test_inserted_braces_are_opaque_and_not_resubstituted(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    book = PromptBook(load_config(arena_fixture["config"]))
    monkeypatch.setattr(book, "template", lambda _: "Task={{TASK}}; Contract={{ACCEPTANCE}}")
    rendered = book.render(
        "test",
        TASK="Fix {{ user.name }} and preserve {{ACCEPTANCE}} literally",
        ACCEPTANCE="authoritative",
    )
    assert rendered == (
        "Task=Fix {{ user.name }} and preserve {{ACCEPTANCE}} literally; Contract=authoritative"
    )


def test_missing_or_malformed_template_variables_fail_before_substitution(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    book = PromptBook(load_config(arena_fixture["config"]))
    monkeypatch.setattr(book, "template", lambda _: "{{TASK}} {{MISSING}}")
    with pytest.raises(ValueError, match="MISSING"):
        book.render("test", TASK="value containing {{MISSING}}")
    monkeypatch.setattr(book, "template", lambda _: "{{ user.name }}")
    with pytest.raises(ValueError, match="invalid prompt variables"):
        book.render("test")
