# Arhiteq — Architecture

Arhiteq is a voice-AI phone-agent platform, API-compatible with Retell AI
(https://docs.retellai.com). It is a drop-in replacement: existing Retell
integrations migrate by changing the base URL and API key only
(see `usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md`).

## Components

```
                            ┌────────────────────────────────────────────┐
                            │                  GKE                       │
 Customer backend ──HTTP──▶ │  arhiteq-api   (FastAPI control plane)    │
   (Supabase edge fns)      │     │  ▲                                   │
        ▲                   │     │  │ dispatch / call state             │
        │ webhooks          │     ▼  │                                   │
        │ (call_ended,      │  Postgres (Cloud SQL)   Redis (Memorystore)│
        │  call_inbound)    │     ▲  │                                   │
        └───────────────────│─────┘  ▼                                   │
                            │  arhiteq-worker (LiveKit Agents, Python)  │
                            │     STT ─ Cartesia Ink-Whisper             │
                            │     LLM ─ Gemini (Google GenAI)            │
                            │     TTS ─ Cartesia Sonic                   │
                            │     │                                      │
                            │  LiveKit server + LiveKit SIP              │
                            └─────┼──────────────────────────────────────┘
                                  │ SIP trunk
                              Telnyx (PSTN numbers, AMD)
```

| Concern            | Technology |
|--------------------|-----------|
| Control-plane API  | FastAPI (Python 3.12), Postgres (Cloud SQL), Redis (Memorystore) |
| Media / rooms      | LiveKit server (self-hosted on GKE) + LiveKit SIP |
| Telephony / PSTN   | Telnyx SIP trunking (elastic SIP → LiveKit SIP), Telnyx AMD |
| STT                | Cartesia Ink-Whisper |
| LLM                | Google GenAI — Gemini (live conversation + post-call analysis) |
| TTS                | Cartesia Sonic |
| Dashboard          | Next.js ("Arhiteq" branding), talks to control plane |
| Infra              | GCP: GKE, Cloud SQL, Memorystore, Artifact Registry; Terraform + Helm |
| Observability      | kube-prometheus-stack (Prometheus + Grafana), per-service /metrics |

## Services

### arhiteq-api (backend/)
The control plane. Owns all persistent state and the public API.

- **Retell-compatible API** (`/v2/*` and top-level resource routes):
  calls (`create-phone-call`, `get-call`, `list-calls`), agents CRUD,
  Retell-LLM (response engine) CRUD, phone numbers CRUD, voices.
  Auth: `Authorization: Bearer <api_key>`; keys are per-workspace, hashed at rest.
- **Webhook dispatcher**: fires `call_started` / `call_ended` / `call_analyzed`
  to the agent- or workspace-level webhook URL, signed with
  `x-retell-signature: v=<unix_ms>,d=<hex hmac_sha256(rawBody + ts, api_key)>`
  (exact Retell format; 5-minute replay window on the consumer side).
  Delivery has retries with backoff; `call_ended` delivery is tracked per call.
- **Inbound router client**: when a call arrives on a number with an
  `inbound_webhook_url`, POSTs `{"event":"call_inbound","call_inbound":{from_number,to_number}}`
  and applies `call_inbound.override_agent_id` / `dynamic_variables` from the
  response. On non-2xx or malformed response it **degrades to the number's
  default agent** — the call always connects.
- **Call orchestration**: `create-phone-call` validates the from-number,
  creates the call row (`call_id` is globally unique and stable), then asks
  LiveKit SIP to dial out via the Telnyx trunk and dispatches an agent job.
- **Post-call analysis**: after the worker finalizes a call, runs Gemini over
  the transcript to produce `call_analysis` (`summary`, `user_sentiment`
  (`Positive|Negative|Neutral`), `in_voicemail`, `call_successful`), then
  emits `call_analyzed`.

### arhiteq-worker (worker/)
LiveKit Agents worker; one job per call.

- Joins the LiveKit room for the call; runs the Cartesia-STT → Gemini →
  Cartesia-TTS pipeline with barge-in/interruption handling.
- Resolves `{{variable}}` templates from `retell_llm_dynamic_variables` in the
  prompt, begin message, and tool definitions before use. Call-scoped system
  variables `{{call.call_id}}`, `{{call.direction}}`, `{{call.from_number}}`,
  `{{call.to_number}}` resolve too (Retell parity — consumer tool specs pass
  `{{call.call_id}}` as `retell_call_id`).
- Retell **default system variables** resolve underneath user variables
  (worker `variables.ResolutionVariables`, computed at lookup time so tools
  see fresh values mid-call): `{{current_time}}` / `{{current_hour}}` /
  `{{current_calendar}}` in the agent's `timezone` (Retell "Current Time
  Awareness"; unset or unknown falls back to `America/Los_Angeles`) plus
  `{{current_time_<IANA tz>}}`-style suffixed variants; `{{session_type}}`,
  `{{session_duration}}` (after answer); phone-call-only `{{direction}}`,
  `{{user_number}}`, `{{agent_number}}`; and `{{call_id}}`, `{{call_type}}`.
  One level of nesting is resolved inside the placeholder *key* only
  (`{{current_time_{{user_timezone}}}}`); substituted values are never
  re-scanned, so `{{...}}` text inside a variable's value reaches the agent
  verbatim. Chat resolves the same grammar control-plane-side
  (`{{chat_id}}`, `{{session_type}}`, `{{session_duration}}`, the
  `current_time` family — backend `services/template_variables.py`, a
  hand-kept mirror of the worker resolver). Unknown names and unknown
  timezone suffixes stay literal.
- **Custom function tools**: executes agent tool declarations
  (`name/description/url/method/parameters`) by POSTing the **flat** argument
  object (never wrapped in `args`) with header `X-Caller-Secret: <function_secret>`,
  resolving `{{var}}` in argument values, and feeding the JSON response back
  to the model as the tool result. A Retell-shaped `call` object
  (`call_id, direction, from_number, to_number, retell_llm_dynamic_variables,
  metadata`) rides alongside the flat args — consumer handlers fall back to
  `call.call_id` / `call.from_number` / `call.retell_llm_dynamic_variables.phone`
  when the model omits them.
- **AMD / voicemail**: Telnyx AMD result (via SIP headers / LiveKit SIP
  attributes) combined with a Gemini-based greeting classifier; on detection
  sets `disconnection_reason=machine_detected` and `call_analysis.in_voicemail=true`.
- Streams transcript segments to the control plane; on room close posts the
  final call record (transcript, duration_ms, disconnection_reason,
  recording), which triggers `call_ended`.
- Recordings via LiveKit Egress to GCS; `recording_url` is a signed URL.

### arhiteq-dashboard (frontend/)
Next.js app cloned from dashboard.retellai.com layout (see `screenshots/`),
rebranded **Arhiteq**. Talks to arhiteq-api with a session (dashboard) token.

## Call flows

### Outbound (`POST /v2/create-phone-call`)
1. Validate API key → workspace; validate `from_number` ownership.
2. Create `calls` row: `call_id` (32-char, stable), agent = number's outbound
   agent unless `override_agent_id`; store `metadata`,
   `retell_llm_dynamic_variables` verbatim.
3. Create LiveKit room `call_<call_id>`; dispatch agent job with call config;
   LiveKit SIP `CreateSIPParticipant` dials Telnyx trunk → PSTN.
4. Respond `201` with the full Retell-shaped call object (readers only need
   `call_id`). Non-2xx on any failure (callers treat non-2xx as not-placed).
5. Answer/no-answer/AMD outcomes update the call; `call_ended` webhook fires
   with `duration_ms`, `disconnection_reason`, transcript, etc.

### Inbound
1. Telnyx routes the DID to LiveKit SIP; dispatch rule starts a worker job.
2. Worker asks the control plane to resolve the call: control plane looks up
   the number, calls its inbound webhook (Surface 2A) with a short timeout,
   falls back to the number's default inbound agent on any error.
3. Worker runs the agent with merged dynamic variables; webhooks as above.

## Conversation flow execution

A conversation flow (`response_engine.type == "conversation-flow"`) replaces
the single prompt with a directed graph of nodes — prompts, tool calls,
branches, transfers — that the worker walks live during the call instead of
holding one static instruction set for the whole conversation. The graph
itself is opaque JSON everywhere except in the worker: the control plane
stores and serves it verbatim (see `docs/INTERNAL_API.md`), and node-type
validation happens only at call start, in the worker.

**Entering a node.** The instructions the model sees at any point are built
from three pieces, in order: the flow's `global_prompt`; the node's own
`instruction.text`, but only when that instruction's type is `prompt`; and a
rendered list of the node's available transitions, naming each edge's id
beside its condition text — the model can only choose a transition by id, so
it has to be able to see them. When a node's instruction type is
`static_text` instead, the worker speaks that text verbatim and never asks
the model to phrase it; a `prompt` instruction is voiced by requesting an
ordinary model turn.

**Transitions.** Every node's edges carry a `transition_condition` of type
`equation` or `prompt`. At every transition point the runtime evaluates the
node's `equation` edges first, in declaration order — the first one that
evaluates true wins, deterministically, in code, with no model call
involved. Only if no equation edge fires do the node's `prompt` edges reach
the model, offered as one synthetic tool call, `transition_to`, whose
argument is an enum of that node's edge ids (never one tool per edge, so
`tool_call_strict_mode` stays meaningful). A `branch` node whose edges are
all equations therefore costs zero LLM calls to route.

