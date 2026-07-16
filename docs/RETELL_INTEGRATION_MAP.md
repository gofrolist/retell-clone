# Consumer integration map (usan-retirement-backend / -crm)

What the existing consumers actually call and read. Arhiteq must satisfy this
exactly. Source: exploration of both repos, 2026-07-10. Binding spec:
`usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md`.

## Switch point

`supabase/functions/_shared/voice-provider.ts` is the ONLY place with the
provider base URL:

```ts
VOICE_API_BASE = Deno.env.get("VOICE_API_BASE") ?? "https://api.retellai.com"
createPhoneCallUrl() => `${VOICE_API_BASE}/v2/create-phone-call`   // POST
getCallUrl(callId)   => `${VOICE_API_BASE}/v2/get-call/${callId}`  // GET
```

Cutover = set `VOICE_API_BASE` to Arhiteq + swap `RETELL_API_KEY`.
**usan-retirement-crm never calls Retell** (pure Supabase RPC frontend reading
the `calls` table) → zero CRM changes needed.

## Surface 1 — REST (consumer → Arhiteq)

- `POST /v2/create-phone-call` — callers: morning-call-dispatcher (3 loops),
  evening-call-dispatcher, trigger-care-call, signup-lead (+metadata),
  run-test-scenario (+metadata), _shared/failed-jobs.ts (retry).
  Body: `from_number, to_number, override_agent_id,
  retell_llm_dynamic_variables{string:string}, metadata?`.
  All callers read only `call_id` from the response; non-2xx = call not placed
  (lead marked `retell_error`). Exception: `run-test-scenario` treats HTTP 429
  / body matching `/concurrency limit|429/i` as "busy, re-queue" — Arhiteq
  returns `429 {"detail":"Concurrency limit reached (20)"}` when live
  (registered+ongoing) workspace calls hit the limit.
- `GET /v2/get-call/{call_id}` — callers and fields read:
  - evaluate-test → `transcript, duration_ms, call_status`
  - schedule-callback, send-family-sms, create-trial →
    `direction, from_number, to_number` (counterparty = `from` if inbound else `to`)

## Surface 2A — inbound router (Arhiteq → consumer, sync)

POST `{supabase}/functions/v1/inbound-call-router` with
`{"event":"call_inbound","call_inbound":{"from_number","to_number"}}`.
Response: `{"call_inbound":{"override_agent_id","dynamic_variables"}}` (all
values strings). 400/500 or malformed → fall back to the DID's default agent;
never drop the call. Reserved: `?caller_secret=<RETELL_FUNCTION_SECRET>` query
param (not enforced yet — router currently verifies nothing).

## Surface 2B — call-ended webhook (Arhiteq → consumer)

POST `{supabase}/functions/v1/retell-call-ended`, header
`x-retell-signature: v={unix_ms},d={hex hmac_sha256(rawBody+ts, RETELL_API_KEY)}`,
5-minute replay window, constant-time compare (`_shared/verify-webhook.ts`).

Events consumed: `call_ended`, `call_analyzed` (others ignored). Fields read:
`call.call_id, call_status, from_number, to_number, direction, duration_ms,
recording_url, transcript, disconnection_reason,
call_analysis.{summary, in_voicemail, user_sentiment, call_successful,
custom_analysis_data}`.

> ⚠️ Consumer reads `call_analysis.summary` — Retell's canonical field is
> `call_summary`. Arhiteq emits **both**.

`determineStatus` (order matters): `in_voicemail===true` → voicemail;
`disconnection_reason==="machine_detected"` → voicemail;
`duration_ms>=10000` → answered; else missed.
`user_sentiment` matched case-insensitively by substring:
positive/happy → positive; negative/sad/concern → concerning; else neutral.

## Surface 3 — agent tools (Arhiteq agent → consumer edge functions)

56 JSON declarations in `retell/{companion(27),inbound(13),sales(16)}/`, shape
`{name, description, url, method:"POST", parameters:{type:"object",properties,required}}`.
Execution contract:
- POST to `url`, body = **flat** args object (never `{"args":{...}}`),
  header `X-Caller-Secret: <RETELL_FUNCTION_SECRET>`. A top-level `call`
  object (`call_id, direction, from_number, to_number,
  retell_llm_dynamic_variables, metadata`) rides alongside — handlers use it
  as fallback (`call.call_id`, `call.from_number`,
  `call.retell_llm_dynamic_variables.phone`).
- Resolve `{{var}}` (dynamic variables) inside argument values before sending.
  Includes call-scoped `{{call.call_id}}` etc. — `log_outcome` and
  `log_churn_reason` specs REQUIRE `retell_call_id={{call.call_id}}`.
- Feed the JSON response back to the model as the tool result.
- `kb_lookup` is a Retell built-in KB tool (no URL) → Arhiteq knowledge-base
  retrieval feature.
- Quirks: `log_outcome` and `end_call` both post to `/end-call`;
  `set_evening_call_preference` posts to `/set-evening-preference`;
  schema drift between agent folders is intentional (keep per-agent copies).

## Agents to import (3)

| Agent | Prompt (in git, `prompts/`) | Env var for id |
|---|---|---|
| Sales "Clara" | `sales_clara_v0.1_retell.txt` (BYO Claude Haiku 4.5) | `RETELL_SALES_AGENT_ID` |
| Companion "Clara" morning+evening | `checkin_v0.2_retell.txt` | `RETELL_COMPANION_AGENT_ID` |
| Betty (QA tester) | `betty_tester_retell.txt` | `RETELL_BETTY_AGENT_ID` |

Inbound calls route to Sales or Companion via the router; `retell/inbound/` is
a tool set, not a separate agent. Dynamic variables in play (all strings):
`phone, first_name, state, user_timezone, prior_conversation,
medications_today, trial_status, is_last_day_of_trial, is_day_1,
needs_evening_setup, time_of_day, consecutive_missed_calls, call_direction,
is_existing_client, is_new_caller, on_dnc_list, opener_variant, bm_greeting,
offer_early_payment`. These are all consumer-supplied (`time_of_day` and
`call_direction` included — the dispatchers compute them); Retell *default*
system variables (`{{current_time}}`, `{{current_time_<tz>}}`,
`{{current_hour}}`, `{{current_calendar}}`, `{{session_type}}`,
`{{session_duration}}`, `{{direction}}`, `{{user_number}}`,
`{{agent_number}}`, `{{call_id}}`, `{{call_type}}`, chat `{{chat_id}}`) are
also implemented, resolving beneath consumer-supplied names
(docs/ARCHITECTURE.md § arhiteq-worker).

## Consumer env vars

`RETELL_API_KEY` (bearer + HMAC key), `RETELL_FUNCTION_SECRET`,
`RETELL_{SALES,COMPANION,BETTY}_AGENT_ID`, `RETELL_FROM_PHONE` **and**
`RETELL_FROM_NUMBER` (both used!), `RETELL_BETTY_PHONE_NUMBER`,
`VOICE_API_BASE`, `ENFORCE_WEBHOOK_SIGNATURES`, `ENFORCE_CALLER_AUTH`.

## Cutover gotchas

- `failed_jobs.endpoint_url` stores absolute URLs — drain/rewrite pending
  voice jobs before final cutover.
- Numbers: `+1(949)919-5585` (Telnyx Main → Companion inbound; provider
  "Custom telephony") and `+1(415)707-8561` (Betty). Port/SIP-trunk lead time
  is the longest pole.
- Knowledge base "UsanRetirement kb" (13 docs) lives only in the Retell
  dashboard — export and import into Arhiteq KB.
- Recordings: archive old Retell `recording_url`s before shutdown.
