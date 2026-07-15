# Internal API — architeq-api ⇄ architeq-worker

Private contract between the control plane and the LiveKit voice worker.
Not exposed publicly; every request carries `X-Internal-Token: <shared token>`
(`ARCHITEQ_INTERNAL_TOKEN` on both sides). Base path `/internal`.

## Job dispatch (api → worker, via LiveKit)

Outbound calls: the control plane creates room `call_<call_id>`, dispatches an
agent job with `metadata = {"call_id": "..."}`, then creates the SIP
participant (dial-out via Telnyx trunk). Inbound calls: LiveKit SIP dispatch
rule creates the room and job; the worker resolves the call by phone numbers.

## Endpoints (worker → api)

### `POST /internal/inbound/resolve`
Body: `{"from_number": "+1...", "to_number": "+1...", "room": "..."}`
The control plane looks up the DID, creates the call row, invokes the
number's inbound webhook (Surface 2A, ≤9.5s timeout) and merges the response,
falling back to the DID's default inbound agent on any error.
Returns `200` with the same shape as `/internal/calls/{call_id}/config`.
`404` if the DID is unknown.

### `GET /internal/calls/{call_id}/config`
Full call execution config:

```jsonc
{
  "call_id": "call_…",
  "direction": "outbound",
  "from_number": "+1…", "to_number": "+1…",
  "call_type": "phone_call" | "web_call",  // gates phone-call-only system vars
      // ({{direction}}, {{user_number}}, {{agent_number}}) worker-side;
      // absent -> fail closed (those placeholders stay literal)
  "agent": { /* full agent row: voice_id, language, interruption_sensitivity,
               responsiveness, enable_backchannel, reminder_trigger_ms,
               reminder_max_count, max_call_duration_ms,
               end_call_after_silence_ms, enable_voicemail_detection,
               voicemail_option, boosted_keywords, webhook_url, … */ },
  "llm": { "model": "…", "model_temperature": 0.0, "general_prompt": "…",
            "begin_message": "…", "start_speaker": "agent",
            "general_tools": [ /* verbatim tool declarations */ ],
            "states": null, "starting_state": null,
            "default_dynamic_variables": {} },
  "dynamic_variables": { "first_name": "John", … },  // merged: defaults < call
  "metadata": {},
  "function_secret": "…"   // sent as X-Caller-Secret on custom tool calls
}
```

### `POST /internal/calls/{call_id}/events`
Lifecycle + streaming updates. Body: `{"event": "...", ...}`:
- `{"event":"call_started","start_timestamp":<unix_ms>}` → status `ongoing`,
  fires `call_started` webhook.
- `{"event":"transcript_update","transcript":"…","transcript_object":[…]}` —
  periodic; keeps get-call fresh mid-call.

### `POST /internal/calls/{call_id}/finalize`
Terminal update; idempotent (second call is a no-op).

```jsonc
{
  "end_timestamp": 1714608491736,
  "duration_ms": 134000,          // talk time (answer→hangup), NOT dial time
  "disconnection_reason": "user_hangup" | "agent_hangup" | "machine_detected"
      | "dial_no_answer" | "dial_busy" | "dial_failed" | "call_transfer"
      | "max_duration_reached" | "inactivity" | "error_…",
  "call_status": "ended" | "not_connected" | "error",
  "transcript": "Agent: …\nUser: …",
  "transcript_object": [...], "transcript_with_tool_calls": [...],
  "recording_url": "https://…" | null,
  "in_voicemail": true | false | null,   // worker-side AMD verdict, if any
  "latency": { "e2e": {"p50": …, "p95": …} } | null,
  "collected_dynamic_variables": {"plan": "pro"} | null  // extract_dynamic_variable
      // output; accepted (extra=allow) but not yet persisted on the call row
}
```

### `GET /internal/agents/{agent_id}/config?call_id={call_id}`
Destination config for the `agent_swap` tool. Returns
`{"agent": {…}, "llm": {…} | null}` (same shapes as in the call config);
the worker re-points the live session at this agent's prompt, tools and
voice mid-call. `call_id` is required and scopes the lookup: `404` for
unknown agents, unknown calls, or agents outside the calling call's
workspace (agent_id comes from user-editable tool config). The worker
refuses to swap when `llm` is null (it would wipe the live prompt/tools).

On finalize the control plane: persists, fires `call_ended` (signed), runs
Gemini post-call analysis (summary/call_summary, user_sentiment,
call_successful, in_voicemail — worker AMD verdict wins if set), then fires
`call_analyzed`.

Transcript line format matters: `Agent: …` / `User: …` lines joined with
`\n` (consumers parse this shape).
