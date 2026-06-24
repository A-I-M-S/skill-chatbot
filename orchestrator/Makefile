.PHONY: venv install dev test test-cov lint format smoke smoke-live ingest-rules ingest-file clean

venv: ## python3.11 -m venv .venv
	python3.11 -m venv .venv

install: ## pip install -e '.[dev]'
	. .venv/bin/activate && pip install -U pip && pip install -e '.[dev]'

dev: ## python -m src.main
	. .venv/bin/activate && python -m src.main

test: ## pytest -q
	. .venv/bin/activate && pytest -q

test-cov: ## pytest --cov=src --cov-report=term-missing
	. .venv/bin/activate && pytest --cov=src --cov-report=term-missing

lint: ## ruff check + format --check
	. .venv/bin/activate && ruff check src tests scripts && ruff format --check src tests scripts

format: ## ruff check --fix + ruff format
	. .venv/bin/activate && ruff check --fix src tests scripts && ruff format src tests scripts

smoke: ## scripts/smoke.py (NDJSON replay)
	. .venv/bin/activate && python scripts/smoke.py

smoke-live: ## scripts/smoke.py --live (requires WA_SMOKE_PHONE)
	. .venv/bin/activate && WA_SMOKE_PHONE=$${WA_SMOKE_PHONE:?set WA_SMOKE_PHONE} python scripts/smoke.py --live

ingest-rules: ## scripts/ingest_rules.py -> Qdrant
	. .venv/bin/activate && python scripts/ingest_rules.py

ingest-file: ## scripts/ingest_file.py <FILE>
	. .venv/bin/activate && python scripts/ingest_file.py

clean: ## remove .venv, build, caches, egg-info
	rm -rf .venv build dist .pytest_cache .ruff_cache .mypy_cache *.egg-info
