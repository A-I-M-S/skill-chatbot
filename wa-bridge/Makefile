.PHONY: install dev build start test test-watch lint auth clean

install: ## npm ci
	npm ci

dev: ## tsx watch src/index.ts
	npm run dev

build: ## tsc -p tsconfig.json -> dist/
	npm run build

start: ## node dist/index.js
	npm start

test: ## vitest run
	npm test

test-watch: ## vitest (watch)
	npm run test:watch

lint: ## eslint . --max-warnings=0
	npm run lint

auth: ## tsx bin/auth.ts (QR CLI)
	npm run auth

clean: ## remove dist + node_modules
	rm -rf dist node_modules
