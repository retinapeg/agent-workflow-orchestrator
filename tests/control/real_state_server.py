"""Browser fixture over a COPY of the real control state, with dispatch disabled.

The live database is opened read-only and copied with SQLite's backup API (safe while the
service writes to it); the app then runs against the copy with ``run_scheduler=False``, so
nothing is dispatched and the live database and service are never touched. Attempt
transcripts are read from their recorded absolute paths (read-only).

Two further guards keep a copy from reaching the live system: every attempt's recorded
process identity is dropped from the copy, so ``engine.cancel`` can never resolve a pid to
the production worker, and the fixture answers 403 to every mutating request except pairing,
conversations (a read-only director turn on the copy) and session bookkeeping.
"""

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from fastapi.responses import JSONResponse

from agent_arena.control.api import make_app
from agent_arena.control.engine import atomic_json

ROOT = Path(__file__).resolve().parents[2]
PAIRING_CODE = b"real-state-one-use-code"
# Mutations the acceptance may send to the copy; everything else is refused.
WRITABLE = ("/api/v1/pair", "/api/v1/conversations", "/api/v1/session")


def copy_state(source: Path, home: Path) -> None:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = home / "control.sqlite3"
    if target.exists():
        return
    uri = source.resolve().as_uri() + "?mode=ro"
    with (
        contextlib.closing(sqlite3.connect(uri, uri=True)) as src,
        contextlib.closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)
    disarm(target)


def disarm(target: Path) -> int:
    """Drop every recorded worker identity from a copied database so no pid in it can be
    matched against the live process table. Returns the number of rows changed."""
    with contextlib.closing(sqlite3.connect(target)) as db:
        changed = 0
        for ident, data in db.execute("SELECT id, data FROM attempts").fetchall():
            row = json.loads(data)
            if row.get("identity") is not None:
                row["identity"] = None
                db.execute(
                    "UPDATE attempts SET data=? WHERE id=?",
                    (json.dumps(row, sort_keys=True, separators=(",", ":")), ident),
                )
                changed += 1
        db.commit()
    return changed


def check_home(home: Path, source: Path) -> None:
    """Refuse any scratch directory that is the live home, inside it, the repository root
    or any directory that itself holds a control home."""
    live = source.parent
    if home == live or home.is_relative_to(live):
        raise SystemExit("Refusing to serve the live control home; pass a scratch directory")
    if home == ROOT or (home / ".arena-control").exists():
        raise SystemExit("Refusing a scratch directory that holds a control home")


def create_app():
    home = Path(os.environ["ARENA_UI_STATE"]).resolve()
    source = Path(
        os.environ.get("ARENA_REAL_DB") or ROOT / ".arena-control" / "control.sqlite3"
    ).resolve()
    check_home(home, source)
    copy_state(source, home)
    atomic_json(
        home / "pairing.json",
        {
            "hash": hashlib.sha256(PAIRING_CODE).hexdigest(),
            "expires": time.time() + 600,
        },
    )
    app = make_app(
        home,
        origin="http://127.0.0.1:" + os.environ.get("ARENA_UI_PORT", "8794"),
        run_scheduler=False,
    )

    @app.middleware("http")
    async def read_mostly(request, call_next):
        path = request.url.path
        if request.method not in {"GET", "HEAD"} and not path.startswith(WRITABLE):
            return JSONResponse(
                {"detail": "This fixture serves a copy of the state; mutations are refused"},
                status_code=403,
            )
        return await call_next(request)

    return app
