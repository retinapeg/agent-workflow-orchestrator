"""Create a new project from a phone request: local repository, private GitHub remote,
enrolment, and the first director request — after the owner approves the exact names.

The GitHub CLI's keyring credential never leaves this process: `gh` runs on the Mac with
the controller's environment, output is redacted before it is stored, and workers get the
usual scrubbed environment. Nothing here overwrites an existing directory or repository.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from .engine import Engine, atomic_json, git

PROPOSAL_SECONDS = 3600
NEW_PROJECT_SCOPE = [
    "README.md",
    "LICENSE",
    "Makefile",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
    "index.html",
    "pyproject.toml",
    "requirements.txt",
    "src",
    "app",
    "web",
    "public",
    "static",
    "templates",
    "tests",
    "docs",
    "scripts",
    "config",
    "data",
]
STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "for",
    "to",
    "in",
    "on",
    "with",
    "that",
    "which",
    "this",
    "my",
    "me",
    "i",
    "we",
    "our",
    "you",
    "your",
    "it",
    "is",
    "are",
    "be",
    "as",
    "at",
    "by",
    "from",
    "into",
    "so",
    "can",
    "will",
    "should",
    "would",
    "let",
    "please",
    "want",
    "need",
    "like",
    "build",
    "create",
    "make",
    "write",
    "develop",
    "design",
    "implement",
    "new",
    "project",
    "software",
    "app",
    "application",
    "tool",
    "simple",
    "small",
    "basic",
    "little",
    "some",
    "using",
    "use",
    "via",
    "based",
    "helps",
    "help",
    "lets",
    "allows",
}
TOKEN = re.compile(r"(gh[opusr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,})")


def redact(text):
    return TOKEN.sub("gh*_[redacted]", text or "")


def derive_name(description, fallback="arena project"):
    """Three meaningful words from the first sentence → 'Title Case' name and a kebab slug."""
    first = re.split(r"[.!?\n]", description.strip(), maxsplit=1)[0]
    words = [w for w in re.findall(r"[A-Za-z0-9]+", first) if w.lower() not in STOPWORDS]
    words = [w for w in words if len(w) > 1][:3] or fallback.split()
    name = " ".join(w if w.isupper() else w.capitalize() for w in words)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "arena-project"
    return name, slug


def _gh(run, *args, timeout=120):
    result = run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={k: v for k, v in os.environ.items() if k not in {"GH_TOKEN", "GITHUB_TOKEN"}},
    )
    return result.returncode, redact(result.stdout), redact(result.stderr)


def github_login(run=subprocess.run):
    code, out, err = _gh(run, "api", "user", "-q", ".login", timeout=30)
    if code or not out.strip():
        raise ValueError("GitHub CLI is not signed in on the Mac: run `gh auth login` there")
    return out.strip()


def remote_exists(run, full_name):
    code, _, err = _gh(run, "repo", "view", full_name, "--json", "name", timeout=30)
    if code == 0:
        return True
    if "Could not resolve" in err or "not found" in err.lower():
        return False
    raise ValueError("GitHub could not be queried: " + err.strip()[:300])


def propose(store, description, *, projects_dir=None, run=subprocess.run):
    """Derive the names, resolve collisions, and park a proposal for explicit approval."""
    description = description.strip()
    if len(description) < 12:
        raise ValueError("Describe the product in a sentence or two first")
    login = github_login(run)
    projects_dir = Path(projects_dir or Path.home() / "Projects").expanduser().resolve()
    name, base = derive_name(description)
    slug, attempt = base, 1
    notes = []
    while True:
        local = projects_dir / slug
        full_name = f"{login}/{slug}"
        taken_local = local.exists() and any(local.iterdir()) if local.exists() else False
        taken_remote = remote_exists(run, full_name)
        if not taken_local and not taken_remote:
            break
        notes.append(
            f"'{slug}' already exists {'locally' if taken_local else 'on GitHub'}; using a suffix"
        )
        attempt += 1
        slug = f"{base}-{attempt}"
        if attempt > 20:
            raise ValueError("Could not find a free project name; choose one by hand")
    proposal = {
        "id": "proposal_" + secrets.token_hex(6),
        "name": name,
        "slug": slug,
        "description": description,
        "local_path": str(local),
        "github": {
            "owner": login,
            "full_name": full_name,
            "url": f"https://github.com/{full_name}",
            "private": True,
        },
        "notes": notes,
        "created": time.time(),
        "expires": time.time() + PROPOSAL_SECONDS,
    }
    folder = store.home / "proposals"
    folder.mkdir(exist_ok=True, mode=0o700)
    atomic_json(folder / (proposal["id"] + ".json"), proposal)
    return proposal


def load_proposal(store, proposal_id):
    path = store.home / "proposals" / (proposal_id + ".json")
    if not re.fullmatch(r"proposal_[0-9a-f]{12}", proposal_id) or not path.is_file():
        raise KeyError(proposal_id)
    proposal = json.loads(path.read_text())
    if proposal["expires"] < time.time():
        raise ValueError("This proposal expired; ask for a fresh one")
    return proposal


def provisions(store, limit=20):
    folder = store.home / "provisions"
    if not folder.is_dir():
        return []
    items = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [json.loads(p.read_text()) for p in items]


class Provisioner:
    """Runs the approved steps in order; every step is recorded so the phone can follow."""

    def __init__(self, store, engine: Engine, *, run=subprocess.run, controller_root=None):
        self.store, self.engine, self.run = store, engine, run
        self.controller_root = Path(controller_root or Path(__file__).resolve().parents[3])

    def _write(self, record):
        folder = self.store.home / "provisions"
        folder.mkdir(exist_ok=True, mode=0o700)
        record["updated"] = time.time()
        atomic_json(folder / (record["id"] + ".json"), record)

    def _step(self, record, name, fn):
        record["steps"].append({"name": name, "state": "running", "started": time.time()})
        record["state"] = name
        self._write(record)
        try:
            detail = fn()
            record["steps"][-1].update(state="done", ended=time.time(), detail=detail)
        except Exception as exc:
            record["steps"][-1].update(
                state="failed", ended=time.time(), detail=redact(str(exc))[:800]
            )
            record["state"] = "failed"
            record["error"] = redact(str(exc))[:800]
            self._write(record)
            raise
        self._write(record)

    def start(self, proposal, approved_by="phone"):
        record = {
            "id": "provision_" + secrets.token_hex(6),
            "proposal_id": proposal["id"],
            "name": proposal["name"],
            "slug": proposal["slug"],
            "local_path": proposal["local_path"],
            "github": proposal["github"],
            "description": proposal["description"],
            "state": "queued",
            "steps": [],
            "approved_by": approved_by,
            "created": time.time(),
        }
        self._write(record)
        with self.store.transaction() as db:
            self.store.event(
                db, "project.provisioning", provision_id=record["id"], slug=record["slug"]
            )
        threading.Thread(
            target=self._run, args=(record,), daemon=True, name="arena-provision"
        ).start()
        return record

    def _run(self, record):
        local = Path(record["local_path"])
        full_name = record["github"]["full_name"]
        try:
            self._step(record, "local repository", lambda: self._create_local(local, record))
            self._step(
                record, "github repository", lambda: self._create_remote(local, full_name, record)
            )
            self._step(record, "verify connection", lambda: self._verify(local))
            self._step(record, "enrol in arena", lambda: self._enrol(local, record))
            self._step(record, "first request", lambda: self._request(record))
            record["state"] = "done"
            self._write(record)
            with self.store.transaction() as db:
                self.store.event(
                    db,
                    "project.provisioned",
                    provision_id=record["id"],
                    project_id=record.get("project_id"),
                    github=record["github"]["url"],
                    local_path=record["local_path"],
                )
        except Exception as exc:  # recorded by _step; nothing is retried or overwritten
            with self.store.transaction() as db:
                self.store.event(
                    db,
                    "project.provision_failed",
                    provision_id=record["id"],
                    reason=redact(str(exc))[:500],
                )

    def _create_local(self, local, record):
        if local.exists() and any(local.iterdir()):
            raise ValueError(f"{local} already exists and is not empty; nothing overwritten")
        local.mkdir(parents=True, exist_ok=True)
        (local / "README.md").write_text(
            f"# {record['name']}\n\n{record['description']}\n\n"
            "Created by Arena. Builds run in isolated workspaces and integrate into `main` "
            "after frozen checks and independent review.\n"
        )
        (local / ".gitignore").write_text(
            ".DS_Store\nnode_modules/\n.venv/\n__pycache__/\ndist/\nbuild/\n.env\n"
        )
        git(local, "init", "-q", "-b", "main")
        git(local, "add", "-A")
        git(
            local,
            "-c",
            "user.name=Arena",
            "-c",
            "user.email=arena@localhost",
            "commit",
            "-qm",
            "Initial commit from Arena",
        )
        return git(local, "rev-parse", "--short", "HEAD")

    def _create_remote(self, local, full_name, record):
        if remote_exists(self.run, full_name):
            raise ValueError(f"GitHub repository {full_name} already exists; nothing replaced")
        code, out, err = _gh(
            self.run,
            "repo",
            "create",
            full_name,
            "--private",
            "--source",
            str(local),
            "--remote",
            "origin",
            "--push",
            "--description",
            record["description"][:200].replace("\n", " "),
            timeout=180,
        )
        if code:
            raise ValueError("gh repo create failed: " + (err or out).strip()[:400])
        return out.strip()[:200] or record["github"]["url"]

    def _verify(self, local):
        result = self.run(
            ["git", "-C", str(local), "ls-remote", "--heads", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
            env=os.environ.copy(),
        )
        if result.returncode or "refs/heads/main" not in result.stdout:
            raise ValueError(
                "The GitHub remote does not expose main: " + redact(result.stderr)[:300]
            )
        return result.stdout.split()[0][:12]

    def _enrol(self, local, record):
        from .__main__ import inferred_manifest

        words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", record["name"])]
        manifest = inferred_manifest(local, record["name"], "auto_local_main", ",".join(words))
        # A brand-new repository has nothing but a README: give workers the conventional
        # tree they will create (explicit paths; the scope validator accepts future paths).
        manifest["scope"] = sorted(set(manifest["scope"]) | set(NEW_PROJECT_SCOPE))
        project = self.engine.enroll(**manifest)
        with self.store.transaction() as db:
            current = self.store.get("projects", project["id"], db)
            current["github_url"] = record["github"]["url"]
            current["push_remote"] = True
            from .store import encode

            db.execute(
                "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
                (
                    encode({k: v for k, v in current.items() if k not in {"id", "rev"}}),
                    project["id"],
                ),
            )
        record["project_id"] = project["id"]
        return project["id"]

    def _request(self, record):
        with self.store.transaction() as db:
            result = self.engine.add_goal(
                db,
                record["project_id"],
                record["description"][:100],
                record["description"],
                self.store.get("projects", record["project_id"], db)["scope"],
                review=True,
                timeout=900,
                planning=True,
                director=True,
            )
        record.update(goal_id=result["goal_id"], task_id=result["task_id"])
        return result["goal_id"]


def push_main(source, *, run=subprocess.run):
    """Push the enrolled source's main to its origin with the Mac's normal credentials."""
    result = run(
        ["git", "-C", str(source), "push", "origin", "main"],
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    if result.returncode:
        raise ValueError("git push failed: " + redact(result.stderr).strip()[:400])
    return redact(result.stderr or result.stdout).strip()[:200]


__all__ = [
    "Provisioner",
    "derive_name",
    "github_login",
    "load_proposal",
    "propose",
    "provisions",
    "push_main",
    "redact",
    "remote_exists",
]
