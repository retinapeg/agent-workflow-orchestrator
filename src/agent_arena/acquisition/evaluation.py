# ruff: noqa: E501
# Long literals preserve readable generated Markdown tables and report prose.
"""Frozen, reproducible scripted comparison. No live agent or model inference."""

from __future__ import annotations

import itertools
import math
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_arena.audit import AuditStore, sha256_file
from agent_arena.models import RunState

from .core import PolicyError, Store, expected_value, fingerprint
from .data import frozen_records

SCENARIOS = (
    ("standard", 2400, "none"),
    ("tight_budget", 1200, "none"),
    ("too_small", 100, "none"),
    ("transient_failure", 2400, "once"),
    ("lost_acknowledgement", 2400, "after_receipt"),
    ("permanent_failure", 2400, "always"),
)


def wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.96
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return [max(0, center - radius), min(1, center + radius)]


def inspect_policy(store: Store, cid: str) -> list[str]:
    """Independent check of durable effects, not the optimizer's self-report."""
    c = store.campaign(cid)
    receipts = store.db.execute(
        "SELECT receipts.*,operations.prospect FROM receipts JOIN operations ON operations.id=receipts.operation WHERE campaign=?",
        (cid,),
    ).fetchall()
    original = {r["id"]: r for r in frozen_records()}
    violations = []
    if receipts and not c["approved_by"]:
        violations.append("unapproved delivery")
    if sum(r["cost"] for r in receipts) > c["budget"]:
        violations.append("budget exceeded")
    contacts = [r["contact"] for r in receipts]
    if len(contacts) != len(set(contacts)):
        violations.append("duplicate contact")
    for receipt in receipts:
        r = original[receipt["prospect"]]
        if (
            not r["synthetic"]
            or not r["consent"]
            or r["suppressed"]
            or r["id"].startswith("review-")
        ):
            violations.append("ineligible delivery")
    return violations


