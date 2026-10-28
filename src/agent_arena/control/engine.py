from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import psutil

from .store import Conflict, Store, encode, uid

ACTIVE = {"leased", "running", "verifying", "reviewing", "recovering"}
SUCCESS = {"ready_for_approval", "completed"}
FORBIDDEN = {".git", ".claude", ".codex", ".env", ".github", "AGENTS.md", "CLAUDE.md"}


def git(repo, *args, input=None):
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_AUTHOR_NAME="Arena staging",
        GIT_AUTHOR_EMAIL="arena@localhost",
        GIT_COMMITTER_NAME="Arena staging",
        GIT_COMMITTER_EMAIL="arena@localhost",
    )
    p = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        input=input,
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    if p.returncode:
        raise ValueError(p.stderr[-2000:] or p.stdout[-2000:])
    return p.stdout.strip()


def safe_scope(paths):
    if not paths or len(paths) > 32:
        raise ValueError("Declare 1–32 owned file or directory paths")
    out = []
    for path in paths:
        p = PurePosixPath(path)
        if (
            not path
            or p.is_absolute()
            or ".." in p.parts
            or path in {".", "*"}
            or any(x in FORBIDDEN for x in p.parts)
            or any(c in path for c in "*?[]\\\n\x00")
        ):
            raise ValueError(
                "Scope must contain explicit relative paths, excluding controller/config files"
            )
        out.append(str(p).rstrip("/"))
    return out


def within(path, scopes):
    return any(path == s or path.startswith(s + "/") for s in scopes)


def overlap(left, right):
    return any(within(a, [b]) or within(b, [a]) for a in left for b in right)


def atomic_json(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as f:
        f.write(encode(data))
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def identity(pid):
    p = psutil.Process(pid)
    return {"pid": pid, "created": p.create_time(), "boot": psutil.boot_time()}


def alive(attempt):
    ident = attempt.get("identity")
    if not ident:
        return False
    try:
        p = psutil.Process(ident["pid"])
        return (
            p.status() != psutil.STATUS_ZOMBIE
            and abs(p.create_time() - ident["created"]) < 0.01
            and abs(psutil.boot_time() - ident["boot"]) < 1
            and attempt["id"] in p.cmdline()
        )
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, PermissionError):
        return None


