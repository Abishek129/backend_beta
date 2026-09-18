# backend_beta

A fresh CMD backend whose plant model is **imported from LoggerFast** instead of hand-curated.
LoggerFast owns the structure (group tree, memberships + roles, table bindings, device edges);
beta owns the screens and the nameplate data the logger has no concept of. Nothing is migrated
from `backend2` — own DB, own port, own admin.

The socket namespace keeps backend2's soul: one id space `ws/mfm/<id>/<screen>/`, dispatched by
equipment type. `Group.id` (< 1,000,000) = a distribution panel; `FEEDER_ID_OFFSET + MFM.id` = a
single meter. **No screens are registered yet** — consumers are pending discussion, so every
`ws/mfm/...` connect answers with an `error` frame (code 4404) and closes 4404.

## Apps

| app | what it holds |
|---|---|
| `core` | the imported hierarchy (`DataSource`, `Group`, `MFM`, `Membership`, `FeedEdge`, `PanelConfig`, `MfmConfig`, `RtmThreshold`), the LoggerFast client + importer, the nav-category axis (`nav_categories`), nav/config/status REST, and the model-agnostic query layer copied from backend2 (`services`, `wire`, `derive`, `ai_frame`, `ai_client`, `widget_frame`). |

## Nav categories

The sidebar is a **fixed two-level tree**: eight categories (HT, AMF, PCC, APFC, UPS, Utility,
Production, HVAC), each always emitted, holding panels and loose meters. Two orthogonal fields
place a row — `PanelConfig.panel_kind` for HT/AMF/PCC/UPS, `Group|MFM.nav_category` for the
other four. Both are seeded from the row's name by the importer and correctable in the admin;
a hand-edited value is pinned and never reseeded. `manage.py import_logger_hierarchy --dry`
prints a per-category census plus the uncategorised and low-confidence rows — **read it before
trusting the heuristic**, since it is guessing at a naming convention we do not control. See
`NAV_CONTRACT.md` §1.1.
| `panels` | EMS sockets: `dispatch.py` (id → type), `registry.py` (`screen(...)` declares a screen), `routing.py` (routes generated from the registry). `consumers/` is empty. |
| `assets` | DG / UPS / transformer asset meters (`ws/asset/*`). Structure only — no models, no consumers. |
| `bms` | Building-management meters (`ws/meter/*`). Structure only — no models, no consumers. |

## Run

```bash
PY=/home/rohith/.pyenv/versions/3.11.9/bin/python
$PY manage.py migrate
$PY manage.py import_logger_hierarchy --dry     # then without --dry
$PY -m daphne -b 0.0.0.0 -p 8891 config.asgi:application
$PY manage.py test core panels
python tests/ws_probe.py ws://127.0.0.1:8891/ws/mfm/<id>/<screen>/
```

Systemd unit + install steps: `deploy/`.

## Ports & data

- HTTP + WS: **8891** (8888/8889/8890 are the other CMD backends).
- Metadata DB: PostgreSQL `cmd_equipment_beta` @ `localhost:5432` (user `postgres`).
- Timeseries: read straight from the logger DB via `LOGGER_TIMESERIES_DSN` (one `DataSource` row).
- Import source: LoggerFast HTTP API at `LOGGER_API_URL` (default `http://127.0.0.1:8003`).

Env lives in `.env` beside `manage.py` — see `.env.example`.
