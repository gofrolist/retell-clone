# Architeq

Voice-AI phone agent platform, drop-in API-compatible with Retell AI.

- **backend/** — FastAPI control plane: Retell-compatible `/v2` API, agents,
  phone numbers, webhooks, post-call analysis.
- **worker/** — LiveKit Agents voice worker: Cartesia STT/TTS, Gemini LLM,
  Telnyx SIP telephony, custom-function tool calls.
- **frontend/** — Architeq dashboard (Next.js), a rebranded clone of the
  Retell dashboard.
- **infra/** — Terraform (GCP/GKE) + Helm (services, LiveKit,
  kube-prometheus-stack).
- **docs/** — [architecture](docs/ARCHITECTURE.md), Retell compatibility,
  migration runbook.

## Local development

```bash
docker compose up -d postgres redis livekit   # deps
make api      # run FastAPI on :8080 (backend/)
make worker   # run LiveKit agent worker (worker/)
make web      # run dashboard on :3000 (frontend/)
make test     # backend contract + unit tests
```

## Compatibility

The public API mirrors Retell (https://docs.retellai.com/api-references/overview)
so existing integrations switch by changing the base URL and API key. The
binding contract is `docs/ARCHITECTURE.md` §"Retell compatibility rules" and
the consumer spec in `usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md`;
it is enforced by `backend/tests/contract/`.
