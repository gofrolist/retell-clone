# Retell API coverage matrix

Status of every resource group in https://docs.retellai.com/api-references/overview
as implemented by Arhiteq. "Full" = same path, method, status code, and field
names; extra fields may be present (allowed by the compatibility policy).

| Resource | Endpoints | Status |
|---|---|---|
| **Call** | `POST /v2/create-phone-call`, `GET /v2/get-call/{id}`, `POST /v2/list-calls`, `POST /v2/register-phone-call`, `POST /v2/create-web-call`, `PATCH /v2/update-call/{id}`, `DELETE /v2/delete-call/{id}`, `PUT /rerun-call-analysis/{id}` | Full |
| **Batch call** | `POST /create-batch-call` | Full (scheduled sends stored; scheduler TODO) |
| **Agent** | `POST /create-agent`, `GET /get-agent/{id}[?version=]`, `GET /list-agents`, `PATCH /update-agent/{id}[?version=]`, `DELETE /delete-agent/{id}` | Full |
| **Agent versions** | `GET /get-agent-versions/{id}`, `GET /get-agent-version/{id}/{version}` (Arhiteq extra), `POST /create-agent-version/{id}`, `POST /publish-agent-version/{id}`, `POST /publish-agent/{id}`, `DELETE /delete-agent-version/{id}/{version}` | Full history with drafts + immutable published snapshots; calls resolve the published version. One open draft per agent (Retell allows several) and no environment tags yet — see `docs/AGENT_VERSIONING.md` |
| **Retell LLM** | `POST /create-retell-llm`, `GET /get-retell-llm/{id}`, `GET /list-retell-llms`, `PATCH /update-retell-llm/{id}`, `DELETE /delete-retell-llm/{id}` | Full |
| **LLM tool types** (`general_tools` / `states[].tools`) | Stored verbatim for the whole Retell union. Executed by the worker: `end_call`, `transfer_call` (cold), `custom`, `press_digit` (DTMF), `check_availability_cal`, `book_appointment_cal`, `send_sms` (Telnyx, needs `TELNYX_API_KEY`), `extract_dynamic_variable`, `agent_swap` (prompt/tools/voice re-point mid-call), `kb_lookup` (lexical retrieval over the attached knowledge bases) | Persist-only for now: `code`, `mcp`, `bridge_transfer`, `cancel_transfer`, warm/agentic transfer options |
| **Conversation flow** | `POST /create-conversation-flow`, `GET /get-conversation-flow/{id}`, `GET /v2/list-conversation-flows`, `PATCH /update-conversation-flow/{id}`, `DELETE /delete-conversation-flow/{id}` | CRUD full and field-lossless, over both the fixture round-trip (real Retell captures in `backend/tests/fixtures/retell_flows/`) and the schema-derived field table in `tests/contract/test_conversation_flow.py` — the latter added because the fixtures only cover fields they happen to set, which is how `begin_after_user_silence_ms` came to be accepted and silently dropped. That field is now stored and returned, but no worker acts on it yet. The voice worker **executes** flow-backed agents on live calls: node types `conversation`, `subagent`, `branch`, `function`, `transfer_call`, `end`, `extract_dynamic_variables`, `press_digit` run; `agent_swap`, `sms`, `mcp`, `code`, `component`, `bridge_transfer`, `cancel_transfer` are rejected at call start — before the greeting, naming the offending node — rather than reached mid-call. A `function` node may reference any tool type, not just a URL-bearing `custom` one: a built-in (`end_call`, `transfer_call`, `press_digit`, `extract_dynamic_variable`, `check_availability_cal`, `book_appointment_cal`, `send_sms`, `agent_swap`) is built by the same dispatch a single-prompt agent uses and wrapped with the node's edge routing. All ten `Equation.operator` values (`== != > >= < <= contains not_contains exists not_exist`) evaluate; the docs' display spellings (`CONTAINS`, `NOT CONTAINS`, `not exists`) are accepted as synonyms. See `docs/ARCHITECTURE.md` for the execution model. **Dashboard-reachable**: the Conversation Flow editor (`frontend/src/components/flow/`, `docs/UI_INVENTORY.md` § Agent detail (Conversation Flow editor)) creates, edits, publishes and versions flows through these same endpoints plus `get-agent-version`/`get-agent-versions` — flows are no longer API-only. Flow agents can be exercised through Test Audio (below); the Simulation test-case suite itself still doesn't run flow cases |
| **Knowledge base** | `POST /create-knowledge-base`, `GET /get-knowledge-base/{id}`, `GET /list-knowledge-bases`, `DELETE /delete-knowledge-base/{id}`, `POST /add-knowledge-base-sources/{id}`, `DELETE /delete-knowledge-base-source/{id}/source/{source_id}` | CRUD full. Retrieval (`kb_lookup`) is lexical BM25 computed per lookup — no embeddings, no ingestion pipeline. Searchable: `text` sources and uploaded text/markdown/CSV files (markdown is split on headings, and a YAML `category:` becomes an exact match for the tool's `category` argument). Not yet searchable: PDF/Office uploads (need a parser dep), files over 2MB, and URL sources — all reported under `skipped_sources` with a reason |
| **Phone number** | `POST /create-phone-number`, `POST /import-phone-number`, `GET /get-phone-number/{num}`, `GET /list-phone-numbers`, `PATCH /update-phone-number/{num}`, `DELETE /delete-phone-number/{num}` | Full (create requires explicit number until Telnyx purchase API is wired) |
| **Voice** | `GET /list-voices`, `GET /get-voice/{id}` | Full (Cartesia catalog) |
| **Chat** | `POST /create-chat`, `GET /get-chat/{id}`, `GET /list-chat`, `POST /v3/list-chats`, `POST /create-chat-completion`, `PATCH /end-chat/{id}` | Full (completions via Gemini) |
| **Chat agent** | `POST /create-chat-agent`, `GET /get-chat-agent/{id}`, `GET /list-chat-agents`, `PATCH /update-chat-agent/{id}`, `DELETE /delete-chat-agent/{id}` | Full |
| **Concurrency** | `GET /get-concurrency` | Full (static limit of 20 until billing exists; `create-phone-call` returns 429 "Concurrency limit reached" when live calls — registered+ongoing — hit the limit) |
| **Webhooks (outbound)** | `call_started`, `call_ended`, `call_analyzed` + inbound `call_inbound` routing webhook | Full incl. `x-retell-signature` |
| **Simulation testing** | `POST /create-test-case-definition`, `GET /get-test-case-definition/{id}`, `PUT /update-test-case-definition/{id}`, `DELETE /delete-test-case-definition/{id}`, `GET /v2/list-test-case-definitions`, `POST /create-batch-test`, `GET /get-batch-test/{id}`, `GET /v2/list-batch-tests`, `GET /get-test-run/{id}`, `GET /v2/list-test-runs/{batch_id}` | Full for `retell-llm` engines (custom-LLM rejected, as in Retell; conversation-flow cases store and list but runs need flow execution) |

Dashboard-only endpoints (Arhiteq additions, `backend/src/arhiteq_api/api/dashboard.py`;
Retell serves these from its private dashboard API): `GET /analytics/calls`
(range/agent filters + `group_by` breakdowns), `GET /analytics/chats`,
`POST /analytics/call-insights` (Gemini insights over recent calls),
contacts CRUD (`/list-contacts`, `/create-contact`, `/update-contact/{id}`,
`/delete-contact/{id}`; incl. `custom_fields` — definitions live in workspace
settings `contact_field_definitions`), alerts CRUD (incl. `compare_to`),
QA-cohort CRUD (list computes success/transfer scores over a deterministic
30-day call sample), batch-call drafts (`/save-batch-call-draft`,
`/list-batch-call-drafts`, `/delete-batch-call-draft/{id}`), API-key management
(`/list-api-keys`, `/create-api-key`, `/revoke-api-key/{id}`),
`GET /list-webhook-deliveries`, `POST /test-workspace-webhook`,
`GET /system-status` (live component checks), `GET|PATCH /workspace`
(incl. `settings`: concurrency purchases/reservations/burst, CPS, token limit,
reliability toggles, billing email — get-concurrency and the call-creation 429
gates enforce these), `DELETE /workspace` (owner-gated full cascade).

Retell parity fields stored + served on agents (worker execution tracked per
field): `pii_config`, `fallback_voice_ids`, `allow_user_dtmf`,
`allow_dtmf_interruption`, `user_dtmf_options`, `opt_in_signed_url`,
`ivr_option`, `call_screening_option`, `timezone` (dashboard "Current Time
Awareness" — the zone un-suffixed `{{current_time}}`/`{{current_hour}}`/
`{{current_calendar}}` resolve in, on calls and chats alike; null keeps
Retell's `America/Los_Angeles`); on Retell LLMs: `mcps` (persist-only —
worker MCP execution pending, same status as the `mcp` tool type). Batch calls
dial within the workspace's outbound concurrency budget minus the batch's
`reserved_concurrency` (overflow tasks are paced by a background drainer as
slots free up); `call_time_window` is stored verbatim (scheduler TODO, as
with `trigger_timestamp`).

Known intentional deviations (all additive or dashboard-only):
- `call_analysis` carries **both** `summary` and `call_summary` (consumer compat).
- `POST /v2/create-web-call` returns an Arhiteq-extra field `livekit_server_url`
  (the browser-reachable LiveKit signalling URL to connect to; additive,
  contract-safe).
- Optional `agent_id` on create-agent / create-chat-agent (id-preserving import).
- `POST /generate-test-case-definitions` has no Retell equivalent: it drafts
  simulation cases (scenario + criteria + tool mocks) from the agent's own
  prompt and tool catalog. Test-case rows also carry `source`
  (`manual` | `generated`), batches carry `agent_id` (and
  `/v2/list-batch-tests` takes an optional `agent_id` filter, since several
  agents can share one LLM), and runs carry `metric_results` (per-criterion
  verdicts behind `result_explanation`) — all additive. Test cases carry
  `llm_model` (Retell's per-case "LLM Setting"): the model that plays the
  agent for that case, `null` for the agent's own. A realtime-audio model
  cannot generate text, so an agent on one is simulated on
  `ARHITEQ_SIMULATION_AGENT_MODEL` instead (unset: `analysis_model`, the
  long-standing behaviour). Which model plays the agent changes individual
  verdicts — read-back criteria are the clearest split — so it is a knob, not
  a default worth moving blindly. `create-batch-test`
  additionally 429s past 3 batches running concurrently per workspace: the
  background work is many model round-trips per case and request-level rate
  limiting never sees it. A run **follows an `agent_swap`** rather than ending
  on it, so a router + specialists agent set is graded as the one call it is:
  the destination's prompt, tools and knowledge bases take over mid-transcript
  (its timezone too), and the conversation so far carries across, exactly as
  the worker does it. Destinations are resolved to their published version and
  scoped to the workspace, and the chain is followed transitively; one that
  cannot load (unknown, another workspace's, or without an LLM) returns an
  error to the model and leaves the run on the current agent, which is what a
  live call does.
- The dashboard's Test Audio panel now works for conversation-flow agents too
  (it no longer requires a Retell LLM) — Run Test places a real LiveKit web
  call against the flow the same way Test Audio does for a single-prompt
  agent. Test LLM (text chat against a saved prompt) is still single-prompt
  only, since a flow has no one prompt to chat against.
- Auth additionally accepts Arhiteq dashboard session JWTs (Google Sign-In).
- Not implemented (no consumer, dashboard-only in Retell): SIP-trunk
  self-serve endpoints, phone-number A/B tests, Retell billing endpoints.

Enforced by `backend/tests/contract/` (194 tests, 90% line coverage, CI gate
at 80%).
