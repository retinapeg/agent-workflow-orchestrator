"""Browser fixture over a COPY of the real control state, with dispatch disabled.

The live database is opened read-only and copied with SQLite's backup API (safe while the
service writes to it); the app then runs against the copy with ``run_scheduler=False``, so
nothing is dispatched and the live database and service are never touched. Attempt
transcripts are read from their recorded absolute paths (read-only).
"""

import contextlib
import hashlib
import os
import sqlite3
import time
from pathlib import Path

from agent_arena.control.api import make_app
from agent_arena.control.engine import atomic_json

ROOT = Path(__file__).resolve().parents[2]
PAIRING_CODE = b"real-state-one-use-code"


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


def create_app():
    home = Path(os.environ["ARENA_UI_STATE"]).resolve()
    source = Path(
        os.environ.get("ARENA_REAL_DB") or ROOT / ".arena-control" / "control.sqlite3"
    ).resolve()
    if home == source.parent:
        raise SystemExit("Refusing to serve the live control home; pass a scratch directory")
    copy_state(source, home)
    atomic_json(
        home / "pairing.json",
        {
            "hash": hashlib.sha256(PAIRING_CODE).hexdigest(),
            "expires": time.time() + 600,
        },
    )
    return make_app(
        home,
        origin="http://127.0.0.1:" + os.environ.get("ARENA_UI_PORT", "8794"),
        run_scheduler=False,
    )
