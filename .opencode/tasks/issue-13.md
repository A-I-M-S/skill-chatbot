# Build task — Issue #13 (Phase 8b: systemd units + log rotation)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md`. Issues #1, #2, #3, #15 are landed. The GitHub issue is **#14** (the title prefix is #13, GitHub number is offset).

## ⚠️ PR-per-issue workflow

1. From `main`, create a branch: `git switch -c feat/13-systemd`
2. Do all your work on that branch.
3. Commit: `git commit -m "chore(svc): systemd --user units + logrotate + ops (#13)"`
4. Push: `git push -u origin feat/13-systemd`
5. Open PR: `gh pr create --base main --head feat/13-systemd --title "chore(svc): systemd --user units + logrotate + ops (#13)" --body "Closes #14 — see .opencode/tasks/issue-13.md for the brief." --label "phase:8-hardening" --label "parallel-safe"`
6. **Do not merge.** Print PR URL.

## What to build

Turn the two dev commands into managed services that survive reboots and don't fill the disk.

## Acceptance criteria (verbatim from issue #14)

- [ ] `scripts/install-systemd.sh` writes the two unit files from `docs/ops.md` to `~/.config/systemd/user/` and runs `daemon-reload` + `enable --now`
- [ ] `/etc/logrotate.d/skill-chatbot` rotates both log files weekly, retains 12, compresses, copies without truncating
- [ ] `Makefile` targets: `install-svc`, `uninstall-svc`, `restart`, `status` (already in #1 — verify, add `uninstall-svc` if missing)
- [ ] SKILL.md: `status` subcommand shape updated to match what the scripts actually print
- [ ] ops.md: a 'first-time setup' section (clone → env → install → auth → enable) and a 'common incidents' section (session lost, OOM, log fill, swap SIM)

## Inputs you must respect

- Plan §1, §2, §3, and the existing `docs/ops.md` content (extend, don't replace).
- The existing `Makefile` from #1 — extend, don't replace.
- The existing `SKILL.md` — extend the status section.

## Hard constraints

- **Do not modify the source code under `wa-bridge/src/` or `orchestrator/src/`.**
- **Do not run `systemctl --user enable --now`** in tests (would persist on the box).
- **The `install-systemd.sh` script should be idempotent** — re-runs should not error.
- **The `uninstall-svc` target must work** — the script must support `--remove`.
- **The logrotate file must be valid syntax** — `logrotate -d /etc/logrotate.d/skill-chatbot` should pass (run with `--force` only if needed for the test).
- **Do not commit secrets.**
- **Do not merge the PR.**
- **Work on the feature branch only.**

## When you're done

Print to stdout:
1. The PR URL.
2. The full `git status` of the repo.
3. The list of new/modified files.
4. The `git log --oneline -3`.
5. The test outputs: `bash -n scripts/install-systemd.sh` (syntax check) and `logrotate -d scripts/logrotate.conf 2>&1` (dry-run).
6. A one-paragraph summary.