**`branch` nodes** speak nothing — they are pure routing, never a turn. If an
equation edge fires, that decides it for free. If the node instead has
`prompt` edges and no equation matched, the worker makes exactly one cheap,
non-streaming completion against the flow's own mapped model (temperature 0)
asking it to name the matching edge id from the transcript so far; no match
routes to the node's `else_edge`.

**`skip_response_edge` vs. `always_edge`.** Both are edges the runtime takes
on its own, never offered to the model as a choice, but they mean different
things. `skip_response_edge` lives on a `conversation` node: the node speaks
its line as usual, and then the runtime advances immediately to the next
node **without waiting for the caller to reply** — "skip response" means skip
waiting for *their* response, not skip saying ours; it is the "say this and
continue" connector, typically chaining a few `static_text` nodes together
before the flow finally waits for a turn. `always_edge`, by contrast, is the
unconditional next hop taken on the *following* user turn — the node still
converses normally first.

**`subagent` nodes execute as `conversation` nodes.** A `subagent`'s field
set is a strict subset of `conversation`'s, so the two share one handler
rather than one being a stub of the other.

**Auto-advancing action nodes.** A `function` node (a flow-scoped HTTP tool
call) or an `extract_dynamic_variables` node runs its own action and then has
to decide where to go. When it has exactly one edge that could plausibly
follow a successful result, the worker takes it automatically — there is
nothing to decide. When it has several, the worker leaves the choice to the
model instead of guessing: only the model, having just seen the tool result
or the extracted values, can tell "found the member" apart from "lookup
failed twice" when both come back as an ordinary (non-error) result. A hard
failure always falls back to the node's `else_edge`.

