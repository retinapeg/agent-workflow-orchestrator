# Operate the Autobuild alpha (current — 28 October 2026)

Everything below this section is the historical build log; this section is what you use.

## URL and pairing

- Private URL (phone and Mac, Tailscale connected): **https://leonards-macbook-air-1.tail11ad03.ts.net**
  (Tailscale Serve, tailnet-only, Funnel off; enabled 28 Oct 2026). After this switch the
  loopback address answers 403 "Host not allowed" by design; use the HTTPS URL on the Mac too.
- New one-time pairing code (10 minutes, one device per code):

  ```sh
  cd ~/Projects/agent-workflow-orchestrator && .venv/bin/python -m agent_arena.control --home .arena-control pair
  ```

The controller is a launchd login service (`local.agent-arena.control.1a6834e84b5a`, plist
`.arena-control/control.plist` symlinked from `~/Library/LaunchAgents`). It starts at login,
restarts itself if it crashes, keeps the Mac from idle-sleeping (`caffeinate -i`), and does not
depend on any Terminal window or browser tab. Closing the lid still sleeps a MacBook Air: leave
the lid open (or use clamshell mode with power and an external display) while builds run.

## Service commands (run from the repository)

```sh
# Is it running?
launchctl print gui/$(id -u)/local.agent-arena.control.1a6834e84b5a | grep -E "state|pid"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8791/        # 200 = UI served

# Restart on new code (workers keep running; the scheduler reconciles them on return)
launchctl kickstart -k gui/$(id -u)/local.agent-arena.control.1a6834e84b5a

# Stop the service (state, evidence and worker ownership are preserved)
.venv/bin/python -m agent_arena.control --home .arena-control uninstall-service
# Start it again / recover after a stop
.venv/bin/python -m agent_arena.control --home .arena-control install-service --port 8791 --origin http://127.0.0.1:8791

# Health of the host, accounts and private transport (no provider call)
.venv/bin/python -m agent_arena.control --home .arena-control doctor
# Re-check subscription logins; --probe validates Fable 5.1 (max) on Claude slots
.venv/bin/python -m agent_arena.control --home .arena-control check-accounts --probe
# Logs
tail -f .arena-control/service.log
```

Never run the manual `start` command while the launchd job is loaded for the same state
directory; the second scheduler refuses the lock and exits.

## Using it

1. Open the URL, enter a pairing code once; the device stays paired for seven days.
2. **New request** → choose a project → type or dictate what you want → **Build this for me**.
3. Watch **Build**: the director (Codex `gpt-6-astra` at max effort, or a validated Claude
   Fable 5.1 slot at max when Codex is busy) inspects the repository and writes the
   specification; tasks run on Codex and Claude workers; frozen checks, independent
   cross-provider review and the director's evidence review follow; verified work is staged.
4. Integration into the project's local `main`:
   - **Manual** policy (default): **Review** → Inspect evidence & diff → Accept → Integrate into main.
   - **Automatic** policy (Projects → "Integrate verified work automatically"): every gate
     above must pass, then the exact staged commit integrates on its own. Nothing is ever
     pushed to a remote; that remains a separate human action.
5. Controls: Pause/Resume queue, project priority, Stop task, Stop all workers, Retry,
   queue an instruction for a running build, answer a director question, Unpair device.

## Enrolling one of your own repositories

Projects are enrolled on the Mac, never from the phone. The source must be a clean, committed
checkout with `main` checked out. Write a manifest and review its checks (they run inside the
macOS sandbox against every candidate):

```json
{
  "name": "My app",
  "repo": "/Users/you/Projects/my-app",
  "scope": ["src", "web"],
  "checks": [{"name": "Tests", "argv": ["{python}", "-m", "pytest", "-q"], "timeout": 600}],
  "protected": ["tests/acceptance"],
  "trusted": true,
  "integration_policy": "auto_local_main"
}
```

```sh
.venv/bin/python -m agent_arena.control --home .arena-control enroll-project --manifest /path/to/manifest.json
```

