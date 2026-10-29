"""Let Arena build in a local repository the owner named in the conversation.

The director offers it (``check``: read-only, git takes no optional locks); only the owner's
tap enrols it (``enroll``, behind POST /api/v1/projects/enroll-local). The repository is
found by its folder name among the git repositories in the owner's Projects folder (never a
path from the phone) and enrolled with the inferred manifest, so verified work integrates
into its local main.

Arena commits in the owner's repository only when the owner taps to save unsaved work first
(``save``, an explicit owner decision): only on main, exactly the files ``git status`` lists
(.gitignore respected), never secret-looking names or large data, one new commit by Arena,
never an amend, stash, reset, clean or another branch.
"""

from __future__ import annotations

import fnmatch
import re
import stat
import subprocess
from pathlib import Path

from .engine import FORBIDDEN, git
from .store import Conflict
from .worker import redact

CONTROLLER = Path(__file__).resolve().parents[3]
# Owner decision: Arena never commits these for the owner. Matched against every part of
# the path, in any case.
SECRET_NAMES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.keystore",
    "id_rsa*",
    "*credential*",
    "*secret*",
    "*token*",
)
SQLITE_LIMIT = 50 * 1024 * 1024
FILE_LIMIT = 100 * 1024 * 1024
UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
IN_PROGRESS = (
    ("MERGE_HEAD", "a merge"),
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
)
CHECKPOINT_AUTHOR = "Arena <arena@local>"
SCOPE_LIMIT = 32
SELF = (
    "{name} is Arena's own repository. Arena works on itself only through its own setup on the Mac."
)
# Engine and manifest refusals, in the owner's words.
PLAIN = (
    (
        "clean committed source",
        "{name} has unsaved changes on your Mac. Arena only builds from committed work, so it "
        "never overwrites yours. Commit or stash them on the Mac, then try again.",
    ),
    ("branch main checked out", "{name} is not on main. Switch it to main on the Mac first."),
    ("Enrolling Arena itself", SELF),
    ("parents of the controller", "{name} holds Arena itself, so Arena cannot build there."),
    ("not a git repository", "{name} is not a git repository."),
    ("no enrollable files", "{name} has no files Arena could work on."),
)


def title(folder):
    """A repository folder's friendly name: "partnerflow-lab" → "Partnerflow Lab"; words
    that already carry capitals ("LeoOS", "API") keep them."""
    words = [w for w in re.split(r"[-_.\s]+", str(folder)) if w]
    return " ".join(w if w != w.lower() else w[:1].upper() + w[1:] for w in words) or folder


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def unsaved(name, count):
    return (
        f"{name} has {plural(count, 'unsaved change')} on your Mac. Arena only builds from "
        "committed work, so it never overwrites yours. Commit or stash them on the Mac, then "
        "try again."
    )


def find(repos, name):
    """The local repository whose folder name is ``name`` (any case; an exact match wins)
    among the catalog's own listing, or None. A path never matches a folder name."""
    wanted = str(name or "").strip()
    for found in (
        [r for r in repos if r["name"] == wanted],
        [r for r in repos if r["name"].lower() == wanted.lower()],
    ):
        if len(found) == 1:
            return found[0]
    return None


def enrolled(store, path):
    """The project (archived ones too) Arena already has for this repository, or None."""
    for project in store.rows("projects"):
        try:
            if Path(project["repo"]).expanduser().resolve() == Path(path).resolve():
                return project
        except (KeyError, OSError, RuntimeError):
            continue
    return None


def _quiet(path, *args):
    try:
        return git(path, *args)
    except ValueError:
        return None


def changes(path):
    """What ``git status`` lists as not committed (staged, unstaged and untracked files,
    .gitignore respected) as [(XY code, path, original path of a staged rename or None)].
    Read-only: git takes no optional locks, so it does not even refresh the index."""
    fields = git(
        path,
        "--no-optional-locks",
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=all",
        strip=False,
    ).split("\0")
    out, i = [], 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        original = None
        if entry[0] in "RC" and i < len(fields):
            original, i = fields[i], i + 1
        out.append((entry[:2], entry[3:], original))
    return out


def check(path, name):
    """Whether Arena can enrol this repository as it stands, read-only.

    Returns {clean, branch, reason, can_checkpoint}: ``clean`` when nothing is uncommitted
    (untracked files included), ``branch`` the one checked out (None when detached),
    ``reason`` the first thing in the way in the owner's words (None when nothing is), and
    ``can_checkpoint`` when that is only unsaved work on main, which the owner may let
    Arena commit for them (``save``)."""
    path = Path(path)
    out = {"clean": False, "branch": None, "reason": None, "can_checkpoint": False}
    try:
        pending = changes(path)
        gitdir = Path(git(path, "rev-parse", "--absolute-git-dir"))
        branch = out["branch"] = _quiet(path, "symbolic-ref", "--short", "-q", "HEAD")
        born = _quiet(path, "rev-parse", "-q", "--verify", "HEAD^{commit}") is not None
    except (ValueError, OSError, subprocess.SubprocessError):
        out["reason"] = f"Arena could not read {name} on your Mac."
        return out
    out["clean"] = not pending
    busy = next((what for marker, what in IN_PROGRESS if (gitdir / marker).exists()), None)
    if path.resolve() == CONTROLLER:
        out["reason"] = SELF.format(name=name)
    elif busy:
        out["reason"] = (
            f"{name} is in the middle of {busy} on your Mac. Finish it there first, then try again."
        )
    elif any(code in UNMERGED for code, _, _ in pending):
        out["reason"] = (
            f"{name} has unresolved conflicts on your Mac. Resolve them there first, then try "
            "again."
        )
    elif branch != "main":
        out["reason"] = (
            f"{name} is on branch {branch}. Switch it to main on the Mac first."
            if branch
            else f"{name} has no branch checked out. Switch it to main on the Mac first."
        )
    elif pending:
        out.update(reason=unsaved(name, len(pending)), can_checkpoint=True)
    elif not born:
        out["reason"] = (
            f"{name} has no commits yet. Make a first commit on the Mac, then try again."
        )
    return out


