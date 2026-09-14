# Provider contracts and limits

All providers receive the coordinator's specification, acceptance contract, and frozen baseline.
Transport capabilities differ; the doctor output records those differences.

For CLI providers, doctor resolves the configured executable using the sanitized provider `PATH`,
then runs a bounded local authentication probe in that same environment. Codex uses
`codex login status`; Claude uses `claude auth status` and must return `loggedIn: true`. Probe output
is discarded rather than recorded because Claude's JSON can contain account metadata. These probes
do not call a model and do not prove that the selected model is enabled for the account.

| Provider | Time control | Output-token control | Dollar control |
| --- | --- | --- | --- |
| Codex CLI | Parent process deadline, capped again by selected mode | Observed usage only; `max_output_tokens` is not a hard CLI cap | No guaranteed per-call cap |
| Claude CLI | Parent process deadline, capped again by selected mode | Observed usage only; `max_output_tokens` is not a hard CLI cap | Configured `--max-budget-usd`, as enforced by Claude Code |
| OpenAI API | SDK network-operation timeout, with explicit opt-in | `max_output_tokens` per request | `max_cost_usd` is rejected |
| Anthropic API | SDK network-operation timeout, with explicit opt-in | `max_tokens` per request | `max_cost_usd` is rejected |

The API SDK timeout is **not a total wall-clock deadline**. Network progress can extend a call beyond
the configured duration and overall run deadline. API preflight and invocation fail unless the
engineer's options explicitly accept this limitation:

```toml
[engineers.codex.options]
api_key_env = "OPENAI_API_KEY"
allow_sdk_timeout_only = true
```

The same option is required for an Anthropic API engineer. Doctor labels this with `OPTED IN: SDK
network-operation timeout only; no parent wall-clock deadline`. Setting any `max_cost_usd`, including
zero, is rejected for a direct API provider even with this opt-in. API retries are disabled. Choose a
CLI provider when the parent-enforced deadline is required. Limits apply per invocation, not as a
cumulative dollar allowance for the competition. Remote billing can finish after local cancellation.

For adaptive runs, the shipped mode cap is the complete step deadline: five minutes for Hackathon
and ten for Engineering, including planning, writing, and configured verification. The older
one-task executor applies the same value to each provider phase. `team status` shows the active
provider, phase, phase deadline, total-run deadline, and
seconds since the last state change. These fields prove what the local coordinator is waiting for;
they do not prove progress inside a remote provider. A remote integration must separately capture
its session/task ID and URL and distinguish stopping the local waiter from terminating the remote
session.

API mode uses whole-file JSON operations. It appends the exact required response schema to every
request and validates that schema locally. Coding responses must contain exactly `summary` (string)
and `operations` (array). Writes require exactly `op`, `path`, `content`; deletes exactly `op`, `path`.
Extra fields, missing fields, and surrounding non-JSON text fail before any file mutation.
It does not rely on provider-specific
structured-output options, keeping compatibility with the documented SDK minimum versions. The
validator supports the object/array/type/required/additional-properties/enum/numeric-bound/anyOf subset
used by the bundled response schemas. Files are not changed on schema failure. Existing file modes,
including executable bits, are preserved when their contents are replaced.

The API context includes a bounded snapshot for every phase, including the baseline during reviews
and judging. Git file-list output is captured with a byte cap, and an oversized listing fails before
loading files. File contents, omission filenames, and the context-limit notice share one snapshot
byte budget. The snapshot identifies omitted files until the budget is exhausted and then records
that remaining paths were not inspected. A review still receives the candidate diff from
the coordinator; it cannot open additional files or execute tools. The expanded system and input
texts together must fit `max_prompt_bytes`; the call is rejected before network access if they do
not. This includes repository text, the specification, and the schema appendix.

API artifacts under each invocation directory are:

- `api-system.txt` and `api-input.txt`: the exact system and expanded input strings sent.
- `api-request.json`: exact SDK create-call arguments, excluding authentication/client settings.
- `raw-response.txt`: the SDK's serialized response, capped at `max_api_response_bytes`. A truncated
  response fails the invocation; it is not treated as usable JSON.
- `response-metadata.json`: observed response ID/model/status/stop reason, usage, full raw-response
  hash and byte count, and truncation status. Metadata is bounded to 8192 bytes; identifier strings
  are capped at 128 characters. Excessive usage metadata is marked omitted and fails the call.
- `transport-error.json`: exception type for transport failure. SDK exception bodies are omitted
  because they can contain request content or credential-bearing URLs.

Raw response and usage artifacts are saved before parsing or validating model output. Invalid JSON,
schema failures, incomplete responses, and oversized responses retain evidence and fail the phase.
These local files include repository contents and model text; store the audit directory accordingly.

Codex JSONL must contain a successful terminal `turn.completed` and no failure/error event. A
truncated stream fails because terminal state cannot be established. The separate last-message file
must be regular, cannot be a symlink, and is read with a bounded read. Claude output must be a
successful result envelope, with valid result/schema content and finite nonnegative cost when cost
is reported. A null structured output falls back to the normal result string.

Interfaces were checked against installed CLI help and official documentation:

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
- [OpenAI Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [Anthropic Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)
