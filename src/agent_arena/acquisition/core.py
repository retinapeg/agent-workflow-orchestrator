"""Transactional policy boundary. No provider SDK, subprocess, network or model calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent_arena.audit import sha256_bytes, utc_now
from agent_arena.models import AgentResult, Phase, jsonable

Record = dict[str, Any]

SCHEMA = "acquisitionops-synthetic-v1"
MAX_BUDGET = 100_000


class PolicyError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return sha256_bytes(canonical(value).encode())


def record_errors(r: Record) -> list[str]:
    errors = []
    for field in ("id", "name", "contact", "segment"):
        if not isinstance(r.get(field), str) or not r[field].strip():
            errors.append(f"missing {field}")
    if r.get("synthetic") is not True:
        errors.append("synthetic=true required")
    if not str(r.get("contact", "")).endswith(".invalid"):
        errors.append("contact must use reserved .invalid domain")
    for field in ("consent", "suppressed"):
        if type(r.get(field)) is not bool:
            errors.append(f"missing boolean {field}")
    for field, low, high in (
        ("cost_cents", 1, MAX_BUDGET),
        ("margin_cents", 0, 1_000_000),
        ("response_bp", 0, 10_000),
        ("conversion_bp", 0, 10_000),
        ("truth_bp", 0, 10_000),
        ("delay_days", 1, 30),
    ):
        if type(r.get(field)) is not int or not low <= r[field] <= high:
            errors.append(f"invalid {field}")
    if not errors and r["conversion_bp"] > r["response_bp"]:
        errors.append("conversion probability exceeds response probability")
    return errors


def expected_value(r: Record) -> int:
    # Units: cents * 10,000, with no floating-point currency comparisons.
    return int(r["conversion_bp"] * r["margin_cents"] - 10_000 * r["cost_cents"])


def select(records: list[Record], budget: int, method: str) -> list[Record]:
    if method not in {"baseline", "scripted-agent"}:
        raise PolicyError("unknown recommendation method")
    records = sorted((r for r in records if expected_value(r) > 0), key=lambda r: r["id"])
    if method == "baseline":
        chosen, spent = [], 0
        for r in sorted(records, key=lambda r: (-expected_value(r), r["id"])):
            if spent + r["cost_cents"] <= budget:
                chosen.append(r)
                spent += r["cost_cents"]
        return chosen
    # Exact 0/1 budget optimisation; sparse frontier bounded by MAX_BUDGET.
    frontier: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for i, r in enumerate(records):
        for cost, (value, ids) in list(frontier.items()):
            new_cost = cost + r["cost_cents"]
            if (
                new_cost <= budget
                and value + expected_value(r) > frontier.get(new_cost, (-1, ()))[0]
            ):
                frontier[new_cost] = (value + expected_value(r), (*ids, i))
    _, (_, indexes) = max(frontier.items(), key=lambda item: (item[1][0], -item[0]))
    return [records[i] for i in indexes]


class Store:
    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        # Never attach domain tables to an existing unrelated database.
        exists = Path(path).exists()
        if Path(path).is_symlink():
            raise PolicyError("database symlinks are refused")
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if exists:
            try:
                marker = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if marker is None or marker[0] != SCHEMA:
                    self.db.close()
                    raise PolicyError("not an AcquisitionOps synthetic database")
            except sqlite3.Error as exc:
                self.db.close()
                raise PolicyError("not an AcquisitionOps synthetic database") from exc
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prospects(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ingest_issues(id TEXT PRIMARY KEY,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaigns(
          id TEXT PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,request_hash TEXT NOT NULL,
          status TEXT NOT NULL,budget INTEGER NOT NULL,method TEXT NOT NULL,plan TEXT NOT NULL,
          blockers TEXT NOT NULL,exclusions TEXT NOT NULL,approved_by TEXT,approved_hash TEXT);
        CREATE TABLE IF NOT EXISTS contacts(contact TEXT PRIMARY KEY,campaign TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(
          id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
          prospect TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0,
          error TEXT,UNIQUE(campaign,prospect));
        CREATE TABLE IF NOT EXISTS receipts(
          operation TEXT PRIMARY KEY REFERENCES operations(id),contact TEXT UNIQUE NOT NULL,
          cost INTEGER NOT NULL,due_day INTEGER NOT NULL,converted INTEGER NOT NULL,
          observed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,day INTEGER NOT NULL,
          campaign TEXT,event TEXT NOT NULL,payload TEXT NOT NULL);
        """)
        with self.tx():
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema',?)", (SCHEMA,))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('day','0')")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @property
    def day(self) -> int:
        return int(self.db.execute("SELECT value FROM meta WHERE key='day'").fetchone()[0])

    def event(self, campaign: str | None, event: str, **payload: Any) -> None:
        self.db.execute(
            "INSERT INTO audit(at,day,campaign,event,payload) VALUES(?,?,?,?,?)",
            (utc_now(), self.day, campaign, event, canonical(payload)),
        )

    def ingest(self, records: list[Record]) -> None:
        if not isinstance(records, list) or not 1 <= len(records) <= 200:
            raise PolicyError("ingest requires 1..200 synthetic records")
        with self.tx():
            for index, r in enumerate(records):
                if not isinstance(r, dict):
                    raise PolicyError("record must be an object")
                key = r.get("id") or f"missing-id-{index}-{fingerprint(r)[:10]}"
                if not isinstance(key, str):
                    raise PolicyError("record id must be a string")
                old = self.db.execute("SELECT payload FROM prospects WHERE id=?", (key,)).fetchone()
                if old and old[0] != canonical(r):
                    self.db.execute(
                        "INSERT OR IGNORE INTO ingest_issues VALUES(?,?)",
                        (key, "contradictory repeated prospect id; original retained"),
                    )
                    self.event(
                        None, "ingest_conflict", prospect=key, rejected_digest=fingerprint(r)
                    )
                    continue
                self.db.execute("INSERT OR IGNORE INTO prospects VALUES(?,?)", (key, canonical(r)))
            if self.db.execute("SELECT COUNT(*) FROM prospects").fetchone()[0] > 200:
                raise PolicyError("synthetic store limit is 200 prospects")
            self.event(None, "ingested", count=len(records), digest=fingerprint(records))

    def candidates(self) -> tuple[list[Record], list[Record], list[Record]]:
        records = [
            json.loads(r[0]) for r in self.db.execute("SELECT payload FROM prospects ORDER BY id")
        ]
        counts: dict[str, int] = {}
        for r in records:
            contact = str(r.get("contact", "")).strip().lower()
            counts[contact] = counts.get(contact, 0) + 1
        reserved = {r[0] for r in self.db.execute("SELECT contact FROM contacts")}
        conflicts = dict(self.db.execute("SELECT id,reason FROM ingest_issues"))
        eligible, blockers, exclusions = [], [], []
        for r in records:
            label = str(r.get("id", "missing-id"))
            issues = record_errors(r)
            if label in conflicts:
                issues.append(conflicts[label])
            contact = str(r.get("contact", "")).strip().lower()
            if counts[contact] > 1:
                issues.append("duplicate contact records require exclusion review")
            if issues:
                blockers.append({"id": label, "reasons": issues})
            elif not r["consent"] or r["suppressed"] or contact in reserved:
                exclusions.append(
                    {"id": label, "reason": "no consent, suppressed or already reserved"}
                )
            elif expected_value(r) <= 0:
                exclusions.append({"id": label, "reason": "non-positive expected net value"})
            else:
                eligible.append(r)
        return eligible, blockers, exclusions

    def propose(self, event_key: str, budget: int = 2400, method: str = "scripted-agent") -> str:
        if not isinstance(event_key, str) or not 1 <= len(event_key) <= 120:
            raise PolicyError("event key required (1..120 characters)")
        if type(budget) is not int or not 1 <= budget <= MAX_BUDGET:
            raise PolicyError(f"budget must be integer cents in 1..{MAX_BUDGET}")
        request_hash = fingerprint([budget, method])
        with self.tx():
            old = self.db.execute(
                "SELECT * FROM campaigns WHERE event_key=?", (event_key,)
            ).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise PolicyError("event key already used for a different request")
                return str(old["id"])
            eligible, blockers, exclusions = self.candidates()
            plan = select(eligible, budget, method)
            cid = "cmp-" + fingerprint(event_key)[:20]
            state = "REVIEW_REQUIRED" if blockers else "PROPOSED"
            if not plan:
                blockers.append(
                    {
                        "id": "campaign",
                        "reasons": ["no positive-value eligible contacts fit budget"],
                    }
                )
                state = "REVIEW_REQUIRED"
            recommendation = AgentResult(
                "acquisition-script",
                Phase.IMPLEMENT,
                "scripted",
                None,
                "completed",
                "Deterministic scripted optimiser; no model inference.",
            )
            self.db.execute(
                "INSERT INTO campaigns VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)",
                (
                    cid,
                    event_key,
                    request_hash,
                    state,
                    budget,
                    method,
                    canonical(plan),
                    canonical(blockers),
                    canonical(exclusions),
                ),
            )
            self.event(
                cid,
                "proposed",
                method=method,
                selected=[r["id"] for r in plan],
                expected_net_cents=sum(expected_value(r) for r in plan) / 10000,
                recommendation=jsonable(recommendation),
                model_calls=0,
                model_cost_usd=0,
            )
            return cid

    def campaign(self, cid: str) -> Record:
        row = self.db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
        if row is None:
            raise PolicyError("unknown campaign")
        result = dict(row)
        for field in ("plan", "blockers", "exclusions"):
            result[field] = json.loads(result[field])
        return result

    def review(self, cid: str, actor: str) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise PolicyError("reviewer name required")
        with self.tx():
            c = self.campaign(cid)
            if c["status"] != "REVIEW_REQUIRED" or not c["plan"]:
                raise PolicyError("only nonempty blocked plans can be reviewed")
            self.db.execute("UPDATE campaigns SET status='PROPOSED' WHERE id=?", (cid,))
            self.event(cid, "exclusions_reviewed", actor=actor, excluded=c["blockers"])

    def validate(self, c: Record, *, approved: bool = False) -> None:
        plan = c["plan"]
        if not plan or sum(r["cost_cents"] for r in plan) > c["budget"]:
            raise PolicyError("empty plan or budget exceeded")
        all_records = [
            json.loads(row[0]) for row in self.db.execute("SELECT payload FROM prospects")
        ]
        conflicts = {row[0] for row in self.db.execute("SELECT id FROM ingest_issues")}
        seen = set()
        for r in plan:
            if record_errors(r) or not r["consent"] or r["suppressed"] or expected_value(r) <= 0:
                raise PolicyError("ineligible prospect")
            contact = r["contact"].strip().lower()
            if (
                r["id"] in conflicts
                or sum(
                    str(other.get("contact", "")).strip().lower() == contact
                    for other in all_records
                )
                != 1
            ):
                raise PolicyError("new contradictory or duplicate records require review")
            if contact in seen:
                raise PolicyError("duplicate plan contact")
            seen.add(contact)
            current = self.db.execute(
                "SELECT payload FROM prospects WHERE id=?", (r["id"],)
            ).fetchone()
            if current is None or current[0] != canonical(r):
                raise PolicyError("prospect changed; review required")
            owner = self.db.execute(
                "SELECT campaign FROM contacts WHERE contact=?", (contact,)
            ).fetchone()
            if owner and owner[0] != c["id"]:
                raise PolicyError("contact reserved by another campaign")
        if approved and c["approved_hash"] != fingerprint([c["budget"], plan]):
            raise PolicyError("approved plan fingerprint mismatch")

    def approve(self, cid: str, actor: str, confirmation: str) -> None:
        if confirmation != "APPROVE" or not isinstance(actor, str) or not actor.strip():
            raise PolicyError("explicit APPROVE and human name required")
        with self.tx():
            c = self.campaign(cid)
            if c["approved_by"]:
                return  # Duplicate approval never creates operations or changes original approver.
            if c["status"] != "PROPOSED":
                raise PolicyError("review blockers before approval")
            self.validate(c)
            for r in c["plan"]:
                self.db.execute(
                    "INSERT INTO contacts VALUES(?,?)", (r["contact"].strip().lower(), cid)
                )
                self.db.execute(
                    "INSERT INTO operations(id,campaign,prospect) VALUES(?,?,?)",
                    (f"{cid}:{r['id']}", cid, r["id"]),
                )
            self.db.execute(
                "UPDATE campaigns SET status='APPROVED',approved_by=?,approved_hash=? WHERE id=?",
                (actor.strip(), fingerprint([c["budget"], c["plan"]]), cid),
            )
            self.event(cid, "approved", actor=actor.strip(), budget_cents=c["budget"])

    def execute(self, cid: str, failure: str = "none") -> None:
        if failure not in {"none", "once", "always", "after_receipt"}:
            raise PolicyError("unknown failure injection")
        with self.tx():
            c = self.campaign(cid)
            if c["status"] in {"COMPLETED", "SETTLED"}:
                return
            if c["status"] not in {"APPROVED", "EXECUTING", "FAILED"} or not c["approved_by"]:
                raise PolicyError("explicit human approval required")
            self.validate(c, approved=True)
            self.db.execute("UPDATE campaigns SET status='EXECUTING' WHERE id=?", (cid,))
            self.event(cid, "execution_started", failure_injection=failure)
        for index, r in enumerate(c["plan"]):
            opid = f"{cid}:{r['id']}"
            with self.tx():
                op = self.db.execute("SELECT * FROM operations WHERE id=?", (opid,)).fetchone()
                receipt = self.db.execute(
                    "SELECT * FROM receipts WHERE operation=?", (opid,)
                ).fetchone()
                if op["status"] == "completed":
                    continue
                if receipt:
                    self.db.execute(
                        "UPDATE operations SET status='completed',error=NULL WHERE id=?", (opid,)
                    )
                    self.event(cid, "receipt_reconciled", operation=opid)
                    continue
                if op["attempts"] >= 3:
                    continue
                self.validate(self.campaign(cid), approved=True)
                self.db.execute(
                    "UPDATE operations SET attempts=attempts+1,status='pending' WHERE id=?", (opid,)
                )
                self.event(cid, "provider_attempt", operation=opid, attempt=op["attempts"] + 1)
                if index == 0 and (
                    failure == "always" or (failure == "once" and op["attempts"] == 0)
                ):
                    self.db.execute(
                        "UPDATE operations SET status='failed',"
                        "error='injected transient failure' WHERE id=?",
                        (opid,),
                    )
                    self.event(cid, "provider_failed", operation=opid, tool_cost_usd=0)
                    continue
                spent = self.db.execute(
                    "SELECT COALESCE(SUM(cost),0) FROM receipts "
                    "JOIN operations ON operations.id=receipts.operation WHERE campaign=?",
                    (cid,),
                ).fetchone()[0]
                if spent + r["cost_cents"] > c["budget"]:
                    raise PolicyError("provider spend would exceed approved budget")
                draw = (
                    int(hashlib.sha256(("outcome-v1:" + r["id"]).encode()).hexdigest()[:12], 16)
                    % 10_000
                )
                self.db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?,?,0)",
                    (
                        opid,
                        r["contact"].strip().lower(),
                        r["cost_cents"],
                        self.day + r["delay_days"],
                        int(draw < r["truth_bp"]),
                    ),
                )
                self.event(
                    cid,
                    "mock_provider_accepted",
                    operation=opid,
                    cost_cents=r["cost_cents"],
                    tool_cost_usd=0,
                )
            # Separate transaction deliberately models delivery followed by a lost acknowledgement.
            if index == 0 and failure == "after_receipt":
                raise RuntimeError(
                    "injected crash after durable mock receipt; restart and execute to reconcile"
                )
            with self.tx():
                self.db.execute(
                    "UPDATE operations SET status='completed',error=NULL WHERE id=?", (opid,)
                )
                self.event(cid, "operation_completed", operation=opid)
        with self.tx():
            pending = self.db.execute(
                "SELECT COUNT(*) FROM operations WHERE campaign=? AND status!='completed'", (cid,)
            ).fetchone()[0]
            status = "FAILED" if pending else "COMPLETED"
            self.db.execute("UPDATE campaigns SET status=? WHERE id=?", (status, cid))
            self.event(cid, "execution_finished", status=status)

    def advance(self, days: int = 7) -> None:
        if type(days) is not int or not 1 <= days <= 30:
            raise PolicyError("advance 1..30 simulated days")
        with self.tx():
            self.db.execute("UPDATE meta SET value=? WHERE key='day'", (str(self.day + days),))
            rows = self.db.execute(
                "SELECT receipts.*,operations.campaign FROM receipts "
                "JOIN operations ON operations.id=receipts.operation "
                "WHERE observed=0 AND due_day<=?",
                (self.day,),
            ).fetchall()
            for row in rows:
                self.db.execute(
                    "UPDATE receipts SET observed=1 WHERE operation=?", (row["operation"],)
                )
                self.event(
                    row["campaign"],
                    "conversion_observed",
                    operation=row["operation"],
                    converted=bool(row["converted"]),
                )
            for row in self.db.execute(
                "SELECT id FROM campaigns WHERE status='COMPLETED'"
            ).fetchall():
                pending = self.db.execute(
                    "SELECT COUNT(*) FROM receipts JOIN operations "
                    "ON operations.id=receipts.operation WHERE campaign=? AND observed=0",
                    (row[0],),
                ).fetchone()[0]
                if not pending:
                    self.db.execute("UPDATE campaigns SET status='SETTLED' WHERE id=?", (row[0],))
                    self.event(row[0], "settled")
            self.event(None, "clock_advanced", days=days)

    def metrics(self, cid: str) -> Record:
        c = self.campaign(cid)
        records = {r["id"]: r for r in c["plan"]}
        receipts = self.db.execute(
            "SELECT receipts.*,operations.prospect FROM receipts "
            "JOIN operations ON operations.id=receipts.operation WHERE campaign=?",
            (cid,),
        ).fetchall()
        observed = [r for r in receipts if r["observed"]]
        expected = sum(expected_value(r) for r in c["plan"]) / 10000
        realized = sum(
            r["converted"] * records[r["prospect"]]["margin_cents"] - r["cost"] for r in observed
        )
        brier = (
            sum(
                (records[r["prospect"]]["conversion_bp"] / 10000 - r["converted"]) ** 2
                for r in observed
            )
            / len(observed)
            if observed
            else None
        )
        return {
            "expected_net_cents": expected,
            "planned_cost_cents": sum(r["cost_cents"] for r in c["plan"]),
            "spent_cents": sum(r["cost"] for r in receipts),
            "observed": len(observed),
            "delivered": len(receipts),
            "conversions": sum(r["converted"] for r in observed),
            "realized_net_cents_observed": realized,
            "brier_observed": brier,
            "model_calls": 0,
            "model_cost_usd": 0,
            "tool_cost_usd": 0,
        }
