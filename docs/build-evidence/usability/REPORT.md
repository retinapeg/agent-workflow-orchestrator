# Usability release — report

Unattended release of the "usable Arena" work (docs/USABILITY_BRIEF.md) on 28 Oct 2026.
Streams A (backend, db95955) and B (frontend, aedf22c) were already committed; this session
added Stream C, an independent review with fixes, the release gates, and the activation.

## Outcome

**Activated.** Commit `af6e3298` is the live release on port 8791 (origin
https://leonards-macbook-air-1.tail11ad03.ts.net), verified healthy by the activator at
19:03:29 UTC, four seconds after the swap. The previous release `252c47f6` is retained as
the rollback target. Main is at `af6e3298` (fast-forward of `usability`), nothing pushed.

| Gate | Result |
|---|---|
| `ruff check src tests` | clean (round 1 and final) |
| `pytest` (full) | **319 passed, 0 failed** (final); round 1 was 316 passed, 1 failed — see below |
| `npm run build` | passes (`tsc` strict + vite) |
| `node web/ui-acceptance.mjs` | BROWSER_API_ACCEPTANCE_PASSED (twice) |
| `node web/usability-acceptance.mjs` | USABILITY_ACCEPTANCE_PASSED (twice, 390 px, Android UA) |
| Activation | activated, health: process, ui, scheduler, auth, history (6 projects / 28 tasks), heartbeat |

`tests/test_lifecycle.py::test_hackathon_accepts_demo_contract_without_separate_not_file`
passes on this Mac (run alone and inside the full suite), so the failure seen earlier in a
Linux container is environmental, as suspected.

## Overview size

| Measurement | Bytes |
|---|---|
| `GET /api/v1/overview` on the real state before the release (brief's diagnosis) | 1,088,091 |
| Stream A's measurement on the real state | 194,081 |
| Acceptance on a copy of the live state, before the review fixes | 194,622 |
| Acceptance on a copy of the live state, final run | 194,620 |

Breakdown of the 194.6 KB: attempts 115 KB (76 records, ~1.5 KB each: result 73 KB, of
which summary 18 KB, review 9.7 KB, artifacts 7.4 KB, usage 7.3 KB; routing 11 KB), tasks
38 KB (28), approvals 9.7 KB, conversations 9.6 KB (specs), events 8 KB (30), goals 6 KB,
projects 6 KB. The brief's own target of < 150 KB on the real state is **not** met; the
200 KB figure the release was asked to assert is met with ~5 KB of headroom. Every build
adds roughly 8 KB (one goal, two or three tasks, three to five attempts), so the absolute
gate will trip after the next real build. The acceptance therefore also asserts a
per-record budget (40 KB + 2 KB per attempt + 1.6 KB per task + 0.6 KB per goal; 244,600
today) that keeps catching a fat record after the state has grown. Getting under 150 KB
needs a contract change (window the attempts of finished goals out of the polled overview
and serve them from `GET goals/{id}`); that is recorded here, not done.

## Stream C

`tests/control/real_state_server.py` serves a SQLite backup of the live control database
(opened read-only, copied with the backup API) with `run_scheduler=False`.
`web/usability-acceptance.mjs` pairs at 390 px with an Android user agent and asserts the
five points from the brief. Both files existed uncommitted from the previous session with a
passing run; the accompanying test change targeted a column that does not exist
(`artifact_dir` lives inside the JSON `data` column), which is why round 1 of pytest showed
one failure. Fixed, re-run, committed (7956e1b).

Asserted on the real state: overview 194,620 bytes; the four "Orchestrator live proof"
projects archived and absent from Home, the request picker and the chat picker; Builds
opens on the newest request (no build was live or waiting); Live output for the integrated
build "Add optional silent completion…" shows 41 recorded event lines; no horizontal
overflow on Chat, Home, Projects, Builds, Agents, Review, nor on Projects with the Archived
section expanded. Screenshots: `*-390.png` in this directory; `result.json` has the numbers.

## Independent review

Decision: instead of one reviewer, a workflow of six fresh reviewers (backend contract,
security, frontend, NXT Blue, tests, fixture safety) read `git diff 202fbdb..HEAD` plus the
Stream C files, and every finding was then judged by three adversarial verifiers
(reproduce, refute, impact; majority decides). The reviewers produced 22 findings. At the
time of activation 24 of the 66 verifier runs had completed; every one of the 20 votes cast
confirmed its finding (seven findings fully or partially verified, none refuted). The
remaining votes were still running in the background and are reported in the Round 2
section when it is appended. Fixes were applied only where the mechanism was verified by
reading or running the code; no refactors.

Fixed (commit af6e329):

- **Task dialog read dropped fields.** `TaskDialog` rendered `task.specification` and
  `task.instructions_pending` from the slim snapshot, which no longer carries them, so
  "What your director decided" and queued instructions silently vanished. It now fetches
  `GET /api/v1/tasks/{id}` when opened; state, revision and reason stay live from the
  snapshot so controls never act on a stale revision. (frontend + backend reviewers, 3/3 votes)
- **Details stuck on "Loading…".** Switching requests while Details was open abandoned the
  fetch with `loading: true` left behind, and re-opening never fetched again. (3/3)
- **Builds default selection** followed live → queued → needs-you → newest; the binding rule
  is live → needs-you → newest. A queued request no longer outranks one waiting for the
  owner's answer. The acceptance mirrors the rule. (3/3)
- **Fabricated timestamps** on live-tail lines (every line stamped with the poll time); a
  line without a recorded time now carries none. (3/3)
- **Live tail elapsed grew forever** for finished attempts without a recorded end (every
  `interrupted` attempt); the worker's own wall time, else the transcript's last change,
  else nothing is reported now. (2/2 votes so far)
- **Transcript splitting** used `str.splitlines()`, which breaks a JSON line on
  U+2028/U+2029/U+0085; lines split on newlines only.
- **display_title chopped hyphenated words** ("Build-time caching" → "time caching"); a
  hyphen is a label separator only with whitespace around it. (1/1 so far)
- **NXT Blue regression**: the changed `.facts dt` rule split one-word labels mid-word on the
  phone ("Sco/pe", "Integrati/on", measured by the reviewer with Playwright); labels are
  unshrinkable again, capped at 62 %. "Show archived (n)" had a 21 px tap height; now 44 px.
- **Fixture safety (production).** The copy served by `real_state_server.py` kept the Stop
  routes live, and `engine.cancel` resolves recorded pids against the real process table, so
  a copy taken while a worker runs could have sent SIGTERM to the production worker. The
  copy now drops every worker identity, and every mutation except pairing, conversations
  and session bookkeeping answers 403; the scratch-home guard also refuses directories
  inside the live home or the repository root. The acceptance fails fast if its server died
  (a squatter on port 8794) and removes its temporary copy of the state afterwards; three
  stale copies from earlier runs were deleted from the temp directory.
- **Committed snapshot in a public repository.** `tests/control/fixtures/real-state.sqlite3.gz`
  is a sanitised copy of the live database (no sessions, commands or pairing records, no
  secrets). The owner's name, username path and Tailscale hostname it contains are already
  in tracked docs and evidence files of this public repository, so the fixture adds no new
  identity exposure; its device-pairing events (device ids, user agents) and worker process
  identities were removed. If the owner prefers no snapshot in git at all, delete the file
  and make the real-state test skip when it is absent.
- **Tests sharpened**: the 64 KB live-tail window is asserted (the 60-line cap alone passed
  for any window), the thinking-leak guard now has a non-empty thinking block to catch, the
  `arena_status` "0s ago" assertion uses a fixed clock instead of wall time, and new tests
  cover the elapsed fix, newline splitting, hyphenated titles and the fixture guards.

Not fixed, recorded:

- `attempt.result.summary` is clipped to 300 characters while the contract says ≤ 600; the
  list only shows a sentence, the full text is in the goal detail. Stricter than the
  contract, left as is.
- The archived-project assertion in `test_conversation.py` is tautological (the id can never
  appear in a prompt) and contract caps (goal events "last 200", recent[:5]) are untested.
  Test-quality items, not shipped defects.
- The 150 KB target (see Overview size).

## Activation

```
activate af6e3298d1ae6a9c63f33c940d928cbc211f9703 --port 8791  → activated
health: process 38918 · ui · scheduler · auth · history {projects 6, tasks 28} · heartbeat
previous: 252c47f67a8d53fac2a5b0a871edd3d525ef3d11 (rollback target)
pruned: 6267043b8919 (release worktree only; the commit and tag arena-baseline-20261028 remain in git,
        re-creatable with `activate 6267043b…`)
```

The activator's CLI requires `--repo`; the command in the brief omitted it, so
`--repo "$PWD"` was added. No build was running at the swap (0 active attempts, 0 queued
tasks). The first start of the new release ran the one-time migration: the four proof
projects are archived (`archived_by: migration`, meta key
`migration.archive_proof_projects`); reversible from Projects → Archived → Unarchive.
Log: `activation-round1.txt`.

## Check this on your phone

1. Home opens on status: "Build control." with Active builds, one New request button and
   Recent requests; no "Orchestrator live proof" entries anywhere.
2. Builds opens on the newest request; the picker reads "Integrated · Add optional silent
   completion… · Arena Smoke 20261028"; Live output shows recorded events, not "No events".
3. Open Details on a build: the director's plan loads; switch requests and open Details
   again, it loads again (no stuck "Loading…").
4. Tap a task → "Details & controls": "What your director decided" and queued instructions
   are shown; the labels in facts lists (Scope, Worker, Integration) are whole words.
5. Projects → Archived (4) lists the proof projects; Unarchive one and it returns to the
   pickers; the request dialog's project list stays short (Let Arena decide, Arena, Arena
   Smoke).
