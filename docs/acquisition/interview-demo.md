# Two-minute interview demonstration

Prerequisite: start `PYTHONPATH=src python3 -m agent_arena.acquisition demo` on a permitted local host and open the printed URL. Browser acceptance is pending in the recovery sandbox; use the test/evaluation fallback below if unavailable.

**0:00–0:20 — Frame the decision.** “This is a synthetic acquisition operator. It chooses expected profit under a budget, then requires a human decision. Nothing can contact a real customer.” Point out mock-only, no-model-calls and simulated-day labels.

**0:20–0:45 — Explain the plan.** Propose `demo-001` with a 2400-cent budget. Show conversion probability, cost and expected net value. “Higher response rate doesn't necessarily mean higher value. Two studios can beat one consultancy within the same budget.” Expand policy exclusions; note missing margin, duplicates, suppression and consent.

**0:45–1:05 — Exercise human authority.** Enter a reviewer name and acknowledge exclusions. Enter an approver name and type APPROVE. “Review excludes invalid data; it doesn't override eligibility. Approval freezes this exact budget and plan.”

**1:05–1:30 — Break and recover.** Select “Crash after receipt” and execute. Show the injected failure and preserved receipt. Choose “None / recover” and execute again. “The mock accepted an operation before the acknowledgement failed. Reconciliation uses the durable receipt, so recovery doesn't spend twice.” For a longer interview, restart with the same printed database path before recovery.

**1:30–1:50 — Close the feedback loop.** Advance seven days. Show conversions, realized net, Brier score and audit entries. “Delayed outcomes aren't counted as failures before they mature.”

**1:50–2:00 — Bound the claim.** “This is a scripted optimizer comparison and a local reliability prototype. The 12 evaluation rows have zero observed policy violations, eight complete campaigns and four intentional safe failures. It isn't a live-model or real-world marketing benchmark.”

Fallback: show `docs/acquisition/evaluation/report.md` and run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/acquisition`. Explain the actual process-crash test. Do not present generated HTML or this script as evidence of browser success.