def risky(path, pending):
    """Uncommitted files Arena will not commit for the owner: secret-looking names (any part
    of the path) and large data. A deleted file has no content left to commit."""
    found = []
    for _, rel, _ in pending:
        try:
            info = (Path(path) / rel).lstat()
        except OSError:
            continue
        parts = [part.lower() for part in Path(rel).parts]
        named = any(fnmatch.fnmatchcase(part, p) for part in parts for p in SECRET_NAMES)
        size = info.st_size if stat.S_ISREG(info.st_mode) else 0
        large = size > FILE_LIMIT or (rel.lower().endswith(".sqlite3") and size > SQLITE_LIMIT)
        if named or large:
            found.append(rel)
    return found


def save(store, path, name):
    """Commit the owner's unsaved work on main for them (their explicit tap): exactly the
    files ``git status`` lists, as one new commit by Arena; never an amend, stash, reset,
    clean or another branch. Refused with nothing committed when anything else is in the
    way or a file looks like a secret or large data. Returns {sha, files}."""
    path = Path(path)
    state = check(path, name)
    if not state["can_checkpoint"]:
        raise Conflict(state["reason"] or f"{name} has no unsaved work to save.")
    pending = changes(path)
    flagged = risky(path, pending)
    if flagged:
        shown = ", ".join(flagged[:10]) + (
            f" and {len(flagged) - 10} more" if len(flagged) > 10 else ""
        )
        raise Conflict(
            f"{name} has files that look like secrets or large data ({shown}); Arena won't "
            "commit those. Move them out or ignore them on the Mac, then try again."
        )
    # Staged changes are in the index already; stage the rest, path by path, literally.
    unstaged = [rel for code, rel, _ in pending if code[1] != " "]
    count = len(pending)
    try:
        if unstaged:
            git(
                path,
                "--literal-pathspecs",
                "add",
                "-A",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                input="\0".join(unstaged) + "\0",
            )
        git(
            path,
            "commit",
            "-q",
            "--author=" + CHECKPOINT_AUTHOR,
            "-m",
            f"Checkpoint by Arena before enrolment ({plural(count, 'file')})",
        )
    except (ValueError, subprocess.SubprocessError) as exc:
        if "index.lock" in str(exc):  # an editor or terminal is running git there right now
            reason = "another git command is running there on your Mac. Try again in a moment"
        else:
            reason = " ".join(redact(str(exc)).split())[:300].rstrip(".")
        raise Conflict(
            f"Arena could not save the unsaved work in {name}, so nothing was committed: {reason}."
        ) from None
    sha = git(path, "rev-parse", "HEAD")
    with store.transaction() as db:
        store.event(db, "project.checkpointed", repo=str(path), name=name, sha=sha, files=count)
    return {"sha": sha, "files": count}


def plain(name, exc):
    """An enrolment refusal in the owner's words."""
    text = str(exc)
    for marker, words in PLAIN:
        if marker in text:
            return words.format(name=name)
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"Copying {name} took too long, so Arena stopped. Try again in a moment."
    reason = " ".join(redact(text).split())[:300].rstrip(".")
    return f"Arena could not enrol {name}: {reason}."


def manifest(path, name):
    """The inferred manifest (whole tree minus build junk, generic checks, local-main
    integration). Arena's and the coding assistants' own files (CLAUDE.md, AGENTS.md) stay
    outside the workers' authority rather than refusing the repository."""
    from .__main__ import inferred_manifest

    try:
        found = inferred_manifest(path, name, "auto_local_main")
    except ValueError as exc:
        raise Conflict(plain(name, exc)) from None
    scope = [
        entry
        for entry in found["scope"]
        if entry not in FORBIDDEN and not any(c in entry for c in "*?[]\\\n\x00")
    ]
    if not scope:
        raise Conflict(f"{name} has no files Arena could work on.")
    if len(scope) > SCOPE_LIMIT:
        raise Conflict(
            f"{name} has more than {SCOPE_LIMIT} files and folders at its top level, more than "
            "Arena can take on from the phone. Enrol it on the Mac instead."
        )
    return {**found, "scope": scope}


def enroll(store, engine, repo, *, checkpoint=False):
    """Enrol a local repository (a catalog entry) so Arena builds in it and integrates into
    its local main, first saving its unsaved work when the owner asked for that. Returns
    {project, already, checkpoint}; every refusal is a Conflict in the owner's words."""
    path = Path(repo["path"]).resolve()
    name = title(repo["name"])
    project = enrolled(store, path)
    if project:
        return {"project": project, "already": True, "checkpoint": None}
    state = check(path, name)
    if state["reason"] and not (checkpoint and state["can_checkpoint"]):
        raise Conflict(state["reason"])
    found = manifest(path, name)  # before any commit: a repository it refuses stays untouched
    saved = save(store, path, name) if state["reason"] else None
    try:
        project = engine.enroll(**found)
    except Conflict:  # enrolled meanwhile, by another tap or device
        project = enrolled(store, path)
        if project is None:
            raise
        return {"project": project, "already": True, "checkpoint": saved}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        message = plain(name, exc)
        if saved:
            message = (
                f"Arena saved the unsaved work in {name} as a commit on main, but could not "
                f"enrol it. {message}"
            )
        raise Conflict(message) from None
    return {"project": project, "already": False, "checkpoint": saved}
