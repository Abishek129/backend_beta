# deploy

The persistent user service runs Daphne from this checkout on port **18889**.

```bash
systemctl --user link /home/rohithdev/EMSbackend/deploy/cmd-backend-beta.service
systemctl --user daemon-reload
systemctl --user enable --now cmd-backend-beta.service
systemctl --user status cmd-backend-beta.service
journalctl --user -u cmd-backend-beta.service -f
```

User lingering must be enabled so the user manager starts at boot even without a login:
`loginctl show-user rohithdev -p Linger` should print `Linger=yes` (already enabled on this host).

- Admin: `http://10.80.200.2:18889/admin/`
- EMS tree: `http://10.80.200.2:18889/api/ems/`
- Runtime settings and secrets come from `/home/rohithdev/EMSbackend/.env` (loaded by Django).
- If the virtual environment or checkout is moved, update `WorkingDirectory` and `ExecStart`
  in the unit, then run `systemctl --user daemon-reload` and restart the service.
