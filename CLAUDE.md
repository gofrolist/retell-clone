# Architeq — repo guide

Retell-AI-compatible voice agent platform (brand: **Architeq**). Monorepo:
`backend/` FastAPI control plane · `worker/` LiveKit voice worker ·
`frontend/` Next.js dashboard · `infra/` Terraform + Helm · `docs/` specs.

## The prime directive

Architeq is a **drop-in Retell replacement**. The wire contract (field names,
nesting, headers, signature format, status codes) is frozen by:
- `docs/RETELL_INTEGRATION_MAP.md` — what consumers actually call/read
- `~/gofrolist/usan-retirement-backend/VOICE_PROVIDER_MIGRATION_SPEC.md`
- `backend/tests/contract/` — executable version of the contract

Never "improve" the API shape. Extra fields are fine; renames/drops are not.
Contract hot spots: webhook signature `v={ms},d={hex hmac_sha256(rawBody+ts)}`
keyed by the workspace API key; tool calls send FLAT args (never `{"args":…}`)
with `X-Caller-Secret`; inbound webhook failures degrade to the DID's default
agent; `call_analysis` carries both `summary` and `call_summary`.

## Commands

- Backend: `cd backend && .venv/bin/python -m pytest tests/ -q` (create venv:
  `python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"` — Python 3.14 required)
- Frontend: `cd frontend && bun run build` (dev: `bun run dev`; bun is the package manager)
- Local stack: `docker compose up -d` then `make api` / `make worker` / `make web`

## Key docs

`docs/ARCHITECTURE.md` (system design), `docs/INTERNAL_API.md` (api⇄worker
contract), `docs/API_COVERAGE.md` (Retell endpoint matrix),
`docs/SECURITY.md` (auth model, SSRF/rate-limit/allowlists),
`docs/UI_INVENTORY.md` (dashboard spec from screenshots),
`docs/MIGRATION.md` (cutover runbook), `infra/README.md` (deploy).
