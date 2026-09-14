# Adversarial Engineering Arena

Adversarial Engineering Arena is a local Python CLI that makes Codex and Claude solve the same
software task independently, attacks each implementation with the other engineer, allows bounded
revision rounds, evaluates immutable candidates with coordinator-owned checks, and exports only the
strongest eligible result. It keeps the original checkout unchanged unless you later run the
explicit `integrate` command.

The installed command is `team`. There is one orchestrator and exactly two selectable operating
modes:

- `team hack ...` — a fast, demo-first competition with one revision round by default.
- `team engineer ...` — a deeper, production-quality competition with two revision rounds by
  default and a configurable maximum of four.

The provider layer is independent of orchestration. Codex CLI, Claude Code CLI, OpenAI Responses
API, Anthropic Messages API, a generic CLI seam, and a deterministic offline provider all implement
the same adapter contract.

## Quick Start

Requirements: Python 3.11+, Git, and at least two configured providers. For the recommended local
path, install and authenticate both `codex` and `claude` first.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp team.example.toml team.toml
team doctor --config team.toml
```

Edit the `[[checks]]` commands in `team.toml` so they are the real commands for the target project.
Then run either mode:

```bash
team hack task.md --repo /absolute/path/to/project --config team.toml
team engineer "Fix the cache race without changing the public API" \
  --repo /absolute/path/to/project --config team.toml
```

Choose the models separately for any invocation with `--codex-model MODEL` and
`--claude-model MODEL`. Both modes and `doctor` accept these options:

```bash
team hack task.md --repo /absolute/path/to/project --config team.toml \
  --codex-model gpt-6-astra --claude-model sonnet
team engineer task.md --repo /absolute/path/to/project --config team.toml \
  --codex-model gpt-6-astra --claude-model opus
team doctor --config team.toml --codex-model gpt-6-astra --claude-model sonnet
```

Use model IDs or aliases supported by your selected provider and account; the harness forwards
them without maintaining a hard-coded model catalog. An omitted option preserves the corresponding
configuration default. Overrides apply to every phase for that engineer, including an optional
judge using it, and appear in the run's effective configuration and provider records. They do not
rewrite `team.toml`. `--codex-model` matches `codex_cli` or `openai_api`;
`--claude-model` matches `claude_cli` or `anthropic_api`, regardless of engineer name. If a family
has no configured provider or has more than one, the option fails before starting a run; set
models individually in the configuration for those ambiguous teams. `doctor` checks executable
availability and saved CLI authentication in the same sanitized environment used for a run; it
does not make a model call or prove access to a selected model or billing entitlement.

Inspect the most recent run without finding its directory manually:

```bash
team status --config team.toml
team results --config team.toml
team open winner --config team.toml
```

The run selects and exports a winner, but does not merge it. Integration is a separate, explicit
gate:

```bash
team integrate /path/to/run \
  --source /absolute/path/to/project \
  --branch arena/winner-cache-race \
  --worktree /absolute/path/to/project-winner
