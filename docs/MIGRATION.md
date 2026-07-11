# Migration runbook: Retell → Architeq (USAN Retirement)

Follows `usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md` §9.
Good news discovered during mapping (see `docs/RETELL_INTEGRATION_MAP.md`):

- The §7 refactor is **already done** — `_shared/voice-provider.ts` exists and
  every call site imports it. Cutover is an env flip, no code changes.
- **usan-retirement-crm needs zero changes** (it only reads Supabase tables
  populated by the webhook).

## Phase 0 — long-lead items (start first)

1. **Numbers.** `+1(949)919-5585` is already "Custom telephony" (Telnyx) on
   Retell — it can be repointed to Architeq's LiveKit-SIP without a port.
   Betty's `+1(415)707-8561` is Twilio-bought — port to Telnyx or replace.
2. **Recordings archive.** Old Retell `recording_url`s die at cutover. Batch
   job: select `recording_url` from `calls`, download, upload to Supabase
   Storage (or Architeq's GCS), rewrite the column.
3. **Knowledge base.** Export the 13 docs of "UsanRetirement kb" from the
   Retell dashboard (they're small MD files + 1 PDF; most exist in git) and
   import into Architeq KB.

## Phase 1 — stand up Architeq

1. Deploy per `infra/README.md` (Terraform → GKE → LiveKit/SIP → services →
   kube-prometheus-stack).
2. Seed a workspace **reusing the consumer's exact API key** so the Bearer
   token and the webhook-HMAC key both keep working:
   `python -m app.seed --api-key "$RETELL_API_KEY" --workspace-name USAN`
3. Import agents **preserving existing agent ids** (so `RETELL_*_AGENT_ID`
   env vars stay untouched):
   ```bash
   python scripts/import_usan_agents.py \
     --api-base https://api.<domain> --api-key "$RETELL_API_KEY" \
     --consumer-repo ~/gofrolist/usan-retirement-backend \
     --companion-agent-id "$RETELL_COMPANION_AGENT_ID" \
     --sales-agent-id "$RETELL_SALES_AGENT_ID" \
     --betty-agent-id "$RETELL_BETTY_AGENT_ID" \
     --webhook-url https://<project>.supabase.co/functions/v1/retell-call-ended
   ```
4. Import numbers and wire routing:
   ```bash
   curl -X POST https://api.<domain>/import-phone-number \
     -H "Authorization: Bearer $RETELL_API_KEY" -H 'content-type: application/json' \
     -d '{"phone_number":"+19499195585","nickname":"Telnyx Main",
          "inbound_agent_id":"'$RETELL_COMPANION_AGENT_ID'",
          "outbound_agent_id":"'$RETELL_COMPANION_AGENT_ID'",
          "inbound_webhook_url":"https://<project>.supabase.co/functions/v1/inbound-call-router"}'
   ```
5. Set Architeq env `ARCHITEQ_FUNCTION_SECRET=$RETELL_FUNCTION_SECRET`
   (worker sends it as `X-Caller-Secret` on every tool call).

## Phase 2 — verification (before any live traffic)

1. `make test` — contract suite green.
2. Synthetic call: `POST /v2/create-phone-call` to a test phone; verify on the
   consumer side: `calls.retell_call_id` written, `call_ended` webhook passes
   signature verification (`ENFORCE_WEBHOOK_SIGNATURES=true` in a staging
   project), transcript/summary populated, tool calls hit edge functions with
   flat args + `X-Caller-Secret`.
3. Betty QA loop: `run-test-scenario` → Betty (Architeq) calls Clara inbound
   number → `evaluate-test` reads get-call. Full Surface 1+2+3 coverage.
4. Voicemail test: call a number that goes to voicemail; verify consumer
   `determineStatus` → `voicemail` (needs `in_voicemail=true` or
   `disconnection_reason=machine_detected`).
5. Latency acceptance (spec §8): Grafana `architeq-voice-latency` dashboard —
   agree the p95 threshold before canary.

## Phase 3 — canary

1. In Supabase Edge Function secrets set `VOICE_API_BASE=https://api.<domain>`
   **for a canary slice** (spec suggests companion morning-calls for a lead
   subset; mechanism per-flag to be agreed). NB: `VOICE_API_BASE` is global
   per deployment — the practical canary is time-boxed windows or a staging
   project first.
2. Watch: consumer `error_log`, Architeq Grafana (call rate, webhook delivery
   100%, tool-call outcomes, AMD), transcripts spot-check.
3. Enable `ENFORCE_WEBHOOK_SIGNATURES=true`, then `ENFORCE_CALLER_AUTH=true`.

## Phase 4 — full cutover

1. **Drain `failed_jobs`**: any pending row with an absolute
   `https://api.retellai.com/...` endpoint_url must be deleted or rewritten —
   `retry-failed-jobs` replays verbatim and would bypass `VOICE_API_BASE`.
2. Flip `VOICE_API_BASE` for all traffic; keep `RETELL_API_KEY` value (it is
   now the Architeq key).
3. Repoint/port remaining numbers; decommission Retell agents after the
   recording archive (Phase 0.2) is confirmed complete.

## Env-var mapping (consumer side, after cutover)

| Var | Value |
|---|---|
| `VOICE_API_BASE` | `https://api.<domain>` (Architeq) |
| `RETELL_API_KEY` | unchanged (imported into Architeq as the workspace key) |
| `RETELL_*_AGENT_ID` | unchanged (ids preserved on import) |
| `RETELL_FROM_PHONE` / `RETELL_FROM_NUMBER` | unchanged (both! two names, both read) |
| `RETELL_FUNCTION_SECRET` | unchanged (= `ARCHITEQ_FUNCTION_SECRET`) |
| `ENFORCE_WEBHOOK_SIGNATURES` / `ENFORCE_CALLER_AUTH` | `true` after stabilization |

## Open items to agree with stakeholders (spec §10)

- Voicemail: Architeq sets **both** `call_analysis.in_voicemail` and
  `disconnection_reason="machine_detected"` on AMD hit. ✅ confirmed design
- `duration_ms` = talk time (answer→hangup), not dial time. ✅ confirmed design
- Signature format `v={ms},d={hex sha256}` over `rawBody+ts`. ✅ implemented + tested
- `recording_url` TTL: GCS signed URLs, 30-day expiry by default
  (`ARCHITEQ_RECORDING_URL_TTL_SECONDS`) — consumer should archive if longer
  retention is needed. **← needs sign-off**
- Number porting timeline. **← needs Telnyx ticket**
