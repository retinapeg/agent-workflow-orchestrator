# Learning AcquisitionOps

This guide concerns the synthetic campaign slice in `src/agent_arena/acquisition`. The original coding-agent harness is described in `docs/GUIDE.md` and remains separate.

## From probability to economic decisions

A response model estimates `P(response | prospect, offer)`. A conversion model estimates `P(conversion | prospect, offer)`. Here conversion probability is unconditional and cannot exceed response probability. If a different model estimates `P(conversion | response)`, multiply it by response probability first. Do not multiply our unconditional conversion scores again.

For prospect i, let pᵢ be predicted conversion probability, mᵢ the contribution margin of a conversion, and cᵢ the contact cost. The expected net value is:

`EVᵢ = pᵢ × mᵢ − cᵢ`.

A consultancy in the fixture has p = 0.16, margin = $300 and cost = $12, yielding $36 expected net. A studio has p = 0.22, margin = $120 and cost = $6, yielding $20.40 expected net. At a $12 budget, **two studios yield $40.80**, exceeding one consultancy's $36. A bakery responds more frequently (70%) but has only $6.40 expected net. Response ranking would miss these distinctions.

The fixture stores cents as integers and probabilities as integer basis points (10,000 = 1). Optimization uses `conversion_bp × margin_cents − 10000 × cost_cents`, preserving exact comparisons. Display metrics divide by 10,000; display dollars divide cents by 100. No binary floating-point number decides a budget admission.

These margins are illustrative contribution amounts, not customer lifetime revenue. A commercial model must distinguish revenue from margin, cash timing, retention, refunds and fixed campaign costs.

## Predictive modelling

The frozen JSON contains a simple segment lookup predictor: bakery, studio, consultancy and retail each have response and conversion rates. These are hand-specified synthetic estimates, **not fitted measurements**. The hidden simulator rates intentionally differ, exposing imperfect calibration. `truth_bp` is used only by the outcome simulator and evaluation oracle, never by selection. Tests perturb truth labels and untrusted notes and check that recommendations do not change.

A data-backed successor could fit regularized logistic regression: `p(x) = 1 / (1 + exp(−(β₀ + βᵀx)))`. Train on an earlier period, tune on a separate validation period, freeze a later test set and evaluate calibration as well as ranking. Avoid features available only after contact. Consent and suppression belong in deterministic eligibility rules, not learned preferences. Segment membership can introduce unfairness; data collection and action policies need domain-specific review before real use.

A high probability of conversion is not evidence that outreach caused it. The economically correct target is often incremental value: `(P(convert | contact) − P(convert | no contact)) × margin − cost`. Estimate that difference with randomized holdouts or a justified causal design. This prototype models conversion under simulated outreach, not uplift.

## Budget optimization

Choose binary decisions xᵢ ∈ {0,1} to maximize `Σ xᵢ EVᵢ`, subject to `Σ xᵢ cᵢ ≤ B`, eligibility, and at most one contact per prospect. Negative expected-value prospects are excluded.

The baseline greedily adds the largest absolute EV contacts that fit. The scripted comparison uses exact 0/1 knapsack dynamic programming. A frontier maps each attainable spend to its best net value and selected IDs. Each new prospect updates a snapshot of the frontier so it cannot be selected twice. Tie breaking favors lower spend and stable prospect order. Integer budgets are bounded at 100,000 cents and ingestion at 200 records to keep this demo tractable.

Greedy EV can waste combinations, as the two-studios example shows. EV-per-dollar ranking is another useful heuristic but is not generally exact for indivisible items. Tests enumerate every subset of a small candidate set and compare the selected objective to that independent oracle.

Model uncertainty could be incorporated through conservative value estimates or a risk constraint. Neither uncertainty intervals nor risk penalties are used by this optimizer; they are reported separately. Correlated outcomes, channel constraints and campaign-wide fixed costs would require a richer objective.

