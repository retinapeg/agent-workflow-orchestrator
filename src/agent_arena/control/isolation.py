"""Keeping a project's coding-assistant files away from Arena's own assistants.

Codex, Claude Code and Gemini CLI read files such as AGENTS.md and CLAUDE.md, and folders
such as .codex/ and .claude/, from the directory they run in, and treat them as
instructions or configuration. In an enrolled project those files are the owner's notes to
their own assistant. They must not steer Arena's builder, reviewer or director, which take
their instructions only from Arena.

So every clone a CLI runs in is a sparse checkout that leaves those names out of the
working tree, at any depth. They stay in the index, so every commit made in the clone still
carries them, unchanged. A candidate that writes one is refused (engine.forbidden).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from .engine import AGENT_FILES, git

# Non-cone sparse-checkout patterns: everything except these names, at any depth, as files
# or directories. git compares them case-insensitively where the disk is (core.ignorecase).
PATTERNS = ("/*", *("!" + name for name in sorted(AGENT_FILES)))
_FOLDED = frozenset(name.casefold() for name in AGENT_FILES)


def hidden(path):
    """Whether a repository-relative path is one the sparse checkout leaves out."""
    return any(part.casefold() in _FOLDED for part in Path(path).parts)


def hide_agent_files(clone):
    """Make a fresh clone leave the assistant files out of its working tree. Call it before
    the first checkout. Raises ValueError when git refuses (git 2.35+ is needed)."""
    git(clone, "sparse-checkout", "set", "--no-cone", *PATTERNS)


def exposed_agent_files(root):
    """Relative paths under root that a coding assistant would open as its own instructions
    or configuration. The file system is asked by name, so the answer is exactly as
    case-insensitive as the disk the CLI reads (APFS usually is)."""
    root = Path(root)
    found = []
    for directory, dirs, _files in os.walk(root):
        here = Path(directory)
        found += [
            (here / name).relative_to(root).as_posix()
            for name in sorted(AGENT_FILES)
            if os.path.lexists(here / name)
        ]
        dirs[:] = [d for d in dirs if d != ".git" and d.casefold() not in _FOLDED]
    return found


@contextlib.contextmanager
def isolated_view(repo, parent):
    """A throwaway clone of repo's committed HEAD without the assistant files, for a CLI
    that only reads. It is removed afterwards. Uncommitted work is not in it, matching what
    Arena builds from. Raises ValueError when the view cannot be made safely."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    holder = Path(tempfile.mkdtemp(prefix="view-", dir=parent))
    try:
        view = holder / Path(repo).name
        # --shared borrows the repository's objects read-only: no copy and no shared inodes.
        git(holder, "clone", "--quiet", "--shared", "--no-checkout", "--", str(repo), str(view))
        git(view, "remote", "remove", "origin")
        hide_agent_files(view)
        git(view, "checkout", "--quiet", "--detach", "HEAD")
        exposed = exposed_agent_files(view)
        if exposed:
            raise ValueError("Assistant files still present: " + ", ".join(exposed[:5]))
        yield view
    finally:
        shutil.rmtree(holder, ignore_errors=True)
