# EMS nav push — `POST /api/ems/push/`

One request replaces the whole EMS sidebar: the categories, their distribution panels, each
panel's incoming / outgoing / spare meters, the standalone meters, and the logger schemas the
meters' tables are read with. `/api/ems/` reflects the result immediately.

Implementation: `core/nav_push.py`. Tests: `core/tests/test_nav_push.py`.

## 1. Categories are now editable data

The categories are no longer a fixed list in code. They are rows of the `NavCategory` table
(`nav_category`):

| field | meaning |
|---|---|
| `code` | stable id; stored on panels/meters as `nav_category`; the FE `category` value |
| `label` | sidebar text |
| `slug` | route segment: `/electrical/<slug>` |
| `sort_order` | sidebar position |
| `panel_kind` | default kind for panels filed here (drives `mfm_type`) |
| `hide_when_empty` | omit the category from `/api/ems/` while it holds nothing |
| `active` | `false` = hidden. Categories are never deleted |

Migration `0010_nav_category_table` seeds the original eight with today's behaviour (APFC
and HVAC `hide_when_empty`). They can be edited in admin → **EMS Nav Categories** or through
the push. The category's `code` cannot be changed after creation, because panels and meters
store it.

What changes in `/api/ems/`:

- Categories come from the table: active ones, in `sort_order`, skipping empty
  `hide_when_empty` ones. A category node's `sort_order` is the stored value.
- A hand-set category (admin, workbook, push) always places a panel. Otherwise the panel goes
  under the first active category whose default `panel_kind` matches its own. After that comes
  the importer's guessed category, then the nearest ancestor's.
- A meter whose only memberships are in retired groups now counts as "in no group". It shows
  as standalone if it was filed by hand; otherwise it counts in `ungrouped_mfm_count`.

## 2. Request

```
POST /api/ems/push/                preview: validates, reports, returns the resulting nav
POST /api/ems/push/?commit=true    writes — only if there are no errors at all
Authorization: Basic <active staff user>
Content-Type: application/json
```

Use HTTPS when this crosses an untrusted network: Basic credentials are otherwise plaintext.

```json
{
  "schemas": [ <schema definition>, … ],
  "categories": [
    {
      "code": "ht", "label": "HT Panels", "slug": "ht-panels",
      "panel_kind": "ht_panel", "hide_when_empty": false,
      "panels": [
        {
          "mfm_id": 2, "name": "33KV HT Panel", "panel_kind": "ht_panel",
          "nameplate": { "nominal_voltage_v": 33000 },
          "incoming": [ <meter>, … ],
          "outgoing": [ <meter>, … ],
          "spare":    [ <meter>, … ]
        }
      ],
      "meters": [ <meter>, … ]
    }
  ]
}
```

`schemas` is optional. `categories` is required and must not be empty, because a push replaces
the whole sidebar. Array order is display order: for categories, for panels within a category,
and for meters within a role. Unknown keys are rejected rather than ignored, so a typo such as
`"incomers"` cannot silently empty a panel.

### Category

| key | |
|---|---|
| `code` | existing category. Omit it for a new one (derived from `slug`/`label`) |
| `label` | required for a new category; renames an existing one |
| `slug` | optional; defaults to the slugified label; changes the route |
| `panel_kind` | optional default kind for panels filed here |
| `hide_when_empty` | optional `true`/`false` |
| `panels`, `meters` | the category's panels and standalone meters |

An entry is matched to a stored category by `code`, else `slug`, else `label` (case-insensitive).
A hidden category that is pushed again is reactivated with the same code.

### Panel

| key | |
|---|---|
| `mfm_id` | existing panel: the `mfm_id` `/api/ems/` returns for it (`Group.id`, < 1,000,000) |
| `name` | required for a new panel; renames an existing one |
| `panel_kind` | optional; see below |
| `nameplate` | optional panel nameplate fields (`PanelConfig`); `null` clears a field |
| `incoming`, `outgoing`, `spare` | the panel's members, in order |

A panel is matched by `mfm_id`. Without one, it is matched by `name` among active panels. If
none match, the most recently retired panel of that name is reactivated and keeps its id.
Otherwise a new panel is created.

`panel_kind`: an explicit value wins. A new panel, or one moving to another category, takes the
category's default kind. Otherwise the panel's kind is kept.

### Meter

| key | |
|---|---|
| `mfm_id` | existing meter: the `mfm_id` `/api/ems/` returns for it (≥ 1,000,000) |
| `table_name` | LoggerFast migrated table. Binds the device, data source and table (pinned `manual`) |
| `name` | optional rename; for a new meter, defaults to the LoggerFast device name |
| `schema` | the field layout the table is read with (below) |
| `nameplate` | optional meter nameplate fields (`MfmConfig`); `null` clears a field |

