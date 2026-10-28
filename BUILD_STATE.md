# Build state — 28 October 2026 (current)

## Autobuild alpha: what works, with evidence

All runs below used the real authenticated Codex and Claude Code subscriptions through
the production controller on **http://127.0.0.1:8791** (launchd login service, state in
`.arena-control`). Every evidence directory under `docs/build-evidence/` holds the
driver timeline, screenshots and `result.json` with attempt ids, models, timings and
integration receipts.

| Run | What it proves | Result |
|---|---|---|
| `first-build-attempt1` | Director/reviewer prompt defect found by a real run (correct code rejected three times for unproducible "evidence"); repair budget exhausted → **blocked**, visible in UI | fixed in `e3befc8` |
| `first-build` | Ordinary instruction typed into the 390px UI → `gpt-6-astra` director (max) → Codex build → Claude review → director sign-off → accepted and integrated from the UI; browser closed 60 s mid-run | `fa1024a` in 178 s |
| `restart-recovery` + `parallel-build` | Director split one request into two independent tasks; **Codex and Claude built concurrently**; controller stopped mid-build and restarted; one attempt per task after reconciliation; cross-provider reviews; `direct` phase ran on **Claude Fable 5.1 (max)** because Codex was busy; integrated from the UI | `7821b1f` |
| `single-strategy-build` | Director chose a single task for the same request on a fresh snapshot; integrated from the UI | `2296939` |
| `zero-tap-build` | Project policy `auto_local_main`: instruction → verified → **integrated with no taps** | `5507b7c` in 210 s |
| `final-unattended-build` | Two-part request on a fresh snapshot after the safety fixes, automatic integration | see result.json |
| `safety-review` | 91-agent adversarial review: 28 claimed, 27 confirmed defects; all unattended-reliability items fixed in `cd36c26` with tests | `tests/control/test_safety_fixes.py` |
| `ui-live`, `ui-acceptance` | Redesigned UI on real controller data at 390/1440 px; browser acceptance against the fixture server | passing |

Release checks on this code: `ruff` clean; full suite **269 passed** before the safety
fixes (`.arena-backups/regression-current.txt`); final run recorded in
`.arena-backups/regression-final.txt`; `npm run build` passes; `node web/ui-acceptance.mjs`
passes. The previously failing host cancellation test passes on the normal host (12/12 in
`tests/test_cancellation.py` + `tests/test_process.py`).

## Operating facts

- Service: `local.agent-arena.control.1a6834e84b5a` (RunAtLoad, KeepAlive, `caffeinate -i`).
  Start/stop/recover commands: `docs/MOBILE_BUILD_CONTROL.md` (top section).
- Director: Codex `gpt-6-astra` at `max` (advertised in the local model catalog); validated
  stand-in Claude `claude-fable-5-1` at `max` when no Codex slot is free (`check-accounts --probe`).
- Workers: Codex (model/effort routed per task: luna/sol/astra, low→max) and Claude (`sonnet`,
  tool-free validated file operations). Reviews are always by the other provider.
- Integration policy per project: `manual` (default) or `auto_local_main`. Nothing is pushed.
- Three enrolled proof projects are disposable snapshots of this repository
  (`.arena-control/proof-source*`); the stale 8790 runtime was stopped and preserved.

## Not yet done

- Private phone access is configured: Tailscale Serve (tailnet-only, Funnel off) at
  `https://leonards-macbook-air-1.tail11ad03.ts.net`, TLS verified, unpaired requests denied
  (401); the service runs with that origin. Owner's cellular verification pending.
- Second Claude account (third slot): enrol after the owner signs it in (`enroll-account`).
- Remaining low-severity review items (global pairing rate limiter, ignored-file overwrite on
  fast-forward, per-branch enrolment) are documented in `docs/build-evidence/safety-review/summary.md`.

---

# Build state — 28 October 2026

Canonical checkout: `/Users/leonardaarons-ditson/Projects/agent-workflow-orchestrator`
Branch: `integration/mobile-build-control`. Baseline: GitHub main `689da43`.
Local URL: http://127.0.0.1:8790 (loopback; application pairing required).
Private runtime: `~/.local/share/agent-arena/build-control`.

## Preservation
- Original checkouts retained without resets, stashes, cleans or branch changes.
- `9e1aec9` and unpushed `dc19aec` carried onto main as `d4d7fa6`, `5daa774`.
- AcquisitionOps and its evidence deliberately preserved in `01fb63a`; separate from build control.
- Backup manifest: `~/.local/share/agent-arena/reconciliation-20261028/manifest.json`.
- Workspace and README archives verified readable with SHA-256. Initial cloud-backed Documents/original archive reads timed out; bounded retry is running. Originals remain intact and must not be deleted.

