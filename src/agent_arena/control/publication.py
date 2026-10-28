"""Exact-commit, recoverable integration into an enrolled source's main branch.

The HTTP transaction records permission first. The scheduler performs Git work
afterward; an intent and receipt bridge a crash between Git and SQLite commits.
No remote push, reset, stash, force update or checkout of the user's branch.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import time
from pathlib import Path

from fastapi import Request
from pydantic import BaseModel

from .engine import atomic_json, git
from .store import Conflict, encode

PublicationChange = BaseModel


def register_routes(app, store, command, revision, change_model):
    # FastAPI resolves annotations against module globals.
    globals()["PublicationChange"] = change_model

    @app.post("/api/v1/approvals/{ident}/integrate")
    def request_integration(ident: str, body: PublicationChange, request: Request):
        def queue(db):
            approval = store.get("approvals", ident, db)
            revision(approval, body.revision)
            task = store.get("tasks", approval["task_id"], db)
            project = store.get("projects", task["project_id"], db)
            if (
                approval["state"] != "accept"
                or body.candidate != approval["candidate"]
                or body.base != approval["base"]
                or project["head"] != approval["candidate"]
                or approval.get("publication", {}).get("state") in {"queued", "integrated"}
            ):
                raise Conflict("Accept and inspect the current exact candidate before integration")
            data = {k: v for k, v in approval.items() if k not in {"id", "rev", "state", "task_id"}}
            data["publication"] = {
                "state": "queued",
                "branch": "main",
                "expected_source": project.get("integrated_head", project["base"]),
                "requested": time.time(),
            }
            db.execute("UPDATE approvals SET data=?,rev=rev+1 WHERE id=?", (encode(data), ident))
            store.event(
                db,
                "integration.authorized",
                approval_id=ident,
                candidate=approval["candidate"],
                branch="main",
            )
            return {"ok": True, "integration": "queued", "pushed": False}

        return command(request, body.model_dump(), queue)


def publish(store, approval):
    """Run once or reconcile an already applied exact commit after a restart."""
    task = store.get("tasks", approval["task_id"])
    project = store.get("projects", task["project_id"])
    staging, source = Path(project["staging"]), Path(project["repo"])
    candidate = approval["candidate"]
    publication = approval["publication"]
    expected = publication["expected_source"]
    directory = store.home / "publications" / approval["id"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    intent = directory / "intent.json"
    with (staging.parent / "integration.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if approval["state"] != "accept":
                raise Conflict("This candidate has not been accepted")
            actual = git(source, "rev-parse", "refs/heads/main")
            saved = json.loads(intent.read_text()) if intent.exists() else None
            if actual == candidate and saved and saved["candidate"] == candidate:
                receipt = {**saved, "recovered": True}
            else:
                if actual != expected:
                    raise Conflict(
                        "Source main advanced; preserve it and build against its new base"
                    )
                if git(staging, "rev-parse", "HEAD") != candidate:
                    raise Conflict(
                        "Staging advanced; inspect and accept the latest combined candidate"
                    )
                evidence = store.home / "attempts" / task["build_attempt"] / "integration.json"
                verified = json.loads(evidence.read_text())
                if verified["candidate"] != candidate or not verified["checks"]:
                    raise Conflict("Integration evidence does not identify this candidate")
                for check in verified["checks"]:
                    log = evidence.parent / check["artifact"]
                    if (
                        check["status"] != "PASS"
                        or hashlib.sha256(log.read_bytes()).hexdigest() != check["sha256"]
                    ):
                        raise Conflict("Frozen verification evidence failed integrity validation")
                git(source, "fetch", "--no-tags", str(staging), candidate)
                if git(source, "merge-base", expected, candidate) != expected:
                    raise Conflict("Candidate is not a fast-forward of source main")
                # Worktree records identify the existing checkout of main, if any.
                checkout = None
                for record in git(source, "worktree", "list", "--porcelain").split("\n\n"):
                    lines = record.splitlines()
                    if "branch refs/heads/main" in lines:
                        checkout = Path(next(x[9:] for x in lines if x.startswith("worktree ")))
                if checkout and git(checkout, "status", "--porcelain", "--untracked-files=all"):
                    raise Conflict("Source main checkout has local changes; nothing overwritten")
                receipt = {
                    "candidate": candidate,
                    "base": expected,
                    "branch": "main",
                    "source": str(source),
                    "approval_id": approval["id"],
                    "checks": verified["checks"],
                    "time": time.time(),
                    "pushed": False,
                }
                atomic_json(intent, receipt)
                if checkout:
                    # Git itself refuses overwriting tracked/untracked local changes.
                    if git(checkout, "rev-parse", "HEAD") != expected:
                        raise Conflict("Source main moved during integration")
                    git(checkout, "merge", "--ff-only", candidate)
                else:
                    git(
                        source,
                        "update-ref",
                        "-m",
                        "Arena approved integration",
                        "refs/heads/main",
                        candidate,
                        expected,
                    )
                if git(source, "rev-parse", "refs/heads/main") != candidate:
                    raise Conflict("Source main changed during integration; inspect receipt")
            atomic_json(directory / "receipt.json", receipt)
            state, reason = "integrated", "Verified candidate integrated into local main"
        except Exception as exc:
            state, reason = "failed", str(exc)[-2000:]
            receipt = {"candidate": candidate, "reason": reason, "time": time.time()}
            atomic_json(directory / "failure.json", receipt)
        with store.transaction() as db:
            live = store.get("approvals", approval["id"], db)
            data = {k: v for k, v in live.items() if k not in {"id", "rev", "state", "task_id"}}
            data["publication"] = {
                **publication,
                "state": state,
                "reason": reason,
                "receipt": receipt,
            }
            db.execute(
                "UPDATE approvals SET data=?,rev=rev+1 WHERE id=?", (encode(data), approval["id"])
            )
            if state == "integrated":
                current = store.get("projects", project["id"], db)
                current["integrated_head"] = candidate
                db.execute(
                    "UPDATE projects SET data=?,rev=rev+1 WHERE id=?",
                    (
                        encode({k: v for k, v in current.items() if k not in {"id", "rev"}}),
                        project["id"],
                    ),
                )
                for task_id in approval.get("covered_tasks", [task["id"]]):
                    covered = store.get("tasks", task_id, db)
                    store.update_task(db, covered, "completed", reason, integrated_commit=candidate)
            store.event(
                db,
                "integration." + state,
                approval_id=approval["id"],
                candidate=candidate,
                reason=reason,
                branch="main",
            )
