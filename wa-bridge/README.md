# wa-bridge

Node.js + TypeScript WhatsApp bridge for skill-chatbot.

- Baileys multi-device session, persistent auth state under `auth_info/`.
- HTTP control surface (`/health`, `/status`, `/send`) for the orchestrator.
- Append-only `inbox.ndjson` for the orchestrator tailer; outbound queue under `queue/outbound.jsonl`.
- Bearer-token auth on the HTTP API; `zod`-validated env; `pino` JSON logs.

## Layout

```
wa-bridge/
├── package.json
├── tsconfig.json
├── vitest.config.ts
├── Makefile
├── .env.example
├── src/        # index, auth, socket, inbox, http, sender, log, env, image
├── bin/auth.ts # QR CLI entrypoint (`npm run auth`)
└── tests/      # vitest specs
```

## Dev loop

From the repo root: `make bridge-install` then `make bridge-dev`. From this dir: `make install` then `make dev`.

## Build

`make bridge-build` produces `dist/`; `make bridge-start` runs it.