**Validation happens once, at call start.** Before the greeting is spoken,
the worker indexes every node (including those nested in `components[]`, so
an edge pointing into a subflow still resolves) and checks that every node's
type is one it knows how to run and that every edge's destination exists.
An unsupported node type or an edge into a missing node aborts the call
immediately, naming the offending node id in the log — never a dead end
discovered ninety seconds into a live call.

**A routing node with nowhere to go ends the call.** One dead end load-time
validation cannot catch is a *dangling* edge — authored with no
`destination_node_id` at all, so there is no destination to check against.
Real Retell captures contain these (a `transfer_call` node's failure edge, a
`function` node's `else_edge`), and a transfer fails on every non-SIP call, so
the path is ordinary rather than exotic. A node that can hold a conversation
(`conversation` / `subagent`) simply stays where it is — the model keeps
talking, which is a legitimate end state. A node that can only route
(`branch`, `function`, `extract_dynamic_variables`, `press_digit`, `transfer_call`) speaks
nothing and cannot advance, so staying put would be silence for the rest of
the call: the worker logs the node id at error and hangs up instead.

**A flow's `default_dynamic_variables`** are merged underneath the call's own
`dynamic_variables`, exactly as the control plane merges an LLM's defaults on
the single-prompt path (defaults < call-level). A flow-backed agent has no
LLM, so that control-plane merge never runs for it and the worker does it —
otherwise a greeting would speak the raw `{{caller_name}}` and every
`equation` edge testing a defaulted variable would read *missing*, silently
degrading equation routing to the fallback edge.

**A bounded automatic-transition budget** stops a cycle of nodes that never
wait for a user turn (an all-equation `branch` looping back on itself, say)
from spinning forever inside a single turn; the budget resets on every real
user turn, so a legitimately long chain of connector nodes is never cut
short.

The worker never fetches a conversation flow on its own — it only ever reads
the `conversation_flow` object already resolved onto the call's config (see
`docs/INTERNAL_API.md`), at the same pinned agent version as everything else
about the call.

