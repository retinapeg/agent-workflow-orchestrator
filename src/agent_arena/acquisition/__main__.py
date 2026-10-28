"""Safe entry point, independent of agent-arena CLI and its worktree machinery."""

import argparse
import json
import tempfile
from pathlib import Path

from .core import Store
from .data import frozen_records


def main() -> None:
    parser = argparse.ArgumentParser(description="AcquisitionOps: synthetic campaigns only")
    parser.add_argument("command", choices=["demo", "evaluate", "snapshot"])
    parser.add_argument(
        "--db", type=Path, help="Resume an AcquisitionOps DB; unrelated DBs are refused"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--output", type=Path, help="New evaluation artifact directory (must not exist)"
    )
    args = parser.parse_args()
    if args.command == "evaluate":
        from .evaluation import evaluate

        output = args.output or Path(tempfile.mkdtemp(prefix="acquisition-eval-")) / "report"
        evaluate(output)
        print(output / "report.md")
        return
    path = args.db or Path(tempfile.mkdtemp(prefix="acquisition-demo-")) / "synthetic.sqlite"
    store = Store(path)
    if not store.db.execute("SELECT 1 FROM prospects LIMIT 1").fetchone():
        store.ingest(frozen_records())
    if args.command == "snapshot":
        print(
            json.dumps(
                {
                    "database": str(path),
                    "day": store.day,
                    "campaigns": [
                        store.campaign(row[0])
                        for row in store.db.execute("SELECT id FROM campaigns")
                    ],
                },
                indent=2,
            )
        )
        store.close()
    else:
        store.close()
        from .web import serve

        serve(path, args.port)


if __name__ == "__main__":
    main()
