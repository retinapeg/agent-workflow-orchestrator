"""Bounded routing policy for the existing queue, using advertised CLI capabilities.

Speed preferences are policy, not latency promises. Measured successful durations
replace the initial preference only when at least three comparable samples exist.
No Fast mode, API fallback, authentication changes or new account slots.

Auto (the default) routes each task by need. The owner may pin a provider's model and
effort from the phone (meta ``routing_override``); a pinned provider then runs every
attempt it is routed (build, review and director phases) on exactly that pair, while
which provider takes which phase, and every check, retry and review rule, stay as they are.
"""

import contextlib
import json
import statistics
import time
from pathlib import Path

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
FAMILIES = ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-sol", "gpt-6.1-terra", "gpt-6-astra"]
PROVIDERS = ("codex", "claude")
# What Claude may be pinned to. Claude Code takes every --effort level for these models
# (and lowers one a model cannot use itself); Codex choices come from its catalog.
CLAUDE_CHOICES = ("sonnet", "claude-opus-5-5", "claude-fable-5-1")


def capabilities(account):
    if account["provider"] == "codex":
        path = Path(account.get("config_home", "")) / "models_cache.json"
        try:
            if time.time() - path.stat().st_mtime > 7 * 86400:
                return {}
            return {
                m["slug"]: [
                    x["effort"]
                    for x in m.get("supported_reasoning_levels", [])
                    if x["effort"] in EFFORTS
                ]
                for m in json.loads(path.read_text())["models"]
                if m.get("visibility") == "list"
            }
        except (OSError, ValueError, KeyError, TypeError):
            return {}
    # Claude's generic --effort help does not establish per-model support.
    # Keep the enrolled model and provider defaults unless validated metadata exists.
    return account.get("model_capabilities", {})


def normalize_override(value):
    """The owner's pins as {"codex": pin | None, "claude": pin | None}, a pin being
    {"model", "effort"}; a provider absent, null or malformed is Auto."""
    value = value if isinstance(value, dict) else {}
    out = {}
    for provider in PROVIDERS:
        pin = value.get(provider)
        model = pin.get("model") if isinstance(pin, dict) else None
        effort = pin.get("effort") if isinstance(pin, dict) else None
        valid = isinstance(model, str) and model and isinstance(effort, str) and effort
        out[provider] = {"model": model, "effort": effort} if valid else None
    return out


def load_override(db):
    """The stored pins (meta ``routing_override``), read in the caller's transaction."""
    row = db.execute("SELECT value FROM meta WHERE key='routing_override'").fetchone()
    try:
        return normalize_override(json.loads(row[0]) if row else None)
    except ValueError:
        return normalize_override(None)


def stored_override(store):
    """The stored pins, read on a connection of its own (for arena_status)."""
    with contextlib.closing(store.connect()) as db:
        return load_override(db)


def routing_options(accounts):
    """What the owner can pin each provider to: Codex models and efforts exactly as its
    catalog advertises them (models without a known effort level are left out), and
    Claude's fixed choices with every effort level."""
    codex = {}
    for account in accounts:
        if account.get("provider") == "codex":
            for slug, levels in capabilities(account).items():
                codex.setdefault(slug, set()).update(levels)
    return {
        "codex": {
            "models": [
                {"slug": slug, "efforts": [e for e in EFFORTS if e in levels]}
                for slug, levels in codex.items()
                if levels
            ]
        },
        "claude": {"models": [{"slug": slug, "efforts": list(EFFORTS)} for slug in CLAUDE_CHOICES]},
    }


def unavailable(account, model, attempts):
    """The CLI reported this model unavailable on this slot recently (model_unavailable)."""
    return any(
        a.get("model") == model
        and a["account_id"] == account["id"]
        and a["state"] == "model_unavailable"
        for a in attempts[-100:]
    )