```

That command creates a new branch and physical worktree. It does not push, force-update, or merge
into the source branch.

`team status` is deliberately one-shot. It reports the current mode, run directory, deadline,
seconds remaining, seconds since real progress, and every active provider/phase. Provider phases are
mode-capped in addition to the global run deadline, so a configured 20-minute provider cannot keep a
Hackathon run opaque for 20 minutes. The shipped defaults cap one provider phase at 5 minutes for
Hackathon and 15 minutes for Engineering; the example Hackathon run has a 45-minute hard stop.

This command is the safe execution substrate for one frozen task. It does not choose the next task
or repeat across a backlog by itself; an adaptive controller must perform that observe, select,
dispatch, verify, and repeat loop. This distinction is deliberate so a fixed lifecycle is never
misreported as autonomous progress.

## What a run does

For every run, the coordinator:

1. Validates the configuration and provider availability without making a model call.
2. Requires a clean source checkout by default and resolves the requested baseline to a full commit
   and tree SHA.
3. Creates a private `git clone --no-local --no-hardlinks`, removes its origin, and creates one
   physical branch/worktree per engineer from the same frozen tree.
4. Gives every engineer the same task, acceptance contract, baseline, and mode guidance. Only the
   provider transport instructions differ.
5. Runs initial implementations concurrently, then freezes each result into an immutable commit and
   records a binary patch.
6. Runs every configured check in a fresh detached validation worktree. Benchmarks run serially from
   fresh trees for every warm-up and measured repetition.
7. Gives Claude the frozen Codex diff and evidence, and gives Codex the frozen Claude diff and
   evidence. A reviewer never receives write access to the opponent's worktree.
8. Returns the critiques to the original authors for the configured number of revision rounds. The
   engineers are told not to copy, merge, cherry-pick, or commit the competitor's code.
9. Re-freezes and re-evaluates every revised candidate. No stale initial result is reused.
10. Applies hard eligibility gates and deterministic arithmetic. An optional blinded judge may only
    choose among eligible candidates inside a configured score band.
11. Writes `winner.patch`, `winner.bundle`, a human report, an integrity manifest, and a salvage
    inventory of losing tests/ideas. It still does not touch the source checkout.

The state machine and invariants are described in [Architecture](docs/ARCHITECTURE.md).

## Modes

| Behaviour | Hackathon | Engineering |
|---|---|---|
| Objective | Convincing, reliable live demo quickly | Strongest correct, maintainable production result |
| Default adversarial rounds | 1 | 2 |
| Configurable rounds | 1–2 | 2–4 |
| Scope preference | Only demo-critical scope | Root cause and affected callers only; justified deeper work only when evidence demands it |
| Built-in documentation gate | `DEMO.md`, `NOT.md`, `DEMO_CHECKLIST.md`, README Quick Start | Reproduction and exact evidence for defects |
| Typical evidence emphasis | E2E demo, reliability, UX, critical correctness | Correctness, regression, security, architecture, performance |

Hackathon candidates must maintain a `DEMO_CHECKLIST.md` covering the startup command, primary demo
flow, demo/sample data, expected outputs, environment variables, fallback mode, and known
limitations. The harness verifies those topics and a short README Quick Start section. Clean startup,
the actual main flow, fallback behaviour, and visible UX still need real configured acceptance
commands; documentation alone cannot prove them.

Mode-specific limits and weights are configured under `[modes.hackathon]` and
`[modes.engineering]`. A practical
task-specific Hackathon setup can map checks to approximately 40% E2E demo, 20% regression/recovery,
15% UX completion, 10% edge correctness, 10% benchmark, and 5% simplicity. An Engineering setup can
map approximately 30% functional correctness, 20% regression/edge behaviour, 15% architecture,
15% security/failure handling, 10% performance, and 10% test/requirement coverage. Because those
dimensions are repository-specific, the harness never fabricates an architecture or UX score; each
percentage must be backed by a configured check or benchmark.

Example override syntax:

```toml
[modes.hackathon]
revision_rounds = 1
check_weights = { e2e = "40", regression = "20", ux = "15", edge = "10", simplicity = "5" }
benchmark_weights = { demo_latency = "10" }

[modes.engineering]
revision_rounds = 3
check_weights = { correctness = "30", regression = "20", architecture = "15", security = "15", coverage = "10" }
benchmark_weights = { performance = "10" }
```

Every named ID must correspond to a `[[checks]]` or `[[benchmarks]]` entry.

## Configuration

Start from [team.example.toml](team.example.toml). The central pieces are:

```toml
[engineers.codex]
kind = "codex_cli"
model = "gpt-6-astra"

[engineers.claude]
kind = "claude_cli"
max_cost_usd = "5.00"

