# deploy

1. `cp deploy/cmd-backend-beta.service ~/.config/systemd/user/`
2. `systemctl --user daemon-reload`
3. `systemctl --user enable --now cmd-backend-beta.service` (logs: `journalctl --user -u cmd-backend-beta -f`)
4. Serves on **:8891** — http://127.0.0.1:8891/admin/ , LAN 192.168.1.20, VPN 100.90.185.31.
5. Secrets/knobs come from `/home/rohith/CMD/backend_beta/.env` (optional; see `.env.example`).
