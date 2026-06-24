# Build task — Issue #1 (Phase 0: Project layout + build tooling)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md` — read it end-to-end before touching anything. The GitHub issue is **#2** (the title prefix is #1, the GitHub number is offset by one because of a duplicate closed during bootstrap).

## What to build

The Phase 0 scaffolding only. No app code, no business logic. Every file you create is plumbing: package manifests, build config, lint config, Makefiles, root .editorconfig + .gitattributes, and a one-time README update to replace the current "Quick start" section with the new dev loop.

## Acceptance criteria (verbatim from issue #2)

- [ ] `wa-bridge/package.json` with deps: `typescript`, `tsx`, `vitest`, `pino`, `dotenv`, `@whiskeysockets/baileys`, `express`, `zod`
- [ ] `wa-bridge/tsconfig.json` (strict, ES2022, NodeNext modules)
- [ ] `wa-bridge/Makefile` targets: `install`, `dev`, `build`, `test`, `lint`
- [ ] `orchestrator/pyproject.toml` (Python 3.11, hatchling) with deps: `openai`, `fastembed`, `qdrant-client`, `python-dotenv`, `pypdf`, `httpx`, `pydantic` (+ dev deps: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `python-json-logger`, `types-python-dateutil`)
- [ ] `orchestrator/Makefile` targets: `venv`, `install`, `dev`, `test`, `lint`
- [ ] `Makefile` at repo root with: `bridge-dev`, `bridge-build`, `orch-dev`, `orch-test`, `smoke`, `logs`
- [ ] `.editorconfig`, root `.gitattributes`
- [ ] `README.md` updated — replace the "Quick start" section with the new dev loop that uses the Makefiles

## Inputs you must respect (from `docs/plans/phase-0-bootstrap.md`)

- Use the **locked tech choices table** in §2 for every version pin.
- Use the **Makefile target catalogue** in §3 as the contract. Every target in the catalogue must exist; nothing extra unless it serves the same purpose.
- Cross-check the **project layout in §1** — every file you create must be at a path listed there. If you need to add a new file, surface it in your final summary so the plan doc can be updated.

## Hard constraints

- Do **not** write any application code. No `src/index.ts`, no `src/main.py`, no `flows/`, no `prompts/`. Those are issues #2–#15.
- Do **not** commit secrets, real creds, or `auth_info/`.
- Do **not** create files outside the paths listed in §1 of the plan (plus the two root files `.editorconfig` and `.gitattributes`).
- Do **not** open PRs, do **not** edit the issue body, do **not** create new issues.
- Do **not** run `npm install` or `pip install` — they require network and the gates will be exercised later. The package manifests and lockfiles are enough for now. If `package-lock.json` is requested, do `npm install --package-lock-only --no-audit --no-fund` (still network). If that fails too, commit just the manifests and leave a `package-lock.json` placeholder with a comment.
- Do **not** add `node_modules/` or `.venv/` to git — already covered by `.gitignore`, just confirm.
- **Commit** the changes when done (single commit, message: `chore(bootstrap): phase 0 scaffolding — package manifests, build config, Makefiles, lint, editorconfig`).
- **Do not push** — I'll push from the orchestrator side.

## When you're done

Print to stdout:
1. The full `git status` of the repo.
2. The exact contents of the new root `Makefile` (so I can sanity-check it).
3. The full `wa-bridge/package.json` (for version pin review).
4. The full `orchestrator/pyproject.toml` (for version pin review).
5. A one-paragraph summary of: which Makefile targets you added, anything you deviated from the plan and why, and the SHA of the commit.