class Engine:
    def __init__(self, store: Store):
        self.store = store
        self.halt = threading.Event()
        self.thread = None
        self.lock = None
        self.children = []
        self.integrations = {}
        self.integrator = ThreadPoolExecutor(max_workers=2, thread_name_prefix="arena-integration")

    def enroll(self, name, repo, scope, checks, protected, trusted=False):
        repo = Path(repo).expanduser().resolve()
        controller = Path(__file__).resolve().parents[3]
        if repo == controller or repo in controller.parents or repo == Path.home():
            raise ValueError("The running controller and home directory cannot be worker projects")
        if not trusted:
            raise ValueError(
                "Enrollment requires explicit review of repository checks/hooks/plugins"
            )
        scope = safe_scope(scope)
        protected = safe_scope(protected)
        if overlap(scope, protected):
            raise ValueError("Worker scope overlaps frozen acceptance files")
        if not checks or any(
            not c.get("argv") or not all(isinstance(x, str) for x in c["argv"]) for c in checks
        ):
            raise ValueError("Frozen acceptance argv commands are required")
        if git(repo, "status", "--porcelain"):
            raise ValueError(
                "Enroll a clean committed source; uncommitted work must be preserved first"
            )
        head = git(repo, "rev-parse", "HEAD")
        project_id = uid("project")
        root = self.store.home / "projects" / project_id
        root.mkdir(parents=True, mode=0o700)
        stage = root / "staging"
        git(root, "clone", "--no-hardlinks", "--", str(repo), str(stage))
        git(stage, "remote", "remove", "origin")
        git(stage, "switch", "-c", "arena/staging")
        data = dict(
            name=name,
            repo=str(repo),
            staging=str(stage),
            base=head,
            head=head,
            scope=scope,
            checks=checks,
            protected=protected,
            enabled=True,
            priority=1,
            last_dispatch=0,
            trusted=True,
            created=time.time(),
        )
        with self.store.transaction() as db:
            if any(p["repo"] == str(repo) for p in self.store.rows("projects", db)):
                raise Conflict("Project already enrolled")
            db.execute("INSERT INTO projects(id,data) VALUES(?,?)", (project_id, encode(data)))
            self.store.event(db, "project.enrolled", project_id=project_id, name=name)
        return self.store.get("projects", project_id)

    def add_goal(
        self,
        db,
        project_id,
        title,
        instructions,
        scope,
        dependencies=(),
        review=True,
        timeout=300,
        planning=False,
        director=False,
    ):
        project = self.store.get("projects", project_id, db)
        scope = safe_scope(scope)
        if not all(within(p, project["scope"]) for p in scope):
            raise ValueError("Task scope exceeds enrolled project authority")
        if not title.strip() or not instructions.strip() or len(instructions) > 20000:
            raise ValueError(
                "Provide an outcome and bounded instructions (maximum 20,000 characters)"
            )
        if not 30 <= timeout <= 7200:
            raise ValueError("Worker timeout must be between 30 and 7200 seconds")
        for dependency in dependencies:
            predecessor = self.store.get("tasks", dependency, db)
            if predecessor["project_id"] != project_id:
                raise ValueError("Dependencies must belong to the same project")
            if predecessor["phase"] == "plan":
                raise ValueError("Depend on the planner's build tasks, not its planning completion")
        goal_id, task_id = uid("goal"), uid("task")
        db.execute(
            "INSERT INTO goals VALUES(?,?,?)",
            (
                goal_id,
                project_id,
                encode(dict(title=title, request=instructions, created=time.time())),
            ),
        )
        data = dict(
            title=title,
            instructions=instructions,
            scope=scope,
            review=review,
            timeout=timeout,
            created=time.time(),
            updated=time.time(),
            reason="Ready for a bounded worker",
            repairs=0,
            max_repairs=2,
            phase="plan" if planning else "build",
            director=director,
            cooldown_until=0,
            retries=0,
            instructions_pending=[],
        )
        db.execute(
            "INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",
            (task_id, project_id, goal_id, "ready", encode(data)),
        )
        for dependency in dependencies:
            db.execute("INSERT INTO task_dependencies VALUES(?,?)", (task_id, dependency))
        self.store.event(
            db, "goal.created", goal_id=goal_id, task_id=task_id, project_id=project_id, title=title
        )
        return {"goal_id": goal_id, "task_id": task_id}

    def start(self):
        self.lock = (self.store.home / "scheduler.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Another scheduler owns this state directory") from None
        self.thread = threading.Thread(target=self.loop, name="arena-scheduler", daemon=True)
        self.thread.start()

    def close(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=70)
        if self.lock:
            self.lock.close()
        self.integrator.shutdown(wait=False, cancel_futures=True)

    def loop(self):
        while not self.halt.is_set():
            try:
                self.tick()
            except Exception as exc:
                with self.store.transaction() as db:
                    self.store.event(db, "scheduler.error", reason=str(exc)[:1000])
            self.halt.wait(0.6)

    def tick(self):
        self.children = [p for p in self.children if p.poll() is None]
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)", (encode(time.time()),))
        for attempt in self.store.rows("attempts"):
            if attempt["state"] not in ACTIVE:
                continue
            result_path = Path(attempt["artifact_dir"]) / "result.json"
            living = alive(attempt)
            if living is None:
                with self.store.transaction() as db:
                    task = self.store.get("tasks", attempt["task_id"], db)
                    if task["state"] != "recovering":
                        self.store.update_task(
                            db,
                            task,
                            "recovering",
                            "Host denied process inspection; "
                            "lease retained until ownership is known",
                        )
                continue
            if result_path.is_file():
                if living is True:
                    # Completion is authoritative only after the guardian exits.
                    continue
                self.finish(attempt, json.loads(result_path.read_text()))
                continue
            if living is True:
                continue  # Independent guardian owns timeout/cancellation; no duplicate dispatch.
            if time.time() - attempt["created"] < 15:
                continue  # Spawn registration window; token remains authoritative.
            from .recovery import orphan_stopped

            if not orphan_stopped(attempt):
                with self.store.transaction() as db:
                    task = self.store.get("tasks", attempt["task_id"], db)
                    if task["state"] != "recovering":
                        self.store.update_task(
                            db,
                            task,
                            "recovering",
                            "Guardian lost; retaining lease until its owned child is stopped",
                        )
                continue
            with self.store.transaction() as db:
                current = self.store.get("attempts", attempt["id"], db)
                if current["state"] not in ACTIVE:
                    continue
                task = self.store.get("tasks", attempt["task_id"], db)
                db.execute("UPDATE attempts SET state='interrupted' WHERE id=?", (attempt["id"],))
                db.execute("UPDATE tasks SET token=token+1 WHERE id=?", (task["id"],))
                recoveries = task.get("recoveries", 0)
                state = (
                    "cancelled"
                    if task.get("cancel_requested")
                    else ("cooldown" if recoveries < 2 else "interrupted")
                )
                self.store.update_task(
                    db,
                    task,
                    state,
                    "Interrupted worker stopped; bounded recovery queued"
                    if state == "cooldown"
                    else "Recovery budget exhausted; evidence preserved",
                    recoveries=recoveries + 1,
                    cooldown_until=time.time() + min(30, 5 * 2**recoveries),
                )
        if self.store.setting("paused"):
            return
        from .publication import publish

        for approval in self.store.rows("approvals"):
            if approval.get("publication", {}).get("state") == "queued":
                project_id = self.store.get("tasks", approval["task_id"])["project_id"]
                self.submit_integration(project_id, publish, self.store, approval)
        for task in self.store.rows("tasks"):
            if task["state"] == "integrating":
                self.submit_integration(task["project_id"], self.integrate, task)
        if psutil.virtual_memory().available < 512 * 1024 * 1024:
            return
        attempt = self.claim()
        if attempt:
            artifact = Path(attempt["artifact_dir"])
            artifact.mkdir(parents=True, mode=0o700, exist_ok=True)
            with (artifact / "guardian.log").open("ab") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "agent_arena.control.worker",
                        str(self.store.home),
                        attempt["id"],
                    ],
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            self.children.append(child)

    def submit_integration(self, project_id, callback, *args):
        pending = self.integrations.get(project_id)
        if pending is not None:
            if not pending.done():
                return
            pending.result()
        self.integrations[project_id] = self.integrator.submit(callback, *args)

    def claim(self):
        with self.store.transaction() as db:
            if json.loads(db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()[0]):
                return None
            attempts = self.store.rows("attempts", db)
            active = [a for a in attempts if a["state"] in ACTIVE]
            limit = json.loads(db.execute("SELECT value FROM meta WHERE key='limit'").fetchone()[0])
            if len(active) >= limit:
                for waiting in self.store.rows("tasks", db):
                    if waiting["state"] == "ready":
                        self.store.update_task(
                            db,
                            waiting,
                            "waiting_capacity",
                            "All configured worker slots are occupied",
                        )
                return None
            tasks = self.store.rows("tasks", db)
            projects = {p["id"]: p for p in self.store.rows("projects", db)}
            accounts = [
                a
                for a in self.store.rows("accounts", db)
                if a.get("enabled")
                and a.get("health") == "ready"
                and a.get("cooldown_until", 0) <= time.time()
                and a["id"] not in {x["account_id"] for x in active}
            ]
            candidates = sorted(
                [
                    t
                    for t in tasks
                    if t["state"]
                    in {
                        "ready",
                        "waiting_capacity",
                        "waiting_dependency",
                        "repair_queued",
                        "cooldown",
                    }
                ],
                key=lambda t: (
                    0 if t["phase"] == "review" or t["repairs"] else 1,
                    projects[t["project_id"]]["priority"],
                    projects[t["project_id"]]["last_dispatch"],
                    t["created"],
                ),
            )
            for task in candidates:
                project = projects[task["project_id"]]
                if not project["enabled"] or task.get("cooldown_until", 0) > time.time():
                    continue
                deps = db.execute(
                    "SELECT t.state FROM task_dependencies d JOIN tasks t ON "
                    "t.id=d.dependency_id WHERE d.task_id=?",
                    (task["id"],),
                ).fetchall()
                if any(x[0] not in SUCCESS for x in deps):
                    if task["state"] != "waiting_dependency":
                        self.store.update_task(
                            db,
                            task,
                            "waiting_dependency",
                            "Waiting for dependencies to reach verified staging",
                        )
                    continue
                peers = [
                    t
                    for t in tasks
                    if t["id"] != task["id"]
                    and t["project_id"] == task["project_id"]
                    and (
                        t["state"] in ACTIVE | {"integrating"}
                        or (
                            t.get("build_attempt")
                            and t["state"]
                            in {"ready", "waiting_capacity", "repair_queued", "cooldown"}
                        )
                    )
                ]
                writers = [t for t in peers if t["phase"] == "build"]
                if task["phase"] == "build" and (
                    len(writers) >= 2 or any(overlap(t["scope"], task["scope"]) for t in peers)
                ):
                    if task["state"] != "waiting_capacity":
                        self.store.update_task(
                            db,
                            task,
                            "waiting_capacity",
                            "Declared scope overlaps an active lease, or two project "
                            "writers are occupied",
                        )
                    continue
                director_phase = task.get("director") and task["phase"] in {
                    "plan",
                    "direct",
                    "replan",
                }
                role = "plan" if director_phase else task["phase"]
                eligible = [
                    a
                    for a in accounts
                    if role in a.get("roles", ["build", "review"])
                    and (not director_phase or a["provider"] == "codex")
                    and (task["phase"] != "review" or a["id"] != task.get("author_account"))
                ]
                if not eligible:
                    if task["state"] != "waiting_capacity":
                        self.store.update_task(
                            db,
                            task,
                            "waiting_capacity",
                            "No healthy eligible slot; review requires an account "
                            "independent of the author",
                        )
                    continue
                from .routing import select_route

                selected = select_route(task, eligible, attempts)
                if selected is None:
                    self.store.update_task(
                        db,
                        task,
                        "blocked",
                        "No supported model route remains; failed approaches are preserved",
                    )
                    continue
                account, routing = selected
                ident = uid("attempt")
                token = task["token"] + 1
                base = (
                    task.get("candidate")
                    if task["phase"] in {"review", "direct"}
                    else project["head"]
                )
                data = dict(
                    created=time.time(),
                    phase=task["phase"],
                    base=base,
                    artifact_dir=str(self.store.home / "attempts" / ident),
                    workspace=str(self.store.home / "workspaces" / ident),
                    identity=None,
                    reason=routing["reason"],
                    model=routing["model"],
                    effort=routing["effort"],
                    routing=routing,
                    instruction_count=len(task.get("instructions_pending", [])),
                )
                db.execute("UPDATE tasks SET token=? WHERE id=?", (token, task["id"]))
                self.store.update_task(db, task, "leased", data["reason"], attempt_id=ident)
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,?,?,?,?)",
                    (ident, task["id"], account["id"], token, "leased", encode(data)),
                )
                project["last_dispatch"] = time.time()
                pd = {k: v for k, v in project.items() if k not in {"id", "rev"}}
                db.execute("UPDATE projects SET data=? WHERE id=?", (encode(pd), project["id"]))
                self.store.event(
                    db, "attempt.claimed", attempt_id=ident, task_id=task["id"], token=token
                )
                self.store.event(
                    db, "routing.selected", attempt_id=ident, task_id=task["id"], **routing
                )
                return self.store.get("attempts", ident, db)
        return None

    def finish(self, attempt, result):
        with self.store.transaction() as db:
            task = self.store.get("tasks", attempt["task_id"], db)
            current = self.store.get("attempts", attempt["id"], db)
            if (
                current["state"] not in ACTIVE
                or task["token"] != attempt["token"]
                or result.get("token") != attempt["token"]
                or result.get("attempt_id") != attempt["id"]
            ):
                return False
            if result.get("status") == "passed":
                if attempt["phase"] == "build" and (
                    not result.get("candidate")
                    or not result.get("checks")
                    or any(c.get("status") != "PASS" for c in result["checks"])
                ):
                    raise ValueError(
                        "Successful completion lacks verified candidate/check evidence"
                    )
                if (
                    attempt["phase"] in {"review", "direct"}
                    and result.get("review", {}).get("approved") is not True
                ):
                    raise ValueError("Successful review lacks an explicit structured approval")
            db.execute(
                "UPDATE attempts SET state=?,data=? WHERE id=?",
                (
                    result["status"],
                    encode(
                        {
                            **{
                                k: v
                                for k, v in current.items()
                                if k not in {"id", "state", "task_id", "account_id", "token"}
                            },
                            "result": result,
                            "ended": time.time(),
                        }
                    ),
                    attempt["id"],
                ),
            )
            if task.get("cancel_requested") or result["status"] == "cancelled":
                self.store.update_task(
                    db, task, "cancelled", "Owned execution stopped; evidence preserved"
                )
            elif result["status"] == "passed":
                if len(task.get("instructions_pending", [])) > attempt.get("instruction_count", 0):
                    self.store.update_task(
                        db,
                        task,
                        "ready",
                        "New instructions arrived during execution; rebuilding before acceptance",
                        phase="plan"
                        if attempt["phase"] == "plan"
                        else "replan"
                        if task.get("director")
                        else "build",
                    )
                elif attempt["phase"] in {"plan", "replan"}:
                    from .planning import validate_plan

                    specification = None
                    if task.get("director"):
                        from .planning import validate_director_spec

                        specification = validate_director_spec(result["plan"], task["scope"])
                        with_spec = {
                            k: v
                            for k, v in self.store.get("goals", task["goal_id"], db).items()
                            if k not in {"id", "project_id"}
                        }
                        with_spec["specification"] = specification
                        db.execute(
                            "UPDATE goals SET data=? WHERE id=?",
                            (encode(with_spec), task["goal_id"]),
                        )
                        if specification["strategy"] == "clarify":
                            self.store.update_task(
                                db,
                                task,
                                "awaiting_input",
                                specification["summary"],
                                question=specification["question"],
                                specification=specification,
                            )
                            self.store.event(
                                db,
                                "director.question",
                                task_id=task["id"],
                                reason=specification["question"],
                            )
                            return True
                        planned = specification["tasks"]
                    else:
                        planned = validate_plan(result["plan"], task["scope"])
                    if attempt["phase"] == "replan":
                        if len(planned) != 1 or planned[0]["dependencies"]:
                            raise ValueError("Repair director must produce one corrective task")
                        item = planned[0]
                        self.store.update_task(
                            db,
                            task,
                            "ready",
                            specification["summary"],
                            phase="build",
                            instructions=item["instructions"]
                            + "\nAcceptance: "
                            + encode(item["acceptance"]),
                            specification=specification,
                            complexity=item.get("complexity", "moderate"),
                            risk=item.get("risk", "medium"),
                            question="",
                        )
                        self.store.event(
                            db,
                            "director.replanned",
                            task_id=task["id"],
                            reason=specification["summary"],
                        )
                        return True
                    ids = [uid("task") for _ in planned]
                    for i, item in enumerate(planned):
                        data = {
                            k: v
                            for k, v in task.items()
                            if k
                            not in {
                                "id",
                                "rev",
                                "token",
                                "state",
                                "project_id",
                                "goal_id",
                                "attempt_id",
                            }
                        }
                        data.update(
                            title=item["title"],
                            instructions=item["instructions"]
                            + (
                                "\nAcceptance: "
                                + encode(item["acceptance"])
                                + "\nSafety constraints: "
                                + encode(specification["constraints"])
                                if specification
                                else ""
                            ),
                            scope=item["scope"],
                            phase="build",
                            created=time.time(),
                            reason="Validated plan; frozen project checks and goal authority "
                            "inherited",
                            planner_attempt=attempt["id"],
                            specification=specification,
                            complexity=item.get("complexity", "moderate"),
                            risk=item.get("risk", "medium"),
                        )
                        db.execute(
                            "INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",
                            (ids[i], task["project_id"], task["goal_id"], "ready", encode(data)),
                        )
                    inherited = [
                        r[0]
                        for r in db.execute(
                            "SELECT dependency_id FROM task_dependencies WHERE task_id=?",
                            (task["id"],),
                        )
                    ]
                    for i, item in enumerate(planned):
                        for dependency in [ids[j] for j in item["dependencies"]] + inherited:
                            db.execute(
                                "INSERT OR IGNORE INTO task_dependencies VALUES(?,?)",
                                (ids[i], dependency),
                            )
                    self.store.update_task(
                        db,
                        task,
                        "completed",
                        specification["summary"]
                        if specification
                        else "Planning complete; child build tasks queued. No code accepted yet.",
                        planned_tasks=ids,
                        specification=specification,
                    )
                elif attempt["phase"] == "review":
                    self.store.update_task(
                        db,
                        task,
                        "ready" if task.get("director") else "integrating",
                        "Independent checks and review passed; director is checking the outcome"
                        if task.get("director")
                        else "Independent review passed",
                        phase="direct" if task.get("director") else task["phase"],
                        review_attempt=attempt["id"],
                    )
                elif attempt["phase"] == "direct":
                    self.store.update_task(
                        db,
                        task,
                        "integrating",
                        result["review"].get("summary")
                        or "The requested outcome passed review; checking the combined change",
                        director_review_attempt=attempt["id"],
                    )
                else:
                    account = self.store.get("accounts", attempt["account_id"], db)
                    self.store.update_task(
                        db,
                        task,
                        "ready" if task["review"] else "integrating",
                        "Checks passed; independent review queued"
                        if task["review"]
                        else "Checks passed; staging queued",
                        phase="review" if task["review"] else "build",
                        candidate=result["candidate"],
                        candidate_base=attempt["base"],
                        author_account=account["id"],
                        author_provider=account["provider"],
                        build_attempt=attempt["id"],
                    )
            elif result["status"] in {"needs_auth", "needs_permission"}:
                self.store.update_task(db, task, result["status"], result["reason"])
                account = self.store.get("accounts", attempt["account_id"], db)
                account["health"] = result["status"]
                account["enabled"] = False
                db.execute(
                    "UPDATE accounts SET rev=rev+1,data=? WHERE id=?",
                    (
                        encode({k: v for k, v in account.items() if k not in {"id", "rev"}}),
                        account["id"],
                    ),
                )
            elif result["status"] in {"rate_limited", "transient"} and task["retries"] < 2:
                delay = min(300, 30 * 2 ** task["retries"])
                self.store.update_task(
                    db,
                    task,
                    "cooldown",
                    result["reason"],
                    retries=task["retries"] + 1,
                    cooldown_until=time.time() + delay,
                )
                account = self.store.get("accounts", attempt["account_id"], db)
                account.update(cooldown_until=time.time() + delay, reset_time=None)
                db.execute(
                    "UPDATE accounts SET rev=rev+1,data=? WHERE id=?",
                    (
                        encode({k: v for k, v in account.items() if k not in {"id", "rev"}}),
                        account["id"],
                    ),
                )
            elif (
                task["repairs"] < task["max_repairs"]
                and result["status"] in {"failed", "model_unavailable"}
                and (attempt["phase"] != "plan" or task.get("director"))
            ):
                self.store.update_task(
                    db,
                    task,
                    "repair_queued",
                    "Bounded repair: " + result["reason"],
                    phase=("plan" if attempt["phase"] == "plan" else "replan")
                    if task.get("director")
                    else "build",
                    repairs=task["repairs"] + 1,
                    repair_evidence=result,
                    repair_workspace=attempt["workspace"],
                )
            else:
                self.store.update_task(db, task, "blocked", result["reason"])
            self.store.event(
                db, "attempt.finished", attempt_id=attempt["id"], status=result["status"]
            )
        return True

    def integrate(self, task):
        from .worker import candidate_fingerprint, checks_run

        project = self.store.get("projects", task["project_id"])
        if not project["enabled"]:
            return
        root = Path(project["staging"])
        with (root.parent / "integration.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current_task = self.store.get("tasks", task["id"])
            if current_task["state"] != "integrating" or current_task["token"] != task["token"]:
                return
            receipt = self.store.home / "attempts" / task["build_attempt"] / "integration.json"
            try:
                current_head = git(root, "rev-parse", "HEAD")
                # Durable intent lets restart recognise a completed staging commit.
                intent = receipt.with_name("integration-intent.json")
                saved = json.loads(intent.read_text()) if intent.exists() else None
                if saved and current_head == saved.get("candidate"):
                    result = saved
                elif saved and current_head == saved.get("base"):
                    git(root, "merge", "--ff-only", saved["candidate"])
                    result = saved
                else:
                    if current_head != project["head"]:
                        raise Conflict(
                            "Staging changed outside its recorded integration; inspect "
                            "before continuing"
                        )
                    build = self.store.get("attempts", task["build_attempt"])
                    git(root, "fetch", "--no-tags", build["workspace"], task["candidate"])
                    tries = task.get("integration_tries", 0)
                    if tries >= 3:
                        raise Conflict("Integration recovery budget exhausted; evidence preserved")
                    with self.store.transaction() as db:
                        live = self.store.get("tasks", task["id"], db)
                        self.store.update_task(
                            db,
                            live,
                            "integrating",
                            "Composition checks running",
                            integration_tries=tries + 1,
                        )
                    work = root.parent / ("integrate-" + task["id"] + "-" + uid("work"))
                    git(root, "worktree", "add", "--detach", str(work), current_head)
                    diff = git(
                        Path(build["workspace"]),
                        "diff",
                        "--binary",
                        task["candidate_base"],
                        task["candidate"],
                    )
                    if diff:
                        git(work, "apply", "--3way", "-", input=diff + "\n")
                    fingerprint = candidate_fingerprint(work)
                    check_results = checks_run(project, work, receipt.parent, "integration")
                    if candidate_fingerprint(work) != fingerprint:
                        raise ValueError("Composition checks changed candidate files")
                    if not all(c["status"] == "PASS" for c in check_results):
                        raise ValueError("Combined staging checks failed: " + encode(check_results))
                    git(work, "add", "--all")
                    git(work, "commit", "--allow-empty", "-m", "Arena staged: " + task["title"])
                    sha = git(work, "rev-parse", "HEAD")
                    result = dict(
                        candidate=sha,
                        base=current_head,
                        checks=check_results,
                        task_id=task["id"],
                        token=task["token"],
                    )
                    atomic_json(intent, result)
                    git(root, "merge", "--ff-only", sha)
                atomic_json(receipt, result)
                with self.store.transaction() as db:
                    live = self.store.get("tasks", task["id"], db)
                    if live["token"] != task["token"] or live["state"] != "integrating":
                        raise Conflict("Stale integration completion")
                    project = self.store.get("projects", project["id"], db)
                    project["head"] = result["candidate"]
                    db.execute(
                        "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
                        (
                            encode({k: v for k, v in project.items() if k not in {"id", "rev"}}),
                            project["id"],
                        ),
                    )
                    covered = [
                        t["id"]
                        for t in self.store.rows("tasks", db)
                        if t["project_id"] == project["id"] and t["state"] == "ready_for_approval"
                    ] + [task["id"]]
                    for old in self.store.rows("approvals", db):
                        if old["task_id"] in covered and old["state"] == "pending":
                            db.execute(
                                "UPDATE approvals SET state='superseded',rev=rev+1 WHERE id=?",
                                (old["id"],),
                            )
                    result["covered_tasks"] = covered
                    result["review_base"] = project.get("accepted_head", project["base"])
                    approval = uid("approval")
                    db.execute(
                        "INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",
                        (
                            approval,
                            task["id"],
                            "pending",
                            encode(
                                {
                                    **result,
                                    "operation": "accept_staged_change",
                                    "created": time.time(),
                                }
                            ),
                        ),
                    )
                    self.store.update_task(
                        db,
                        live,
                        "ready_for_approval",
                        "Combined checks passed; exact staging commit awaits acceptance",
                        staged=result["candidate"],
                        approval_id=approval,
                    )
            except Exception as exc:
                with self.store.transaction() as db:
                    live = self.store.get("tasks", task["id"], db)
                    self.store.update_task(
                        db,
                        live,
                        "blocked",
                        "Integration preserved for inspection: " + str(exc)[-2000:],
                    )

    def cancel(self, task_id):
        self.store.get("tasks", task_id)
        for attempt in self.store.rows("attempts"):
            if attempt["task_id"] == task_id and attempt["state"] in ACTIVE:
                state = alive(attempt)
                if state:
                    os.killpg(attempt["identity"]["pid"], signal.SIGTERM)
                elif state is None:
                    raise Conflict(
                        "Process identity ambiguous; refusing to signal an unrelated process"
                    )
