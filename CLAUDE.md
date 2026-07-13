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

## Layout

App code lives under `src/` in every app. Python apps use Python 3.14, uv
(uv.lock, PEP 735 `[dependency-groups]`), and the `uv_build` backend; package
names match project names:
- `backend/src/architeq_api/` — serve with `uvicorn architeq_api.main:app`
- `worker/src/architeq_worker/` — run with `python -m architeq_worker.main`
- `frontend/src/{app,components,lib}` — `@/*` resolves to `./src/*`; bun is
  the package manager

## Commands

- Backend tests: `cd backend && uv run pytest` (first time: `uv sync`)
- Worker tests: `cd worker && uv run --only-group dev pytest` (dev group only —
  skips the heavy livekit-agents stack; pytest `pythonpath=["src"]` makes the
  package importable without installing it)
- Frontend: `cd frontend && bun run build` (dev: `bun run dev`)
- Local stack: `docker compose up -d` then `make api` / `make worker` / `make web`
- pre-commit hooks (gitleaks, ruff check+format, pytest, eslint) run on commit;
  full sweep: `pre-commit run --all-files`
- Releases: PR titles must be conventional commits (`pr-title` check);
  merging release-please's release PR tags + deploys everything; never bump
  image tags by hand (see `infra/README.md` § Releasing)

## Key docs

`docs/ARCHITECTURE.md` (system design), `docs/INTERNAL_API.md` (api⇄worker
contract), `docs/API_COVERAGE.md` (Retell endpoint matrix),
`docs/SECURITY.md` (auth model, SSRF/rate-limit/allowlists),
`docs/UI_INVENTORY.md` (dashboard spec from screenshots),
`docs/MIGRATION.md` (cutover runbook), `infra/README.md` (deploy).
