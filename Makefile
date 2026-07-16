# Arhiteq local development.
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
	$(LOAD_ENV); cd backend && uv run uvicorn arhiteq_api.main:app --reload --port 8080

worker:
	$(LOAD_ENV); cd worker && uv run python -m arhiteq_worker.main dev

web:
	# Cap the dev-server V8 heap: Next 16.2.10's dev RSC flight client leaks
	# ~3MB/request (9M+ retained {weak,response} wrappers), so an uncapped heap
	# climbs to ~12GB and freezes the machine in multi-second mark-compacts.
	# Capping turns that slow death into a fast, obvious crash you just restart.
	cd frontend && NODE_OPTIONS=--max-old-space-size=4096 bun run dev

test:
	cd backend && uv run pytest
