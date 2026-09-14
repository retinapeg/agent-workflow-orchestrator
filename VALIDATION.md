# Validation Record

## Adaptive Engineering and Hackathon controller — 14 September 2026

Validated in the isolated `agent-workflow-orchestrator` clone on macOS without invoking a model:

- Ruff formatting: pass, 51 files.
- Ruff lint: pass.
- MyPy strict over all 21 source modules: pass.
- Pytest: 146 passed in 101.01 seconds.
- The full offline lifecycle used scripted providers only; it made no Codex, Claude, or Devin call.
- Deterministic adaptive validation covered two different accepted Engineering tasks followed by
  `done`, one Claude-written Hackathon beat with Codex scope approval, scope rejection, two rejected
  steps, a timed-out writer result, compact status/stop, isolated detached background start,
  cumulative last-green export, and exact-tree integration.
- Added and exercised hard per-phase/run deadlines, deadline-safe last-green export, structured
  non-command planner output, concrete path ownership, hashed MVP contracts, exact demo-beat
  selection, coordinator-owned acceptance evidence, Git-hook suppression, minimal generated-commit
  environments, live provider/task status, and fail-closed substantive output.
- Both Claude skill drafts have valid YAML frontmatter. The bundled `quick_validate.py` could not run
  because its environment lacks PyYAML; no dependency was installed solely for that optional check.
- Editable tool install: `adversarial-engineering-arena==0.3.1`; commands `team` and `agent-arena`.
- Wheel: `adversarial_engineering_arena-0.3.1-py3-none-any.whl`, SHA-256
  `6cb8d98f0ea61492179d31b28f7952d7869348cada83901323eed67b4e951b14`.
- Source distribution build: pass; its final hash is reported outside this self-containing file.

The earlier 9 September record below remains the evidence for the original 0.1.0 live
Codex-versus-Claude run; it was not rerun for this extension.

Validated on 9 September 2026 on macOS with Python 3.11, Git, Codex CLI, and Claude Code CLI.

## Release checks

- Ruff formatting: pass.
- Ruff lint: pass.
- MyPy over all 20 source modules: pass.
- Pytest: 124 passed in 68.95 seconds.
- Wheel build and clean-environment install smoke: pass
  (`adversarial_engineering_arena-0.1.0-py3-none-any.whl`, SHA-256
  `354635c332a80d430905ba20ca0fb0b08329d810175f70f2f2c809906d13cf53`); the installed
  `team --version` returned `team 0.1.0`.
- Deterministic offline full lifecycle: pass, including both implementations, reciprocal review,
  revision, fresh reevaluation, scoring, winner export, manifest verification, and safe integration.

## Real Codex versus Claude competition

Run ID: `20260909T181636Z-62f5afc7`

- Mode: Hackathon, one reciprocal review/revision round.
- Frozen source baseline: `e38c99c575cdf328ea3b8e3bc308bdf7822b7ead`.
- Invocation-only model choices: Codex `gpt-6-astra`; Claude `sonnet`.
- Both CLI authentication preflights passed without a model call.
- Both initial implementation phases completed.
- Both directed review phases completed and produced schema-valid critiques.
- Both revision phases completed.
- Both final candidates passed the required test and internal demo-readiness gate.
- Both candidates scored 10,000/10,000. The stable configured tie-break selected Codex because its
  final diff had 53 changed lines versus Claude's 73.
- The Codex review found a real Unicode lowercasing edge case in Claude's initial implementation;
  Claude accepted and fixed it during revision. This is direct evidence that the adversarial feedback
  loop changed a candidate for a defensible reason.
- Observed Claude CLI list-cost metadata across its three phases totalled `$0.403936`; this is retained
  provider metadata, not a claim about the user's final subscription charge. Codex CLI emitted token
  usage but no dollar cost.
- The original source checkout remained clean at the same HEAD.
- Manifest verification passed for 92 audited files before integration and 93 after the integration
  receipt was added.

## Exact-tree integration proof

The explicit integration command created branch `arena/live-winner-20260909` in a separate scratch
worktree. It did not push and did not merge into the original branch.

- Frozen winner tree: `1ba8db0f8e2c6dfacde55c7c9d662791264e1e0a`.
- Integrated worktree tree: `1ba8db0f8e2c6dfacde55c7c9d662791264e1e0a`.
- Integrated acceptance tests: four passed.
- Original source HEAD after integration: `e38c99c575cdf328ea3b8e3bc308bdf7822b7ead`.

## Problems discovered and fixed during operation

The first live attempt failed closed because the standalone Codex executable was too old for Astra
and Claude's macOS credential lookup could not see `USER` in the minimal provider environment. The
final harness now supports an explicit executable path, performs real non-model authentication
preflight, preserves only the required non-secret POSIX identity variables, and avoids retaining the
account-bearing status response.

The first authenticated contest then exposed a fairness issue: Python tests generated untracked
`__pycache__` files, which a repository without `.gitignore` could mistake for candidate changes. The
final harness installs configurable untracked-runtime ignore rules only in its private clone. Tests
prove that tracked files and explicitly force-added files remain visible to policy validation.

Independent adversarial audits also led to stronger global-deadline cleanup, process-group
cancellation, judge blinding, exact integration binding, frozen-overlay handling, strict config
typing/unknown-key rejection, private artifact permissions, and manifest validation.

## Preserved audit bundle

The audited run is packaged without `private/**`, which the run manifest deliberately excludes
because Git worktree bookkeeping is mutable. All 93 manifest-bound evidence files and the manifest
are present, including prompts, raw bounded provider output, diffs, reviews, revisions, evaluations,
scores, winner, report, workspace allocation, events, usage/cost metadata, and integration receipt.

- Archive: [live-run-20260909T181636Z-62f5afc7-audit.tar.gz](../adversarial-engineering-arena-validation/live-run-20260909T181636Z-62f5afc7-audit.tar.gz)
- SHA-256: `ef8a01e7ad5dfc580af4ae86bdf90e60f175c5ab8e5554dfb1bec08bd1a0bbaa`
- Extract-and-verify result: `OK: 93 audited files match their SHA-256 hashes`.

The archive intentionally contains absolute local paths and model/provider transcripts because it is
an audit artifact. Review it before sharing outside the machine.
