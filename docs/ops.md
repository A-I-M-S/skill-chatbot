# Ops

## systemd units (user-level)

`~/.config/systemd/user/skill-chatbot-bridge.service`

```ini
[Unit]
Description=skill-chatbot wa-bridge
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/.openclaw/workspace/dev/projects/skill-chatbot/wa-bridge
EnvironmentFile=/root/.openclaw/workspace/dev/projects/skill-chatbot/.env
ExecStart=/usr/bin/node dist/index.js
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/skill-chatbot/bridge.log
StandardError=append:/var/log/skill-chatbot/bridge.log

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/skill-chatbot-orchestrator.service`

```ini
[Unit]
Description=skill-chatbot orchestrator
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/.openclaw/workspace/dev/projects/skill-chatbot/orchestrator
EnvironmentFile=/root/.openclaw/workspace/dev/projects/skill-chatbot/.env
ExecStart=/root/.openclaw/workspace/dev/projects/skill-chatbot/orchestrator/.venv/bin/python -m src.main
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/skill-chatbot/orchestrator.log
StandardError=append:/var/log/skill-chatbot/orchestrator.log

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now skill-chatbot-bridge skill-chatbot-orchestrator
loginctl enable-linger $USER   # survive logout
```

## Re-auth (Baileys session dropped)

```bash
systemctl --user stop skill-chatbot-bridge
cd /root/.openclaw/workspace/dev/projects/skill-chatbot/wa-bridge
npm run auth    # prints QR to stdout
# scan from WhatsApp → Linked Devices → Link a Device
systemctl --user start skill-chatbot-bridge
```

## Smoke test

```bash
# Replay recorded NDJSON (no real WA traffic)
python3 orchestrator/scripts/smoke.py

# End-to-end (creates real bookings in the Outlook calendar — use the test account only)
python3 orchestrator/scripts/smoke.py --live
```

## Logs

```bash
journalctl --user -u skill-chatbot-bridge -f
journalctl --user -u skill-chatbot-orchestrator -f
tail -f /var/log/skill-chatbot/*.log
```

## Backups

- `state.sqlite` — daily, retained 30d. Restore by `cp` + `chmod 600`.
- `auth_info/` — never back up off-host. Treat as a credential.
- `inbox.ndjson` — optional, useful for replay. Rotate weekly.
