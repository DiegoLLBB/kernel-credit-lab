.PHONY: up down logs test lint smoke migrate revision seed

up:            ## Build and start the full stack
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker

test:          ## Run backend tests (incl. integration against the running stack)
	docker compose exec -e KERNEL_INTEGRATION=1 api pytest -q

lint:
	docker compose exec api ruff check .
	docker compose exec api ruff format --check .

smoke:         ## Ping every configured model; IMAGE=path/to/page.png to test SAM 3.1
	docker compose exec api python -m scripts.smoke_models $(if $(IMAGE),--image $(IMAGE),)

migrate:
	docker compose exec api alembic upgrade head

revision:      ## make revision m="add bonds"
	docker compose exec api alembic revision --autogenerate -m "$(m)"

seed:          ## Load the Dec 2025 snapshot (issuers, watchlist, instruments, curated sources)
	docker compose exec api python -m scripts.load_seed seed/snapshot_2025-12-31.json