## Delivered and under validation
- React/TypeScript/Vite UI, matching the phone reference, served by FastAPI.
- SQLite tasks/events/idempotent commands, account slots, scoped claims and independent worker guardians.
- Actual subscription Codex execution -> frozen OS-sandboxed checks -> tool-free independent Claude review -> serial private staging -> exact-commit local acceptance.
- Paired device sessions, CSRF/Origin/Host guards, SSE replay, disconnect state, source-preserving staging and bounded attempts.
- Current work: concurrency/recovery/failure tests and hardening; planner/dependency UX and operational documentation remain.

## Measured evidence
- Legacy command `.venv/bin/python -m pytest -q tests --ignore=tests/acquisition`: **197 passed in 187.24s**, one BrokenPipe cancellation-thread warning. Warning fixed; focused rerun pending.
- `npm run build --prefix web`: TypeScript + Vite build passed.
- `ARENA_LIVE=1 node web/live-smoke.mjs`: **passed**, submitted via 390px phone UI, closed/reopened page, 390px/1440px views, zero browser errors.
- Real run: task `task_0a5a9cd65e47cda8`, Codex attempt `attempt_80ef21fdd8bc40a9` (31.735s), Claude review `attempt_2fc86dd6e659e2ea` (9.083s). Approximately 42s to ready_for_approval; 2 CLI invocations, zero repairs, no human intervention after submission. One usage record per invocation; not a claim about hidden provider request counts.
- Candidate `fcc2db62379d2ecc67059b0c2c352a1c8cabe3b0`; staged composition checks passed. Nothing pushed or merged into the source.
- Evidence and actual screenshots: `docs/build-evidence/`. Initial control fault suite: 10 passed / 2 failed; fixed capacity-state reporting and null final-message parsing, rerun in progress.

## Concrete external limits
- Tailscale 1.104.2 reports NeedsLogin. No private HTTPS phone URL has been configured or tested. No public exposure.
- Codex 0.165.0 and Claude 2.1.310 exercised with saved subscription login. No API fallback.
- Claude uses its default keychain login; explicitly setting CLAUDE_CONFIG_DIR even to the default path lost auth. No settings/credentials were changed. Claude is restricted to tool-free independent review pending writer sandbox validation.
- Third account not enrolled; three-slot logic is tested with isolated fake executables, not represented as three real authenticated accounts.
- No global CLI configuration edits. No deployment, launchd installation or public push performed.

## Autobuild follow-up — 28 October, approximately 03:45 UTC

The misdirected Agent Exchange task was cancelled without changes to that repository.
Work remained on `integration/mobile-build-control`; existing uncommitted work was
preserved. Operational instructions are in [MOBILE_BUILD_CONTROL.md](docs/MOBILE_BUILD_CONTROL.md).

This session added combined approval diffs from the last accepted commit, covered-task
rejection with project pause, validated HTTPS origins, remote port/origin/keep-awake
options in generated service plists, authenticated scheduler health, planner dependency
validation, and explicit needs_permission reporting when host process identity is denied.
The frontend retains an individual-diff fallback for the still-running older backend.

Verification: 19 focused control/process tests passed (five full worker tests explicitly
excluded after their host-permission failures); the original full run was 7 passed /
5 failed due to sysctl process-identity denial. See `docs/build-evidence/autobuild-followup-*`.
Nested sandbox-exec is also denied. New browser launch failed, and browser-tool origin
approval was denied. Earlier live evidence remains intact and is not presented as a
fresh verification. The backend on 8790 was not restarted; no phone HTTPS URL exists yet.

A separate process began changing shared backend files during this follow-up, including
publication, recovery and structured Claude build modules. Those edits were preserved,
not removed or represented as this session's verified work. Implementation ownership
clarification is pending. Do not treat the shared working tree as frozen while that
editor is active. No other repository was modified by this session.

## Completion pass — current implementation, 28 October

Feature scope frozen. The three remaining proof-script lint issues are fixed.
The recovered overnight queues and original branch history remain unchanged.
GitHub main was rechecked at `689da43d74462d0bc2e548ac7b1a275efb791378`.
Before this pass, the working files were archived at
`.arena-backups/pre-finish-20261028.tar.gz` (SHA-256
`9958d1df90244d99d31d58f4c9f7862e63ee4540a781992f41d949dd6afb0448`).

The current permitted runtime is `.arena-control`, with both existing subscription
logins enrolled and a clean, isolated snapshot of the orchestrator enrolled as
`Orchestrator live proof`. The older runtime outside the repository was preserved.
No new provider invocation occurred: the preflight correctly stopped before one.

