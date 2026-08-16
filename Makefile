.DEFAULT_GOAL := help

.PHONY: help setup ui fake cli test lint build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install python deps, frontend deps, build frontend
	uv sync
	cd web && npm install && npm run build

ui: ## Run the web UI against your instance (needs .env)
	@test -f .env || { echo "No .env found — copy .env.example to .env and fill in your values first."; exit 1; }
	uv run cross-user-dedup-ui $(ARGS)

fake: ## Run the web UI against fake seeded data (no .env needed)
	uv run python scripts/serve_fake.py $(ARGS)

cli: ## Run the CLI dry-run report (ARGS='--apply' to apply)
	@test -f .env || { echo "No .env found — copy .env.example to .env and fill in your values first."; exit 1; }
	uv run cross-user-dedup $(ARGS)

test: ## Run python tests
	uv run pytest

lint: ## Lint python and frontend
	uv run ruff check .
	cd web && npm run lint

build: ## Rebuild the frontend (commit web/dist afterwards)
	cd web && npm run build

clean: ## Remove caches (keeps web/dist and .venv)
	rm -rf .pytest_cache .ruff_cache src/*.egg-info
