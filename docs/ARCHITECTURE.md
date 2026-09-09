# Architecture

## Invariants

1. Every engineer starts from the same resolved commit and tree SHA.
2. Every engineer receives byte-identical task and acceptance content.
3. Every engineer has a distinct physical worktree and generated branch inside a private run clone.
4. The private clone has no origin and no local-object alternates or hardlinks requested by clone.
5. Reviews consume frozen patches/evidence, never a writable opponent checkout.
6. The coordinator owns checks, benchmarks, score arithmetic, and eligibility.
7. Every check starts from an immutable candidate commit in a fresh detached worktree.
8. Required failures make a candidate ineligible; the judge cannot override them.
9. Each mode has a bounded, configured number of adversarial rounds.
10. Integration reproduces exactly one winner tree; implementations are never blindly merged.
11. Failure preserves all completed evidence and records a terminal state.
12. Worktrees are not represented as a hostile-process security boundary.

## Components

| Module | Responsibility |
|---|---|
| `config` | Strict TOML parsing, exact modes, limits, checks, benchmarks and redacted audit view |
| `gitops` | Source proof, private clone, branches/worktrees, ancestry checks, immutable freezes and bundles |
| `providers` | Codex CLI, Claude CLI, OpenAI API, Anthropic API, generic CLI and scripted adapters |
| `process` | Shell-free argv execution, capped concurrent output drains, deadlines and process-group termination |
| `prompting` | Shared task/acceptance framing and phase-specific structured contracts |
| `evaluator` | Fresh validation trees, trusted overlays, checks, repeated benchmarks and fail-closed parsing |
| `scoring` | Decimal mode-specific arithmetic, eligibility and stable tie-breaking |
| `orchestrator` | Lifecycle, concurrency, reciprocal review, bounded revisions, judge and winner export |
| `audit` | Atomic artifacts, ordered events, hashes and integrity verification |
| `integration` | Separate exact-tree branch/worktree creation with rollback on failure |
| `cli` | Small `team hack`, `team engineer`, status, results, open, verify and integrate surface |

## State machine

```text
CREATED
  -> PREFLIGHT
  -> SNAPSHOT
  -> IMPLEMENT (providers run concurrently)
  -> FREEZE_INITIAL
  -> EVALUATE_INITIAL
  -> for each configured round:
       CROSS_REVIEW (reviewers concurrently; targets are immutable diffs)
       -> REVISE (authors concurrently)
       -> FREEZE_FINAL
       -> EVALUATE_FINAL
  -> SCORE
  -> OPTIONAL_JUDGE (eligible candidates inside score band only)
  -> EXPORT_WINNER -> COMPLETE
                 or -> NO_WINNER
```

Any uncaught lifecycle failure becomes `FAILED`; a user interruption becomes `CANCELLED`. Provider
failure is normally candidate-local so the other implementation can still finish. A failed or
skipped required revision makes that candidate ineligible instead of silently falling back to an
earlier generation.

## Concurrency

Initial implementations run concurrently. In every review phase, engineers run concurrently, while
one reviewer processes multiple opponents in stable ID order. Revisions run concurrently. Evaluation
commands and benchmarks run serially to reduce machine-contention bias; every invocation gets a fresh
tree so one check cannot contaminate another.

The current two-engineer setup produces both directed reviews: Codex of Claude and Claude of Codex.
With a future third engineer, the same scheduler produces every directed reviewer/target pair.

## Candidate identity

An implementation is never “whatever happens to be in a folder.” Freeze verifies the expected
worktree top level, generated branch, required commit ancestry, changed-file count, path policy, and
diff size. The coordinator stages all content and creates a signed-off local freeze commit. Its commit,
tree, baseline-relative binary patch, changed paths, line count, and policy findings become immutable
evaluation inputs.

The source checkout is not one of the working copies. `git clone --no-local --no-hardlinks` creates a
run-owned repository, then its origin is removed. This protects the source from routine agent Git
commands while retaining normal worktree semantics inside the run.

## Extension point

Every provider implements:

```python
class Provider:
    def preflight(self) -> dict: ...
    def run(self, request: AgentRequest, artifact_dir: Path) -> AgentResult: ...
```

`AgentRequest` normalizes phase, model, prompt, workspace, timeout, output/token/cost limits, and an
optional JSON schema. `AgentResult` normalizes status, text, usage, observed cost, bounded process
evidence, applied operations, and error. Adding Gemini does not require an orchestration change.