Completed implementation includes validated Claude file operations, bounded recovery
with process ownership fencing, instruction changes that invalidate in-flight results,
nonblocking composition checks, frozen candidate bytes across verification, exact
accepted-commit integration into local main, integration receipts, private Tailscale
configuration and a complete real-agent/browser acceptance entrypoint. Review-to-main
requires explicit approval; there is no automatic remote push.

Current evidence:
- `lint-current.txt`: full source/tests/proof-script lint passes.
- `frontend-build-current.txt`: TypeScript/Vite production build passes.
- `legacy-regression-current.txt`: **197 passed**, 155.42 seconds.
- `controller-regression-final.txt`: **23 passed**, five OS-dependent worker cases
  deselected after their concrete host denials. Separate completion tests include
  actual Git operations but synthetic verification records; they are not live-agent proof.
- `full-regression-current.txt`: full-suite result, including host-denied worker cases.
- `browser-acceptance-current.txt`: Chromium launch denied by macOS Mach IPC before
  a page opened. No new browser screenshots are claimed.
- `live-proof-blocked.json`: Codex and Claude subscription authentication both pass;
  process identity `sysctl` and nested `sandbox-exec` are denied. Tailscale cannot load
  preferences in this sandbox. No new real-agent, restart or phone proof is claimed.

Run `.venv/bin/python -m scripts.verify_on_host` from normal Mac Terminal. This is the
narrow supported host path, preserves security protections, uses only isolated source
copies for the live change, and includes an actual paired-phone/cellular acceptance
step. Full start/stop/recovery commands are in `docs/MOBILE_BUILD_CONTROL.md`.
**Definition of done remains unmet until that live/phone acceptance passes.**


## Final director requirement

The existing planning stage now serves as the engineering director; no new runner
or orchestration framework was introduced. The mobile form accepts only a project
and ordinary-language request, with phone keyboard dictation. The director receives
actual repository/history/decision context, validates a structured specification,
asks only consequential behavior questions, chooses task dependencies and returns
after independent review to judge the evidence. Repairs reuse the same bounded
scheduler. Every generated operation remains subject to the existing checks,
workspaces and exact-commit integration approval.

Requested director implementation: authenticated Codex `gpt-6-astra` at `max`,
confirmed in the local model catalog, not yet live-invoked. “Astra Pro” was not
invented as a CLI alias. Source/model evidence: `director-model.json`. Director
policy tests use explicitly synthetic responses and do not establish a live run.
The live acceptance driver now submits a nontechnical request and requires both
successful director stages before claiming success.

Automatic startup is implemented through the existing generated launchd plist,
plus a narrowly named user LaunchAgents symlink installed by the host acceptance
command after verification. It has not been installed from this restricted session.
Original `.git` metadata is read-only here; a clean review checkpoint is prepared
in a permitted isolated checkout without discarding the original working changes.


## Final verification and review checkpoint

- Complete regression suite: **252 passed, 5 failed**, 325.67 seconds. Every failure
  reports `needs_permission` / denied `sysctl` before provider execution. See
  `docs/build-evidence/full-regression-final.txt`.
- Latest focused controller/director suite: **28 passed**, five host-dependent cases
  deselected, 12.35 seconds. Includes the final context/decision and steering changes.
- Lint passes across source, tests and both acceptance scripts. TypeScript/Vite build
  passes. Browser launch and live proof remain blocked by the documented sandbox.
- Clean review branch: `review/mobile-director-20261028` in
  `.arena-control/review-checkout`. The original branch and dirty working files are
  preserved. Bundle and source hashes are in `.arena-backups`.
- No private phone URL, automatically running service, live director request or
  completed end-to-end build is claimed. The single host command above is the next
  required execution boundary; it does not disable security protections.


## Adaptive routing follow-up

Added capability-aware routing inside the existing claim transaction. Director
model requirements remain fixed. Worker model/effort choices use task complexity,
risk, advertised capabilities and comparable outcomes; unsupported Claude effort
flags are omitted. Existing cooldowns and finite retries remain authoritative.
Failure evidence drives bounded director diagnosis, effort escalation and provider
switching. Every route is persisted with the attempt and event record.

Latest routing/director/controller suite: **33 passed**, five known host-dependent
worker cases deselected. The previous complete suite remains **252 passed / 5
host-permission failures**; the new routing tests are recorded separately in
`routing-controller-tests.txt`. Lint and the frontend build pass. No later routing
change is represented as a completed real-agent or phone test. The clean isolated
review branch contains a follow-up routing commit; original working files and the
first review checkpoint remain preserved.
