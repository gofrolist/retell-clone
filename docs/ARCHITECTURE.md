# Architeq — Architecture

Architeq is a voice-AI phone-agent platform, API-compatible with Retell AI
(https://docs.retellai.com). It is a drop-in replacement: existing Retell
integrations migrate by changing the base URL and API key only
(see `usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md`).

## Components

```
                            ┌────────────────────────────────────────────┐
                            │                  GKE                       │
 Customer backend ──HTTP──▶ │  architeq-api   (FastAPI control plane)    │
   (Supabase edge fns)      │     │  ▲                                   │
        ▲                   │     │  │ dispatch / call state             │
        │ webhooks          │     ▼  │                                   │
        │ (call_ended,      │  Postgres (Cloud SQL)   Redis (Memorystore)│
        │  call_inbound)    │     ▲  │                                   │
        └───────────────────│─────┘  ▼                                   │
                            │  architeq-worker (LiveKit Agents, Python)  │
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
| Dashboard          | Next.js ("Architeq" branding), talks to control plane |
| Infra              | GCP: GKE, Cloud SQL, Memorystore, Artifact Registry; Terraform + Helm |
| Observability      | kube-prometheus-stack (Prometheus + Grafana), per-service /metrics |

## Services

### architeq-api (backend/)
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

### architeq-worker (worker/)
LiveKit Agents worker; one job per call.

- Joins the LiveKit room for the call; runs the Cartesia-STT → Gemini →
  Cartesia-TTS pipeline with barge-in/interruption handling.
- Resolves `{{variable}}` templates from `retell_llm_dynamic_variables` in the
  prompt, begin message, and tool definitions before use.
- **Custom function tools**: executes agent tool declarations
  (`name/description/url/method/parameters`) by POSTing the **flat** argument
  object (never wrapped in `args`) with header `X-Caller-Secret: <function_secret>`,
  resolving `{{var}}` in argument values, and feeding the JSON response back
  to the model as the tool result.
- **AMD / voicemail**: Telnyx AMD result (via SIP headers / LiveKit SIP
  attributes) combined with a Gemini-based greeting classifier; on detection
  sets `disconnection_reason=machine_detected` and `call_analysis.in_voicemail=true`.
- Streams transcript segments to the control plane; on room close posts the
  final call record (transcript, duration_ms, disconnection_reason,
  recording), which triggers `call_ended`.
- Recordings via LiveKit Egress to GCS; `recording_url` is a signed URL.

### architeq-dashboard (frontend/)
Next.js app cloned from dashboard.retellai.com layout (see `screenshots/`),
rebranded **Architeq**. Talks to architeq-api with a session (dashboard) token.

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
4. Tool calls send **flat** JSON args — never `{"args": {...}}`.
5. All `retell_llm_dynamic_variables` (arbitrary string keys/values) reach the
   agent as `{{key}}` template values — no renaming, no dropping.
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
frontend/   Next.js dashboard (Architeq branding)
infra/
  terraform/  GCP: GKE, Cloud SQL, Memorystore, Artifact Registry, DNS/IPs
  helm/       architeq umbrella chart (api, worker, frontend),
              livekit + livekit-sip values, kube-prometheus-stack values
docs/       architecture, compatibility, migration runbook
screenshots/  Retell dashboard reference for the UI clone
```

## Observability

- Every service exposes `/metrics` (Prometheus). Key series:
  `architeq_calls_total{direction,status}`, `architeq_call_duration_seconds`,
  `architeq_webhook_deliveries_total{event,outcome}`,
  `architeq_tool_calls_total{tool,outcome}`,
  `architeq_llm_ttfb_seconds` / `architeq_tts_ttfb_seconds` (latency SLO:
  p95 agent response — acceptance criterion from the migration spec §8),
  `architeq_amd_detections_total{result}`.
- kube-prometheus-stack installed via Helm; ServiceMonitors per service;
  Grafana dashboards in `infra/helm/monitoring/dashboards/`.
