# Arhiteq

Voice-AI phone agent platform, drop-in API-compatible with Retell AI.

- **backend/** — FastAPI control plane (`src/arhiteq_api/`): Retell-compatible
  `/v2` API, agents, phone numbers, webhooks, post-call analysis.
- **worker/** — LiveKit Agents voice worker (`src/arhiteq_worker/`): Cartesia
  STT/TTS, Gemini LLM, Telnyx SIP telephony, custom-function tool calls.
- **frontend/** — Arhiteq dashboard (Next.js, code in `src/`), a rebranded
  clone of the Retell dashboard.
- **infra/** — Terraform (GCP/GKE) + Helm (services, LiveKit,
  kube-prometheus-stack).
- **docs/** — [architecture](docs/ARCHITECTURE.md), Retell compatibility,
  migration runbook.

## Local development

Toolchain: Python 3.14 + [uv](https://docs.astral.sh/uv/) for the Python apps,
[bun](https://bun.sh/) for the frontend.

```bash
cd backend && uv sync && cd ..               # one-time: backend venv
cd worker && uv sync && cd ..                # one-time: worker venv
cd frontend && bun install && cd ..          # one-time: frontend deps
pre-commit install                           # one-time: git hooks

docker compose up -d postgres redis livekit  # deps
make api      # run FastAPI on :8080 (uvicorn arhiteq_api.main:app)
make worker   # run LiveKit agent worker (python -m arhiteq_worker.main)
make web      # run dashboard on :3000 (frontend/)
make test     # backend contract + unit tests
```

## Compatibility

The public API mirrors Retell (https://docs.retellai.com/api-references/overview)
so existing integrations switch by changing the base URL and API key. The
binding contract is `docs/ARCHITECTURE.md` §"Retell compatibility rules" and
the consumer spec in `usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md`;
it is enforced by `backend/tests/contract/`.
