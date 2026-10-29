"""Short, human titles for requests and goals.

Goal titles used to be the first 100 characters of the request text, so a build compiled
from a conversation showed up on the phone as ``"OUTCOME:\\nAdd a Copy button…"`` and an
``Integrated · OUTCOME: …`` picker entry. ``display_title`` turns any request text into one
readable line: labels and markdown stripped, first sentence only, at most 72 characters.
"""

from __future__ import annotations

import re

TITLE_LIMIT = 72

# Leading labels the director and owners write: "OUTCOME:", "Outcome -", "Request:", also
# wrapped in markdown emphasis ("**OUTCOME:**") and repeated ("Request: Outcome: …").
# A hyphen counts as a separator only with whitespace around it ("Outcome - Add…"), so
# "Build-time caching" or "Task-runner" keep their first word.
_LABEL = re.compile(
    r"^\s*[*_#>\-\s]*(?:outcome|request|goal|task|title|summary|build)"
    r"\s*(?::|[–—]|(?<=\s)-(?=\s))\s*[*_]*\s*",
    re.I,
)
_MARKDOWN = (
    (re.compile(r"^\s{0,3}#{1,6}\s+"), ""),  # headings
    (re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+"), ""),  # list markers
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),  # links and images
    (re.compile(r"`+([^`]*)`+"), r"\1"),  # inline code
    (re.compile(r"(\*\*|__)(.+?)\1"), r"\2"),  # bold
    (re.compile(r"(?<![A-Za-z0-9])[*_](.+?)[*_](?![A-Za-z0-9])"), r"\1"),  # italics
    (re.compile(r"[*_]{1,3}"), ""),  # stray emphasis markers
)
# A sentence ends at ./!/? followed by whitespace, or at a paragraph break; a single
# newline is a soft wrap, except after a markdown heading line, which stands alone.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n\s*\n")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+.*$", re.M)


# Chips the old Home form put in front of a request ("Add to an existing project: …").
_LEGACY = re.compile(
    r"^\s*(?:add to an existing project|build a new feature|fix a bug|improve arena|build|fix|"
    r"improve)\s*:\s*",
    re.I,
)


def strip_legacy(text: str | None) -> str:
    """The request without the legacy Home prefixes (stacked ones too), capitalised."""
    original = value = (text or "").strip()
    previous = None
    while value != previous:
        previous = value
        value = _LEGACY.sub("", value, count=1)
    return value[:1].upper() + value[1:] if value != original else value


def goal_title(goal: dict, limit: int = TITLE_LIMIT) -> str:
    """A build's name: the first sentence of the director's agreed outcome when the goal
    has a specification, else its title or request without the legacy Home prefixes.
    Empty when none of them has words (callers choose their own default)."""
    spec = goal.get("specification")
    if isinstance(spec, dict) and isinstance(spec.get("outcome"), str):
        title = display_title(spec["outcome"], limit)
        if title:
            return title
    for text in (goal.get("title"), goal.get("request")):
        first = display_title(strip_legacy(text), 2000)
        title = display_title(strip_legacy(first), limit)
        if title:
            return title
    return ""


def display_title(text: str | None, limit: int = TITLE_LIMIT) -> str:
    """One readable line (≤ ``limit`` chars, ellipsis when cut) from request text.

    Empty or label-only input gives ``""`` so callers can fall back to their own default.
    """
    value = (text or "").strip()
    previous = None
    while value and value != previous:  # labels may be stacked or wrapped in emphasis
        previous = value
        value = _LABEL.sub("", value, count=1).strip()
    if _HEADING.match(value):
        value = value.split("\n", 1)[0]
    first = _SENTENCE_END.split(value, maxsplit=1)[0].strip()
    for pattern, replacement in _MARKDOWN:
        first = pattern.sub(replacement, first)
    first = re.sub(r"\s+", " ", first).strip().rstrip(".").strip()
    if not first and value:  # markdown-only first line: use the remainder instead
        rest = _SENTENCE_END.split(value, maxsplit=1)
        return display_title(rest[1], limit) if len(rest) > 1 else ""
    if len(first) <= limit:
        return first
    cut = first[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-–—(") + "…"
