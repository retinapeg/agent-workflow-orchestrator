# AcquisitionOps reproducible evaluation

SCRIPTED COMPARISON — not live agent evidence

Frozen fixture SHA-256: `aa0a158f8292f9a392c67878c5d5361cd67c16eda7c18b0b8bae8280f9fd66fa`. Six scenarios × two methods; no paid API or model calls.

| Scenario | Method | State | Violations | EV error (cents) | Regret (cents) | Simulated human steps | Failures | ms |
|---|---|---|---:|---:|---:|---:|---:|---:|
| standard | baseline | SETTLED | 0 | 1200.0 | 480.0 | 2 | 0 | 7.12 |
| standard | scripted-agent | SETTLED | 0 | 1080.0 | 0.0 | 2 | 0 | 6.94 |
| tight_budget | baseline | SETTLED | 0 | 600.0 | 480.0 | 2 | 0 | 6.35 |
| tight_budget | scripted-agent | SETTLED | 0 | 480.0 | 0.0 | 2 | 0 | 6.62 |
| too_small | baseline | REVIEW_REQUIRED | 0 | 0.0 | 0.0 | 0 | 0 | 3.58 |
| too_small | scripted-agent | REVIEW_REQUIRED | 0 | 0.0 | 0.0 | 0 | 0 | 3.26 |
| transient_failure | baseline | SETTLED | 0 | 1200.0 | 480.0 | 2 | 1 | 6.95 |
| transient_failure | scripted-agent | SETTLED | 0 | 1080.0 | 0.0 | 2 | 1 | 7.72 |
| lost_acknowledgement | baseline | SETTLED | 0 | 1200.0 | 480.0 | 2 | 1 | 7.11 |
| lost_acknowledgement | scripted-agent | SETTLED | 0 | 1080.0 | 0.0 | 2 | 1 | 8.55 |
| permanent_failure | baseline | FAILED | 0 | 1200.0 | 480.0 | 2 | 3 | 8.41 |
| permanent_failure | scripted-agent | FAILED | 0 | 1080.0 | 0.0 | 2 | 3 | 9.38 |

Valid campaign completion: 8/12. Expected terminal states met: 12/12. Empty budgets and exhausted retries intentionally do not count as completed campaigns.

All scenarios include missing records, duplicate contacts, suppression and untrusted instruction text. Each attempts unapproved execution as a negative control. Replays and restart are exercised. Separate pytest adversarial tests inject tampered plans and contradictory ingestion.

Costs: actual model calls = 0; model cost = $0; mock tool fee = $0. Campaign spend is simulated and recorded per unique receipt. Scripted human steps count review/approval events, not real human time.

Uncertainty: `results.json` includes observed Brier scores, descriptive Wilson intervals, net outcome standard deviation, and realized-minus-predicted net value. EV estimation error compares predicted expectation to known synthetic truth; it is distinct from random realized error. Immature outcomes are excluded from observed metrics.

- Single frozen fixture; six correlated scenarios are not independent samples.
- Segment probabilities are illustrative imported scores, not fitted or calibrated on real data.
- Outcome draws are deterministic synthetic Bernoulli simulations; not causal lift estimates.
- Wilson intervals are descriptive binomial approximations; heterogeneous, correlated scenarios limit interpretation.
- Exact optimiser differs from greedy baseline by algorithm, not by model intelligence.
- Wall-clock latency is measured locally and will vary; no live agent latency or token cost exists.

Reproduce: `PYTHONPATH=src python3 -m agent_arena.acquisition evaluate --output /tmp/acquisition-report-UNIQUE`.
Results, traces, fixture and manifest are durable artifacts. Timing and timestamps vary; selection, outcomes and policy results should match.
