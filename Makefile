.PHONY: help bridge-install bridge-dev bridge-build bridge-start bridge-test bridge-lint bridge-auth \
        orch-venv orch-install orch-dev orch-test orch-lint \
        smoke smoke-live ingest-rules ingest-file \
        install-svc uninstall-svc restart status logs \
        snapshot-upstream issues clean

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bridge-install: ## wa-bridge: npm ci
	cd wa-bridge && npm ci

bridge-dev: ## wa-bridge: tsx watch src/index.ts
	cd wa-bridge && npm run dev

bridge-build: ## wa-bridge: tsc -> dist/
	cd wa-bridge && npm run build

bridge-start: ## wa-bridge: node dist/index.js
	cd wa-bridge && node dist/index.js

bridge-test: ## wa-bridge: vitest run
	cd wa-bridge && npm test

bridge-lint: ## wa-bridge: eslint
	cd wa-bridge && npm run lint

bridge-auth: ## wa-bridge: QR CLI (npm run auth)
	cd wa-bridge && npm run auth

orch-venv: ## orchestrator: python3.11 -m venv .venv
	cd orchestrator && python3.11 -m venv .venv

orch-install: ## orchestrator: pip install -e '.[dev]'
	cd orchestrator && . .venv/bin/activate && pip install -U pip && pip install -e '.[dev]'

orch-dev: ## orchestrator: python -m src.main
	cd orchestrator && . .venv/bin/activate && python -m src.main

orch-test: ## orchestrator: pytest -q
	cd orchestrator && . .venv/bin/activate && pytest -q

orch-lint: ## orchestrator: ruff check + format --check
	cd orchestrator && . .venv/bin/activate && ruff check src tests scripts && ruff format --check src tests scripts

smoke: ## NDJSON replay smoke (orchestrator)
	cd orchestrator && . .venv/bin/activate && python scripts/smoke.py

smoke-live: ## live smoke (sets WA_SMOKE_PHONE=)
	cd orchestrator && . .venv/bin/activate && WA_SMOKE_PHONE=$${WA_SMOKE_PHONE:?set WA_SMOKE_PHONE} python scripts/smoke.py --live

ingest-rules: ## ingest booking_rules.yaml into Qdrant
	cd orchestrator && . .venv/bin/activate && python scripts/ingest_rules.py

ingest-file: ## ingest a file (FILE=path): python scripts/ingest_file.py $$FILE
	cd orchestrator && . .venv/bin/activate && python scripts/ingest_file.py $(FILE)

install-svc: ## install systemd --user units
	bash scripts/install-systemd.sh

uninstall-svc: ## remove systemd --user units
	bash scripts/install-systemd.sh --remove

restart: ## systemctl --user restart both daemons
	systemctl --user restart skill-chatbot-bridge skill-chatbot-orchestrator

status: ## systemctl --user status both daemons
	systemctl --user status skill-chatbot-bridge skill-chatbot-orchestrator --no-pager

logs: ## journalctl --user -f for both units
	journalctl --user -u skill-chatbot-bridge -u skill-chatbot-orchestrator -f

snapshot-upstream: ## refresh references/upstream/ snapshots
	bash scripts/snapshot-upstream.sh

issues: ## list GitHub issues labelled phase:0-bootstrap
	gh issue list --repo A-I-M-S/skill-chatbot --label phase:0-bootstrap

clean: ## remove build / venv / node_modules / .pytest_cache artifacts
	rm -rf wa-bridge/dist wa-bridge/node_modules \
	       orchestrator/.venv orchestrator/build orchestrator/*.egg-info \
	       orchestrator/.pytest_cache orchestrator/.ruff_cache orchestrator/.mypy_cache