**The dashboard editor round-trips the same opaque JSON.** `frontend/src/
components/flow/` (`docs/UI_INVENTORY.md` § Agent detail (Conversation Flow
editor)) reads and writes `ConversationFlow` through the ordinary
`get-conversation-flow` / `update-conversation-flow` endpoints; it models only
the fields its UI can express (the seven executable node types, edge
transition conditions, notes, global settings) and otherwise carries every
other field it doesn't understand through unchanged — a node's
`finetune_transition_examples`, a flow's `flex_mode`, anything Retell sends
that has no editor control still comes back on save exactly as it went in.
Nothing enforces that by convention; a value-diffed reducer (`flowModel.ts`)
is the only path allowed to touch flow state, and a "fidelity" test suite
(`frontend/src/components/flow/__tests__/flowModel.test.ts`) loads three real,
sanitized Retell captures (`backend/tests/fixtures/retell_flows/
{prior_auth_hotline,clara_outbound,identity_verify_transfer}.json` — the same
fixtures the backend and worker treat as the schema authority) and asserts
they parse and re-serialize losslessly. A change that reconstructs flow state
by hand instead of dispatching through the reducer defeats this guarantee
without failing on coverage alone — the fidelity test is what actually
enforces it, not a documented convention.

**Known runtime limitations:**
- Per-node overrides other than `start_speaker` — a node's own
  `interruption_sensitivity`, voice speed, response eagerness, or per-node
  LLM choice — survive round-trip (nodes are stored and served as opaque
  JSON) but the runtime never reads them; only the flow-level `model_choice`
  is honoured. `start_speaker` itself is read on the START node only
  (`flow.py:start_speaker_for` has one caller: `main.py`, applied to
  `graph.start`), so a value on any other node also survives round-trip
  unread — which is why the editor offers the control on the start node
  alone.
- `finetune_transition_examples` / `finetune_conversation_examples` likewise
  survive round-trip as part of a node's JSON but are not consulted at
  runtime.
- A `transfer_call` node's `ignore_e164_validation` survives round-trip but is
  deliberately not acted on: every dial-out path enforces strict E.164 with
  no opt-out (`docs/SECURITY.md` § Transfer destinations). This is the one
  place the runtime declines a stored flag on purpose rather than for lack of
  support.
- `components[]` (subflows) are indexed so a `destination_node_id` pointing
  into one resolves, but there is no `component` node type to invoke a
  subflow as a unit — a graph that actually contains one is rejected at call
  start like any other unsupported node type.
- A flow's `model_choice` names a model from Retell's own catalogue (real
  flows carry `gpt-5.1`); Arhiteq is Gemini-only, so the worker maps it onto
  the Gemini catalogue through the same helper the single-prompt path uses
  for `llm.model` — an OpenAI model id never reaches a provider.