## Agent architecture and deterministic authority

The recommendation stage proposes an economic plan. It cannot approve it, increase the budget, waive suppression or call a delivery provider. An existing `AgentResult` envelope describes the scripted recommendation honestly: provider kind `scripted`, model absent, zero inference calls.

A future live model might explain tradeoffs or propose bounded candidate segments through an authenticated user CLI. It would remain outside the policy boundary and would need its own evaluated failure cases. This implementation does not call the CLI from the application and does not claim an agent-assisted live benchmark.

The important control is separation of proposal from authority: all proposed records pass deterministic rules, a human explicitly reviews exclusions and approves, then execution revalidates the stored plan. Untrusted notes never enter an executable instruction path. New conflicting data can invalidate an approved plan; stale approval cannot silently authorize a changed recommendation.

## Durable execution and failures

Think of three boundaries:

1. Proposal: the event key is unique. Replaying the same event yields the same campaign; conflicting content under that key is rejected.
2. Approval: one transaction freezes the plan fingerprint, records an actor, reserves contacts and creates operations. Concurrent approvals cannot reserve the same contact.
3. Provider acceptance: a receipt keyed by operation persists before completion is acknowledged. If the process exits in between, the next run finds that receipt, records reconciliation and avoids another charge.

The mock provider and workflow share SQLite. This makes local exactly-once effects testable, including an actual child process exiting after receipt commit. A real distributed provider needs durable idempotency keys, lookup/reconciliation APIs and clear retention semantics. A transaction in a local DB alone cannot make a remote side effect exactly once.

Retries are explicit and bounded at three attempts per operation. Injected failures and attempted calls are audited; only accepted receipts incur simulated campaign cost. Lost acknowledgements are recovered before retry limits are considered, because a valid existing receipt is evidence of completion. No retry mechanism can move an unapproved campaign into execution.

## Outcomes and evaluation

Outcomes have a durable due day. Advancing the simulated clock exposes only matured outcomes. An unobserved conversion is pending, not zero. Replaying the clock cannot duplicate conversion events.

The Brier score is `mean((pᵢ − yᵢ)²)` for observed conversions yᵢ ∈ {0,1}. Lower is better, but a tiny synthetic sample cannot validate calibration. Expected-value estimation error compares the model's expected net with the simulator's known expected net. Realized-minus-predicted value also contains sampling variability and must not be interpreted as pure model bias.

Assuming independent Bernoulli conversions and fixed contact costs, net-value variance is `Σ pᵢ(1−pᵢ)mᵢ²`. The evaluator reports its square root using hidden synthetic rates. Correlated prospects would require covariance terms. Descriptive Wilson conversion intervals are approximate and do not solve heterogeneity or reused-scenario correlation.

The frozen evaluation contains six scenarios for each algorithm: normal, tight budget, infeasible budget, transient failure, lost acknowledgement and permanent failure. Every scenario includes suppression, missing and duplicate records, and prompt-injection text. Unapproved execution is attempted as a negative control. Tests additionally exercise tampering, changing truth labels, conflicting updates and concurrency.

Report policy violations, actual successful completion, expected safe terminal states, model error, optimizer regret, interventions, execution failures, latency and costs separately. The evaluator performs scripted approval actions; they are **simulated human steps**, not measured human effort. Timings are local observations. Model calls, model costs and mock tool fees are zero by construction. The development assistant's token usage is separate and unknown until the coordinator obtains a final CLI receipt.

## Questions to check understanding

- Why can a higher-response segment be less valuable than a lower-response segment?
- Why does the $12 example defeat greedy absolute-EV selection?
- What survives a crash after provider acceptance but before acknowledgement?
- Why must approval include the budget and the selected record snapshots?
- Which metric distinguishes prediction bias from random realized variation?
- What additional contract is necessary before replacing the SQLite mock with a remote provider?
- What evidence would be needed to call the comparison a live agent benchmark?
