"""Phone-sized views of the control state.

The overview is polled every few seconds, so it carries only what the lists need; full
records (specifications, plans, check output, review text) are fetched per goal, task or
attempt on demand. Nothing here reads attempt artifact files.
"""

from __future__ import annotations

import json

from .titles import display_title

INSTRUCTIONS_CHARS = 400
REASON_CHARS = 300
REQUEST_CHARS = 300
SUMMARY_CHARS = 300  # the full text is in the goal/attempt detail
REVIEW_SUMMARY_CHARS = 600
FINDING_CHARS = 300
FINDINGS_LIMIT = 10
ROUTING_REASON_CHARS = 200
RESULT_KEYS = (
    "status",
    "reason",
    "seconds",
    "cost",
    "usage",
    "invocation",
    "provider_started",
    "provider_ended",
    "attempt_id",
    "token",
    # Small and shown on the build card: the staged commit and the paths it touched.
    "candidate",
    "changed",
)
ROUTING_KEYS = ("provider", "model", "effort", "action")


def clip(value, limit):
    """Truncate text to ``limit`` characters with an ellipsis; non-text passes through."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def slim_task(task: dict) -> dict:
    out = {
        k: v
        for k, v in task.items()
        if k not in {"specification", "repair_evidence", "instructions_pending"}
    }
    instructions = task.get("instructions") or ""
    out["instructions"] = instructions[:INSTRUCTIONS_CHARS]
    out["instructions_truncated"] = len(instructions) > INSTRUCTIONS_CHARS
    out["instructions_pending_count"] = len(task.get("instructions_pending") or [])
    out["reason"] = clip(task.get("reason") or "", REASON_CHARS)
    return out


def slim_goal(goal: dict) -> dict:
    out = {k: v for k, v in goal.items() if k != "specification"}
    request = goal.get("request") or ""
    out["request"] = request[:REQUEST_CHARS]
    out["request_truncated"] = len(request) > REQUEST_CHARS
    out["display_title"] = display_title(goal.get("title")) or display_title(request) or "Request"
    return out


def with_title(goal: dict) -> dict:
    """The full goal record plus ``display_title`` (detail endpoint)."""
    return {
        **goal,
        "display_title": display_title(goal.get("title"))
        or display_title(goal.get("request"))
        or "Request",
    }


def plain_summary(summary) -> str:
    """A planner's summary is its whole JSON specification; the list only needs the
    sentence inside it (the full record keeps the JSON)."""
    text = summary if isinstance(summary, str) else ""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(parsed, dict):
            for key in ("summary", "outcome"):
                if isinstance(parsed.get(key), str) and parsed[key].strip():
                    return parsed[key]
    return text


def slim_checks(checks):
    """Check verdicts without their argv, digests and output."""
    if not isinstance(checks, list):
        return checks
    return [
        {"name": c.get("name"), "status": c.get("status"), "seconds": c.get("seconds")}
        for c in checks
        if isinstance(c, dict)
    ]


def slim_approval(approval: dict) -> dict:
    out = dict(approval)
    if "checks" in approval:
        out["checks"] = slim_checks(approval["checks"])
    publication = approval.get("publication")
    if isinstance(publication, dict) and isinstance(publication.get("receipt"), dict):
        receipt = publication["receipt"]
        out["publication"] = {
            **publication,
            "receipt": {**receipt, "checks": slim_checks(receipt.get("checks"))},
        }
    return out


def slim_usage(usage):
    """Per-call usage keeps its numbers and model; the bookkeeping flags (``model_source``,
    ``not_applicable``, ``single_request``…) only matter in the full record."""
    if not isinstance(usage, list):
        return usage
    return [
        {k: v for k, v in record.items() if isinstance(v, int | float) and not isinstance(v, bool)}
        for record in usage
        if isinstance(record, dict)
    ]


def slim_result(result) -> dict | None:
    if not isinstance(result, dict):
        return result
    out = {k: result[k] for k in RESULT_KEYS if k in result}
    if "usage" in out:
        out["usage"] = slim_usage(out["usage"])
    if "checks" in result:
        out["checks"] = slim_checks(result["checks"])
    review = result.get("review")
    if isinstance(review, dict):
        findings = review.get("findings") or []
        out["review"] = {
            "approved": review.get("approved"),
            "summary": clip(review.get("summary") or "", REVIEW_SUMMARY_CHARS),
            "findings": [
                clip(f if isinstance(f, str) else json.dumps(f), FINDING_CHARS)
                for f in findings[:FINDINGS_LIMIT]
            ],
            "findings_total": len(findings),
        }
    elif "review" in result:
        out["review"] = review
    if "summary" in result:
        out["summary"] = clip(plain_summary(result.get("summary")), SUMMARY_CHARS)
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        out["artifacts"] = sorted(artifacts)
    elif isinstance(artifacts, list):
        out["artifacts"] = [a if isinstance(a, str) else str(a) for a in artifacts]
    return out


def slim_attempt(attempt: dict) -> dict:
    out = {k: v for k, v in attempt.items() if k not in {"workspace", "artifact_dir", "identity"}}
    out["reason"] = clip(attempt.get("reason") or "", ROUTING_REASON_CHARS)
    if "result" in attempt:
        out["result"] = slim_result(attempt["result"])
    routing = attempt.get("routing")
    if isinstance(routing, dict):
        out["routing"] = {
            **{k: routing.get(k) for k in ROUTING_KEYS},
            "reason": clip(routing.get("reason") or "", ROUTING_REASON_CHARS),
        }
    return out


def goal_events(db, goal_id: str, task_ids, attempt_ids, approval_ids, *, after=0, limit=200):
    """Events touching one goal (its tasks, their attempts and approvals), oldest first.

    ``after`` returns only events with a greater id (incremental polling); without it the
    last ``limit`` events are returned.
    """
    wanted = {
        "goal_id": {goal_id},
        "task_id": set(task_ids),
        "attempt_id": set(attempt_ids),
        "approval_id": set(approval_ids),
    }
    idents = sorted(i for group in wanted.values() for i in group)
    if not idents:
        return []
    # SQLite prefilter on the JSON text; the exact field match happens below.
    clause = " OR ".join("data LIKE ?" for _ in idents)
    params = [f"%{i}%" for i in idents]
    if after:
        rows = db.execute(
            f"SELECT * FROM events WHERE id>? AND ({clause}) ORDER BY id ASC",
            [after, *params],
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT * FROM events WHERE ({clause}) ORDER BY id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()[::-1]
    out = []
    for row in rows:
        try:
            data = json.loads(row["data"])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if any(data.get(field) in ids for field, ids in wanted.items()):
            out.append({"id": row["id"], "time": row["time"], "kind": row["kind"], "data": data})
    return out[-limit:] if not after else out[:limit]
