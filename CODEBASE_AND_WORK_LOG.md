# CMD/EMS backend: codebase guide and work log

Last verified: **2026-10-31**, on the machine `neuract-naidupet`, in `/home/CMDbackend`.

This file has two jobs. It records everything done in the work on editable categories and the
nav push (October 27 to 31, 2026), and it explains how the whole backend works, for a developer
who has not seen it before.

**How far to trust each statement.** Unless a section says otherwise, it was checked against the
code, the running service or a test on the date above. Sections marked *(from
`Project_context.md`)* repeat that document and were not re-verified, because they describe the
plant server or past findings that cannot be reached from this machine.

**No secrets are in this file.** Passwords and keys live in `.env`, which is not committed.

## Contents

1. [Summary](#1-summary)
2. [Current state](#2-current-state)
3. [Known defects and limits](#3-known-defects-and-limits)
4. [What was done](#4-what-was-done)
5. [The push endpoint](#5-the-push-endpoint)
6. [Codebase overview](#6-codebase-overview)
7. [Data model](#7-data-model)
8. [Navigation](#8-navigation)
9. [HTTP API](#9-http-api)
10. [WebSocket screens](#10-websocket-screens)
11. [Reading meter data](#11-reading-meter-data)
12. [Configuration and nameplates](#12-configuration-and-nameplates)
13. [Logger schemas](#13-logger-schemas)
14. [Thresholds and events](#14-thresholds-and-events)
15. [Ways to load data](#15-ways-to-load-data)
16. [Admin](#16-admin)
17. [Tests](#17-tests)
18. [Settings and environment](#18-settings-and-environment)
19. [Operations](#19-operations)
20. [Recommended next steps](#20-recommended-next-steps)
21. [Other documents](#21-other-documents)
22. [Glossary](#22-glossary)

---

## 1. Summary

**What this is.** A Django backend for an energy monitoring dashboard. It serves the sidebar (the
"EMS nav"), screens for individual electrical meters (MFMs), and combined screens for distribution
panels. It keeps its own setup data in PostgreSQL. Meter readings stay in LoggerFast's database and
are only ever read.

**What this work added.**

- Sidebar categories (HT Panels, PCC Panels and so on) are now editable data in a new
  `nav_category` table. They used to be a fixed list in code.
- `POST /api/ems/push/` replaces the whole sidebar from one JSON request. It takes categories,
  panels, each panel's incoming/outgoing/spare meters, standalone meters, meter tables and schemas.
  It previews by default, and `?commit=true` saves.
- The backend now runs on this machine as a service on port 18889, using a new local database.

**Where it stands.**

| Question | Answer |
|---|---|
| Is the code committed? | Yes, on branch `ems-nav-push`, pushed to `origin`, built on `nav-omit-empty-sections`. **Not merged** |
| Is it running? | Yes, on port 18889. It restarted by itself after the Oct 29 reboot |
| Does it hold plant data? | **No.** 0 panels and 0 meters. The sidebar shows six empty categories |
| Can it show live readings? | **No.** No login is available for the readings database |
| Can a push bind meters to tables? | **No.** This machine's LoggerFast API requires a login the backend does not send |
| Tests | 292 run, 288 pass. The 4 failures were already failing before this work |
| Is it ready for a real plant push? | **No.** Fix defects D1 to D3 in [section 3](#3-known-defects-and-limits) first |

---

## 2. Current state

### This machine

| Item | State |
|---|---|
| Code | `/home/CMDbackend`, branch `ems-nav-push`, one commit on top of `b74b2ce` (`nav-omit-empty-sections`). Pushed to `origin`, not merged |
| Service | `cmd-backend-beta.service`, a user service under `neuract`. Active and enabled, 0 restarts |
| Boot behaviour | Linger is on. The machine rebooted on Oct 29 at 10:28:18 and the service was up at 10:28:22, so auto-start has been seen working |
| Running code | Matches the disk. The service last started on Oct 29, after the last code edit on Oct 27 |
| Address | `0.0.0.0:18889`, HTTP and WebSocket |
| Python | 3.14.8 in `.venv` |
| Backend database | Local PostgreSQL on 5432, database `cmd_equipment_beta`, owner role `cmd_beta`, about 10 MB |
| Migrations | Applied through `core.0010_nav_category_table` |
| Rows | 8 categories (all active), 0 panels, 0 meters, 0 schemas, 1 admin user |
| LoggerFast API | `127.0.0.1:8003`. `/health` returns 200; `/devices` and `/tables` return 401 |
| Readings database | `target_version1` exists on local 5432, owned by `loggerfast`. No login is available |
| `LOGGER_TIMESERIES_DSN` | Not set. The default points at port 5433, where nothing is listening |

`GET /api/ems/` currently returns HT, AMF, PCC, UPS, Production and Utility, all empty. APFC and
HVAC are hidden because they are empty and flagged `hide_when_empty`.

`/api/status/` reports `logger_api_reachable: true`. That only means `/health` answered. It does
**not** mean table lookups work; see D5.

### What works and what does not

| Action | Works today? | Why |
|---|---|---|
| Admin page, categories page, nav endpoint | Yes | |
| Push categories and panels | Yes | |
| Push meters by `mfm_id` | Not yet | No meters exist here |
| Push meters by `table_name` | **No** | Needs the LoggerFast login (D5) |
| Add a meter by hand in the admin | Yes | The device picker falls back to typing a device id |
| `/api/import/` with table names | **No** | Same LoggerFast lookup |
| `manage.py import_logger_hierarchy` | **No** | `/devices` and `/tables` calls send no token (D5) |
| Live readings on any screen | **No** | No database login, and no meters bound |

### Other copies

The plant server (`10.80.200.2`, `/home/rohithdev/EMSbackend`) is separate and cannot be reached
from this machine. It has its own data and does not have this work. Going live there requires a
commit, a push and the steps in [section 19](#19-operations).

### Traffic seen on this service

The journal shows requests from Oct 29 14:42 to Oct 30 18:15 IST. Only failures are logged, and
these were all 404s, 405s or WebSocket paths with no route:

- `GET /api/import/` 73 times and `GET /api/ems/push/` 47 times
- WebSocket paths such as `ws/bms/overview/` (15), `ws/asset/1/overview/` (5),
  `ws/timeseries/live/` (3), `ws/meter/MTR-1/overview/` (3)
- HTTP paths such as `/api/source-status/` (10), `/media/` (10), `/zz-nonexistent-probe/`,
  `/api/../admin/login/`

No 400, 401 or 500 responses were logged and the data is unchanged. There is no access log, so
**I do not know who sent these**. The mix looks like a frontend plus an automated sweep. If it
wasn't someone on your team, treat that as a reason to restrict the port.

---

## 3. Known defects and limits

Nothing in this section has been fixed. The task for this document was to write it, not to change
behaviour. "Verified" means reproduced with a throwaway test on Oct 31, not just read in the code.

### Fix before pushing real plant data

| # | Severity | Problem |
|---|---|---|
| D1 | High | A push reports a meter nameplate change, but screens keep the old value |
| D2 | High | A meter moved by a push keeps pointing at its old panel on the Overview screen |
| D3 | Medium | A panel's equipment type stays out of date until the next push |

**D1. Meter nameplate changes silently do nothing. Verified.**

- *What happens.* Pushing `"rated_kva": 2000` for a meter at 1000 kVA reports
  `rated_kva: 1000.0 → 2000.0`, and `MfmConfig` becomes 2000, but `/api/mfm/<id>/config/` still
  returns 1000. The same happened with `nominal_voltage_v` (11000 pushed, 415 returned).
- *Why.* `resolve_mfm_config` gives priority to direct fields on `MFM`. `ensure_mfm_equipment`
  copies values into those fields only when they are empty, so the first value sticks forever. A
  fresh meter pushed twice with different values shows the same problem. `null` cannot clear them
  either.
- *Scope.* `rated_kva`, `nominal_voltage_v`, `nameplate_voltage_kv`, `frequency_hz` and
  `fault_level_ka`. Plant meters are affected from the first push, because the workbook imports
  fill these direct fields. `/api/import/` appears to have the same problem from reading the code,
  but that was not tested. Panel nameplates are fine (verified).
- *Fix.* In `_apply_meter`, also write those five fields onto the `MFM` row when the push gives them.

**D2. Stale panel reference after a move. Verified.**

- *What happens.* A meter moved from Panel A to Panel B has its membership updated, but
  `MFM.reference` stays Panel A. The Overview topology block (`_topology` in
  `panels/consumers/single.py`) then shows Panel A with `mode: None`. A meter moved to standalone
  while Panel A is retired shows the retired Panel A with `mode: input_output`.
- *Why.* The push calls `ensure_primary_reference`, which only fills an empty reference.
  `Breaker.panel_key` has the same limitation.
- *Fix.* After memberships are written, reset `reference` (and the breaker's `panel_key`) when the
  current reference is no longer one of the meter's active panels. Clear it for a standalone meter,
  as `core/audited_workbook.py` already does for Production meters.

**D3. Equipment type lags one push behind. Verified.**

- *What happens.* Moving a panel that was placed by its kind or by an importer guess updates
  `panel_kind` straight away, but `Equipment.panel_type` keeps the old type until the next push. A
  panel with a hand-set category in an active category is not affected.
- *Effect.* Type-level RTM bands and type-based 3D viewer rules use the old type. `mfm_type` is
  correct, because it reads `panel_kind`.
- *Why.* `_apply_panel` saves a different Python object from the `g.config` that
  `ensure_panel_equipment` reads. `core/sheet_import.py` has the same pattern, not tested.
- *Fix.* Set `g.config = cfg` before calling `ensure_panel_equipment`.

### Important limits

**D4. Pushed schema fields do not change how readings are read. Verified by searching the code.**
Only `LoggerSchema.timestamp_column` affects queries. The field list (`address`, `scale`, `formula`
and so on) is stored and shown in the admin, but no reading code uses it. Column names and
corrections come from fixed rules in `core/services.py`. See [section 13](#13-logger-schemas). If
the intent is that a pushed schema decides how readings are interpreted, that is **not built**.

**D5. LoggerFast login is not used for table lookups. Verified.**
`core/logger_devices.py` creates its client with no credentials, and `LoggerClient.devices()` and
`.tables()` send no token, because a code comment says those routes are public. On this machine they
return 401. So a login in `.env` is not enough on its own: both of those need changing. The same
calls block `import_logger_hierarchy`. The error is misleading: it says `LoggerFast is unreachable`
and only the `HTTP 401` at the end shows the real cause.

**D6. Standalone meters cannot be ordered.** They always sort by name, and the push's `meters` array
order is ignored, because `MFM` has no order field. Categories, panels and role members all follow
push order.

**D7. A push cannot set a meter's device type.** `MfmConfig.device_type` is not accepted, so a new
meter is `lt_panel` until changed in the admin. Both types use the same consumer classes, but the
frame's `mfm_type` and the nameplate defaults differ. A push also does not manage twins, feed edges,
breakers or equipment mapping.

**D8. The LoggerFast hierarchy import can undo a push.** For rows linked to LoggerFast, the next
`import_logger_hierarchy` run does all of the following:

- reactivates groups the push retired
- overwrites name, slug, parent and sort order
- restores imported memberships
- deletes memberships the push added between two linked rows

It leaves hand-set categories and hand-set table bindings alone. Decide which system owns the
structure before using both.

**D9. A category slug can be refused unnecessarily. Verified.** A new category cannot use the slug
of a panel that the same push is retiring. The error is safe but wrong; use another slug, or push
twice.

**D10. No concurrency control or history.** If two people commit at once, the later push wins, and
nothing records who pushed what. New sockets to a retired panel's id get code 4404, so a frontend
page bookmarked on that panel stops working until the panel is pushed again.

### Security on this machine

| Item | State |
|---|---|
| Network | Binds `0.0.0.0` with `ALLOWED_HOSTS` defaulting to `*` |
| Debug mode | `DJANGO_DEBUG=1`, because that is the only mode where Daphne serves the admin's CSS. Error pages expose internals |
| CORS | Open to all origins, with credentials allowed, unless `DJANGO_CORS_ORIGINS` is set |
| Read endpoints and sockets | No login required |
| Push and import | HTTP Basic over plain HTTP, with no rate limit |

**Credentials to rotate.** The admin password and the `cmd_beta` database password were both shown
in the chat where this work was done, and the Django secret key was written to `.env` from there.
Commands are in [section 19](#19-operations). Two other candidate passwords were also tried against
PostgreSQL that day and refused, so the PostgreSQL log will show failed logins for `postgres`,
`neuract` and `loggerfast` on Oct 27. Those attempts were mine, not an intrusion. If either candidate
is a real password elsewhere, it is also in that chat and should be changed.

### Not tested

- A committed push containing meters on PostgreSQL. Meter paths ran only on SQLite with LoggerFast
  mocked. On PostgreSQL I ran one preview, with no meters, which rolled back.
- The test suite on PostgreSQL. `cmd_beta` cannot create a test database.
- Real LoggerFast table binding, and live readings.
- Migration 0010 on real plant data. Its data fix was verified on SQLite with five sample panels,
  and all of them landed in the expected category.

### Problems that were already there

| Item | Detail |
|---|---|
| `manage.py import_audited_mfms` | Fails on the shipped `ems_import_template.xlsx` with `Meters row 2: panel membership requires a valid role`. The template says `Incomings`/`Outgoings`, but the parser in `core/audited_workbook.py` knows only `incomer`/`incoming`/`outgoer`/`outgoing`/`spare`. This is a real bug, not just an outdated test |
| 3 outdated tests | `test_manual.py` still expects hidden categories to appear: `test_a_panel_member_is_never_also_a_standalone_leaf`, `test_an_importer_guess_alone_never_files_an_ungrouped_meter`, `test_the_page_lists_the_eight_categories_with_add_links` |
| `requirements.txt` | Pins `psycopg2-binary==2.9.10`, which will not install on Python 3.14. This `.venv` has 2.9.11 |
| `.env` comment | The commented `LOGGER_TIMESERIES_DSN` example lacks the `?options=-csearch_path%3Dneuract` that the default DSN has. It probably needs it, but this could not be confirmed without a login |
| Outdated comment | `panels/consumers/__init__.py` says the consumers are "deliberately empty". They are not |
| Dead code | `resolve_mfm_category` and `UNCATEGORISED` in `core/nav_categories.py` are used only inside that file or not at all |

### Open product decisions *(from `Project_context.md`)*

- **Plant totals:** there is no agreed plant root. Never infer totals from UPS panel 24 or HT panel 2.
- **Source roles:** grid/DG/solar classification was deferred.
- **Physical topology:** transformer pairs and feed edges need verified configuration.
- **Equipment mapping:** the workbook has no column for which asset a meter measures.
- **BMS and Assets:** registered as modules only, with no analytics.
- **Event consistency:** raw logger sag/swell flags and computed crossing counts can disagree.
- **Data quality:** missing power-quality inputs can still produce a passing verdict on some paths.

---

## 4. What was done

### Timeline

| Date | Step |
|---|---|
| Oct 27 | The branch was requested. `git` was missing and the folder was owned by root. The user fixed the ownership, installed `git` and cloned the branch |
| Oct 27 | Read `Project_context.md` and traced how categories and placement worked |
| Oct 27 | The user set the requirements (see decisions below) |
| Oct 27 | Built the category table, push endpoint, admin page, tests and `EMS_NAV_PUSH.md` |
| Oct 27 | Brought the service up here: the user created the role and database; I wrote `.env`, migrated, created the admin user and installed the service. The user turned on linger |
| Oct 29 | Machine rebooted; the service came back by itself |
| Oct 31 | Re-verified everything, ran new experiments, found D1 to D3 and D5, wrote this file |
| Oct 31 | On request, committed and pushed the work to the new branch `ems-nav-push`, with D1 to D3 still unfixed |

### Decisions

| Decision | Choice | Made by |
|---|---|---|
| What happens to things left out of a push | Full sync: soft-retire panels, hide categories, remove memberships, never retire meters | User |
| How a push identifies meters | By `mfm_id`, or by LoggerFast table to find or create | User |
| Empty categories | A per-category `hide_when_empty` flag, seeded on for APFC/HVAC | User |
| Tables and schemas in the push | Accepted per meter, with optional full schema definitions | User |
| Where docs for new features go | A new `.md` file; `NAV_CONTRACT.md` stays unchanged | User |
| Where to run it | This machine | User |
| Storage for category codes | Keep `nav_category` as a text code, not a foreign key, to avoid changing every importer | Me |
| Placement rule | A hand-set category beats `panel_kind`. Required so two categories can share a kind | Me |
| Stored schema versions | Immutable, same as the existing catalog import | Me (existing rule) |
| Auth for the push | Staff login over HTTP Basic, same as `/api/import/` | Me (existing pattern) |
| Database login | A dedicated `cmd_beta` role rather than the `postgres` superuser | Me |

### Files changed

17 tracked files changed (515 lines added, 262 removed).

| File | Change |
|---|---|
| `core/models.py` | Added `NavCategory`. Moved `PANEL_KIND_CHOICES` to module level. `nav_category` on `Group` and `MFM` lost its fixed choices and grew to 32 characters |
| `core/nav.py` | Categories now come from the database. New placement order, `Categories`, `panel_axes`, `category_label`, `panel_category_index`, `standalone_mfms`. A meter whose only groups are retired now counts as being in no group |
| `core/nav_categories.py` | Now only the importer's vocabulary and seed list. Placement helpers moved to `core/nav.py` |
| `core/admin.py` | Category dropdowns and filters read the database. Added the **EMS Nav Categories** page. The panel filter uses the nav's own placement rules |
| `core/sheet_import.py` | Accepts any active category; the template's dropdown lists them. Extracted `bind_device` for reuse |
| `core/logger_schemas.py` | Extracted `install_schema` from `install_schema_catalog` |
| `core/slugs.py`, `core/importer.py` | Reserve database category slugs |
| `core/audited_workbook.py` | Import moved to `core.nav` |
| `core/views.py`, `core/urls.py` | Added `ems_push` and the route. Shared the auth and commit helpers with `ems_import` |
| `config/settings.py` | Database login from `DB_*` environment values; blank or unset keeps the old values. Added the admin menu entry |
| `.env.example`, `.gitignore` | Added `DB_*` placeholders. `.env` is now ignored; it was not before |
| `README.md` | Rewrote the "Nav categories" section and added push examples |
| `core/tests/test_manual.py` | One assertion updated, because hand-filed panels now store their category |
| `core/tests/test_nav.py` | Query budget raised from 6 to 7. Added 5 category tests |

New files:

| File | Purpose |
|---|---|
| `core/nav_push.py` | The push service, 825 lines |
| `core/migrations/0010_nav_category_table.py` | Creates the table, seeds 8 categories, repins conflicting panels |
| `core/tests/test_nav_push.py` | 19 tests |
| `EMS_NAV_PUSH.md` | The push API contract |
| `CODEBASE_AND_WORK_LOG.md` | This file |

Outside the repo or not committed:

| Item | Detail |
|---|---|
| `.env` | Mode 600, ignored by git |
| `.venv` | Project requirements, with `psycopg2-binary` 2.9.11 |
| `~/.config/systemd/user/cmd-backend-beta.service` | Copy of `deploy/cmd-backend-beta.service` with this folder's paths |
| PostgreSQL role and database | `cmd_beta`, `cmd_equipment_beta`, created by the user |
| Linger for `neuract` | Turned on by the user |

Two untracked files in the folder are **not from this work**. Nothing uses them, and they were left
out of the commit:

- `Project_context.md`: the team's handoff document.
- `device_mappings_v1.sql.gz`: a `pg_dump` of `neuract.device_mappings` with 7,019 rows across 107
  tables (3,892 `modbus`, 3,127 `derived`). Columns: table name, field key, protocol, address, data
  type, scale, deadband, device id, encoding. I looked only at its structure and did not load it.

### Behaviour changes

1. A hand-set category now always decides where a panel goes. Before, an HT/AMF/PCC/UPS `panel_kind`
   overrode it. The migration rewrites conflicting panels so they stay where they were.
2. Filing a panel by hand always stores its category, including HT/AMF/PCC/UPS. Those used to store
   a blank.
3. A meter whose only memberships are in retired groups now shows as standalone if filed by hand,
   and otherwise counts as ungrouped. Before, it vanished without being counted.
4. A category node's `sort_order` in `/api/ems/` is the stored value, not its position in the list.

---

## 5. The push endpoint

The full contract is in [EMS_NAV_PUSH.md](EMS_NAV_PUSH.md). Its section 6 lists some limits; D1 to
D10 above are more complete.

```bash
# Preview: nothing is saved. The response shows every change and the resulting nav.
curl -u admin:<password> -H 'Content-Type: application/json' -d @nav.json \
     http://<host>:18889/api/ems/push/

# Save, all or nothing.
curl -u admin:<password> -H 'Content-Type: application/json' -d @nav.json \
     "http://<host>:18889/api/ems/push/?commit=true"
```

```json
{
  "schemas": [{
    "code": "sch_X", "version": 1, "timestamp_column": "timestamp_utc",
    "fields": [{"name": "voltage_avg", "kind": "measured", "data_type": "float32",
                "unit": "V", "address": "40001", "scale": 0.1}]
  }],
  "categories": [
    {"code": "pcc", "panels": [
      {"mfm_id": 19,
       "nameplate": {"nominal_voltage_v": 415},
       "incoming": [{"mfm_id": 1000005}],
       "outgoing": [{"table_name": "some_logger_table", "name": "Feeder 1", "schema": "sch_X"}]}
    ]},
    {"label": "Solar Panels", "hide_when_empty": true, "panels": [], "meters": []}
  ]
}
```

**Before a full-sync commit:** anything left out leaves the sidebar. Build the body from
`GET /api/ems/`, read the preview's `changes`, and then commit.

### How the code runs

`run_push` in `core/nav_push.py` has three stages, all inside one database transaction:

1. **Parse** (`parse`): checks the JSON shape with no database access. Unknown keys are errors, so a
   typo such as `"incomers"` cannot quietly empty a panel.
2. **Resolve** (`_resolve_categories`, `_resolve_meters`, `_resolve_schemas`, `_resolve_panels`):
   matches entries to stored rows. LoggerFast is asked only about tables that would change a binding.
   Every check runs, so one response lists all the errors.
3. **Apply** (`_apply`), in this order:
   1. Install schemas.
   2. Upsert categories and hide the rest.
   3. Retire panels that were left out.
   4. Write meters.
   5. Write panels and their memberships.
   6. Unfile standalone meters that were left out.
   7. Retire plain groups that would still show a meter the push did not place.

`build_tree()` then runs inside the transaction, so the preview's `nav` is exactly what a commit
would produce. The transaction rolls back unless `commit` was requested and there were no errors.

### Matching rules

| Thing | Matched by |
|---|---|
| Category | `code`, else `slug`, else `label` (case-insensitive). Hidden ones reactivate |
| Panel | `mfm_id`, else `name` among active panels, else the most recently retired panel of that name (keeps its id), else created |
| Meter | `mfm_id`, else the meter already holding that table's device, else the active meter on that table, else created |

A panel's kind: an explicit `panel_kind` wins. A new or moving panel takes the category's default.
Otherwise it is kept.

---

## 6. Codebase overview

### Stack

| Part | Version |
|---|---|
| Python | 3.14.8 here. The README's run section mentions 3.11.9 from its original environment |
| Django | 5.2.11 |
| Django REST Framework | 3.16.1 (installed; views are plain Django `JsonResponse`) |
| Channels / Daphne | 4.3.2 / 4.2.1, with the in-memory channel layer |
| Admin theme | django-jazzmin 3.0.2 |
| Spreadsheets | openpyxl 3.1.5 |
| PostgreSQL driver | psycopg2-binary 2.9.11 here (2.9.10 in `requirements.txt`) |
| Others | django-cors-headers 4.9.0, requests 2.32.5, python-dotenv 1.2.1, websockets 15.0.1 |

### Apps

| App | Purpose |
|---|---|
| `core` | Models, navigation, configuration, reading meter data, importers, admin, REST views |
| `panels` | The WebSocket screens |
| `assets`, `bms` | Empty placeholders. Their route lists are empty, so `ws/bms/...` and `ws/asset/...` raise "No route found" |
| `config` | Settings, ASGI and URLs |

### `core/`

| File | Lines | Purpose |
|---|---|---|
| `models.py` | 823 | All tables ([section 7](#7-data-model)) |
| `nav.py` | 389 | Builds the sidebar tree and resolves placement |
| `nav_categories.py` | 191 | Default categories and the importer's name-matching rules |
| `nav_push.py` | 825 | The push endpoint's logic |
| `views.py`, `urls.py` | 191, 18 | HTTP endpoints |
| `config.py` | 247 | Resolves a panel's or meter's effective configuration |
| `config_defaults.py`, `constants.py` | 85, 14 | Per-type defaults; `FEEDER_ID_OFFSET` |
| `services.py` | 1876 | All reads of meter data |
| `time_filters.py` | 133 | Validates range, sampling and shift filters |
| `derive.py` | 85 | Pure formulas for derived values |
| `thresholds.py` | 97 | Page status rules and defaults |
| `rtm.py`, `rtm_defaults.py` | 16, 59 | Real-time monitoring bands |
| `equipment.py` | 109 | Creates equipment, breaker and threshold rows; fills `reference` |
| `importer.py` | 1123 | LoggerFast hierarchy sync |
| `sheet_import.py` | 815 | Generic workbook import and template |
| `audited_workbook.py`, `audited_upload.py` | 256, 199 | Plant-specific workbook rules |
| `logger_client.py`, `logger_devices.py` | 151, 102 | LoggerFast HTTP client; device and table lookup |
| `logger_schemas.py` | 257 | Schema catalog parser and installer |
| `slugs.py` | 46 | Slug generation and collision checks |
| `viewer.py` | 56 | 3D viewer rule resolution |
| `widget_frame.py` | 84 | Shared KPI frame shape |
| `wire.py` | 136 | Safe JSON for sockets (non-finite numbers become `null`) and connect guards |
| `ai_client.py`, `ai_frame.py` | 56, 77 | Optional AI summary service, off by default |
| `admin.py` | 996 | Admin pages ([section 16](#16-admin)) |
| `management/commands/` | | `import_logger_hierarchy`, `import_audited_mfms`, `import_logger_schemas` |
| `templates/admin/core/` | | EMS Nav page, workbook upload page, threshold grids |
| `static/core/js/jazzmin_bs5_compat.js` | | Fixes Jazzmin's tabs and user menu |

### `panels/`

| File | Lines | Purpose |
|---|---|---|
| `registry.py` | 31 | `screen()`: the one place a screen is declared |
| `routing.py` | 26 | Generates one route per screen, plus a catch-all |
| `dispatch.py` | 110 | Picks a consumer from the id at connect; rejects with 4404 |
| `consumers/base.py` | 252 | Single-meter base classes |
| `consumers/panel_base.py` | 233 | Panel base classes |
| `consumers/single.py` | 787 | The 11 single-meter screens |
| `consumers/distribution.py` | 576 | The 5 panel screens |
| `consumers/derived.py` | 63 | Voltage basis and numeric event rules |

### Other

| Path | Purpose |
|---|---|
| `deploy/` | Service unit for the plant paths, and install steps |
| `tests/ws_probe.py` | Prints the frames a socket sends; a manual check |
| `ems_import_template.xlsx` | Reference workbook with 102 meter rows; **not blank** |
| `MFM_NOMINAL_VOLTAGES.csv/.xlsx` | Nominal-voltage report, not an upload template |

### Migrations

| # | Topic |
|---|---|
| 0001 | Initial tables |
| 0002 | `nav_category` and `nav_category_source` |
| 0003 | Hand-built panels and meters |
| 0004 | Category order and choices |
| 0005 | Equipment metadata |
| 0006 | Logger schemas |
| 0007 | Viewer catalog |
| 0008 | RTM thresholds by panel type |
| 0009 | MFM threshold labels |
| 0010 | `nav_category` table, seed rows, repin conflicting panels (this work) |

---

## 7. Data model

### Four ideas that must stay separate

| Idea | Stored in | Meaning |
|---|---|---|
| Meter | `MFM` | Identity, schema, data source and table |
| Reference panel | `MFM.reference` → `Group` | The one primary panel where the meter physically sits |
| Role in a panel | `Membership(group, mfm, role)` | Incoming, outgoing or spare, **relative to that panel** |
| Measured asset | `MFM.equipment` → `Equipment` | What the meter measures |

One meter can be outgoing from Panel A and incoming to Panel B. Role belongs to the panel, not the
meter. There is one membership per panel and meter pair.

### Tables

| Model | Table | Purpose |
|---|---|---|
| `NavCategory` | `nav_category` | Sidebar categories (new) |
| `Group` | `plant_group` | A plain group, or a distribution panel when `is_panel` is true |
| `MFM` | `mfm` | A meter |
| `Membership` | `membership` | A meter's place in a group; role is required in a panel and empty in a plain group |
| `FeedEdge` | `feed_edge` | A device-to-device feed link |
| `DataSource` | `data_source` | One readings database connection, shared by many meters |
| `LoggerSchema`, `LoggerSchemaField` | `logger_schema`, `logger_schema_field` | Versioned field layouts |
| `Equipment` | `equipment` | Asset identity; has exactly one of panel type or asset type |
| `PanelType`, `AssetType` | `core_paneltype`, `core_assettype` | Classifications |
| `PanelConfig` | `panel_config` | A panel's nameplate and `panel_kind` |
| `MfmConfig` | `mfm_config` | A meter's nameplate and `device_type` |
| `EquipmentConfig` | `equipment_config` | An asset's nameplate |
| `Nameplate` | `nameplate` | Traceable reference facts |
| `MfmThreshold` | `mfm_threshold` | Per-meter limits |
| `RtmThreshold` | `rtm_threshold` | Per-metric bands, for one panel or one panel type |
| `Breaker` | `breaker` | Static breaker details, not live state |
| `ViewerAsset`, `ViewerRule`, `ViewerSetting` | `viewer_asset`, `viewer_rule`, `viewer_setting` | 3D model catalog and rules |

Admin-only proxies with no table of their own: `DistributionPanel`, `EmsNav` (on `Group`), and
`DistributionPanelThreshold` (on `PanelConfig`).

### Rules worth knowing

- **Soft retirement.** Rows are retired (`active=False`) and not deleted, so ids and frontend URLs
  survive. Slugs must be unique only among active rows.
- **`logger_id` blank means built by hand.** The LoggerFast importer never touches such a row.
- **`nav_category_source`:** `manual` means a person or push set it, and the importer leaves it alone.
- **`table_binding`:** `manual` means the importer never rebinds the meter's table.

### Identifiers

| Id | Used for |
|---|---|
| `Group.id` | A panel's screens. Always below 1,000,000 |
| `MFM.id` | Internal and admin use |
| `MFM.eid` = 1,000,000 + `MFM.id` | A meter's screens and config. The API calls this `mfm_id` |
| `Equipment.key` | The asset viewer endpoint |
| `logger_id` | LoggerFast identity; not a screen id |
| Tree node `id` | UI keys such as `grp-4` or `grp-4-mfm-1000003` |

The frontend must use the `mfm_id` returned by `/api/ems/`. It should not calculate offsets or
hardcode ids. Key nodes by `id`, because a meter in two panels appears twice with the same `mfm_id`.

---

## 8. Navigation

`build_tree()` in `core/nav.py` produces a tree exactly two container levels deep:

```text
Category
├── Distribution panel
│   ├── Overview        → the panel's aggregate screen
│   ├── Incoming        → member meter screens (omitted if empty)
│   ├── Outgoing        → member meter screens (omitted if empty)
│   └── Spare           → member meter screens (omitted if empty)
└── Standalone meter    → single-meter screens
```

### Which categories appear

Active `NavCategory` rows, in `sort_order`. A row with `hide_when_empty` is left out while it holds
nothing. Clients must not assume eight categories or consecutive `sort_order` values.

### Where a panel goes

Strongest first:

1. A hand-set `nav_category` (`nav_category_source='manual'`).
2. Its `panel_kind`, through the first active category whose default kind matches.
3. An importer-guessed `nav_category`.
4. The nearest ancestor's category. Retired ancestors still count.

A code naming no active category counts as unset. A panel nothing places is not shown and is counted
in `uncategorised_panel_count`.

Migration 0010 seeds these default kinds: HT `ht_panel`, AMF `amf_panel`, PCC `pcc_panel`, UPS
`ups_panel`. The rest use `distribution_panel`, which places nothing.

### Where a meter goes

- **In an active panel:** under that panel, once per membership. It is never also shown loose.
- **In an active plain group:** one loose leaf tree-wide, under its own category or else the group's.
- **In no active group, filed by hand:** a standalone leaf under that category.
- **Otherwise:** not shown, and counted in `ungrouped_mfm_count`. A meter with only an importer guess
  stays out on purpose.

### Order

| Level | Order |
|---|---|
| Categories | `sort_order` |
| Inside a category | Panels first, then loose meters |
| Panels | `sort_order`, then name, then id |
| Members in a role | `position`, then id |
| Sections in a panel | Fixed: Overview, Incoming, Outgoing, Spare |
| Standalone meters | Name, then id (D6) |

### Response

`/api/ems/` returns `count`, `leaf_count`, `matched_mfm_count`, `ungrouped_mfm_count`,
`uncategorised_panel_count`, `uncategorised_mfm_count` and `tree`. The tree costs at most 7 queries
regardless of plant size, and a test enforces that. Node shapes are in `NAV_CONTRACT.md` section 3.

---

## 9. HTTP API

From `core/urls.py`, all under `/api/`.

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/ems/` | The sidebar tree | None |
| POST | `/api/ems/push/` | Replace the sidebar; `?commit=true` saves | Staff, HTTP Basic |
| GET | `/api/mfm/<id>/config/` | Effective configuration for a panel or meter id | None |
| GET | `/api/status/` | Health and row counts | None |
| GET | `/api/bms/`, `/api/assets/` | Always `{"count": 0, "tree": []}` | None |
| GET | `/api/kit-preview/catalog/` | Active 3D model list | None |
| GET | `/api/equipment/key/<key>/viewer/` | Viewer settings for an asset | None |
| GET | `/api/import/template/` | Generated blank workbook | None |
| POST | `/api/import/` | Upload a workbook; preview unless `commit=true` | Staff, HTTP Basic |
| | `/admin/` | Django admin | Session and CSRF |

The Basic-auth views deliberately ignore the admin session cookie. They are CSRF-exempt, so honouring
the cookie would let any web page an admin visits post to them.

Full payload examples for the frontend are in `FRONTEND_INTEGRATION.md`.

---

## 10. WebSocket screens

All sockets use `ws/mfm/<id>/<screen>/`.

### How a connection is routed

1. `panels/routing.py` builds one route per entry in `panels/registry.py`. Importing a consumer
   module registers its screens.
2. `type_dispatch` looks up the id. Below 1,000,000 is an active panel. Otherwise it is an active
   meter: `transformer` if `MfmConfig.device_type` says so, else `lt_panel`.
3. An id or type not served by that screen, or an unknown screen under `ws/mfm/`, is accepted and
   then closed with code **4404** and a typed error frame. The frontend gets a clear reason instead
   of an opaque handshake failure.

### Consumer base classes

| Class | Behaviour | Refresh |
|---|---|---|
| `SingleMFMConsumer` | Sends a snapshot, then updates | Every 15 s |
| `FilteredMFMConsumer` | Rebuilds when the client sends new filters | On request |
| `QueueMFMConsumer` | Initial queue, then new rows | Every 5 s (`MFM_LIVE_POLL_SECONDS`) |
| `PanelConsumer` | Base for panels | None by default |
| `FilteredPanelConsumer` | Rebuilds on new filters | On request |
| `LivePanelConsumer` | Live panel frames | Every 5 s |

A failure sends a typed error frame (`telemetry_error`) and may close with **4500**.

### Single-meter screens

The same classes serve both transformer and LT-panel meters.

| Screen | Class | Base |
|---|---|---|
| `overview` | `OverviewConsumer` | Single |
| `real-time-monitoring` | `RealTimeMonitoringConsumer` | Queue |
| `energy-power` | `EnergyPowerConsumer` | Queue |
| `voltage-current` | `VoltageCurrentConsumer` | Queue |
| `energy-summary` | `EnergySummaryConsumer` | Filtered |
| `power-energy-analysis` | `PowerEnergyAnalysisConsumer` | Filtered |
| `load-anomalies` | `LoadAnomaliesConsumer` | Filtered |
| `voltage-history` | `VoltageHistoryConsumer` | Filtered |
| `current-history` | `CurrentHistoryConsumer` | Filtered |
| `power-quality-summary` | `PowerQualitySummaryConsumer` | Single |
| `power-quality-history` | `PowerQualityHistoryConsumer` | Filtered |

A meter with no data source or no table gets a 4404 error frame.

### Panel screens

| Screen | Class | Base |
|---|---|---|
| `energy-distribution` | `EnergyDistributionConsumer` | Filtered |
| `real-time-monitoring` | `PanelRealTimeMonitoringConsumer` | Live |
| `energy-power` | `PanelEnergyPowerConsumer` | Filtered |
| `voltage-current` | `PanelVoltageCurrentConsumer` | Filtered |
| `power-quality-summary` | `PanelPowerQualityConsumer` | Filtered |

The frontend's Overview and Energy & Distribution pages share `energy-distribution`. There is no
panel `overview` socket.

A panel has no table of its own. Its consumer reads each active member's own table, schema and
timestamp column, then groups by role. Incoming members count as sources and outgoing members as
consumers. Spares stay visible but are excluded from reconciliation. Do not add panel totals to their
members' totals; that double counts.

### Filters

Ranges: `today`, `yesterday`, `this_week`, `last_7d`, `this_month`, `last_month`, `last_30d`,
`last_24h`, `custom`. Sampling depends on the range and is validated in `core/time_filters.py`.
Shifts are `all`, `a`, `b`, `c`, starting at 06:00, 14:00 and 22:00 IST. Each endpoint's metadata is
authoritative for what it supports.

**Open sockets keep the configuration they loaded when they connected.** Clients must reconnect to
see threshold, config or binding changes.

---

## 11. Reading meter data

`core/services.py` is the only code that reads readings, and it never writes. It uses one psycopg2
connection pool per `DataSource` connection string.

A meter's data location is `MFM.data_source` (the connection) plus `MFM.table_name`.

Rules it applies:

| Rule | Detail |
|---|---|
| Time column | Uses the schema's `timestamp_column`, or `timestamp` with no schema. If that column is missing, tries `timestamp`, `timestamp_utc`, `ts`, `time`, `datetime` |
| Column aliases | `_SCHEMA_ALIAS` maps vendor spellings to standard names only when the standard column is absent, e.g. `active_energy_import_kWh` to `active_energy_import_kwh` |
| Data corrections | Applied only when the connection string contains `target_version1` (`is_real`). `CMD_DATA_CORRECTION=0` turns them off |
| Power sign | Some power columns use absolute values, because their sign reflects CT polarity |
| Energy direction | `MFM.energy_direction` picks sum, import, export or net. `energy_scale` and `power_scale` correct wrong multipliers |
| No-data values | Logger sentinels such as 65535 and 6553.5 are excluded from aggregates |
| Non-finite numbers | Become `null` on the wire, so a NaN cannot break the frontend's JSON parsing |

Voltage basis: `voltage_avg_basis` reports `L-L` on the real logger and `L-N` otherwise. Compare
measured and nominal voltage on the same basis.

**`DataSource` rows store the connection string at creation.** When a meter is first bound,
`data_source_for` finds or creates a row from `LOGGER_TIMESERIES_DSN` (or
`LOGGER_TIMESERIES_DSN_<target>`). Set the right value **before** binding meters. To correct it later,
edit the one row in the admin; that repoints every meter using it.

---

## 12. Configuration and nameplates

`core/config.py` answers `/api/mfm/<id>/config/` and supplies consumers with limits. Ratings are
configuration, not telemetry.

**Panel**, weakest to strongest: type defaults, then `EquipmentConfig`, then `PanelConfig`. Nominal
voltage is returned **line-to-line** (e.g. 33000).

**Meter**, weakest to strongest:

1. Defaults for its device type.
2. `EquipmentConfig`.
3. `MfmConfig`.
4. **Direct fields on `MFM`** (`rated_capacity_kva`, `nominal_voltage_v`, `nameplate_voltage_kv`,
   `frequency_hz`, `fault_level_ka`). These win, which is the cause of D1.
5. Values derived from kVA for ratings still blank, assuming a power factor of 0.9. That is an
   assumption, not a measurement.
6. `MfmThreshold` overrides for overview and power-quality limits.

Nominal voltage is returned **line-to-neutral** for meters (L-L ÷ √3, 1 decimal place), and
`nominal_voltage_ll_v` keeps the L-L value. Do not multiply a panel's nominal by √3.

Other details:

- Null means inherit. Zero is a real value.
- `energy_target_kwh_today` set explicitly wins, including zero. Blank falls back to rated kW × 24 h.
- `mfm_type` is the panel's `panel_kind` for a panel, and `transformer` or `lt_panel` for a meter.

---

## 13. Logger schemas

A `LoggerSchema` is a versioned definition (`code` + `version`) with a `timestamp_column` and a list
of `LoggerSchemaField` rows. A stored version is immutable: `install_schema` accepts an identical
definition and raises an error for any difference, so a changed layout needs a new version.

### What a schema does today

| Part | Used by reading code? |
|---|---|
| `timestamp_column` | **Yes.** It is passed to every query for that meter |
| `code`, `version`, `name`, `active` | Only reported in the meter's `/config/` response |
| `fields` (`address`, `scale`, `unit`, `formula`, ...) | **No.** Stored and shown in the admin only |

So pushing a meter's `table_name` does change where readings come from, and `timestamp_column` does
change which column orders them. Changing a field's `scale` or `formula` in a new schema version does
**not** change any number on a screen, because those rules are fixed in `core/services.py`.

Definitions may be listed under `schemas`, or given inline on a meter. A meter's `schema` can be
`"sch_X"` (always version 1), `{"code": "sch_X", "version": 2}`, or a full definition.

`mfm_schema_details.md` documents the plant's four schemas: `sch_DG`, `sch_EM6400_NG+`,
`sch_EMMS7_96`, `sch_LG2599`. The test suite confirms it parses to 239 fields. They are **not**
installed on this machine.

---

## 14. Thresholds and events

These classify dashboards. They are **not** protection or breaker settings.

### Where values come from

| Scope | Resolution | Admin page |
|---|---|---|
| Panel voltage/current and power quality | `PanelConfig`, then `EquipmentConfig`, then defaults | `/admin/core/distributionpanelthreshold/` |
| Panel RTM | The group's row, then its panel type's row, then defaults | `/admin/core/rtmthreshold/` |
| Single-meter Overview and power quality | `MfmThreshold` over defaults | `/admin/core/mfmthreshold/` |

### Default RTM bands (`core/rtm_defaults.py`)

Four edges separate Low, Normal, Moderate, High and Critical.

| Metric | Banded on | Edges |
|---|---|---|
| kW | % of rated kW | 40, 60, 80, 95 |
| kVA and Amps | % of rated kVA | 40, 60, 80, 95 |
| kVAr | kVAr/kVA % | 20, 40, 60, 80 |
| Power factor | Raw PF, higher is better | 0.98, 0.95, 0.90, 0.85 |
| Voltage | Absolute deviation % | 1, 2, 3, 5 |
| Voltage unbalance | % | 1, 2, 3, 5 |
| Current unbalance | % | 5, 10, 15, 20 |

### Other defaults (`core/thresholds.py`)

| Setting | Default |
|---|---|
| Overview load and energy | Elevated 75%, critical 90% |
| Overview power factor | Good ≥ 0.95, fair ≥ 0.90 |
| Overview frequency | Nominal 50 Hz; fair above ±0.05, unstable above ±0.10 |
| Overview current unbalance | Watch 5%, unbalanced 10% |
| Overview voltage deviation | Elevated 3%, critical 5% |
| Overview busbar temperature | Warn 45 °C, critical 55 °C |
| Panel V&C row | Voltage deviation warn 3, danger 5; current unbalance warn 5, danger 8 |

A panel V&C row takes the worst of voltage deviation and current unbalance, using strict `>`. Panel
power-quality severity also uses current THD against `thd_i_limit_pct` and PF against `true_pf_min`.
Panel and single-meter power-quality rules differ, so do not merge them in the frontend. An explicit
logger PASS/FAIL verdict wins on the single-meter side.

### Events *(from `Project_context.md`)*

- **Sag and swell:** fixed ±10% of nominal, separate from the admin thresholds. For 33 kV L-L that is
  29,700 V and 36,300 V. Counts are crossings into an abnormal state, not abnormal samples.
- **Known mismatch:** table `sag`/`swell` fields can come from logger flags while summary counts use
  numeric crossings, so a raw flag of 1 can coexist with zero computed events.
- **Load anomalies:** compare normalized actual and peak kW with a 30-day same-hour profile. Constants
  include 85% peak overload, a 5 to 20 point spread and a 150% cap on the expected centre.
- **Past findings:** some readings have been physically implausible (PF above 1, very high I-THD at low
  current). These trace to source data. Do not clamp them without an agreed data-quality rule.

---

## 15. Ways to load data

| Route | Entry point | Removes things? | Categories | Needs LoggerFast |
|---|---|---|---|---|
| **Nav push** | `POST /api/ems/push/` | **Yes, full sync** | Any, and creates new ones | Only for tables that change |
| Generic workbook | `POST /api/import/` | No | Any active | Yes, for table names |
| Admin workbook | `/admin/core/mfm/import-workbook/` (superuser) | No | Any active (from reading the code, not tested), with plant rules for HT, UPS, Utility and Production | Yes |
| Audited command | `manage.py import_audited_mfms` | Retires Production panels | Six audited ones only | Yes. Currently broken on the shipped workbook |
| LoggerFast sync | `manage.py import_logger_hierarchy [--dry]` | Retires what LoggerFast no longer has | Guesses from names | Yes, including login |
| Schema catalog | `manage.py import_logger_schemas` | No | Assigns by workbook row | No |
| By hand | Admin pages | Your choice | Any | Optional |

Common to the workbook routes: preview by default, all-or-nothing commit, no deletions, 10 MB limit,
and a 5,000-row limit on the generic route. None of them creates physical logger tables.

The name-based guesser in `core/nav_categories.py` matches whole words only, so `ups` never matches
`groups`. A miss leaves a row unfiled rather than guessing. `manage.py import_logger_hierarchy --dry`
prints a census to review before trusting it.

### Plant rules in the audited routes *(from `Project_context.md`)*

- The original HT rows belong to one `33KV HT Panel`.
- UPS uses one panel named `UPS`, and Utility uses one named `Utility Panel`.
- Production holds standalone meters, not panels.
- Meter display names come from resolved table or device labels.

Historical inventory (a snapshot in `EMS_STRUCTURE.md`, Oct 5): 9 panels, 85 meters in panels, 17
standalone meters, 102 total. Historical panel ids: HT 2, AMF 16/17/18, PCC 19/20/21, UPS 24,
Utility 25. These are not API constants.

---

## 16. Admin

At `/admin/`, themed with Jazzmin.

| Page | Purpose |
|---|---|
| **EMS Nav** | The live tree, read-only, with add-panel and add-meter links per category. Links to the workbook upload |
| **EMS Nav Categories** | New. Edit label, slug, order, default kind, hide-when-empty and active. `code` is locked after creation, and categories cannot be deleted |
| **Distribution Panels** | One Category dropdown plus Incomers, Outgoers, Spares, nameplate and RTM rows. LoggerFast-linked panels cannot be deleted |
| **MFMs** | Device picker, bindings, energy corrections and nameplate, with grid editing in the list |
| **Groups** | All groups, with bulk pin, unpin and clear actions for categories |
| **Memberships**, **Feed Edges**, **Breakers**, **Equipment**, **Nameplates**, **Data Sources** | Direct editing |
| **MFM Thresholds**, **Distribution Panel Thresholds**, **RTM Thresholds** | Wide grids with a frozen identity column; RTM has per-metric tabs |
| **Logger schemas** | Definitions and their fields |
| **Viewer** assets, rules, settings; **Panel types**; **Asset types** | Reference data |

---

## 17. Tests

### Running them

`.venv` has what is needed. PostgreSQL is not required.

```bash
mkdir -p /tmp/cmdtest
cat > /tmp/cmdtest/sqlite_settings.py <<'EOF'
from config.settings import *  # noqa
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EOF
PYTHONPATH=/tmp/cmdtest DJANGO_SETTINGS_MODULE=sqlite_settings \
  .venv/bin/python manage.py test core panels
```

Also run `manage.py check`, `manage.py makemigrations --check --dry-run` and `git diff --check`. All
were clean on Oct 31.

LoggerFast is mocked, so a pass says nothing about live connectivity or data quality.

### Result on Oct 31

292 tests, 288 pass, 3 errors, 1 failure:

| Test | Cause |
|---|---|
| `test_manual…test_a_panel_member_is_never_also_a_standalone_leaf` | Looks up the hidden HVAC category |
| `test_manual…test_an_importer_guess_alone_never_files_an_ungrouped_meter` | Same |
| `test_manual…test_the_page_lists_the_eight_categories_with_add_links` | Expects APFC on the page |
| `test_audited_workbook…test_confirmed_panel_rules_and_engineering_units` | Real parser bug, see [section 3](#3-known-defects-and-limits) |

All four failed the same way before this work started. D1 to D3 have **no** test coverage in the
repo; the experiments that found them were temporary.

| Test file | Tests | | Test file | Tests |
|---|---|---|---|---|
| `test_importer` | 64 | | `test_equipment` | 4 |
| `test_nav` | 41 | | `test_logger_schemas` | 4 |
| `test_sheet_import` | 32 | | `test_viewer` | 4 |
| `test_nav_categories` | 20 | | `test_distribution_threshold_admin` | 2 |
| `test_nav_push` | 19 | | `test_rtm_admin` | 2 |
| `test_manual` | 18 | | `test_shell_endpoints` | 2 |
| `test_config` | 17 | | `test_audited_workbook` | 1 |
| `test_thresholds` | 15 | | `test_daily_target_admin` | 1 |
| `panels/test_dispatch` | 12 | | `test_energy_aliases` | 5 |
| `test_admin` | 11 | | `test_mfm_threshold_labels` | 5 |
| `test_audited_upload` | 8 | | `test_time_filters` | 5 |

---

## 18. Settings and environment

Settings load `.env` beside `manage.py`. Real environment variables win.

| Variable | Default | Purpose |
|---|---|---|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | `cmd_equipment_beta`, `postgres`, blank, `localhost`, `5432` | Backend database. Blank or unset uses the default, so the plant server is unaffected |
| `DJANGO_SECRET_KEY` | A development key | **Set it** |
| `DJANGO_DEBUG` | `1` | Daphne serves admin CSS/JS only when on |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated |
| `DJANGO_CORS_ORIGINS` | Open | Comma-separated; setting it restricts CORS |
| `LOGGER_API_URL` | `http://127.0.0.1:8003` | LoggerFast API |
| `LOGGER_API_USER`, `LOGGER_API_PASSWORD` | Blank | Used for `/groups*` and `/device_edges` only (D5) |
| `LOGGER_API_TIMEOUT` | `20` | Seconds |
| `LOGGER_TIMESERIES_DSN` | `postgresql://postgres@127.0.0.1:5433/target_version1?options=-csearch_path%3Dneuract` | Readings database |
| `LOGGER_TIMESERIES_DSN_<target>` | None | Connection for a specific LoggerFast database target |
| `CMD_REAL_DSN_MARKER` | `target_version1` | Marks a connection as the real logger |
| `CMD_DATA_CORRECTION` | `1` | Data corrections on or off |
| `AI_SUMMARY_ENABLED`, `AI_SUMMARY_URL`, `AI_SUMMARY_TIMEOUT`, `AI_SUMMARY_CONCURRENCY` | `0`, `http://127.0.0.1:8077`, `8`, `3` | Optional summary service |

Timezone is `Asia/Kolkata`. `MFM_LIVE_POLL_SECONDS` is read from Django settings and defaults to 5.

`.env` on this machine sets the `DB_*` values, `LOGGER_API_URL`, `LOGGER_API_TIMEOUT`,
`DJANGO_DEBUG=1`, `DJANGO_SECRET_KEY` and `AI_SUMMARY_ENABLED=0`. `LOGGER_API_USER` and
`LOGGER_API_PASSWORD` are present but blank.

---

## 19. Operations

### On this machine

```bash
systemctl --user status cmd-backend-beta.service
systemctl --user restart cmd-backend-beta.service      # after any code or .env change
journalctl --user -u cmd-backend-beta.service -n 100 --no-pager
kill -USR1 <pid>                                       # dumps all thread stacks to the journal

cd /home/CMDbackend
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check

curl http://127.0.0.1:18889/api/status/
curl http://127.0.0.1:18889/api/ems/
.venv/bin/python tests/ws_probe.py ws://127.0.0.1:18889/ws/mfm/<id>/<screen>/
```

The service file is `~/.config/systemd/user/cmd-backend-beta.service`. Run `daemon-reload` after
editing it.

### Rotating the exposed credentials

```bash
# Admin login
.venv/bin/python manage.py changepassword admin

# Database role: then put the same value in DB_PASSWORD in .env
sudo -u postgres psql -c "ALTER ROLE cmd_beta PASSWORD '<new password>'"

# Secret key: generate one, set DJANGO_SECRET_KEY in .env (this logs out admin sessions)
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))"

systemctl --user restart cmd-backend-beta.service
```

Database administration here needs `sudo -u postgres`, which asks for a password interactively.

### To enable table binding and readings

1. Get a LoggerFast login and fix D5. Then set `LOGGER_API_USER` and `LOGGER_API_PASSWORD`.
2. Get the `dbviewer_ro` password (preferred, because it is read-only) or the `loggerfast` one.
3. Set `LOGGER_TIMESERIES_DSN` **before** binding any meter. It most likely needs the search path:
   `postgresql://dbviewer_ro:<password>@127.0.0.1:5432/target_version1?options=-csearch_path%3Dneuract`
4. Restart the service.

### Deploying to the plant server

This has not been done. The plant has real data, so take more care than on this empty instance.

1. Fix D1 to D3 on `ems-nav-push` and push.
2. On `10.80.200.2` as `rohithdev`, back up first:
   `pg_dump -h localhost -U postgres cmd_equipment_beta > before-0010.sql`
3. In `/home/rohithdev/EMSbackend`, check `git status` is clean, then run
   `git fetch origin && git switch ems-nav-push` and `.venv/bin/python manage.py migrate`.
4. Run `systemctl --user restart cmd-backend-beta.service`.
5. Compare `/api/ems/` and `/api/status/` with their values from before the upgrade. They should match.
6. **Preview** the first push. A push that leaves something out retires it, so read `changes` closely.
7. Do not run `import_logger_hierarchy` after pushing without considering D8.

---

## 20. Recommended next steps

In order:

1. **Fix D1, D2 and D3**, each with a regression test. They are small, and all three give wrong
   results on plant data.
2. **Decide about D4.** Should pushed schema fields actually change how readings are computed? That
   is a sizeable feature. If `timestamp_column` is enough, say so in `EMS_NAV_PUSH.md`.
3. **Rotate the exposed credentials** and restrict the port if this machine is reachable by others.
4. **Fix D5** and get the LoggerFast login and a readings login.
5. **Decide whether this machine should hold plant data**, then do a first push or import.
6. **Add standalone meter ordering (D6)** if the sidebar order matters.
7. **Fix the small existing problems:** the role aliases in `audited_workbook.py`, the 3 outdated
   tests, and the `psycopg2-binary` pin.
8. **Merge `ems-nav-push`** once D1 to D3 are fixed, and then consider the plant deployment.

---

## 21. Other documents

| Document | What it is | Status after this work |
|---|---|---|
| `EMS_NAV_PUSH.md` | Push API contract | Current. Does not yet list D1 to D5 |
| `README.md` | Entry point | Nav section current. Run section refers to the plant's Python path |
| `NAV_CONTRACT.md` | Sidebar contract for the frontend | **Partly outdated**, left unchanged on request. Node shapes are correct, but sections 1.1 and 1.2, rule 1 and "always 8" describe the old fixed categories |
| `Project_context.md` | Team handoff (calls itself `PROJECT_CONTEXT.md`) | Mostly current. Sections 6 and 7 predate editable categories and the push. Sections 2 and 16 describe the plant server |
| `FRONTEND_INTEGRATION.md` | Socket payloads and filters | Not re-checked. No changes to socket payloads in this work |
| `MFM_PANEL_EQUIPMENT_STRUCTURE.md` | Reference, role and equipment concepts | Concepts current |
| `THRESHOLD_IMPLEMENTATION.md`, `thresholds.md` | Threshold design and requirements | Unaffected. `thresholds.md` is the original requirements |
| `single_panel_apis.md`, `distribution_panel.md` | Screen and API specs captured from the frontend | Historical |
| `mfm_schema.md`, `mfm_schema_details.md` | Schema design and field reference | `mfm_schema_details.md` is also the catalog source |
| `additional.md` | Non-telemetry data used with MFMs | Not re-checked |
| `EMS_STRUCTURE.md` | Plant inventory from Oct 5 | Snapshot, not this machine |
| `P4_BACKEND_CONTRACT_STATUS.md` | Gap analysis from Sept 24 | Historical |
| `deploy/README.md` | Service install steps | Plant paths |

---

## 22. Glossary

| Term | Meaning |
|---|---|
| **MFM** | Multi-function meter: one electrical meter, with its own set of screens |
| **Distribution panel** | A `Group` with `is_panel=True`. It has no readings table of its own and combines its members |
| **Plain group** | A non-panel `Group`, usually from LoggerFast. Not a sidebar node, but its meters can appear as loose leaves |
| **Membership / role** | A meter's place in a panel: incoming, outgoing or spare |
| **Standalone meter** | A meter directly under a category, with no panel |
| **Loose leaf** | The sidebar node for a meter not in a panel |
| **Category** | A top-level sidebar section. A `NavCategory` row |
| **`panel_kind`** | What a panel is. Drives `mfm_type`, and can place a panel nobody filed |
| **`nav_category`** | The category code a panel or meter is filed under |
| **Hand-set / pinned** | `nav_category_source='manual'`, meaning the importer will not change it |
| **Full sync** | Whatever a push leaves out leaves the sidebar |
| **Retire / soft-retire** | Set `active=False`. The row, its id and its settings remain |
| **Hide** | Set a category's `active=False` |
| **Unfile** | Clear a meter's category while pinning it, so the importer does not refile it |
| **Preview** | A push or import without `commit`. It runs fully, then rolls back |
| **LoggerFast** | The external logging system that owns devices, tables and readings |
| **`DataSource`** | One stored connection to a readings database |
| **Logger schema** | A versioned description of a meter table's fields |
| **RTM** | The Real-Time Monitoring screen |
| **V&C / PQ** | Voltage & Current / Power Quality screens |
| **L-L / L-N** | Line-to-line and line-to-neutral voltage. V(L-N) = V(L-L) ÷ √3 |
| **THD** | Total harmonic distortion |
| **HT / LT** | High tension and low tension |
| **AMF / PCC / UPS / APFC** | Auto mains failure, power control centre, uninterruptible power supply and automatic power factor correction panels |
