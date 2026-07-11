# Architeq local development.
#   make up      start postgres/redis/livekit (docker compose)
#   make api     run the FastAPI control plane with reload
#   make worker  run the LiveKit Agents worker in dev mode
#   make web     run the Next.js dashboard
#   make test    backend test suite
#   make down    stop the compose stack

.PHONY: up down api worker web test logs ps

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

api:
	cd backend && uvicorn app.main:app --reload --port 8080

worker:
	cd worker && python -m worker.main dev

web:
	cd frontend && bun run dev

test:
	cd backend && pytest
