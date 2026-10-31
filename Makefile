.DEFAULT_GOAL := help
TRIP ?= trip.yaml

.PHONY: help install hooks format lint typecheck test test-live test-live-sources check dry-run run refresh site publish passphrase migrate migration clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and the git hooks
	uv sync
	uv run pre-commit install

hooks: ## Run every pre-commit hook on all files
	uv run pre-commit run --all-files

format: ## Format the code with ruff
	uv run ruff format .

lint: ## Lint with ruff and apply safe fixes
	uv run ruff check --fix .

typecheck: ## Static type check with mypy
	uv run mypy

test: ## Run the tests (live tests are skipped)
	uv run pytest -q

test-live: ## Run the live smoke test (spends a few cents)
	uv run pytest -m live tests/test_live.py -s

test-live-sources: ## Cross-check the cheap sources against the reference actors (spends a few cents)
	uv run pytest -m live tests/test_live_sources.py -s

check: ## Everything CI would verify, without changing files
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest -q

dry-run: ## Show what a run would fetch and cost (TRIP=trip.yaml)
	uv run casa-certa run $(TRIP) --dry-run

run: ## Analyze the houses and write the spreadsheet (TRIP=trip.yaml)
	uv run casa-certa run $(TRIP)

refresh: ## Re-fetch everything from Airbnb, costs money (TRIP=trip.yaml)
	uv run casa-certa run $(TRIP) --refresh

site: ## Build the encrypted site from the stored data, no network (TRIP=trip.yaml)
	uv run casa-certa site $(TRIP)

publish: ## Publish the built site to the gh-pages branch of origin
	uv run casa-certa publish

passphrase: ## Print a new random passphrase for SITE_PASSPHRASE
	uv run casa-certa passphrase

migrate: ## Apply database migrations
	uv run alembic upgrade head

migration: ## Create a migration: make migration m="add a column"
	$(if $(m),,$(error usage: make migration m="message"))
	uv run alembic revision --autogenerate -m "$(m)"

clean: ## Remove caches
	rm -rf .mypy_cache .pytest_cache .ruff_cache
	find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +
