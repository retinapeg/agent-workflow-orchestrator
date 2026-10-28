# Adversarial review of the device-session change — 28 Oct 2026

3 reviewer lenses, 2 refuters per finding (29 agents). Confirmed items were fixed in the follow-up commit: absolute 180-day cap on renewal, renewal via MAX(expires) with last_seen-only writes otherwise, bookkeeping off the event loop and never failing a request, idempotent revoke, 403/404 shown as terminal reasons in the bootstrap, docs corrected, tests for no-churn/legacy rows/cap.


## auth-security

- **[medium] CONFIRMED** `api.py:129` — Sliding renewal has no absolute lifetime and is driven by automatic polling, so sessions never expire while any tab is open
  - Fix: Cap total lifetime using the created column that the diff already records, e.g. renew only if now - (auth.get('created') or now) < MAX_SESSION_SECONDS (90 or 180 days), so a device must re-pair at most once per cap; optionally only count mutations or the bootstrap /api/v1/session call as 'use' rather than the 5 s poll.
- **[low] CONFIRMED** `api.py:134` — touch() writes back a stale expires value when not renewing, which can undo a concurrent renewal
  - Fix: Only touch last_seen when not renewing: UPDATE sessions SET last_seen=? WHERE hash=?; and for the renew branch use expires=MAX(expires, ?) so a slow request can never shorten a session.
- **[high] CONFIRMED** `App.tsx:101` — Confirmed root cause of repeated Android pairing: fixed 7-day expiry with no renewal plus bootstrap treating any network failure as 'not paired' (both fixed by this diff)
  - Fix: No further backend change needed for the root cause. Recommend adding a UI test or manual check for the offline-shell path (SW-cached '/' with the API unreachable) to confirm the 'Connecting... retrying' state persists and the pairing form is never shown without a 401, and keep /api/v1/session in the SW's uncached (/api/) set as it is now.

## android-repair

- **[high] CONFIRMED** `App.tsx:100` — Confirmed root cause (fixed by this change): old bootstrap turned every outage into a pairing prompt
  - Fix: No further code change needed for this cause; the 401-only rule in App.tsx:101-103 is the fix. Keep the retry path and keep `get()` (App.tsx:73-76) flipping to the pairing screen only on a genuine 401.
- **[low] CONFIRMED** `MOBILE_BUILD_CONTROL.md:58` — Confirmed secondary cause (fixed): 7-day absolute expiry with no renewal; docs still say seven days
  - Fix: Update lines 58 and 164 of docs/MOBILE_BUILD_CONTROL.md to '30-day sliding session, renewed on use'.
- **[medium] rejected** `engine.py:268` — Remaining trigger not addressed: controller crash-loops on scheduler-lock conflict, leaving the phone on 'Connecting… retrying'
  - Fix: In `serve`/`Engine.start`, retry the flock for a bounded window (e.g. up to 30 s) before raising and print the owning PID from server.json; make enable_phone_access.sh/install-service wait for the old PID to exit, not just for the port. Out of scope for the session change but it is the remaining cause of the symptom.
- **[low] rejected** `App.tsx:115` — Permanent non-401 errors are retried forever with no way out
  - Fix: Treat 403 (and 404) as terminal in the bootstrap: show the server `detail` text and the expected origin from connection.json instead of retrying; keep unbounded retry only for network errors and 5xx.
- **[low] rejected** `api.py:132` — Session touch performs a blocking SQLite write inside the async middleware
  - Fix: Wrap the write in `await starlette.concurrency.run_in_threadpool(touch, auth, request, response)`, or move the last_seen/renewal update into the route handlers' existing threadpool path and set the cookie from the middleware.

## correctness-regression

- **[low] rejected** `App.tsx:100` — Confirmed root cause of the Android re-pairing (now fixed by the change)
  - Fix: No code change needed; this entry records the diagnosis the user asked for. The retry-on-non-401 bootstrap plus 30-day sliding sessions address both causes.
- **[low] CONFIRMED** `App.tsx:105` — Bootstrap retries forever on permanent non-401 errors and hides the server's detail
  - Fix: Treat 403 (and 404) as terminal: read `j.detail` from the JSON body and render it on the Connecting card without rescheduling; keep the 3 s retry only for network errors and 5xx/502/503, optionally with backoff when `document.visibilityState === 'hidden'`.
- **[low] rejected** `api.py:242` — Device revoke declares a revision-bearing body but bypasses revision and idempotency contracts; retry shows a false error
  - Fix: Either make the endpoint idempotent (return {"ok": True} when no row matches, or route it through `command()` so the Idempotency-Key replays the stored result), and drop `Change` in favour of an empty/no body so the UI does not have to invent `revision: 0`.
- **[low] CONFIRMED** `api.py:132` — touch() performs a blocking SQLite write transaction on the event-loop thread inside the async middleware
  - Fix: Run the update off the loop (`await run_in_threadpool(touch, ...)`) and wrap it in try/except so a lock timeout only skips the last_seen/renewal bookkeeping instead of failing the request; the cookie re-set can still be applied when the update succeeds.
- **[low] CONFIRMED** `test_control.py:658` — Test proves renewal but not the no-churn, legacy-row, or cross-restart cookie paths
  - Fix: Add assertions: (1) after pairing, `client.get('/api/v1/session')` has no `set-cookie`; (2) insert a 3-column-style row (NULL id/label/last_seen) and verify it is listed with the 12-char hash id and can be revoked by it; (3) create a second `make_app(store.home)` and send the first client's cookie to it to prove restart survival; (4) check `last_seen` advanced.
