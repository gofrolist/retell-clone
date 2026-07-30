# Internal API — arhiteq-api ⇄ arhiteq-worker

Private contract between the control plane and the LiveKit voice worker.
Not exposed publicly; every request carries `X-Internal-Token: <shared token>`
(`ARHITEQ_INTERNAL_TOKEN` on both sides). Base path `/internal`.

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
Caller facts are merged beneath the webhook's dynamic variables: `phone`
defaults to `from_number`, `user_timezone` defaults to `America/Los_Angeles`,
and a workspace contact matching `from_number` contributes
`first_name`/`last_name`/`user_timezone`. Webhook values win, except that an
empty-string webhook value never erases one of these caller facts.
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
            "default_dynamic_variables": {} } | null,
  "conversation_flow": { "global_prompt": "…", "nodes": [ /* graph, verbatim */ ],
            "start_node_id": "…", "start_speaker": "agent",
            "tools": [ /* flow-scoped tool defs, by tool_id */ ],
            "components": [ /* subflows */ ], "model_choice": { "model": "…" } | null,
            "model_temperature": 0.0 | null, "kb_config": {...} | null,
            "knowledge_base_ids": [...], "default_dynamic_variables": {} } | null,
  "dynamic_variables": { "first_name": "John", … },  // merged: defaults < call
  "metadata": {},
  "function_secret": "…"   // sent as X-Caller-Secret on custom tool calls
}
```

A conversation-flow agent (`response_engine.type == "conversation-flow"`) has
no Retell-LLM row of its own, so `llm` is `null` and `conversation_flow`
carries the graph instead — the worker branches on which of the two is
present. `conversation_flow` is `null` for an ordinary single-prompt agent.
Because a flow has no `llm.model`, it names its own model in
`model_choice`/`model_temperature`; the worker maps `model_choice.model` onto
the Gemini catalogue the same way it maps `llm.model` (see
`docs/ARCHITECTURE.md`).

Like `agent` and `llm`, `conversation_flow` is resolved at the call's pinned
agent version, not the live draft — editing (or even publishing over) a flow
while a call is running can never change what that call is executing. The
worker never fetches a conversation flow directly; this is the only shape it
ever sees one in.

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
      // items additionally carry "time_ms" (offset from answer, ms) and — on
      // tool_call_invocation/tool_call_result — "tool_call_id" pairing them
  "recording_url": "https://…" | null,
  "in_voicemail": true | false | null,   // worker-side AMD verdict, if any
  "latency": { "e2e": {"p50": …, "p95": …} } | null,
  "collected_dynamic_variables": {"plan": "pro"} | null  // extract_dynamic_variable
      // output; persisted on the call row, surfaced in get-call's
      // collected_dynamic_variables (Retell contract field)
}
```

### `GET /internal/agents/{agent_id}/config?call_id={call_id}`
Destination config for the `agent_swap` tool. Returns
`{"agent": {…}, "llm": {…} | null, "conversation_flow": {…} | null}` (same
shapes as in the call config); the worker re-points the live session at this
agent's prompt, tools and voice mid-call. `call_id` is required and scopes
the lookup: `404` for unknown agents, unknown calls, or agents outside the
calling call's workspace (agent_id comes from user-editable tool config).
The worker refuses to swap when `llm` is null (it would wipe the live
prompt/tools) — this includes swapping onto a conversation-flow agent,
which has no `llm`; `agent_swap` targeting a flow agent is not supported.

### `POST /internal/calls/{call_id}/knowledge-base/query`
Retrieval behind the `kb_lookup` tool.
Body: `{"query": "…", "knowledge_base_ids": ["know_…"]?, "category": "…"?,
"top_k": 3?}` → `{"query", "results": [{"title", "content", "source_id",
"knowledge_base_id", "knowledge_base_name", "score"}], "skipped_sources": […]}`.

`knowledge_base_ids` omitted (or empty) falls back to the ids on the LLM
version this call is pinned to. Like agent config, `call_id` scopes the
lookup to the call's workspace — the ids arrive from user-editable tool
config, so ids belonging to another workspace simply match nothing. `404`
for an unknown call.

Ranking is lexical BM25, chunked per lookup (`services/knowledge.py`).
Searchable: `text` sources, plus uploaded documents whose bytes decode as
UTF-8 text (`.md`/`.txt`/`.csv`, or a `text/*` content type). Markdown is
split on its own headings, so `title` reads
`pricing.md › Pricing Snapshot › Current trial offer`, and a YAML
`category:` in frontmatter is matched exactly against the request's
`category` (a stronger signal than the substring guess used for untagged
sources — neither ever filters a chunk out, they only boost).

`skipped_sources` entries carry a `reason`: PDF/Office uploads (no parser
dependency), files over 2MB, bytes that are not valid UTF-8, URL sources,
and `"not loaded"` when the caller passed knowledge bases without their
blobs. `results` is empty when nothing matched, which the worker turns into
an explicit "say you don't know" instruction rather than an error.

On finalize the control plane: persists, fires `call_ended` (signed), runs
Gemini post-call analysis (summary/call_summary, user_sentiment,
call_successful, in_voicemail — worker AMD verdict wins if set), then fires
`call_analyzed`.

Transcript line format matters: `Agent: …` / `User: …` lines joined with
`\n` (consumers parse this shape).
