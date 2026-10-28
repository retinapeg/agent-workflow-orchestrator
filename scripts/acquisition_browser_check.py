"""Opt-in host browser verification. Installs nothing; never touches existing campaign data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "BLOCKED: Playwright is not installed; no installation attempted."
        ) from None
    root = Path(__file__).resolve().parents[1]
    artifacts = Path(tempfile.mkdtemp(prefix="acquisition-browser-"))
    database = artifacts / "synthetic.sqlite"
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    url = f"http://127.0.0.1:{args.port}"
    processes = []
    logs = []
    result = {"status": "not_passed", "database": str(database), "checks": []}

    def start_server(attempt):
        log = (artifacts / f"server-{attempt}.log").open("xb")
        logs.append(log)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_arena.acquisition",
                "demo",
                "--db",
                str(database),
                "--port",
                str(args.port),
            ],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"Local server exited: inspect {log.name}; no permissions bypassed"
                )
            try:
                with urlopen(url, timeout=0.2) as response:
                    if b"AcquisitionOps" in response.read():
                        return process
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Local server readiness timed out")

    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    try:
        process = start_server(1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.goto(url)
            page.get_by_role("button", name="Propose campaign", exact=True).click()
            page.get_by_text("REVIEW_REQUIRED", exact=True).wait_for()
            page.get_by_label("Reviewer", exact=True).fill("Browser reviewer")
            page.get_by_role("button", name="Acknowledge and exclude flagged records").click()
            page.get_by_label("Approver", exact=True).fill("Browser approver")
            page.get_by_label("Type APPROVE").fill("APPROVE")
            page.get_by_role("button", name="Approve mock campaign").click()
            page.get_by_text("APPROVED", exact=True).wait_for()
            page.get_by_label("Failure simulation").select_option("after_receipt")
            page.get_by_role("button", name="Execute / retry mock provider").click()
            page.get_by_text("EXECUTING", exact=True).wait_for()
            result["checks"].append(
                "proposal, exclusion review, explicit approval, lost acknowledgement"
            )
            stop(process)
            process = start_server(2)
            page.goto(url)
            page.get_by_text("EXECUTING", exact=True).wait_for()
            page.get_by_role("button", name="Execute / retry mock provider").click()
            page.get_by_text("COMPLETED", exact=True).wait_for()
            page.get_by_role("button", name="Advance outcome clock").click()
            page.get_by_text("SETTLED", exact=True).wait_for()
            page.screenshot(path=str(artifacts / "settled-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(artifacts / "settled-mobile.png"), full_page=True)
            result["checks"].append(
                "browser reload after server restart, receipt recovery, delayed outcomes"
            )
            browser.close()
        from agent_arena.acquisition.core import Store

        store = Store(database)
        cid = store.db.execute("SELECT id FROM campaigns").fetchone()[0]
        metrics = store.metrics(cid)
        assert metrics["spent_cents"] == metrics["planned_cost_cents"]
        assert metrics["delivered"] == len(store.campaign(cid)["plan"])
        assert metrics["observed"] == metrics["delivered"]
        result["metrics"] = metrics
        store.close()
        result["status"] = "passed"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for process in processes:
            stop(process)
        for log in logs:
            log.close()
        (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(artifacts / "result.json", flush=True)


if __name__ == "__main__":
    main()
