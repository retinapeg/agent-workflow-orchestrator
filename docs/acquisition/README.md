# AcquisitionOps

AcquisitionOps is a local, synthetic-only campaign planning laboratory. It demonstrates a commercially relevant boundary: probabilistic recommendations propose whom to contact, while deterministic rules and a human decision control execution. A campaign can recover after a lost acknowledgement without contacting a prospect twice or charging the simulated budget twice.

No dependency installation, model credentials or network service is required for the application. Python 3.11+ is sufficient. Run from the repository root:

```sh
PYTHONPATH=src python3 -m agent_arena.acquisition demo
```

Open the printed `http://127.0.0.1:8765` URL. The command creates a **new isolated synthetic SQLite database** in a temporary directory and prints its path. It does not launch Agent Arena, coding agents, queues or worktrees. Keep that path to resume later:

```sh
PYTHONPATH=src python3 -m agent_arena.acquisition demo --db /path/printed/synthetic.sqlite
```

The UI is implemented, and its action handlers and HTML escaping are tested. This recovery sandbox denies loopback binding, so **browser verification is pending** on a permitted host. Server-rendered HTML tests are not browser evidence.

## Walk through an actual campaign

1. Propose a campaign with event key `demo-001` and budget `2400` cents. The baseline greedily chooses positive expected-value contacts; the scripted agent runs an exact budget optimizer. Both use expected value, not response ranking.
2. Inspect the selected businesses and expected net value. Expand policy exclusions: missing economic data and duplicate contacts are explicit review blockers. Suppressed and nonconsenting records are excluded.
3. Enter your reviewer name and acknowledge exclusion of the flagged records. The original invalid records remain stored for audit; review cannot make them eligible.
4. Enter your approver name and type `APPROVE`. This freezes the budget and plan fingerprint and atomically reserves the selected contacts. No execution happens during approval.
5. Select `Crash after receipt`, then execute. The mock provider persists its first receipt before the injected failure. State remains `EXECUTING`. Restart the application with the same database, then execute with `None / recover`. The receipt is reconciled and remaining operations run once.
6. Advance seven simulated days. Due outcomes become visible; examine conversion count, realized net value, Brier score and audit events.

Use a new event key for another campaign. Replaying an unchanged event key returns its original campaign. Reusing it with a different budget/method is rejected. A second campaign cannot reserve contacts already reserved by an approved campaign, including permanently failed operations. This conservative contact policy prevents ambiguous retries from becoming duplicate outreach; there is no automatic release/cancellation path in this slice.

## Evidence and reproducibility

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q tests/acquisition
PYTHONPATH=src python3 -m agent_arena.acquisition evaluate --output /tmp/acquisition-report-UNIQUE
```

The evaluation output directory must not exist. Evaluation creates fresh synthetic databases, crosses restart boundaries, writes scenario audit traces, and hashes its artifacts using the existing `AuditStore`. No evaluator invokes the old worktree harness. [Recorded results](evaluation/report.md) compare **scripted deterministic algorithms, not live agents**: 12 scenario/method rows, 8 completed campaigns, 12 expected terminal states, zero observed policy violations. Two insufficient-budget and two exhausted-retry scenarios deliberately remain incomplete. See raw JSON for measured latency, uncertainty, errors, costs and intervention counts.

Optional coordinator browser acceptance, on a host with an already installed Playwright Python package and Chromium:

```sh
PYTHONPATH=src .venv/bin/python scripts/acquisition_browser_check.py --port 8766
```

This script is provided for host verification; it has not been run successfully in this sandbox. It installs nothing, creates its own new synthetic DB, and only starts/stops its own local HTTP server. Missing package/browser/listener permission is a blocker, not a reason to change sandbox settings.

## Commercial relevance and limits

A small business may prefer fewer high-margin prospective conversions over a larger low-value audience. The prototype makes that reasoning visible, provides a review point, records simulated expenditure, and preserves execution evidence after restart. The design can support a real campaign operator's review and reconciliation workflow, but it is **not ready for real customer data or delivery**.

Scores are frozen illustrative segment rates (a simple lookup predictor), not a model trained on business data. The simulator's hidden outcome rates differ from predictions to expose estimation error. A response is not a conversion, and conversion likelihood is not incremental lift. Real deployment would require experimentally estimated uplift, consent provenance, authenticated roles, a proper provider idempotency contract, global financial controls, data retention rules, independent policy administration and monitoring. None is claimed here.

The application cannot send email, SMS, print orders or payments: it contains no delivery SDK or network client. The only listener is loopback HTTP. All contacts use `.invalid`; all records require a synthetic flag. Model/tool invoice costs are exactly zero in this evaluation because there are no such calls. Campaign dollars are fictional economics. CLI development token usage is separate and is not an application benchmark.

Local names plus typed approval provide an explicit demo interaction, not verified human identity. Anyone with filesystem access can edit SQLite. This is not a tamper-proof or multi-tenant audit service. The mock provider shares SQLite with the workflow; its recovery behavior does not establish exactly-once semantics for a future real provider.

[Architecture](architecture.md) · [Learning guide](../../LEARNING_GUIDE.md) · [Two-minute interview demo](interview-demo.md) · [Recovery report](../../RECOVERY_JOB_C_REPORT.md)
