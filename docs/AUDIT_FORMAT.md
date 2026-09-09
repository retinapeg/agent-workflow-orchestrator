# Audit Format

Audit directories are created with mode `0700`; retained regular artifacts are normalized to `0600`.
Artifacts written through the coordinator's audit store are atomic; provider-created auxiliary files
are permission-normalized before manifesting. JSON files are UTF-8, sorted-key, two-space-indented
documents. `events.jsonl` contains one ordered JSON object per line.

## Core identity

- `run.json`: schema version, run ID, terminal/current state, creation/update timestamps, and global
  error when present.
- `repository.json`: absolute source top level, Git common directory, source branch and HEAD,
  requested base ref, resolved base commit/tree, cleanliness, porcelain status, and credential-redacted
  remotes.
- `common-inputs.json`: task and acceptance SHA-256, mode, round count, base commit/tree, and the
  initial tree recorded for every engineer. Equality of those trees is a lifecycle invariant.
- `workspaces.json`: coordinator-observed allocation written before implementation begins. It records
  the private repository path and, for every engineer, the engineer ID, generated branch, absolute
  private worktree path, frozen base commit, and baseline tree. These values are read back from Git by
  `RepositoryManager` after worktree creation rather than reconstructed later from naming rules. The
  schema is provider-independent and therefore applies equally to CLI, API, and scripted runs.
- `effective-config.json`: selected mode and effective configuration. Environment values and
  key/token/secret/password/auth-like options are redacted.
- `inputs/trusted-overlay/**`: the one frozen coordinator-owned acceptance overlay used for every
  candidate evaluation. Its hashes and destination are bound into `acceptance.json`.

## Provider evidence

Every phase directory contains `prompt.md`, `response.md`, and `result.json`. CLI invocations also
contain bounded `stdout.log` and `stderr.log`. `result.json` records provider kind, configured model,
status, usage, observed cost where available, process arguments/cwd/status/duration/truncation, API
operation count, and normalized errors.

Initial and revision generation directories add `candidate.json` and `candidate.patch`.
`candidate.json` identifies the coordinator freeze commit/tree, required parent, changed paths/lines,
patch size, policy violations, validity, and patch location.

Review directories add `critique.json` with reviewer, target, status, validation error, and the
structured critique. Findings are data: severity, path/line, reproduction, violated requirement,
explanation, confidence, and suggested attack tests.

## Evaluation evidence

Each check stores command arguments, required/weight metadata, exit status, timeout, duration, pass,
error, and separate bounded logs. Each benchmark stores warm-up and measured process evidence,
individual finite metric strings, median aggregate, normalized Decimal score, direction, threshold
gate, and error. Candidate evaluation summaries bind all results to one engineer/generation/commit.

## Selection and export

- `deterministic-scores.json`: eligibility, score basis points, earned/total Decimal weights,
  disqualifications, and changed-line tie-break evidence.
- `decision.json`: ranking, deterministic winner, final winner, judge candidate set, whether the judge
  was used, judge reason, and fallback reason.
- `winner.json`: engineer ID, final generation, baseline, selected commit/tree, patch hash, and
  non-integration notice.
- `winner.patch`: exact baseline-relative binary patch.
- `winner.bundle`: Git bundle containing a temporary private ref to the selected commit lineage.
- `salvage.md`: non-merging inventory of losing patches, candidate-authored tests/specs, and attack
  review locations.
- `report.md`: human summary and security/integration status.

## Manifest

`manifest.json` records relative path, byte size, and SHA-256 for every retained file except itself and
`private/**`. Verification rejects missing files, hash mismatches, unexpected unlisted files, and
symlink artifacts. The private Git clone is excluded because Git maintenance and worktree state are
mutable; immutable candidate identities and patches are included.

A successful explicit integration adds a timestamped `integration-receipts/*.json`, rebuilds the
manifest, and records the new branch/worktree, source, baseline, winner identity/tree, integration
  commit, whether the selected tree was already equal to baseline, and explicit `pushed=false` /
  `merged_into_original_branch=false` facts.