def classify(task):
    complexity = task.get("complexity", "moderate")
    risk = task.get("risk", "medium")
    if not task.get("complexity") and all(
        Path(p).suffix in {".md", ".txt", ".json", ".webmanifest"} for p in task["scope"]
    ):
        complexity, risk = "simple", "low"
    text = (task["title"] + " " + " ".join(task["scope"])).lower()
    if any(
        word in text
        for word in ("auth", "payment", "security", "migration", "credential", "permission")
    ):
        risk = "high"
    return complexity, risk


def director_tier(task, repair):
    """Cheapest director that fits: short requests on narrow scopes plan with a mid model;
    broad or long requests use Astra; repairs escalate; evidence review stays mid-tier."""
    if task["phase"] == "direct":
        return ("astra", "high") if repair else ("mid", "high")
    if repair >= 2:
        return ("astra", "max")
    if repair == 1:
        return ("astra", "xhigh")
    light = len(task.get("instructions", "")) <= 500 and len(task.get("scope", [])) <= 4
    return ("mid", "high") if light else ("astra", "high")


def select_route(task, accounts, attempts, override=None):
    """The best (account, route) for this task, or None. ``override`` holds the owner's
    pins (normalize_override); None, or a provider pinned to nothing, routes on Auto."""
    pins = normalize_override(override)
    complexity, risk = classify(task)
    director = task.get("director") and task["phase"] in {"plan", "direct", "replan"}
    previous = [
        a
        for a in attempts
        if a["task_id"] == task["id"]
        and a["phase"] == "build"
        and a["state"] not in {"leased", "running", "verifying"}
    ]
    failure = previous[-1] if previous and previous[-1]["state"] != "passed" else None
    repair = task.get("repairs", 0)
    options = []
    for account in accounts:
        available = capabilities(account)
        if director:
            # The director is routed by need, not by prestige: a mid-tier model at high
            # effort plans small requests and judges evidence; Astra takes broad requests
            # and repairs (xhigh, then max). A Claude slot with probe-validated Fable 5.1
            # stands in only when no Codex slot can take the phase now.
            tier, wanted = director_tier(task, repair)
            if account["provider"] == "codex":
                preferred = ["gpt-6.1-sol", "gpt-6-sol"] if tier == "mid" else ["gpt-6-astra"]
                # The effort chosen below rounds up to the nearest supported level.
                models = [m for m in preferred if available.get(m)] or (
                    ["gpt-6-astra"] if available.get("gpt-6-astra") else []
                )
                if not models:
                    continue
            elif available.get("claude-fable-5-1"):
                models = ["claude-fable-5-1"]
            else:
                continue
        elif available and account["provider"] == "claude":
            # Validated Claude models by probe: the smallest that fits the work.
            ladder = (
                ["claude-haiku-5-5", "sonnet"]
                if complexity == "simple" and risk == "low" and not repair
                else ["sonnet"]
                if complexity != "complex" and risk != "high" and repair < 2
                else ["claude-fable-5-1", "sonnet"]
            )
            # Never escalate past the enrolled model just because a bigger one was validated.
            models = [m for m in ladder if m in available] or [account["model"]]
        elif available and account["provider"] == "codex":
            suitable = (
                FAMILIES
                if complexity == "simple" and risk == "low" and not repair
                else FAMILIES[1:]
                if complexity != "complex" and risk != "high" and repair < 2
                else ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol"]
            )
            models = [m for m in suitable if m in available]
        else:
            models = [account["model"]]
        # The owner's pin replaces the model and effort for every attempt routed to this
        # provider. A pinned model the CLI reported unavailable on this slot falls back to
        # Auto for the attempt, and the route records the pin it could not use.
        pin = pins.get(account["provider"])
        skipped = pin if pin and unavailable(account, pin["model"], attempts) else None
        if skipped:
            pin = None
        if pin:
            models = [pin["model"]]
        for index, model in enumerate(models):
            history = [
                a
                for a in attempts
                if a["account_id"] == account["id"]
                and a.get("model") == model
                and a["phase"] == task["phase"]
                and a["state"] in {"passed", "failed", "model_unavailable"}
                and a.get("routing", {}).get("risk") == risk
                and a.get("routing", {}).get("complexity") == complexity
            ][-20:]
            if unavailable(account, model, attempts):
                continue
            desired = (
                director_tier(task, repair)[1]
                if director
                else "xhigh"
                if repair >= 2
                else "high"
                if repair or risk == "high" or complexity == "complex"
                else "low"
                if complexity == "simple"
                else "medium"
            )
            levels = available.get(model, [])
            supported = [e for e in EFFORTS if e in levels]
            effort = (
                next(
                    (e for e in supported if EFFORTS.index(e) >= EFFORTS.index(desired)),
                    supported[-1],
                )
                if supported
                else None
            )
            if pin:
                effort = pin["effort"]
            successes = [a for a in history if a["state"] == "passed"]
            failure_rate = (len(history) - len(successes)) / len(history) if history else 0
            duration = (
                statistics.median(a.get("result", {}).get("seconds", 999) for a in successes)
                if len(successes) >= 3
                else None
            )
            same_failed_provider = bool(
                failure and account["provider"] == failure.get("routing", {}).get("provider")
            )
            # Failed approaches lose preference; poor observed reliability outranks nominal speed.
            score = (
                account["provider"] == task.get("author_provider")
                if task["phase"] == "review"
                else False,
                account["provider"] != "codex" if director else False,
                same_failed_provider if repair >= 2 else False,
                failure_rate >= 0.5 if len(history) >= 2 else False,
                not available if risk == "high" else False,
                duration is None,
                duration if duration is not None else index,
                index,
                account["id"],
            )
            action = (
                "director_diagnosis"
                if director and task["phase"] == "replan"
                else "retry_after_cooldown"
                if task.get("retries") and not repair
                else "switch_provider"
                if failure and not same_failed_provider
                else "owner_override"
                if pin
                else "increase_effort"
                if repair and effort
                else "provider_defaults"
                if not available
                else "initial"
            )
            shown = effort or "default effort"
            reason = (
                f"Escalated director: {model} at {shown} to diagnose repair #{repair}"
                if action == "director_diagnosis"
                else f"Director stand-in: Fable 5.1 at {shown} because no Codex slot is free"
                if director and model == "claude-fable-5-1"
                else f"Director: {model} at {shown} — "
                + (
                    "small request, a mid-tier planner is enough"
                    if director_tier(task, repair)[0] == "mid"
                    else "broad request, strongest planner"
                )
                if director
                else f"Switched provider after failure evidence: {model} at {shown}"
                if action == "switch_provider"
                else f"Escalated for repair #{repair}: {model} at {shown}"
                if repair
                else f"Economical: small, low-risk change → {model} at {shown}"
                if complexity == "simple" and risk == "low"
                else f"Standard: {complexity}, {risk} risk → {model} at {shown}"
            )
            if pin:
                reason = f"Owner override: {model} at {effort}"
            elif skipped:
                reason = (
                    f"Owner override unavailable ({skipped['model']} at {skipped['effort']}); "
                    f"Auto: {reason}"
                )
            options.append(
                (
                    score,
                    account,
                    {
                        "provider": account["provider"],
                        "model": model,
                        "effort": effort,
                        "complexity": complexity,
                        "risk": risk,
                        "action": action,
                        "reason": reason,
                        "repair_number": repair,
                        "history_samples": len(history),
                        "successful_samples": len(successes),
                        "median_seconds": duration,
                        "capability_source": "owner_override"
                        if pin
                        else "advertised_cli_catalog"
                        if available and account["provider"] == "codex"
                        else "validated_probe"
                        if available
                        else "enrolled_model_provider_defaults",
                        "failure_attempt": failure["id"] if failure else None,
                        **({"override_unavailable": dict(skipped)} if skipped else {}),
                    },
                )
            )
    if not options:
        return None
    _, account, route = min(options, key=lambda x: x[0])
    return account, route
