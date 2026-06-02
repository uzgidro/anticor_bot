.PHONY: install lint test migration migrate run up down logs

install:
	python -m venv .venv && .venv/bin/pip install -e ".[dev]"

lint:
	ruff check bot tests

test:
	pytest -q

# Generate a new migration: make migration m="add X"
migration:
	alembic revision --autogenerate -m "$(m)"

migrate:
	alembic upgrade head

# Run the bot locally (polling) — requires a populated .env.
run:
	python -m bot

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f bot
