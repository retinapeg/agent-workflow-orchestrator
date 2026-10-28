# ruff: noqa: E501
# HTML/CSS literals are intentionally kept intact; all other lint rules apply.
"""Loopback-only dashboard; forms call the same deterministic service as tests."""

from __future__ import annotations

import html
import json
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .core import PolicyError, Store, expected_value


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render(store: Store, token: str, notice: str = "") -> str:
    def form(
        action: str, cid: str = "", body: str = "", label: str = "Continue", css: str = ""
    ) -> str:
        return (
            f'<form method="post" action="/{action}" class="{css}"><input type="hidden" name="token" value="{esc(token)}">'
            f'<input type="hidden" name="campaign" value="{esc(cid)}">{body}<button>{esc(label)}</button></form>'
        )

    cards = []
    for row in store.db.execute("SELECT id FROM campaigns ORDER BY rowid DESC"):
        c = store.campaign(row[0])
        m = store.metrics(c["id"])
        rows = "".join(
            f"<tr><td>{esc(r['name'])}</td><td>{esc(r['segment'])}</td><td>{r['response_bp'] / 100:.1f}%</td><td>{r['conversion_bp'] / 100:.1f}%</td><td>${r['cost_cents'] / 100:.2f}</td><td>${expected_value(r) / 1_000_000:.2f}</td></tr>"
            for r in c["plan"]
        )
        actions = ""
        if c["status"] == "REVIEW_REQUIRED" and c["plan"]:
            actions += form(
                "review",
                c["id"],
                '<label>Reviewer <input name="actor" required placeholder="Your name"></label>',
                "Acknowledge and exclude flagged records",
            )
        if c["status"] == "PROPOSED":
            actions += form(
                "approve",
                c["id"],
                '<label>Approver <input name="actor" required placeholder="Your name"></label><label>Type APPROVE <input name="confirmation" required pattern="APPROVE"></label>',
                "Approve mock campaign",
            )
        if c["status"] in {"APPROVED", "EXECUTING", "FAILED"}:
            actions += form(
                "execute",
                c["id"],
                '<label>Failure simulation <select name="failure"><option value="none">None / recover</option><option value="once">First attempt fails</option><option value="after_receipt">Crash after receipt</option><option value="always">First contact always fails (3 attempts max)</option></select></label>',
                "Execute / retry mock provider",
            )
        blockers = "".join(
            f"<li>{esc(b['id'])}: {esc('; '.join(b['reasons']))}</li>" for b in c["blockers"]
        )
        exclusions = "".join(
            f"<li>{esc(b['id'])}: {esc(b['reason'])}</li>" for b in c["exclusions"]
        )
        ops = [
            dict(r)
            for r in store.db.execute(
                "SELECT prospect,status,attempts,error FROM operations WHERE campaign=?", (c["id"],)
            )
        ]
        cards.append(f"""<article><div class="heading"><h2>{esc(c["id"])}</h2><span class="badge">{esc(c["status"])}</span></div>
<p>{esc(c["method"])} · Approved by: {esc(c["approved_by"] or "awaiting human decision")}</p>
<div class="metrics"><div>Budget<strong>${c["budget"] / 100:.2f}</strong></div><div>Expected net value<strong>${m["expected_net_cents"] / 100:.2f}</strong></div><div>Mock spend<strong>${m["spent_cents"] / 100:.2f}</strong></div><div>Observed outcomes<strong>{m["observed"]} / {m["delivered"]}</strong></div></div>
<p>Selection maximizes expected conversion margin minus contact cost. A high response rate alone does not make a profitable prospect.</p>
<div class="scroll"><table><thead><tr><th>Business</th><th>Segment</th><th>Response</th><th>Conversion</th><th>Cost</th><th>Expected net</th></tr></thead><tbody>{rows}</tbody></table></div>
<details><summary>Policy exclusions and review history</summary><ul>{blockers}{exclusions}</ul></details>
{actions}<p>Observed conversions: {m["conversions"]} · Observed net: ${m["realized_net_cents_observed"] / 100:.2f} · Brier score: {esc(m["brier_observed"])}. Unobserved outcomes are pending, not failures.</p>
<details><summary>Operations and retries</summary><pre>{esc(json.dumps(ops, indent=2))}</pre></details></article>""")
    audit = "".join(
        f"<tr><td>{r['seq']}</td><td>Day {r['day']}</td><td>{esc(r['event'])}</td><td>{esc(r['campaign'] or '—')}</td><td><code>{esc(r['payload'])}</code></td></tr>"
        for r in store.db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT 100")
    )
    create = form(
        "propose",
        body='<label>Event key <input name="event" value="demo-001" required maxlength="120"></label><label>Budget (cents) <input name="budget" type="number" min="1" max="100000" value="2400" required></label><label>Recommendation <select name="method"><option value="scripted-agent">Scripted agent · exact EV optimizer</option><option value="baseline">Deterministic baseline · greedy EV</option></select></label>',
        label="Propose campaign",
    )
    advance = form(
        "advance",
        body='<label>Simulated days <input name="days" type="number" min="1" max="30" value="7" required></label>',
        label="Advance outcome clock",
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AcquisitionOps | Campaign control</title>
<style>body{{margin:0;background:#101725;color:#edf2fa;font:16px system-ui;line-height:1.55}}main{{max-width:1180px;margin:auto;padding:32px 24px}}h1{{font-size:40px;margin:10px 0}}h2{{font-size:19px}}p,summary{{color:#b9c7db}}header{{margin-bottom:28px}}.eyebrow{{color:#5fe0bb;text-transform:uppercase;letter-spacing:2px;font-size:12px}}article,.panel{{background:#192437;border:1px solid #33435d;border-radius:14px;padding:24px;margin:20px 0}}.heading,.metrics,form{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}.heading{{justify-content:space-between}}.metrics{{justify-content:space-between;background:#111b2b;padding:18px;border-radius:10px}}strong{{display:block;font-size:24px;color:#72e6c3}}.badge{{background:#304763;border-radius:20px;padding:6px 12px;font-size:12px}}label{{display:grid;gap:4px;font-size:13px}}input,select,button{{padding:10px;border:1px solid #51627c;border-radius:6px;background:#101725;color:white;font:inherit}}button{{background:#72e6c3;color:#102820;font-weight:700;cursor:pointer}}form{{margin:20px 0}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #344259;vertical-align:top}}th{{color:#91a9c9}}.scroll{{overflow:auto}}code,pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}details{{margin:16px 0}}.notice{{border-left:4px solid #ffca79;padding:12px;background:#302b23}}footer{{color:#91a9c9;font-size:13px}}@media(max-width:600px){{main{{padding:16px}}h1{{font-size:30px}}article{{padding:14px}}}}</style></head>
<body><main><header><div class="eyebrow">Synthetic operations laboratory</div><h1>AcquisitionOps</h1><p>Review the economics. Approve the plan. Observe what happens.</p><span class="badge">MOCK PROVIDER ONLY</span> <span class="badge">DAY {store.day}</span> <span class="badge">NO MODEL CALLS</span></header>
{('<div class="notice" role="status">' + esc(notice) + "</div>") if notice else ""}
<section class="panel"><h2>Plan a campaign</h2><p>Frozen synthetic small businesses. Invalid and duplicate records require explicit exclusion review. Reusing an event key returns the same campaign.</p>{create}{advance}</section>
{"".join(cards) or "<p>No campaigns yet. Propose one above to begin.</p>"}
<section class="panel"><h2>Durable audit trail</h2><div class="scroll"><table><tr><th>#</th><th>Clock</th><th>Event</th><th>Campaign</th><th>Evidence</th></tr>{audit}</table></div></section>
<footer>Local portfolio prototype. All dollar amounts represent simulated campaign economics. Scripted recommendations are deterministic code, not live AI inference. Restart with the same database to recover approvals and receipts.</footer></main></body></html>"""


def dispatch(store: Store, action: str, fields: dict[str, list[str]]) -> str:
    def get(k: str) -> str:
        return fields.get(k, [""])[0]

    cid = get("campaign")
    if action == "/propose":
        cid = store.propose(get("event"), int(get("budget")), get("method"))
        return f"Proposal ready: {cid}"
    if action == "/review":
        store.review(cid, get("actor"))
        return "Flagged records excluded and review recorded. Approval is still required."
    if action == "/approve":
        store.approve(cid, get("actor"), get("confirmation"))
        return "Human approval saved. Mock execution is now available."
    if action == "/execute":
        store.execute(cid, get("failure"))
        return f"Execution state: {store.campaign(cid)['status']}"
    if action == "/advance":
        store.advance(int(get("days")))
        return "Simulated clock advanced. Due outcomes recorded."
    raise PolicyError("unknown action")


def make_handler(store: Store, token: str, port: int) -> type[BaseHTTPRequestHandler]:

    class Handler(BaseHTTPRequestHandler):
        def page(self, status: int = 200, notice: str = "") -> None:
            data = render(store, token, notice).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def allowed_host(self) -> bool:
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self) -> None:
            if not self.allowed_host():
                self.send_error(403)
                return
            if self.path != "/":
                self.send_error(404)
                return
            self.page()

        def do_POST(self) -> None:
            if not self.allowed_host():
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise PolicyError("request too large or empty")
                origin = self.headers.get("Origin")
                if origin and origin not in {
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                }:
                    raise PolicyError("foreign origin refused")
                fields = parse_qs(self.rfile.read(length).decode(), max_num_fields=20)
                if not secrets.compare_digest(fields.get("token", [""])[0], token):
                    raise PolicyError("invalid form token")
                notice = dispatch(store, self.path, fields)
                self.page(notice=notice)
            except (ValueError, RuntimeError, sqlite3.Error) as exc:
                self.page(400, str(exc))

    return Handler


def serve(path: str | Path, port: int = 8765) -> None:
    token = secrets.token_urlsafe(32)
    store = Store(path)
    try:
        server = HTTPServer(("127.0.0.1", port), make_handler(store, token, port))
    except OSError:
        store.close()
        raise
    print(f"AcquisitionOps: http://127.0.0.1:{port} · database {path}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()