`scope` is the only place workers may write; `protected` paths are frozen (checks live there).
Repositories carrying `AGENTS.md`, `CLAUDE.md`, `.codex/` or `.claude/` are refused until
audited isolation exists for them.

## Private phone access (configured 28 Oct 2026; re-run after a Tailscale change)

```sh
tailscale login                                    # one-time, opens a browser sign-in
# Enable Serve/HTTPS certificates for the tailnet when `tailscale serve` prints the admin link;
# leave Funnel unticked. Then one command does the rest and prints the URL and pairing code:
scripts/enable_phone_access.sh
```

Serve (not Funnel) publishes the HTTPS origin inside your tailnet only; application pairing
is still required on top of it. Install Tailscale on the phone with the same account.

---

# Autobuild: phone-to-Mac build control

The existing implementation is on `integration/mobile-build-control`. The web UI is
served at **http://127.0.0.1:8790**. That loopback address works on the Mac, not on a
phone. Remote operation requires a private HTTPS connection and device pairing.

## Start and inspect on the Mac

From the canonical `agent-workflow-orchestrator` checkout:

```sh
.venv/bin/python -m pip install -e '.[control,dev]'
npm ci --prefix web
npm run build --prefix web
.venv/bin/python -m agent_arena.control status
.venv/bin/python -m agent_arena.control start --port 8790
.venv/bin/python -m agent_arena.control pair
```

Do not start a second instance over an existing one. `status` summarizes persisted
records; it is not proof of liveness. The authenticated `/api/v1/health` endpoint
reports scheduler heartbeat age on the updated backend. A 503 means no recent tick;
long synchronous staging checks can also delay that heartbeat. `/` being HTTP 200
only proves the UI is served. Older backends do not have the health endpoint.

Runtime state defaults to `~/.local/share/agent-arena/build-control`: SQLite,
private staging repositories, worker workspaces, transcripts, checks and receipts.
Use `--home /absolute/path` **before** the subcommand to select a different runtime.
Never point a test at the real state directory. Never delete it to resolve an error.

A manually started service continues after the browser closes, but it is not yet a
login service. The Mac must remain powered, online and awake. Closing the laptop
lid, logout and reboot have different effects from closing a browser tab.

## Private phone connection

1. Sign into Tailscale on the Mac and phone, using the intended private tailnet.
   Confirm `tailscale status` succeeds. Restrict tailnet access to the intended
   devices/users using its existing access controls.
