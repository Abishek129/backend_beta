# backend_beta

Frontend connection contract: [`FRONTEND_INTEGRATION.md`](FRONTEND_INTEGRATION.md).

A fresh CMD backend with a backend-owned plant model that can be built in the admin or from a
spreadsheet and can optionally synchronize LoggerFast-linked rows. Beta owns hierarchy,
equipment metadata, screens and nameplate data; LoggerFast supplies device/table bindings and
may synchronize the linked subset. Nothing is migrated from `backend2` — own DB, own port,
own admin.

The socket namespace keeps backend2's soul: one id space `ws/mfm/<id>/<screen>/`, dispatched by
equipment type. `Group.id` (< 1,000,000) = a distribution panel; `FEEDER_ID_OFFSET + MFM.id` = a
single meter. The eleven single-MFM screens are registered for transformer and LT-panel meter
IDs. Distribution panels aggregate their independently bound member MFMs by membership role.

## Apps

| app | what it holds |
|---|---|
| `core` | the backend-owned hierarchy (`Group`, `MFM`, `Membership`, `FeedEdge`), canonical equipment/type/nameplate records, breaker and per-MFM threshold metadata, optional LoggerFast bindings/import, panel/MFM/equipment configuration, the nav-category axis, nav/config/status REST, and the model-agnostic query layer copied from backend2. |

## Nav categories

The sidebar is a **two-level tree**: categories holding panels and loose meters. The categories
are `NavCategory` rows — user-editable (label, slug, order, default panel kind, hide-when-empty,
active) in admin → **EMS Nav Categories** and through `POST /api/ems/push/`. Migration 0010
seeded the original eight (HT, AMF, PCC, UPS, APFC, Production, Utility, HVAC; APFC/HVAC hidden
while empty). A hand-set `Group|MFM.nav_category` places a row; otherwise the importer's guesses
place it — a panel's `PanelConfig.panel_kind` through the category defaulting to that kind, then
a guessed `nav_category`. A hand-edited value is pinned and never reseeded.
`manage.py import_logger_hierarchy --dry` prints a per-category census plus the uncategorised
and low-confidence rows — **read it before trusting the heuristic**, since it is guessing at a
naming convention we do not control. See `EMS_NAV_PUSH.md` §1.

The whole sidebar can be pushed as JSON — a **full sync** (`core/nav_push.py`; payload and rules
in `EMS_NAV_PUSH.md`):

```bash
curl -u <admin>:<password> -H 'Content-Type: application/json' -d @nav.json \
     http://<host>:<port>/api/ems/push/                 # preview: report + resulting nav
curl -u <admin>:<password> -H 'Content-Type: application/json' -d @nav.json \
     "http://<host>:<port>/api/ems/push/?commit=true"   # write, all or nothing
```

Or in bulk from a spreadsheet (`core/sheet_import.py` — its column lists are the format):

```bash
curl -o ems_import_template.xlsx http://<host>:<port>/api/import/template/
curl -u <admin>:<password> -F file=@filled.xlsx http://<host>:<port>/api/import/              # preview
curl -u <admin>:<password> -F file=@filled.xlsx "http://<host>:<port>/api/import/?commit=true" # write
```

Add/update only, never deletes; all-or-nothing; the preview runs the same code and rolls back.
The optional `Schema` column assigns an existing active `LoggerSchema` version to each MFM.

The audited LoggerFast schema catalogue and the HT/AMF/PCC assignments in this repository are
installed idempotently with:

```bash
python manage.py import_logger_schemas \
  --details mfm_schema_details.md \
  --workbook ems_import_template.xlsx
```

This imports all four schema blueprints and their measured/derived fields, then assigns MFMs by
their retained spreadsheet row. It does not replace the physical `gic_*` table binding. Existing
schema versions are immutable: a changed field definition must be introduced as a new version.

The audited plant workbook uses engineering-unit strings and human-facing table labels, so its
plant rows are installed through the dedicated adapter (preview first, then commit):

```bash
python manage.py import_audited_mfms --workbook ems_import_template.xlsx
python manage.py import_audited_mfms --workbook ems_import_template.xlsx --commit
```