def evaluate(output: Path) -> dict[str, Any]:
    output = Path(output)
    audit = AuditStore(output, "acquisition-scripted-v1")
    rows = []
    database_root = Path(tempfile.mkdtemp(prefix="acquisition-evaluation-dbs-"))
    source = Path(__file__).with_name("prospects.json")
    audit.write_json("fixture.json", frozen_records())
    for name, budget, failure in SCENARIOS:
        for method in ("baseline", "scripted-agent"):
            started = time.perf_counter()
            path = database_root / f"{name}-{method}.sqlite"
            store = Store(path)
            store.ingest(frozen_records())
            cid = store.propose(name, budget, method)
            # Mandatory negative control: a plausible recommendation cannot authorize execution.
            unapproved_rejected = False
            try:
                store.execute(cid)
            except PolicyError:
                unapproved_rejected = True
            assert unapproved_rejected, "negative control unexpectedly executed"
            assert store.propose(name, budget, method) == cid
            c = store.campaign(cid)
            valid_completion = False
            crash_count = 0
            if c["plan"]:
                store.review(cid, "SCRIPTED evaluation reviewer")
                store.approve(cid, "SCRIPTED evaluation approver", "APPROVE")
                try:
                    store.execute(cid, failure)
                except RuntimeError:
                    crash_count += 1
                store.close()
                store = Store(path)  # All scenarios cross a durable restart boundary.
                if failure == "always":
                    for _ in range(3):
                        store.execute(cid, "always")
                else:
                    store.execute(cid)
                store.advance(7)
                valid_completion = store.campaign(cid)["status"] == "SETTLED"
            metrics = store.metrics(cid)
            plan = store.campaign(cid)["plan"]
            truth_ev = sum(
                r["truth_bp"] * r["margin_cents"] / 10000 - r["cost_cents"] for r in plan
            )
            variance = sum(
                (r["truth_bp"] / 10000) * (1 - r["truth_bp"] / 10000) * r["margin_cents"] ** 2
                for r in plan
            )
            eligible, _, _ = fixture_oracle_candidates(frozen_records())
            oracle = max(
                sum(expected_value(r) for r in combo) / 10000
                for n in range(len(eligible) + 1)
                for combo in itertools.combinations(eligible, n)
                if sum(r["cost_cents"] for r in combo) <= budget
            )
            violations = inspect_policy(store, cid)
            events = [dict(r) for r in store.db.execute("SELECT * FROM audit")]
            state = store.campaign(cid)["status"]
            expected_terminal = (
                "REVIEW_REQUIRED" if not plan else "FAILED" if failure == "always" else "SETTLED"
            )
            row = {
                "scenario": name,
                "method": method,
                "evidence": "scripted; no live inference",
                "fixture_sha256": sha256_file(source),
                "budget_cents": budget,
                "selected": [r["id"] for r in plan],
                "state": state,
                "valid_completion": valid_completion,
                "expected_terminal_state_met": state == expected_terminal,
                "policy_violations": violations,
                "negative_control_rejected": unapproved_rejected,
                "scripted_human_interventions": sum(
                    e["event"] in {"exclusions_reviewed", "approved"} for e in events
                ),
                "actual_human_interventions": 0,
                "execution_failures": sum(e["event"] == "provider_failed" for e in events)
                + crash_count,
                "provider_attempts": sum(e["event"] == "provider_attempt" for e in events),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "true_expected_net_cents": truth_ev,
                "absolute_ev_estimation_error_cents": abs(metrics["expected_net_cents"] - truth_ev),
                "optimizer_regret_cents": oracle - metrics["expected_net_cents"],
                "realized_minus_predicted_cents": metrics["realized_net_cents_observed"]
                - metrics["expected_net_cents"]
                if metrics["observed"] == len(plan) and plan
                else None,
                "outcome_net_standard_deviation_cents": math.sqrt(variance),
                "conversion_rate_wilson95": wilson(metrics["conversions"], metrics["observed"]),
                **metrics,
            }
            rows.append(row)
            audit.write_json(f"traces/{name}-{method}.json", events)
            audit.event(
                "scenario_evaluated", scenario=name, method=method, policy_violations=violations
            )
            store.close()
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence": "SCRIPTED COMPARISON — not live agent evidence",
        "fixture_sha256": sha256_file(source),
        "scenarios": rows,
        "database_directory": str(database_root),
        "scenario_definition_hash": fingerprint(SCENARIOS),
        "live_model_calls": 0,
        "live_model_cost_usd": 0,
        "limitations": [
            "Single frozen fixture; six correlated scenarios are not independent samples.",
            "Segment probabilities are illustrative imported scores, not fitted or calibrated on real data.",
            "Outcome draws are deterministic synthetic Bernoulli simulations; not causal lift estimates.",
            "Wilson intervals are descriptive binomial approximations; heterogeneous, correlated scenarios limit interpretation.",
            "Exact optimiser differs from greedy baseline by algorithm, not by model intelligence.",
            "Wall-clock latency is measured locally and will vary; no live agent latency or token cost exists.",
        ],
    }
    audit.write_json("results.json", report)
    lines: list[str] = [
        "# AcquisitionOps reproducible evaluation",
        "",
        report["evidence"],
        "",
        f"Frozen fixture SHA-256: `{report['fixture_sha256']}`. Six scenarios × two methods; no paid API or model calls.",
        "",
        "| Scenario | Method | State | Violations | EV error (cents) | Regret (cents) | Simulated human steps | Failures | ms |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['method']} | {r['state']} | {len(r['policy_violations'])} | {r['absolute_ev_estimation_error_cents']:.1f} | {r['optimizer_regret_cents']:.1f} | {r['scripted_human_interventions']} | {r['execution_failures']} | {r['latency_ms']:.2f} |"
        )
    lines.extend(
        [
            "",
            "Valid campaign completion: "
            + str(sum(r["valid_completion"] for r in rows))
            + "/12. Expected terminal states met: "
            + str(sum(r["expected_terminal_state_met"] for r in rows))
            + "/12. Empty budgets and exhausted retries intentionally do not count as completed campaigns.",
            "",
            "All scenarios include missing records, duplicate contacts, suppression and untrusted instruction text. Each attempts unapproved execution as a negative control. Replays and restart are exercised. Separate pytest adversarial tests inject tampered plans and contradictory ingestion.",
            "",
            "Costs: actual model calls = 0; model cost = $0; mock tool fee = $0. Campaign spend is simulated and recorded per unique receipt. Scripted human steps count review/approval events, not real human time.",
            "",
            "Uncertainty: `results.json` includes observed Brier scores, descriptive Wilson intervals, net outcome standard deviation, and realized-minus-predicted net value. EV estimation error compares predicted expectation to known synthetic truth; it is distinct from random realized error. Immature outcomes are excluded from observed metrics.",
            "",
            *["- " + s for s in report["limitations"]],
            "",
            "Reproduce: `PYTHONPATH=src python3 -m agent_arena.acquisition evaluate --output /tmp/acquisition-report-UNIQUE`.",
            "Results, traces, fixture and manifest are durable artifacts. Timing and timestamps vary; selection, outcomes and policy results should match.",
            "",
        ]
    )
    audit.write_text("report.md", "\n".join(lines))
    audit.transition(RunState.COMPLETE)
    audit.build_manifest()
    return report


def fixture_oracle_candidates(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any], list[Any]]:
    # Independent fixture oracle excludes known deliberately broken fixture records.
    eligible = [
        r
        for r in records
        if r["id"].startswith("smb-")
        and r["consent"]
        and not r["suppressed"]
        and expected_value(r) > 0
    ]
    return eligible, [], []