[[checks]]
id = "tests"
category = "test"
command = ["python3", "-m", "pytest", "-q"]
required = true
weight = "60"
```

Commands are argument arrays. The coordinator always uses direct process execution (`shell=False`),
so task text, agent IDs, branches, and command arguments are never interpolated into a shell.

Important run controls:

- `artifact_root` must be outside the source repository. Runs are private (`0700`) by default.
- `require_clean_source = true` prevents accidental omission of uncommitted work.
- `protected_paths` disqualifies edits to trusted tests, workflow policy, or other coordinator-owned
  paths.
- `allowed_paths`, when non-empty, makes any edit outside the listed globs a policy violation.
- `untracked_artifact_excludes` keeps common interpreter caches, tool caches, local environments, and
  dependency trees out of candidate commits when a source repository lacks its own ignore rules.
  These Git-ignore patterns apply only inside the private clone and cannot hide edits to tracked files
  or files an agent explicitly force-adds.
- `trusted_overlay` points to coordinator-owned acceptance files. They are copied and hashed once at
  run start; every fresh validation checkout receives that same frozen copy. Pre-existing candidate
  symlinks and `.git` targets are rejected before any overlay file is replaced.
- Provider and check environment pass-through lists are deliberately separate. The default provider
  list preserves the non-secret POSIX identity variables `USER` and `LOGNAME`, which macOS credential
  stores may need to locate a saved CLI login. If you replace the allowlist, retain those names on
  POSIX systems. API keys are not passed to candidate test processes by default.
- Time, prompt, output, diff, file-count, API-context, operation-count, response-byte, and token
  limits are centralized in `[limits]`.

The effective configuration is frozen into each audit run. Configuration values under names that
look like keys, tokens, secrets, passwords, or auth data are redacted from that snapshot.
Unknown configuration keys, wrong scalar types (including quoted booleans), and unsupported mode
names fail before any provider is invoked, so misspelled safety controls cannot silently become no-ops.
Task text, repository content, model responses, and command output are retained within their byte
limits and may themselves contain sensitive material; the harness does not claim general secret
scrubbing.

## Deterministic evaluation and scoring

Required failures are hard gates. A candidate is ineligible if any of these is true:

- its provider implementation/revision phase did not complete;
- its branch, ancestry, worktree identity, or immutable freeze failed validation;
- it changed a protected path or escaped an allowed path scope;
- a required test, lint, type, build, security, or task-specific check failed or timed out;
- a required benchmark was missing, malformed, non-finite, outside its gate, or failed to execute;
- a required internal mode check failed.

An ineligible candidate cannot be rescued by a benchmark, a model review, or the judge.

For eligible candidates, each passing check earns its effective mode weight. Benchmarks contribute a
clamped 0–1 normalized fraction between configured `worst` and `best` values. Arithmetic uses
`Decimal` and produces an integer score from 0 to 10,000 basis points. The stable ordering is:

1. eligible before ineligible;
2. higher deterministic score;
3. fewer changed lines when `prefer_smaller_diff = true`;
4. engineer ID, ascending, as the final reproducible tie-break.

This scoring function is deterministic for recorded evidence. Model output and runtime benchmarks
themselves are not inherently deterministic; use fixed seeds, controlled fixtures, and stable hosts
when reproducibility matters.

### Checks

Checks run one at a time from clean candidate commits. A successful exit status is a pass. Output is
captured with explicit byte caps, and truncation is recorded. Normal completion, timeouts, and Ctrl-C
clean up the owned process group, not only its parent. Direct SDK calls have the explicitly documented
cancellation limitation in [Provider contracts and limits](docs/PROVIDER_LIMITS.md).

### Benchmarks

A benchmark command must emit a final line shaped like:

```text
ARENA_METRIC {"latency_ms": 12.34}
```

The configured metric must be numeric and finite. Booleans, `NaN`, infinity, missing keys, malformed
JSON, failed repetitions, and missing output fail closed. The measured aggregate is the median.

### Optional judge

Set:

```toml
[judge]
enabled = true
engineer = "codex"
score_band_basis_points = 100
```

The judge sees blinded labels, frozen diffs, and coordinator evidence. It may choose only an eligible
candidate no more than the configured score band below the deterministic leader. Invalid JSON, an
unknown choice, timeout, provider failure, or a choice outside that set falls back to the deterministic
winner and records the reason. A score band of zero makes the judge a true tie-breaker.

## Provider adapters

### Codex CLI (recommended)

The adapter uses non-interactive `codex exec`, JSONL audit output, an explicit model, an explicit
approval policy, `workspace-write` only for implementation/revision, and `read-only` for review and
judging. It does not use the deprecated `--full-auto` compatibility flag. Saved CLI authentication is
reused. `team doctor` first runs the local, non-model `codex login status` command with bounded output
and the same sanitized environment used for agent phases; authentication output is not copied into
the doctor result. Current Codex documentation documents the status command in
[Authentication](https://learn.chatgpt.com/docs/auth) and describes the JSONL event stream,
structured output schemas, sandbox settings, automation authentication, and session behavior in
[Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

`doctor` reports the exact resolved executable. If an older standalone CLI is earlier on `PATH` and
the selected model requires a newer build, select the intended binary explicitly:

```toml
[engineers.codex.options]
executable = "/absolute/path/to/codex"
```

The same `options.executable` override is available under a `claude_cli` engineer. This is an
executable choice, separate from the per-run `--codex-model` and `--claude-model` choices.

Codex CLI does not expose a documented hard per-invocation dollar limit. The harness enforces the
parent timeout and records token usage when emitted; it does not mislabel an observed token count as
a guaranteed cost cap.

### Claude Code CLI (recommended)

The adapter uses print mode with JSON output, `--safe-mode`, `--restricted`, an empty strict MCP
configuration, and file tools only (`Read`, `Glob`, `Grep`, `Edit`, and `Write`) for implementation.
Review/judge phases remove write tools. `team doctor` runs the local, non-model `claude auth status`
command in that same sanitized environment, requires a successful status with `loggedIn: true`, and
does not retain the command's account-bearing JSON. Candidate tests remain coordinator-owned rather
than giving the engineer Bash access. `--max-budget-usd` is set when configured, and a parent timeout
remains in force. See Anthropic's
[Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) and
[headless/SDK guidance](https://code.claude.com/docs/en/headless).

### Direct OpenAI and Anthropic APIs

Install the optional clients:

```bash
python -m pip install -e ".[api]"
```

Then adapt [configs/api.example.toml](configs/api.example.toml). API mode deliberately does not let a
text model execute arbitrary shell commands. It sends a bounded UTF-8 repository snapshot and accepts
only validated whole-file `write` or `delete` operations. Absolute paths, traversal, `.git`, duplicate
targets, symlinks, special files, excessive operations, and oversized payloads are rejected before
mutation. Multi-file application is backed up and rolled back on an application error.

This is intentionally a small-repository fallback. Files beyond the context/file budgets are listed
as omitted. Use the coding CLIs for large repositories where iterative exploration matters.

### Gemini or another provider

Orchestration is based on a provider-neutral `Provider.run(AgentRequest) -> AgentResult` contract and
supports any number of configured engineers. A native Gemini adapter can be added without changing
worktree creation, cross-review scheduling, evaluation, scoring, or integration. The existing
`generic_cli` kind can be used earlier with an argv-array command that accepts the prompt on standard
input, but its sandbox and budget guarantees belong to that external CLI and must be assessed before
use.

### Offline scripted provider

The deterministic `scripted` provider exercises the complete lifecycle without credentials or paid
calls. It uses the same path validator as API mode; it is not a shortcut around orchestration.

```bash
PYTHONPATH=src python3 examples/offline/run_demo.py
```

The demo creates a disposable Git repository, runs the full Codex-labelled versus Claude-labelled
implementation/review/revision/evaluation loop, selects the correct candidate, verifies the audit,
and prints the retained run directory.

## Audit layout

Every run retains:

```text
run.json                         current terminal state and timestamps
effective-config.json            selected mode plus redacted effective config
inputs/trusted-overlay/          one frozen acceptance overlay, when configured
spec.md                          exact task text
acceptance.json                  checks, weights, overlay manifest and hashes
repository.json                  source identity, branch, baseline/tree and redacted remotes
common-inputs.json               proof of equal task/acceptance/tree inputs
workspaces.json                  coordinator-observed branch/worktree allocation before agents run
events.jsonl                     append-only ordered lifecycle events
engineers/<id>/initial/          prompt, response, raw logs, usage, candidate patch/commit
engineers/<id>/reviews/...       reciprocal structured attacks
engineers/<id>/revisions/...     every bounded revision round
evaluations/<id>/<generation>/   fresh-checkout command evidence and benchmark samples
deterministic-scores.json         exact eligibility and arithmetic
judge/                           optional blinded prompt/result/fallback evidence
decision.json                    deterministic order and final selection
winner.patch                     selected baseline-relative binary patch
winner.bundle                    portable Git bundle for the selected lineage
winner.json                      selected immutable commit/tree and patch hash
salvage.md                       losing patches, tests and review locations for human reuse
report.md                        concise human-readable result
manifest.json                    SHA-256 and size of every retained audit artifact
private/                         private clone/worktrees, excluded from the hash manifest
```

Use `team verify /path/to/run` before integration. See [Audit Format](docs/AUDIT_FORMAT.md) for the
field-level contract.

`workspaces.json` is written by the coordinator before it invokes any implementation provider. The
same record is produced for CLI, direct API, generic CLI, and deterministic scripted providers; it
does not depend on an agent self-reporting where it worked.

Prompts and diffs can contain proprietary source material. Treat the audit directory as sensitive.
The hash manifest detects accidental or later artifact changes when the manifest remains trustworthy;
it is not a tamper-proof ledger against a process running as the same OS user.

## Safe integration

`team integrate` first verifies the full manifest, run state, winner patch hash, exact source path,
clean source status, and unchanged source `HEAD` as recorded at run start. The selected `--base-ref`
may intentionally differ from that source `HEAD`. It cross-checks the eligible decision and winner
records, validates the new branch name, refuses an existing destination, creates a new worktree from
the recorded baseline, applies the recorded binary patch, commits it, and requires the resulting tree
SHA to equal the frozen winner tree. A valid winner identical to baseline produces an explicit empty
integration commit and a receipt marked `no_changes=true`.

If anything fails after branch creation, only the newly created integration worktree/branch is rolled
back. The source branch is never reset. A successful receipt records that nothing was pushed and the
original branch was not merged. If the source has advanced, the command stops: rerun evaluation on
the new base instead of accepting an untested three-way merge.

## Security boundary

This release is a trusted-local workflow harness, not a hostile-code sandbox. A private clone and
separate worktrees strongly reduce accidental collisions and eliminate the source repository as a Git
remote, but processes running as the same OS user can still discover sibling directories, source
files, credentials, and Git metadata. Running candidate checks also executes candidate code.

For unknown or adversarial repositories, wrap each provider and each evaluation command in a real
container, VM, or OS sandbox with only the intended checkout mounted writable, a read-only acceptance
overlay, no host credentials, restricted network access, and separate resource limits. The detailed
boundary is in [Threat Model](docs/THREAT_MODEL.md).

## Development and verification

```bash
python -m pytest -q
python -m ruff check .
python -m mypy src
PYTHONPATH=src python3 examples/offline/run_demo.py
```

The test suite covers configuration profiles, bounded output, descendant-process termination, path
traversal and symlink rejection, a complete Hackathon competition, a two-round Engineering
competition, source non-mutation, private-clone isolation, manifest verification, deterministic
winner selection, and exact-tree integration.

## Deliberate first-release limitations

- Local worktrees are workflow isolation, not an OS security boundary.
- Direct API mode uses bounded whole-file operations rather than an iterative shell/tool agent.
- CLI dollar budgets are only called hard limits where the underlying CLI enforces them.
- The harness never installs target dependencies or invents target checks. Put authoritative setup,
  tests, lint, types, smoke flows, security checks, and benchmarks in configuration.
- Review-proposed attack commands are stored as suggestions and are never executed automatically.
- Losing code is preserved but never blended into the winner. Human review is required before
  selectively transferring a useful losing test or idea.
- Integration creates a branch/worktree only. Publishing and merging remain explicit user actions.