Its confirmed hierarchy rules are encoded explicitly: all HT rows belong to `33KV HT Panel`,
UPS has one `UPS` distribution panel, Utility has one `Utility Panel`, and Production MFMs are
loose category leaves without roles or distribution panels.

For ongoing uploads, a superuser can open **Admin → EMS Nav → Import Excel workbook** (also linked
from **Admin → MFMs**), or go directly to `/admin/core/mfm/import-workbook/`. The admin form accepts
`.xlsx`/`.xlsm` files up to 10 MB, with separate **Preview** (rolls back) and **Commit upload**
actions. It accepts the shared workbook's human-facing `Table name` labels and engineering units,
resolves each label to one migrated LoggerFast physical table, then uses the generic transactional
importer to create/update panels, MFMs, memberships, schemas and bindings. For an ambiguous label,
add a `Logger device ID` or `Physical table` column to the Meters sheet. Omitted rows are never
deleted. An unreadable workbook, unknown table, conflicting membership or missing schema rejects
the whole upload. The LoggerFast API must be reachable during validation.

## Single-MFM endpoints

Transformer and LT-panel MFM IDs serve these screens under
`/ws/mfm/<frontend-id>/`: `overview`, `real-time-monitoring`, `energy-power`,
`energy-summary`, `power-energy-analysis`, `load-anomalies`, `voltage-current`,
`voltage-history`, `current-history`, `power-quality-summary`, and
`power-quality-history`. History screens share range, custom-window, sampling,
shift, validation, and response-envelope behavior.

Supporting HTTP endpoints are:

- `GET /api/mfm/<frontend-id>/config/`
- `GET /api/equipment/key/<equipment-key>/viewer/?default=panel`

The config route accepts either ID family; the viewer route uses the canonical equipment key.

## Distribution-panel endpoints

Panel `Group.id` values serve the five aggregate screens actually used by the frontend:
`energy-distribution`, `real-time-monitoring`, `energy-power`, `voltage-current`, and
`power-quality-summary`. A panel has no telemetry table of its own. Each frame reads every active
member from that MFM's own data source, table, logger schema, timestamp column and correction
profile, then groups the result by `Membership.role` (`incoming`, `outgoing`, `spare`). Spares are
shown in topology but excluded from source/consumer reconciliation.

Shared shell endpoints are `GET /api/ems/`, `/api/bms/`, `/api/assets/`, and
`/api/kit-preview/catalog/`; the latter lists active `ViewerAsset` records.

Panels and meters can also be built by hand: admin → **EMS Nav** shows the categories with
*+ Distribution panel* (Incomers / Outgoers / Spares) and *+ MFM* (LoggerFast device picker) on
each. Hand-built rows have no `logger_id` and the importer never touches them (§1.2).
| `panels` | EMS sockets: `dispatch.py` (id → type), `registry.py` (`screen(...)` declares a screen), `routing.py` (routes generated from the registry), single-MFM consumers, and membership-based distribution-panel aggregate consumers. |
| `assets` | DG / UPS / transformer asset meters (`ws/asset/*`). Structure only — no models, no consumers. |
| `bms` | Building-management meters (`ws/meter/*`). Structure only — no models, no consumers. |

## Run

```bash
PY=/home/rohith/.pyenv/versions/3.11.9/bin/python
$PY manage.py migrate
$PY manage.py import_logger_hierarchy --dry     # then without --dry
$PY -m daphne -b 0.0.0.0 -p 18889 config.asgi:application
$PY manage.py test core panels
python tests/ws_probe.py ws://127.0.0.1:18889/ws/mfm/<id>/<screen>/
```

Systemd unit + install steps: `deploy/`.

## Ports & data

- HTTP + WS: **18889** (8888/8889/8890 are the other CMD backends).
- Metadata DB: PostgreSQL `cmd_equipment_beta` @ `localhost:5432` (user `postgres`).
- Timeseries: read straight from the logger DB via `LOGGER_TIMESERIES_DSN` (one `DataSource` row).
- Import source: LoggerFast HTTP API at `LOGGER_API_URL` (default `http://127.0.0.1:8003`).

Env lives in `.env` beside `manage.py` — see `.env.example`.
