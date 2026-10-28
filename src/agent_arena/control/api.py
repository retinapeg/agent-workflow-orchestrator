from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .engine import ACTIVE, Engine, git
from .store import Conflict, Store, encode
from .worker import redact


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Goal(Model):
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=20000)
    scope: list[str]
    dependencies: list[str] = []
    review: bool = True
    planning: bool = False
    timeout: int = Field(default=300, ge=30, le=7200)
    revision: int


class Change(Model):
    revision: int
    text: str = Field(default="", max_length=4000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=100)
    limit: int | None = Field(default=None, ge=1, le=3)
    integration_policy: str | None = Field(default=None, pattern="^(manual|auto_local_main)$")
    candidate: str | None = None
    base: str | None = None
    decision: str | None = None


class Pair(Model):
    code: str = Field(min_length=8, max_length=128)


# A trusted device pairs once. Its session lasts SESSION_SECONDS and is renewed on use
# once RENEW_AFTER_SECONDS have elapsed, so an active device never re-pairs; an unused
# one expires. Logout and per-device revocation end a session immediately.
SESSION_SECONDS = 30 * 86400
RENEW_AFTER_SECONDS = 7 * 86400


class Intent(Model):
    instructions: str = Field(min_length=1, max_length=20000)
    revision: int


def make_app(home: Path, origin="http://127.0.0.1:8790", run_scheduler=True):
    from .configuration import validate_origin

    origin = validate_origin(origin)
    store = Store(home)
    engine = Engine(store)
    origins = {origin, "http://localhost:8790", "http://127.0.0.1:8790"}
    from urllib.parse import urlsplit

    hosts = {urlsplit(o).netloc for o in origins}
    failures = []

    @asynccontextmanager
    async def lifespan(app):
        if run_scheduler:
            engine.start()
        yield
        if run_scheduler:
            engine.close()

    app = FastAPI(
        title="Arena build control",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.engine = engine

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(
            {"detail": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 400
        )

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Object not found"}, status_code=404)

    def session(request):
        token = request.cookies.get("arena_session", "")
        with contextlib.closing(store.connect()) as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE hash=? AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        return dict(row) if row else None

    def session_cookie(response, token):
        response.set_cookie(
            "arena_session",
            token,
            httponly=True,
            secure=origin.startswith("https:"),
            samesite="strict",
            max_age=SESSION_SECONDS,
        )

    def touch(auth, request, response):
        """Record activity; renew the cookie and expiry once the renewal interval passed."""
        now = time.time()
        renew = auth["expires"] - now < SESSION_SECONDS - RENEW_AFTER_SECONDS
        seen = auth.get("last_seen") or 0
        if not renew and now - seen < 60:
            return
        with store.transaction() as db:
            db.execute(
                "UPDATE sessions SET last_seen=?, expires=? WHERE hash=?",
                (now, now + SESSION_SECONDS if renew else auth["expires"], auth["hash"]),
            )
        if renew:
            session_cookie(response, request.cookies["arena_session"])

    @app.middleware("http")
    async def security(request, call_next):
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        if request.url.path.startswith("/api/"):
            if (
                request.method not in {"GET", "HEAD"}
                and request.headers.get("origin") not in origins
            ):
                return JSONResponse({"detail": "Origin rejected"}, status_code=403)
            if request.url.path != "/api/v1/pair":
                auth = session(request)
                if not auth:
                    return JSONResponse(
                        {"detail": "Pair this device on the host first"}, status_code=401
                    )
                if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
                    request.headers.get("x-csrf-token", "").encode("utf-8", "replace"),
                    auth["csrf"].encode(),
                ):
                    return JSONResponse({"detail": "CSRF token rejected"}, status_code=403)
                response = await call_next(request)
                if request.url.path != "/api/v1/logout":
                    touch(auth, request, response)
                return finish_headers(request, response)
        response = await call_next(request)
        return finish_headers(request, response)

    def finish_headers(request, response):
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api/") else "no-cache"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' "
            "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.post("/api/v1/pair")
    def pair(body: Pair, request: Request):
        failures[:] = [t for t in failures if time.time() - t < 60]
        if len(failures) >= 5:
            return JSONResponse(
                {"detail": "Pairing rate limited; wait one minute"}, status_code=429
            )
        path = store.home / "pairing.json"
        with store.transaction() as db:
            code = json.loads(path.read_text()) if path.exists() else {}
            if code.get("expires", 0) < time.time() or not secrets.compare_digest(
                code.get("hash", ""), hashlib.sha256(body.code.encode()).hexdigest()
            ):
                failures.append(time.time())
                return JSONResponse({"detail": "Pairing code expired or invalid"}, status_code=403)
            if db.execute("SELECT 1 FROM meta WHERE key=?", ("paired:" + code["hash"],)).fetchone():
                return JSONResponse({"detail": "Pairing code already used"}, status_code=403)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            device = secrets.token_urlsafe(8)
            label = request.headers.get("user-agent", "")[:160]
            db.execute(
                "INSERT INTO sessions(hash,expires,csrf,id,label,created,last_seen) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    time.time() + SESSION_SECONDS,
                    csrf,
                    device,
                    label,
                    time.time(),
                    time.time(),
                ),
            )
            db.execute("INSERT INTO meta VALUES(?,?)", ("paired:" + code["hash"], "true"))
            store.event(db, "device.paired", device_id=device, label=label)
        response = JSONResponse({"csrf": csrf, "device_id": device})
        session_cookie(response, token)
        return response

    @app.get("/api/v1/session")
    def get_session(request: Request):
        auth = session(request)
        return {"csrf": auth["csrf"], "device_id": auth.get("id"), "expires": auth["expires"]}

    @app.get("/api/v1/devices")
    def devices(request: Request):
        auth = session(request)
        with contextlib.closing(store.connect()) as db:
            rows = db.execute("SELECT * FROM sessions WHERE expires>?", (time.time(),)).fetchall()
        return [
            {
                "id": r["id"] or r["hash"][:12],
                "label": r["label"] or "Paired before device labels were recorded",
                "created": r["created"],
                "last_seen": r["last_seen"],
                "expires": r["expires"],
                "current": r["hash"] == auth["hash"],
            }
            for r in rows
        ]

    @app.post("/api/v1/devices/{ident}/revoke")
    def revoke_device(ident: str, body: Change, request: Request):
        with store.transaction() as db:
            row = db.execute(
                "SELECT hash FROM sessions WHERE id=? OR substr(hash,1,12)=?", (ident, ident)
            ).fetchone()
            if not row:
                raise KeyError(ident)
            db.execute("DELETE FROM sessions WHERE hash=?", (row["hash"],))
            store.event(db, "device.revoked", device_id=ident)
        return {"ok": True}

    @app.post("/api/v1/logout")
    def logout(request: Request):
        auth = session(request)
        with store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE hash=?", (auth["hash"],))
        response = JSONResponse({"ok": True})
        response.delete_cookie("arena_session")
        return response

    def snapshot():
        with contextlib.closing(store.connect()) as db:
            db.execute("BEGIN")
            projects, tasks, attempts, accounts, approvals = [
                store.rows(t, db)
                for t in ["projects", "tasks", "attempts", "accounts", "approvals"]
            ]
            events = [
                {**dict(r), "data": json.loads(r["data"])}
                for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")
            ]
            dependencies = {}
            for task_id, dependency_id in db.execute(
                "SELECT task_id, dependency_id FROM task_dependencies"
            ):
                dependencies.setdefault(task_id, []).append(dependency_id)
            meta = {
                r["key"]: json.loads(r["value"])
                for r in db.execute(
                    "SELECT * FROM meta WHERE key IN ('paused','limit','heartbeat','control_rev')"
                )
            }
            db.commit()
        for a in accounts:
            for key in ("config_home", "executable"):
                a.pop(key, None)
        for a in attempts:
            a.pop("identity", None)
        for t in tasks:
            t["dependencies"] = dependencies.get(t["id"], [])
        return {
            "projects": projects,
            "tasks": tasks,
            "attempts": attempts,
            "agents": accounts,
            "approvals": approvals,
            "events": events,
            "paused": meta["paused"],
            "limit": meta["limit"],
            "revision": meta.get("control_rev", 0),
            "heartbeat": meta.get("heartbeat"),
            "version": "0.4.1-local",
            "server_time": time.time(),
            "api_fallback": False,
            "goals": store.rows("goals"),
        }

    @app.get("/api/v1/overview")
    def overview():
        return snapshot()

    @app.get("/api/v1/health")
    def health():
        with contextlib.closing(store.connect()) as db:
            row = db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
        age = time.time() - float(row[0]) if row else None
        healthy = age is not None and age < 30
        return JSONResponse(
            {"scheduler_ready": healthy, "heartbeat_age_seconds": age},
            status_code=200 if healthy else 503,
        )

    @app.get("/api/v1/projects")
    def projects():
        return store.rows("projects")

    @app.get("/api/v1/agents")
    def agents():
        return snapshot()["agents"]

    @app.get("/api/v1/events")
    async def events(request: Request, after: int = 0):
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = 0

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                if not session(request):
                    return
                with contextlib.closing(store.connect()) as db:
                    maximum = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
                    if maximum - cursor > 500 or cursor > maximum:
                        cursor = maximum
                        yield f"id: {cursor}\nevent: snapshot\ndata: {{}}\n\n"
                    rows = db.execute(
                        "SELECT * FROM events WHERE id>? ORDER BY id LIMIT 100", (cursor,)
                    ).fetchall()
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: update\ndata: {encode(dict(row))}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    def revision(obj, expected):
        if obj["rev"] != expected:
            raise Conflict("This view is stale. Refresh before changing it.")

    def command(request, payload, callback):
        return store.command(
            request.headers.get("idempotency-key", ""),
            {"path": request.url.path, **payload},
            callback,
        )

    @app.post("/api/v1/projects/{project_id}/requests")
    def product_request(project_id: str, body: Intent, request: Request):
        def create(db):
            project = store.get("projects", project_id, db)
            revision(project, body.revision)
            return engine.add_goal(
                db,
                project_id,
                body.instructions.strip()[:100],
                body.instructions,
                project["scope"],
                review=True,
                timeout=900,
                planning=True,
                director=True,
            )

        return command(request, body.model_dump(), create)

    @app.post("/api/v1/projects/{project_id}/goals")
    def goal(project_id: str, body: Goal, request: Request):
        def create(db):
            revision(store.get("projects", project_id, db), body.revision)
            return engine.add_goal(
                db,
                project_id,
                body.title,
                body.instructions,
                body.scope,
                body.dependencies,
                body.review,
                body.timeout,
                body.planning,
            )

        return command(request, body.model_dump(), create)

    @app.post("/api/v1/projects/{ident}/{action}")
    def project_action(ident: str, action: str, body: Change, request: Request):
        if action not in {"pause", "resume", "policy"}:
            raise ValueError("Unsupported project action")

        def update(db):
            p = store.get("projects", ident, db)
            revision(p, body.revision)
            if action in {"pause", "resume"}:
                p["enabled"] = action == "resume"
            if body.priority is not None:
                p["priority"] = body.priority
            if body.integration_policy is not None:
                p["integration_policy"] = body.integration_policy
            db.execute(
                "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
                (encode({k: v for k, v in p.items() if k not in {"id", "rev"}}), ident),
            )
            store.event(db, "project.changed", project_id=ident)
            return {"ok": True}

        return command(request, body.model_dump(), update)

    @app.post("/api/v1/agents/{ident}")
    def agent_action(ident: str, body: Change, request: Request):
        def update(db):
            a = store.get("accounts", ident, db)
            revision(a, body.revision)
            if body.enabled and a["health"] != "ready":
                raise Conflict("Run the host account health check before enabling this slot")
            a["enabled"] = bool(body.enabled)
            db.execute(
                "UPDATE accounts SET rev=rev+1,data=? WHERE id=?",
                (encode({k: v for k, v in a.items() if k not in {"id", "rev"}}), ident),
            )
            store.event(db, "account.changed", account_id=ident)
            return {"ok": True}

        return command(request, body.model_dump(), update)

    @app.post("/api/v1/control/{action}")
    def control(action: str, body: Change, request: Request):
        if action not in {"pause-dispatch", "resume-dispatch", "stop-all", "capacity"}:
            raise ValueError("Unsupported control")

        def update(db):
            row = db.execute("SELECT value FROM meta WHERE key='control_rev'").fetchone()
            rev = int(row[0]) if row else 0
            if body.revision != rev:
                raise Conflict("Control view is stale")
            if action != "capacity":
                db.execute(
                    "UPDATE meta SET value=? WHERE key='paused'",
                    (encode(action != "resume-dispatch"),),
                )
            if body.limit is not None:
                db.execute("UPDATE meta SET value=? WHERE key='limit'", (encode(body.limit),))
            db.execute("INSERT OR REPLACE INTO meta VALUES('control_rev',?)", (encode(rev + 1),))
            if action == "stop-all":
                for t in store.rows("tasks", db):
                    if t["state"] in ACTIVE:
                        store.update_task(
                            db, t, t["state"], "Cancellation requested", cancel_requested=True
                        )
            store.event(db, "control.changed", action=action)
            return {"ok": True}

        result = command(request, body.model_dump(), update)
        if action == "stop-all":
            unconfirmed = []
            for t in store.rows("tasks"):
                if t.get("cancel_requested"):
                    try:
                        engine.cancel(t["id"])
                    except Exception as exc:  # Keep signalling the rest; report the gap.
                        unconfirmed.append({"task_id": t["id"], "reason": str(exc)[:500]})
            if unconfirmed:
                result = {**result, "unconfirmed": unconfirmed}
        return result

    @app.post("/api/v1/tasks/{ident}/{action}")
    def task_action(ident: str, action: str, body: Change, request: Request):
        if action not in {"instructions", "cancel", "retry", "answer"}:
            raise ValueError("Unsupported task action")

        def update(db):
            t = store.get("tasks", ident, db)
            revision(t, body.revision)
            if t["state"] in {"integrating", "completed", "ready_for_approval", "cancelled"}:
                raise Conflict("This task is staged, complete or cancelled; create a new request")
            if action == "answer":
                if t["state"] != "awaiting_input" or not body.text.strip():
                    raise Conflict("This request is not waiting for a behavior answer")
                if t.get("clarifications", 0) >= 2:
                    raise Conflict("Clarification limit reached; send a new product request")
                store.update_task(
                    db,
                    t,
                    "ready",
                    "Thanks. The director is updating the specification.",
                    instructions_pending=[*t.get("instructions_pending", []), body.text],
                    clarifications=t.get("clarifications", 0) + 1,
                    question="",
                )
            elif action == "instructions":
                if not body.text.strip():
                    raise ValueError("Instruction is empty")
                store.update_task(
                    db,
                    t,
                    "ready" if t.get("director") and t["state"] not in ACTIVE else t["state"],
                    "Instruction queued for the next attempt; current CLI is unchanged",
                    instructions_pending=[*t.get("instructions_pending", []), body.text],
                    phase=("plan" if t["phase"] == "plan" else "replan")
                    if t.get("director") and t["state"] not in ACTIVE
                    else t["phase"],
                    question="",
                )
            elif action == "cancel":
                store.update_task(
                    db,
                    t,
                    t["state"] if t["state"] in ACTIVE else "cancelled",
                    "Cancellation requested",
                    cancel_requested=True,
                )
            else:
                if t["state"] not in {
                    "blocked",
                    "interrupted",
                    "failed",
                    "needs_auth",
                    "needs_permission",
                }:
                    raise Conflict("Only stopped/blocked work can be retried")
                if t["repairs"] >= t["max_repairs"]:
                    raise Conflict("Repair budget exhausted; create a new explicitly scoped goal")
                if any(
                    a["task_id"] == ident and a["state"] in ACTIVE
                    for a in store.rows("attempts", db)
                ):
                    raise Conflict("An attempt still owns this task")
                store.update_task(
                    db,
                    t,
                    "ready",
                    "Explicit retry queued",
                    repairs=t["repairs"] + 1,
                    cancel_requested=False,
                )
            return {"ok": True}

        result = command(request, body.model_dump(), update)
        if action == "cancel":
            engine.cancel(ident)
        return result

    @app.get("/api/v1/attempts/{ident}")
    def attempt(ident: str):
        a = store.get("attempts", ident)
        a.pop("identity", None)
        root = Path(a["artifact_dir"])
        a["files"] = (
            [
                p.name
                for p in root.iterdir()
                if p.is_file() and p.name not in {"child.json", "guardian.lock"}
            ]
            if root.exists()
            else []
        )
        return a

    @app.get("/api/v1/attempts/{ident}/checks")
    def attempt_checks(ident: str):
        return store.get("attempts", ident).get("result", {}).get("checks", [])

    @app.get("/api/v1/attempts/{ident}/diff")
    def attempt_diff(ident: str):
        a = store.get("attempts", ident)
        p = Path(a["artifact_dir"]) / "diff.patch"
        return {
            "text": redact(p.read_text()[:200000])
            if p.exists()
            else "Candidate diff not yet available"
        }

    @app.get("/api/v1/attempts/{ident}/artifacts/{name}")
    def artifact(ident: str, name: str):
        a = store.get("attempts", ident)
        if "/" in name or name.startswith(".") or name in {"child.json", "guardian.lock"}:
            raise ValueError("Artifact not exportable")
        p = Path(a["artifact_dir"]) / name
        if not p.is_file() or p.is_symlink():
            raise KeyError(name)
        return {"name": name, "text": redact(p.read_text(errors="replace")[-200000:])}

    @app.post("/api/v1/approvals/{ident}/decision")
    def decision(ident: str, body: Change, request: Request):
        if body.decision not in {"accept", "reject", "request_changes"}:
            raise ValueError("Unsupported decision")
        # Resolve the actual staging head outside the write transaction.
        a = store.get("approvals", ident)
        t = store.get("tasks", a["task_id"])
        p = store.get("projects", t["project_id"])
        actual = git(Path(p["staging"]), "rev-parse", "HEAD")

        def update(db):
            a = store.get("approvals", ident, db)
            revision(a, body.revision)
            project = store.get("projects", t["project_id"], db)
            if (
                a["state"] != "pending"
                or body.candidate != a["candidate"]
                or body.base != a["base"]
                or project["head"] != a["candidate"]
                or actual != a["candidate"]
            ):
                raise Conflict(
                    "Approval no longer matches the current staging commit/base; "
                    "inspect the latest change"
                )
            decision_data = {
                k: v for k, v in a.items() if k not in {"id", "rev", "state", "task_id"}
            }
            decision_data.update(feedback=body.text, decided=time.time())
            db.execute(
                "UPDATE approvals SET state=?,data=?,rev=rev+1 WHERE id=?",
                (body.decision, encode(decision_data), ident),
            )
            live = store.get("tasks", a["task_id"], db)
            store.update_task(
                db,
                live,
                "completed" if body.decision == "accept" else "blocked",
                "Staged change accepted locally; nothing published"
                if body.decision == "accept"
                else (body.text or "Change rejected; staging and evidence preserved"),
            )
            if body.decision == "accept":
                project["accepted_head"] = a["candidate"]
                for task_id in a.get("covered_tasks", []):
                    covered = store.get("tasks", task_id, db)
                    if covered["state"] == "ready_for_approval":
                        store.update_task(
                            db,
                            covered,
                            "completed",
                            "Accepted as part of the inspected combined staging commit",
                        )
            else:
                for task_id in a.get("covered_tasks", []):
                    covered = store.get("tasks", task_id, db)
                    if covered["state"] == "ready_for_approval":
                        store.update_task(db, covered, "blocked", "Combined change not accepted")
                # Preserve rejected evidence; do not silently advance dependent work.
                project["enabled"] = False
            db.execute(
                "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
                (
                    encode({k: v for k, v in project.items() if k not in {"id", "rev"}}),
                    project["id"],
                ),
            )
            store.event(
                db,
                "approval.decided",
                approval_id=ident,
                candidate=a["candidate"],
                decision=body.decision,
            )
            return {"ok": True, "published": False}

        return command(request, body.model_dump(), update)

    @app.get("/api/v1/approvals/{ident}/diff")
    def approval_diff(ident: str):
        approval = store.get("approvals", ident)
        task = store.get("tasks", approval["task_id"])
        project = store.get("projects", task["project_id"])
        base = approval.get("review_base", approval["base"])
        patch = git(Path(project["staging"]), "diff", "--binary", base, approval["candidate"])
        return {
            "base": base,
            "candidate": approval["candidate"],
            "text": redact(patch[:200000]),
            "truncated": len(patch) > 200000,
        }

    from .publication import register_routes

    register_routes(app, store, command, revision, Change)

    # ARENA_STATIC_DIR lets the UI fixture serve a preview build; the service uses web/dist.
    static = Path(
        os.environ.get("ARENA_STATIC_DIR") or Path(__file__).resolve().parents[3] / "web" / "dist"
    )
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}")
    def shell(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Unknown API endpoint"}, status_code=404)
        if path in {"manifest.webmanifest", "sw.js", "icon.svg"} and (static / path).is_file():
            return FileResponse(static / path)
        if (static / "index.html").exists():
            return FileResponse(static / "index.html")
        return JSONResponse(
            {"detail": "Build frontend with cd web && npm ci && npm run build"}, status_code=503
        )

    return app
