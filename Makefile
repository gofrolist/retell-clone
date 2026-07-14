# Architeq local development.
#   make up      start postgres/redis/livekit (docker compose)
#   make dev     start the compose stack + api/worker/web together
#   make api     run the FastAPI control plane with reload
#   make worker  run the LiveKit Agents worker in dev mode
#   make web     run the Next.js dashboard
#   make test    backend test suite
#   make down    stop the compose stack

.PHONY: up down dev api worker web test logs ps

# Root .env (gitignored, template: .env.example) supplies LiveKit dev creds,
# the shared internal token, and provider API keys for local runs.
LOAD_ENV = set -a; [ -f .env ] && . ./.env; set +a

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

# Everything at once: backing services, then the three app processes in
# parallel (interleaved logs; Ctrl-C stops all three, containers keep running).
dev: up
	$(MAKE) -j3 api worker web

api:
	$(LOAD_ENV); cd backend && uv run uvicorn architeq_api.main:app --reload --port 8080

worker:
	$(LOAD_ENV); cd worker && uv run python -m architeq_worker.main dev

web:
	cd frontend && bun run dev

test:
	cd backend && uv run pytest
