# Retell API coverage matrix

Status of every resource group in https://docs.retellai.com/api-references/overview
as implemented by Architeq. "Full" = same path, method, status code, and field
names; extra fields may be present (allowed by the compatibility policy).

| Resource | Endpoints | Status |
|---|---|---|
| **Call** | `POST /v2/create-phone-call`, `GET /v2/get-call/{id}`, `POST /v2/list-calls`, `POST /v2/register-phone-call`, `POST /v2/create-web-call`, `PATCH /v2/update-call/{id}`, `DELETE /v2/delete-call/{id}`, `PUT /rerun-call-analysis/{id}` | Full |
| **Batch call** | `POST /create-batch-call` | Full (scheduled sends stored; scheduler TODO) |
| **Agent** | `POST /create-agent`, `GET /get-agent/{id}`, `GET /list-agents`, `PATCH /update-agent/{id}`, `DELETE /delete-agent/{id}`, `GET /get-agent-versions/{id}`, `POST /publish-agent/{id}` | Full (single live version per agent; no history table) |
| **Retell LLM** | `POST /create-retell-llm`, `GET /get-retell-llm/{id}`, `GET /list-retell-llms`, `PATCH /update-retell-llm/{id}`, `DELETE /delete-retell-llm/{id}` | Full |
| **Conversation flow** | `POST /create-conversation-flow`, `GET /get-conversation-flow/{id}`, `GET /v2/list-conversation-flows`, `PATCH /update-conversation-flow/{id}`, `DELETE /delete-conversation-flow/{id}` | CRUD full; flow *execution* by the voice worker is single-prompt only for now |
| **Knowledge base** | `POST /create-knowledge-base`, `GET /get-knowledge-base/{id}`, `GET /list-knowledge-bases`, `DELETE /delete-knowledge-base/{id}`, `POST /add-knowledge-base-sources/{id}`, `DELETE /delete-knowledge-base-source/{id}/source/{source_id}` | CRUD full; retrieval/embedding pipeline TODO (kb_lookup tool pending) |
| **Phone number** | `POST /create-phone-number`, `POST /import-phone-number`, `GET /get-phone-number/{num}`, `GET /list-phone-numbers`, `PATCH /update-phone-number/{num}`, `DELETE /delete-phone-number/{num}` | Full (create requires explicit number until Telnyx purchase API is wired) |
| **Voice** | `GET /list-voices`, `GET /get-voice/{id}` | Full (Cartesia catalog) |
| **Chat** | `POST /create-chat`, `GET /get-chat/{id}`, `GET /list-chat`, `POST /v3/list-chats`, `POST /create-chat-completion`, `PATCH /end-chat/{id}` | Full (completions via Gemini) |
| **Chat agent** | `POST /create-chat-agent`, `GET /get-chat-agent/{id}`, `GET /list-chat-agents`, `PATCH /update-chat-agent/{id}`, `DELETE /delete-chat-agent/{id}` | Full |
| **Concurrency** | `GET /get-concurrency` | Full (static limits until billing exists) |
| **Webhooks (outbound)** | `call_started`, `call_ended`, `call_analyzed` + inbound `call_inbound` routing webhook | Full incl. `x-retell-signature` |

Dashboard-only endpoints (Architeq additions, `backend/src/architeq_api/api/dashboard.py`;
Retell serves these from its private dashboard API): `GET /analytics/calls`,
contacts CRUD (`/list-contacts`, `/create-contact`, `/update-contact/{id}`,
`/delete-contact/{id}`), alerts CRUD, QA-cohort CRUD, API-key management
(`/list-api-keys`, `/create-api-key`, `/revoke-api-key/{id}`),
`GET /list-webhook-deliveries`, `GET|PATCH /workspace`.

Known intentional deviations (all additive or dashboard-only):
- `call_analysis` carries **both** `summary` and `call_summary` (consumer compat).
- Optional `agent_id` on create-agent / create-chat-agent (id-preserving import).
- Auth additionally accepts Architeq dashboard session JWTs (Google Sign-In).
- Not implemented (no consumer, dashboard-only in Retell): SIP-trunk
  self-serve endpoints, phone-number A/B tests, Retell billing endpoints.

Enforced by `backend/tests/contract/` (111 tests, 89% line coverage, CI gate
at 80%).
