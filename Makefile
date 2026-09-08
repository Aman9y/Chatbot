# Convenience targets. On Windows use Git Bash, or run the underlying commands directly.
PY ?= .venv/Scripts/python.exe
ifeq (,$(wildcard $(PY)))
PY := python
endif

.PHONY: help install infra infra-down migrate revision run test lint import replay config

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

install: ## Create venv deps
	$(PY) -m pip install -e ".[dev]"

infra: ## Start Postgres + Redis
	docker compose up -d db redis

infra-down: ## Stop Postgres + Redis
	docker compose down

migrate: ## Apply DB migrations
	$(PY) -m alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add x"
	$(PY) -m alembic revision --autogenerate -m "$(m)"

run: ## Run the API with reload
	$(PY) -m uvicorn app.main:app --reload --port 8000

test: ## Run the test suite
	$(PY) -m pytest -q

lint: ## Ruff lint
	$(PY) -m ruff check app tests

import: ## Import leads: make import f=path/to/leads.csv
	$(PY) -m app.cli import-leads "$(f)" --source legacy_db

replay: ## Replay a webhook fixture: make replay f=tests/fixtures/webhook_inbound_text.json
	$(PY) -m app.cli replay-webhook "$(f)"

config: ## Show resolved config and unresolved Phase-1 items
	$(PY) -m app.cli check-config

sweeps: ## Run all scheduler sweeps once
	$(PY) -m app.cli run-sweeps

worker: ## Run the Celery worker
	$(PY) -m celery -A app.scheduler.celery_app worker --loglevel=info

beat: ## Run Celery beat (fires the sweeps on a schedule)
	$(PY) -m celery -A app.scheduler.celery_app beat --loglevel=info
