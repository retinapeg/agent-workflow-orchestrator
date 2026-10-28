from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path


def uid(prefix: str) -> str:
    return prefix + "_" + secrets.token_hex(8)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, home: Path):
        self.home = home.expanduser().resolve()
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.home / "control.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('schema','1'),('paused','false'),('limit','3');
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,
                rev INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,
                rev INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id),
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id),
                goal_id TEXT REFERENCES goals(id),
                state TEXT NOT NULL,
                rev INTEGER NOT NULL DEFAULT 0,
                token INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_dependencies(task_id TEXT REFERENCES tasks(id),
                dependency_id TEXT REFERENCES tasks(id),
                PRIMARY KEY(task_id,
                dependency_id));
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                account_id TEXT NOT NULL REFERENCES accounts(id),
                token INTEGER NOT NULL,
                state TEXT NOT NULL,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,
                task_id TEXT REFERENCES tasks(id),
                rev INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(key TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                time REAL NOT NULL,
                kind TEXT NOT NULL,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,
                expires REAL NOT NULL,
                csrf TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state,project_id);
            CREATE INDEX IF NOT EXISTS attempts_state ON attempts(state,account_id);
            """)
        with self.connect() as db:
            present = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            for column, kind in (
                ("id", "TEXT"),
                ("label", "TEXT"),
                ("created", "REAL"),
                ("last_seen", "REAL"),
            ):
                if column not in present:
                    db.execute(f"ALTER TABLE sessions ADD COLUMN {column} {kind}")
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextlib.contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if row is None:
            raise KeyError("Object not found")
        out = dict(row)
        if "data" in out:
            out = {**json.loads(out.pop("data")), **out}
        return out

    def get(self, table, ident, db=None):
        if table not in {"projects", "accounts", "goals", "tasks", "attempts", "approvals"}:
            raise ValueError("Invalid table")
        if db is not None:
            return self.unpack(db.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone())
        with contextlib.closing(self.connect()) as conn:
            return self.get(table, ident, conn)

    def rows(self, table, db=None):
        if table not in {"projects", "accounts", "goals", "tasks", "attempts", "approvals"}:
            raise ValueError("Invalid table")
        if db is not None:
            return [self.unpack(r) for r in db.execute(f"SELECT * FROM {table}")]
        with contextlib.closing(self.connect()) as conn:
            return self.rows(table, conn)

    def setting(self, key):
        with contextlib.closing(self.connect()) as db:
            return json.loads(
                db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]
            )

    def event(self, db, kind, **data):
        db.execute(
            "INSERT INTO events(time,kind,data) VALUES(?,?,?)",
            (time.time(), kind, encode({"schema": 1, **data})),
        )

    def command(self, key, payload, callback):
        if not key or len(key) > 128:
            raise ValueError("An idempotency key is required")
        digest = hashlib.sha256(encode(payload).encode()).hexdigest()
        with self.transaction() as db:
            old = db.execute("SELECT * FROM commands WHERE key=?", (key,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise Conflict("Idempotency key reused for a different command")
                return json.loads(old["response"])
            result = callback(db)
            db.execute("INSERT INTO commands VALUES(?,?,?)", (key, digest, encode(result)))
            return result

    def update_task(self, db, task, state, reason, **extra):
        data = {
            k: v
            for k, v in task.items()
            if k not in {"id", "rev", "state", "token", "project_id", "goal_id"}
        }
        data.update(extra, reason=reason, updated=time.time())
        db.execute(
            "UPDATE tasks SET state=?,rev=rev+1,data=? WHERE id=?",
            (state, encode(data), task["id"]),
        )
        self.event(db, "task.changed", task_id=task["id"], state=state, reason=reason)

    def backup(self, destination: Path):
        if destination.exists():
            raise ValueError("Backup destination must be new")
        with contextlib.closing(self.connect()) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        os.chmod(destination, 0o600)
