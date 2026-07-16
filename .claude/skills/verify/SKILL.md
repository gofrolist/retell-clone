---
name: verify
description: Build/launch/drive recipe for verifying Arhiteq changes locally (API + dashboard + worker resolution paths)
---

# Verifying Arhiteq changes locally

## Backing services
`docker compose up -d --wait` wants ports 5432/6379/7880. On this machine
6379 is usually taken by a personal `redis-local` container and
`retell-clone-postgres-1` is often already running — the API only needs
postgres, so skip compose if it fails on redis.

## API (control plane)
```bash
set -a; . ./.env; set +a          # root .env has DB URL + internal token
cd backend && uv run uvicorn arhiteq_api.main:app --port 8080
```
- Seed a workspace/API key/LLM/agent with a small script through
  `arhiteq_api.db` + models (commit the Workspace BEFORE dependent rows —
  the unit of work does not order these FKs correctly).
- Auth: `Authorization: Bearer <key_material>`; internal endpoints take
  `X-Internal-Token: $ARHITEQ_INTERNAL_TOKEN` (in root .env).
- Useful surfaces: `POST /v2/register-phone-call` (no dial),
  `POST /v2/create-web-call`, `GET /internal/calls/{id}/config`.

## Dashboard
```bash
cd frontend && NEXT_PUBLIC_API_KEY=<seeded key> bun run dev
```
- Port 3000 is usually occupied by an unrelated app → Next falls back to
  3001, which is NOT in the backend CORS allowlist. Restart the API with
  `ARHITEQ_CORS_ORIGINS='["http://localhost:3000","http://localhost:3001"]'`.
- `NEXT_PUBLIC_API_KEY` is the dev auth fallback — no login needed.
- Agent editor lives at `/agents/{agent_id}`.

## Worker
A live voice call needs LiveKit + SIP + provider keys — not driveable
locally. Closest end-to-end: fetch a real `GET /internal/calls/{id}/config`
payload from the running API and replay the worker's exact chain
(`CallConfig.from_dict` → `resolution_variables()` → `resolve_template`)
in `uv run --only-group dev python` inside `worker/`.
