# Threat Model

## Intended use

The default local mode is intended for repositories and developer machines the user already trusts.
It protects against accidental cross-agent edits, unsafe automatic merging, stale evaluation, shell
injection through task text, runaway output, and ordinary timeout descendants.

## Protected assets

- The user's source checkout and current branch.
- The common task, acceptance commands, trusted overlay, score policy, and baseline identity.
- Each engineer's independent candidate.
- API/CLI credentials and unrelated host files.
- Audit evidence and the selected immutable winner.

## Implemented controls

- Clean source required by default; full commit/tree identities recorded.
- Private no-local/no-hardlink clone; origin removed; object alternates rejected.
- Generated safe run, branch, and worktree identifiers.
- Separate physical worktrees and immutable coordinator freeze commits.
- Adaptive planners and scope reviewers receive fresh detached worktrees at the exact last-green or
  candidate commit; ignored writer residue is never reused as review input.
- Adaptive planner ownership is limited to exact files inside a required coordinator allowlist.
- Direct argv execution without a shell.
- Separate provider and candidate-test environment allowlists. Credential environment values are
  not intentionally copied into configuration evidence or candidate-test environments.
- Codex explicit sandbox and no-approval configuration.
- Claude safe/restricted mode with an empty strict MCP set and no Bash in the built-in profile.
- API/scripted operations restricted to bounded UTF-8 whole-file writes/deletes under the workspace.
- Absolute paths, parent traversal, `.git`, backslash paths, symlinks, special files, duplicates, and
  over-budget operations rejected.
- Coordinator-owned checks in fresh trees; an optional external overlay is copied and hashed once,
  then that frozen copy is safely overlaid for every candidate.
- Capped prompt, context, output, response, diff, operation, file, token, time and cost controls where
  the selected provider exposes an enforceable control. Unsupported guarantees fail closed or are
  explicitly acknowledged in configuration.
- Process-group cleanup after normal completion, timeout, and coordinator cancellation.
- Required checks are ineligibility gates.
- Review text is stored as untrusted data and never executed.
- Blinded constrained judge with deterministic fallback.
- Manifest verification and exact-tree integration on a new branch/worktree.

## Explicit non-guarantees

Git worktrees share a common Git directory. More importantly, a local process running as the same OS
user can walk outside its current directory regardless of its prompt. The private clone prevents
routine mistakes from reaching the original Git metadata, but does not stop a malicious agent from
discovering the original path, audit directory, sibling worktrees, browser credentials, SSH keys, or
other user files. Native CLI restrictions improve the boundary but are not a substitute for an
independently administered OS sandbox.

Evaluation commands import or execute candidate code with evaluator permissions. A hidden acceptance
overlay prevents simple test editing, but does not make executing an untrusted candidate safe. An
allowlist containing Python, Node, Git, a compiler, or a package manager would still permit general
execution and therefore is not described as a sandbox.

The hash manifest detects changes only relative to the manifest. A same-user malicious process that
can rewrite both files and manifest is outside the tamper-resistance claim. Store completed audits in
write-protected or remote append-only storage when stronger provenance is required.

Prompts, repository snapshots, model responses, command output, exception summaries, and audit paths
can themselves contain sensitive values. The harness avoids deliberately copying configured
credential environment values and strips credentials/query values from recorded Git remotes, but it
does not offer a general secret-scrubbing guarantee. Do not put secrets in task text, source files,
commands, URLs, or model output; protect the retained run directory as sensitive data.

Coordinator cancellation actively terminates processes launched through the local CLI/check runner.
Direct API SDK calls cannot be forcibly stopped by that process registry; API mode is therefore
disabled unless its weaker SDK-timeout behavior is explicitly acknowledged. A remote provider may
finish and bill an in-flight request after local cancellation.

## Strong-isolation deployment profile

For untrusted code, run each engineering phase and each evaluation in a separate short-lived
container, VM, or sandbox account with:

- only that candidate checkout mounted writable;
- coordinator acceptance data mounted read-only;
- no original source, sibling candidate, audit, home, SSH, cloud, or browser credential mounts;
- no network by default, with domain allowlists only when required;
- CPU, memory, process, filesystem, output, and wall-clock quotas;
- dependency installation separated from credential-bearing model invocation;
- artifacts copied out by the trusted coordinator after termination.

The provider interface deliberately permits a future container runner without changing the contest
state machine or score model.