2. Run `tailscale serve --bg http://127.0.0.1:8790`. Complete Tailscale's HTTPS
   setup if prompted. Record the exact `https://...ts.net` URL printed by Serve.
   Use Serve, not public Funnel. [Official Tailscale Serve reference](https://tailscale.com/docs/reference/tailscale-cli/serve).
3. Restart the application with that exact origin, preserving the same runtime:

   ```sh
   .venv/bin/python -m agent_arena.control stop
   # Wait until lsof -nP -iTCP:8790 -sTCP:LISTEN shows no listener.
   .venv/bin/python -m agent_arena.control start --port 8790 --origin https://YOUR-MAC.YOUR-TAILNET.ts.net
   .venv/bin/python -m agent_arena.control pair
   ```

4. Open the HTTPS URL on the phone with Tailscale connected. Enter the one-use
   pairing code within ten minutes. Each device gets its own seven-day HttpOnly,
   SameSite=Strict session. HTTPS configuration sets Secure cookies; use the
   HTTPS URL for the UI after switching, including on the Mac.
5. Submit a disposable scoped goal, close the browser, reopen it, inspect the
   persisted task/attempts/checks, read the combined diff and accept the exact
   staged commit. Repeat with the phone on cellular to verify away-from-home use.

No phone URL has been verified in this engineering session. The earlier host
record said NeedsLogin; the current restricted shell cannot load Tailscale
preferences. No tunnel, public listener, tailnet permission or login was changed.

## Run after login

Generate a plist with the actual remote origin; the generator refuses to overwrite
an existing file. `--keep-awake` wraps the service in `caffeinate -i` to prevent idle
system sleep while it runs. It does not promise execution with the lid closed.

```sh
.venv/bin/python -m agent_arena.control service-plist /tmp/autobuild-control.plist --port 8790 --origin https://YOUR-MAC.YOUR-TAILNET.ts.net --keep-awake
```

Inspect the generated file first. On the host, stop the manual service and wait for
the port to be released, then place the plist in
`~/Library/LaunchAgents/local.agent-arena.control.plist` and load it with
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.agent-arena.control.plist`.
Do not replace an existing service configuration blindly. The generator does not
install it. Do not run the manual service and launchd against the same state.
Logs are `service.log` for launchd and `server.log` for manual startup.

## Task execution and approval

Enroll an already authenticated subscription account on the host:

```sh
.venv/bin/python -m agent_arena.control enroll-account 'Codex local' --provider codex
.venv/bin/python -m agent_arena.control enroll-account 'Claude review' --provider claude
.venv/bin/python -m agent_arena.control check-accounts
```

Existing accounts need not be enrolled again. No API key fallback is used. Codex
builds in isolated workspaces. The verified running configuration uses Claude for tool-free independent review;
**Claude code writing has not been verified**. Concurrent source edits are adding a
structured build path and source integration; those are outside this verification snapshot. The third slot needs
its own verified supported login; a second Claude slot is deliberately unavailable.
Quota and reset times are unknown rather than estimated.

Project enrollment requires a reviewed manifest and a clean committed source:

```json
{
  "name": "Example project",
  "repo": "/absolute/path/to/clean/source",
  "scope": ["src"],
  "checks": [{"name": "Acceptance", "argv": ["{python}", "acceptance/check.py"], "timeout": 120}],
  "protected": ["acceptance"],
  "trusted": true
}
```

Run `enroll-project --manifest /path/to/manifest.json` after reviewing its executable
checks. The running controller cannot enroll itself. Uncommitted work is not
silently included, stashed or reset. Agent startup configuration in a target repo
currently blocks execution pending audited isolation. Project enrollment is a
host operation; phone users can submit only within enrolled scopes.

On the phone, choose New goal, select the project, specify completion criteria and
owned paths. Optional planning makes one bounded call and validates up to eight
child tasks and an acyclic dependency graph. Each child retains the original scope
limits, frozen checks and review policy. Depend on build tasks, not the planner's
completed planning record. Dependencies must belong to the selected project.

The normal path is build → frozen checks → independent review → combined staging
checks → approval. Account leases, scope checks and serialized staging limit races.
Closing the page does not stop a worker. SSE events replay, and periodic snapshots
restore the view. Stop task cancels owned execution; Pause queue prevents new
claims while existing workers continue. Retry is bounded; no infinite repair loop.

Acceptance is **local staging acceptance**, not a push, deployment or merge into
the source working copy. Inspect the combined diff from the last accepted commit
to the candidate. Superseded pending approvals are covered by the latest combined
approval. Stale revision, candidate or base decisions are rejected. Truncated diffs
must be inspected on the Mac before acceptance.

Reject/request changes preserve the staging history and pause that project. Queue
a scoped corrective goal and explicitly resume after deciding how to proceed.
Rejected staged code is not automatically reverted: subsequent combined review
still includes all changes since the last accepted commit. Dependent work remains
blocked until its predecessors have suitable verified results.

## Recovery and evidence

- An ambiguous process identity fails closed. Permission denial now records
  `needs_permission` before any provider invocation.
- Revoke all device sessions with `revoke-sessions`; use the device dialog to
  unpair only that device. A new `pair` code does not revoke existing devices.
- `backup /absolute/new-file.sqlite3` uses SQLite's online backup; preserve the
  workspaces/artifacts as well if making a complete runtime backup.
- Keep failed/interrupted workspaces and inspect transcripts before a retry.
- Subscription login failures require the provider's normal host login, followed
  by `check-accounts`. Do not paste credentials into the phone UI.

The original live evidence is in `docs/build-evidence/live-smoke.json`. Updated
verification and current host restrictions are recorded in `BUILD_STATE.md` and
`docs/build-evidence/autobuild-followup-*.txt`. An earlier successful live run is
not proof that the latest backend has been restarted or that remote phone access
works. The existing live smoke script uses the default runtime and submits a real
subscription task; do not rerun it against unrelated projects or an already solved
fixture without preparing a fresh disposable runtime first.

## Current completion and acceptance entrypoint

The implementation now includes tool-free Claude builds (validated whole-file JSON
operations using the existing harness validator), bounded crash retries, retained
leases for ambiguous processes, composition checks outside the dispatch loop, and
explicit integration of an accepted commit into an enrolled project's **local
main**. The current implementation still needs unrestricted-host live acceptance;
the earlier screenshots and greeting run do not validate these later changes.

Use this single command in **normal macOS Terminal as the signed-in user**:

```sh
cd /Users/leonardaarons-ditson/Projects/agent-workflow-orchestrator && .venv/bin/python -m scripts.verify_on_host
```

It operates on `.arena-control` and an isolated copy of this repository. It runs
lint, the complete test suite, the frontend build, browser/API acceptance and the
real Codex/Claude proof. The real proof submits through a 390px browser, closes and
reopens it, requires concurrent provider invocations, restarts the controller while
workers run, requires independent review and frozen checks, approves the exact
candidate, and verifies that the isolated source's `main` has that commit. It
fails if either worker is replaced or invoked twice during that restart test.

After the local proof, the command uses the supported Tailscale login flow if
needed, refuses to replace another service or use an endpoint with Funnel enabled,
configures private HTTPS, and loads a user launchd job from a plist **inside the
repository**, installing one state-specific symlink in your user LaunchAgents folder. It displays a fresh one-time pairing code. Complete the prompted
phone step with Wi-Fi off: pair, submit the indicated manifest change, inspect the
review, accept, then choose **Integrate into main**. The script waits for the new
pairing and tested main integration and asks for confirmation that the phone used
cellular. It records the result in `docs/build-evidence/host-acceptance/result.json`.
It then creates a review commit on the existing integration branch, refusing to
include pre-existing staged work. It never pushes to GitHub or changes unrelated
repositories. The narrow source scopes and frozen checks apply to the disposable
proof project, not every future project you enroll.

Required host capabilities are process identity reads (`sysctl` via psutil), normal
`sandbox-exec` invocation for verification, Chromium Mach IPC, and the existing
subscription keychain and Tailscale preferences. **No sudo, Full Disk Access grant,
API key, disabled SIP, disabled agent sandbox or global permission change is
required by this command.** This agent session denies the first three capabilities
and Tailscale preferences; `doctor` reports that before invoking providers.

The launchd job keeps the Mac awake with `caffeinate -i`, restarts a crashed
controller, and starts again after you log in. Installation creates one symlink in
`~/Library/LaunchAgents` pointing at the repository's state-specific plist. It
refuses to replace an unrelated file. No system-wide service or security setting
is changed. Keep the Mac powered and its lid open; subscription keychain access
requires your user session. The host command installs this only after live tests pass.

All commands below run from this repository. Do not mix manual startup with a
loaded launchd job for the same state directory.

```sh
# Manual start (before launchd installation); port 8791 avoids the older 8790 runtime.
.venv/bin/python -m agent_arena.control --home .arena-control start --port 8791
# Stop that manual controller. Its bounded worker guardians retain ownership.
.venv/bin/python -m agent_arena.control --home .arena-control stop

# Stop a launchd-managed controller; preserve state, plist and worker evidence.
.venv/bin/python -m agent_arena.control --home .arena-control uninstall-service
# Reinstall/recover the stopped service with the saved private connection settings.
.venv/bin/python -m agent_arena.control --home .arena-control install-service

# Health/preflight, fresh pairing, revoke all paired devices, and online backup.
.venv/bin/python -m agent_arena.control --home .arena-control doctor
.venv/bin/python -m agent_arena.control --home .arena-control pair
.venv/bin/python -m agent_arena.control --home .arena-control revoke-sessions
.venv/bin/python -m agent_arena.control --home .arena-control backup .arena-backups/control.sqlite3
```

On reconnect the scheduler reconciles persisted attempts before dispatch. A dead
worker receives at most two recovery attempts after its recorded child is stopped;
an ambiguous PID retains its slot and cannot publish a result. Auth/permission
failures are not retried blindly. An interrupted main integration is reconciled
from its exact-commit intent and receipt. Dirty or advanced source main is preserved
and reported as a failed integration requiring a new reviewed base.

## Engineering director (final request path)

The phone form now asks only for the project and the desired change. Native phone
keyboard dictation works in that field. It always calls `/projects/{id}/requests`,
which schedules the existing planning stage as the director. It cannot dispatch a
vague request directly to a worker. The advanced goals API is retained for existing
queues and compatibility, but is not the phone request path.

The director uses the authenticated Codex CLI with **`gpt-6-astra`, reasoning `max`**.
That exact model and effort are advertised by the local account's model cache; see
`docs/build-evidence/director-model.json`. No separate “Astra Pro” CLI alias or paid
API route is assumed, and a live invocation has not yet been verified in this
restricted session. [Official Astra model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra).

The director reads the actual candidate repository, recent commits, source working
state, current tasks, build evidence and recorded approval feedback. It emits a
validated specification containing outcome, source observations, acceptance
criteria, safety constraints, scopes and dependencies. It chooses a single,
parallel or sequential plan. A consequential behaviour question dispatches nothing
until answered in the phone interface. Generated scope cannot exceed enrollment,
and neither specifications nor repair plans can alter frozen checks.

Workers receive concrete instructions and acceptance criteria. Independent review
remains mandatory. The director then reviews the evidence against the product
outcome; failures return for a bounded corrective specification through the same
scheduler. Composition verification and explicit exact-commit main approval remain
deterministic. The director's specification and progress summary are visible in
the UI, and its actual requested model is recorded for every invocation.

The live proof now begins with ordinary language: “Make this app easier to recognise
on my phone, and stop malformed server addresses getting through.” It requires a
successful Astra specification and Astra evidence review, in addition to both real
worker providers, frozen checks, concurrent execution, controller restart and main
integration. The actual-phone step asks for a home-screen name change in ordinary
language. These are pending acceptance tests, not claimed completed demonstrations.


## Bounded worker routing

Routing is a policy inside `Engine.claim`, not a second scheduler. The director
labels task complexity/risk; deterministic risk floors and the enrolled scope
prevent an obvious security change being treated as a trivial text edit. Codex
choices come from its recent advertised CLI model catalog and each model's listed
effort levels. Claude retains its authenticated enrolled model and provider-default
effort unless validated per-model capability metadata is present. Generic CLI help
is not treated as proof that every model accepts every effort. No Fast mode or paid
API fallback is enabled.

Initial speed preferences are heuristics, not benchmark claims. At least three
successful comparable observations are required before measured durations influence
a route. Poor success history, failed model availability, cooldown, author/reviewer
separation and account leases also constrain selection. Repairs receive failure
evidence and a fresh director specification; reasoning effort can increase only to
supported levels, and repeated failures favour another available provider. Existing
repair/retry/recovery budgets still bound the total work. The director remains the
explicitly requested Astra route; it is not silently replaced with a cheaper model.

Every attempt records model, effort (or provider default), task assessment, decision,
failure predecessor, escalation count and observed outcomes. `routing.selected`
events preserve those decisions across restarts. The phone displays the useful
reason, while complete evidence remains available in task details. Routing tests
are offline policy tests; real remote acceptance remains the release gate.
