"""Pick the enrolled project a plain-language request is about, without a model call.

The owner writes "add CSV export to the tracker" or "improve Arena's review screen" and
expects the right repository to be chosen when that is unambiguous. Scoring is
deterministic and explainable: every candidate carries the reasons for its score, so the
UI can show them and the owner can override with one tap when nothing clearly wins.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePath

# Phrases that mean "this tool itself": the project whose record carries ``self: true``.
SELF_PHRASES = (
    "arena",
    "autobuild",
    "yourself",
    "your own",
    "this app",
    "this dashboard",
    "this build view",
    "the dashboard",
    "build control",
)

# Words that appear in nearly every request and must not count as a project match.
STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "application",
    "add",
    "build",
    "bug",
    "change",
    "feature",
    "fix",
    "for",
    "in",
    "it",
    "its",
    "make",
    "my",
    "new",
    "of",
    "on",
    "or",
    "project",
    "repo",
    "repository",
    "that",
    "the",
    "this",
    "to",
    "with",
}

# A clear winner: at least this score, and at least this multiple of the runner-up.
MIN_SCORE = 2
MIN_MARGIN = 2.0


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if len(t) >= 2 and t not in STOPWORDS]


def _phrase_in(phrase: str, haystack: str) -> bool:
    phrase = normalize(phrase)
    if not phrase:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def score_project(text: str, project: dict) -> tuple[int, list[str]]:
    """Return (score, reasons) for one project against the request text."""
    haystack = normalize(text)
    words = set(tokens(text))
    score = 0
    why: list[str] = []
    name = str(project.get("name") or "")
    basename = PurePath(str(project.get("repo") or "")).name
    name_words = set(tokens(name))

    # A name made only of generic words ("App") never matches on its own.
    if name_words and _phrase_in(name, haystack):
        score += 4
        why.append(f"named '{name}'")
    elif name:
        hits = sorted(name_words & words)
        if hits:
            score += len(hits)
            why.append("name word " + ", ".join(f"'{h}'" for h in hits))

    if basename and normalize(basename) != normalize(name):
        if tokens(basename) and _phrase_in(basename, haystack):
            score += 4
            why.append(f"repository '{basename}'")
        else:
            # Directory words already credited through the name do not count twice.
            hits = sorted((set(tokens(basename)) - name_words) & words)
            if hits:
                score += len(hits)
                why.append("repository word " + ", ".join(f"'{h}'" for h in hits))

    for keyword in project.get("keywords") or []:
        if isinstance(keyword, str) and _phrase_in(keyword, haystack):
            score += 2
            why.append(f"keyword '{keyword}'")

    if project.get("self"):
        found = [p for p in SELF_PHRASES if _phrase_in(p, haystack)]
        if found:
            score += 3 + (len(found) - 1)
            why.append("refers to Arena itself (" + ", ".join(f"'{p}'" for p in found) + ")")

    return score, why


def resolve_project(text: str, projects: list[dict]) -> dict:
    """Choose the project a request is about.

    Returns ``{"project": <project> | None, "candidates": [...], "reason": str}``.
    ``candidates`` lists every project with ``id``, ``name``, ``score`` and ``why``,
    best first, so a caller can offer them when nothing wins clearly.
    """
    scored = []
    for project in projects:
        score, why = score_project(text, project)
        scored.append(
            {
                "id": project.get("id"),
                "name": project.get("name"),
                "score": score,
                "why": "; ".join(why) if why else "no match in the request",
            }
        )
    scored.sort(key=lambda c: (-c["score"], str(c["name"] or "").lower()))
    by_id = {p.get("id"): p for p in projects}

    if not projects:
        return {"project": None, "candidates": [], "reason": "No project is enrolled"}
    if len(projects) == 1:
        only = scored[0]
        return {
            "project": by_id[only["id"]],
            "candidates": scored,
            "reason": f"Only one project is enrolled: {only['name']}",
        }
    # A project named in full (its enrolled name or repository folder, as a phrase) wins
    # outright. Longer names are consumed first so that "arena-smoke-20261028" does not also
    # count as naming a project called "Arena"; two distinct full names stay ambiguous.
    lowered = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    phrases = []
    for p in projects:
        for raw in (str(p.get("name") or ""), Path(str(p.get("repo") or "")).name):
            ph = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
            words = ph.split()
            if len(ph) >= 3 and any(w not in STOPWORDS for w in words):
                phrases.append((ph, p))
    phrases.sort(key=lambda e: -len(e[0]))
    exact = []
    for ph, p in phrases:
        token = " " + ph + " "
        if token in lowered:
            lowered = lowered.replace(token, "  ")
            if all(e.get("id") != p.get("id") for e in exact):
                exact.append(p)
    if len(exact) == 1:
        chosen = next(c for c in scored if c["id"] == exact[0].get("id"))
        return {
            "project": exact[0],
            "candidates": scored,
            "reason": f"{chosen['name']}: named in full",
        }
    top, second = scored[0], scored[1]
    if top["score"] >= MIN_SCORE and top["score"] >= MIN_MARGIN * second["score"]:
        return {
            "project": by_id[top["id"]],
            "candidates": scored,
            "reason": f"{top['name']}: {top['why']}",
        }
    if top["score"] == 0:
        reason = "The request does not name a project"
    else:
        reason = f"Several projects match: {top['name']} and {second['name']}"
    return {"project": None, "candidates": scored, "reason": reason}