Give `mfm_id` for an existing meter, or `table_name` to find or create one. Without an id,
the meter already bound to that table's device is reused; otherwise a new meter is created.
LoggerFast is only queried for tables that change a binding, so re-pushing an unchanged nav
works while LoggerFast is down.

The same meter may appear under several panels, for example outgoing from one panel and incoming
to another, but only once per panel. It cannot be both standalone and in a panel. All its
appearances must agree on `name`, `table_name`, `schema` and nameplate values.

### Schema

A meter's `schema` is one of:

- `"sch_EMMS7_96"`: version 1 of a stored or pushed schema;
- `{"code": "sch_EMMS7_96", "version": 2}`;
- a full definition inline (same shape as the entries of `schemas`).

Schema definition:

```json
{
  "code": "sch_EMMS7_96", "version": 1, "name": "EMMS7 96",
  "logger_id": "sch_1788773139707",
  "timestamp_column": "timestamp_utc",
  "description": "",
  "fields": [
    { "name": "voltage_avg", "kind": "measured", "data_type": "float32", "unit": "V",
      "address": "40001", "scale": 0.1, "description": "" },
    { "name": "power_kw", "kind": "derived", "data_type": "float32", "unit": "kW",
      "formula": "…", "evaluation_order": 1 }
  ]
}
```

`code`, `timestamp_column` and `fields` are required, and `version` defaults to 1. A measured
field needs an `address`, and a derived one needs a `formula`. The readings sockets use the
schema's `timestamp_column`.

Stored schema versions are immutable. Pushing a version that already exists is fine if it is
identical, and an error if anything differs. A changed layout needs a new `version`.

## 3. Full sync — what the push leaves out

| thing | in the push | not in the push |
|---|---|---|
| category | created / updated / reactivated | hidden (`active=false`) |
| panel | created / updated / reactivated, filed by hand under its category | soft-retired: id, config and memberships kept |
| panel membership | exactly the pushed lists, roles and order | deleted from that panel |
| standalone meter | filed by hand under its category | unfiled (`nav_category=''`, pinned) |
| meter | created / updated / reactivated / bound | **never retired**, it only leaves the sidebar |
| plain group (LoggerFast hierarchy) | — | soft-retired if it would still show a meter the push did not place |
| schema | installed / verified | untouched |

After a commit, `/api/ems/` shows exactly the pushed nav. Retired panels can be brought back by
pushing them again.

## 4. Response

`200` when the push is valid, `400` with errors, `401` without staff credentials.

```json
{
  "ok": true, "committed": false,
  "summary": { "panel": { "update": 1, "retire": 1, "unchanged": 8 }, "mfm": { "create": 1 } },
  "errors":   [ { "path": "categories[0].panels[1].incoming[0]", "message": "…" } ],
  "warnings": [],
  "changes":  [ { "entity": "panel", "action": "retire", "name": "Old Board",
                  "path": "", "detail": "not in the push" } ],
  "nav": { …exactly the /api/ems/ body the push produces… }
}
```

- The preview runs the same code as a commit inside a transaction that is rolled back. Its
  `changes` and `nav` are what a commit would produce.
- `nav` is present only when `ok` is true.
- All errors are reported together, each with its JSON `path`.
- `changes[].entity` is one of `schema`, `category`, `panel`, `membership`, `mfm`, `group`.
- `changes[].action` is one of `create`, `update`, `reactivate`, `retire`, `hide`, `unfile`,
  `delete`.
- `unchanged` rows are counted in `summary` only.

## 5. Recommended client flow

1. `GET /api/ems/` and build the editor from it, keeping each panel's and meter's `mfm_id`.
2. Send the edited tree to `/api/ems/push/` without `commit` and show `changes`, `errors` and
   the preview `nav`.
3. On confirmation, send the same body with `?commit=true`.
4. Reconnect open sockets: running consumers keep the configuration they resolved at connect.

## 6. Known limits

- **LoggerFast hierarchy sync.** A push can retire panels and plain groups that are linked to
  LoggerFast (`logger_id` set), and delete their memberships. The next
  `manage.py import_logger_hierarchy` run re-activates linked groups and restores their
  imported memberships. Run that importer only when LoggerFast should override the pushed nav.
  The October 5 snapshot (`EMS_STRUCTURE.md`) shows no meters in plain groups.
- **No concurrency control.** Two editors pushing at once: the last commit wins.
- **Workbook imports stay additive.** `/api/import/` and the admin workbook upload still only add
  and update. Both accept any active category by its code, label or slug (for the admin upload,
  from reading the code, not tested). `manage.py import_audited_mfms` still knows only its six
  audited categories.