## Retell compatibility rules (non-negotiable)

Contract-critical behaviors, from the migration spec — covered by the
contract test suite in `backend/tests/contract/`:

1. `call_id` is unique + stable across create-phone-call response, get-call,
   and every webhook.
2. Webhook signature: `v={unix_ms},d={lowercase hex hmac_sha256(rawBody+ts)}`,
   key = the workspace API key.
3. `call_ended` semantics: `duration_ms` = talk time in ms; voicemail is
   signaled via `call_analysis.in_voicemail=true` **and/or**
   `disconnection_reason="machine_detected"`.
4. Tool calls send **flat** JSON args — never `{"args": {...}}` — plus a
   top-level `call` object (additive, matches Retell).
5. All `retell_llm_dynamic_variables` (arbitrary string keys/values) reach the
   agent as `{{key}}` template values — no renaming, no dropping. Call-scoped
   `{{call.*}}` system variables are additionally available and win over
   same-named user variables. Retell default system variables
   (`{{current_time}}`, `{{direction}}`, `{{session_duration}}`, …) resolve
   only when no user variable has that name. Variable *values* are delivered
   verbatim — substitution output is never re-scanned for placeholders.
6. Inbound router response is read as
   `{"call_inbound": {"override_agent_id", "dynamic_variables"}}`; failure
   degrades to default agent, never drops the call.
7. Unknown/extra request fields are accepted and ignored (`metadata` stored
   as-is); responses carry correct HTTP status codes (2xx success only).
8. Support for appending `?caller_secret=<secret>` to the inbound webhook URL
   (config flag, off by default).

## Repository layout

```
backend/    FastAPI control plane (+ alembic migrations, contract tests)
worker/     LiveKit Agents voice worker
frontend/   Next.js dashboard (Arhiteq branding)
infra/
  terraform/  GCP: GKE, Cloud SQL, Memorystore, Artifact Registry, DNS/IPs
  helm/       arhiteq umbrella chart (api, worker, frontend),
              livekit + livekit-sip values, kube-prometheus-stack values
docs/       architecture, compatibility, migration runbook
screenshots/  Retell dashboard reference for the UI clone
```

## Observability

- Every service exposes `/metrics` (Prometheus). Key series:
  `arhiteq_calls_total{direction,status}`, `arhiteq_call_duration_seconds`,
  `arhiteq_webhook_deliveries_total{event,outcome}`,
  `arhiteq_tool_calls_total{tool,outcome}`,
  `arhiteq_llm_ttfb_seconds` / `arhiteq_tts_ttfb_seconds` (latency SLO:
  p95 agent response — acceptance criterion from the migration spec §8),
  `arhiteq_amd_detections_total{result}`,
  `arhiteq_worker_active_jobs` (concurrency; also the worker HPA's custom
  metric).
- The worker's series are recorded in per-call job subprocesses, so its
  `/metrics` runs in prometheus_client multiprocess mode and is served by
  livekit (`prometheus_port` / `prometheus_multiproc_dir`); a worker series is
  absent until a call writes it, and supervisor-side series (`lk_agents_*`,
  `process_*`) are not exported. See `worker/README.md` § Metrics.
- LiveKit is scraped too: `livekit-server` and `livekit-sip` via
  ServiceMonitors their charts render (each gated on a metrics port being set
  — see `infra/README.md` § 4), `egress` via a PodMonitor, because that chart
  creates no Service. `livekit_sip_*` is where trunk problems surface first.
- kube-prometheus-stack installed via Helm; ServiceMonitors per service;
  Grafana dashboards in `infra/helm/monitoring/dashboards/`, alert rules in
  `infra/helm/monitoring/rules/`. Alertmanager delivers to Telegram — bot
  token from the `alertmanager-telegram` Secret, chat id and message template
  rendered by `infra/helm/monitoring/gen-values.sh` from `prod.env`. Grouped
  by `alertname`/`namespace`/`severity`, with `critical` on a faster route,
  and capped at 8 alerts per message so a large group stays inside Telegram's
  4096-character limit.
