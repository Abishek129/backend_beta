# NAV_CONTRACT — `backend_beta` `/api/ems/`

FE-facing contract for the nav tree served by `backend_beta` (the LoggerFast-imported
hierarchy). CMD_V2 is **not** modified by this project; this file is the spec its owner
codes against.

- Base URL: `http://<host>:8891` — archbox `http://127.0.0.1:8891`, LAN `http://192.168.1.20:8891`,
  VPN `http://100.90.185.31:8891`.
- Endpoints: `GET /api/ems/`, `GET /api/mfm/<id>/config/`, `GET /api/status/`.
- Plain JSON (no DRF envelope), no auth, CORS open in dev.

---

## 1. Where the tree comes from

LoggerFast owns the **structure**: a group tree of any depth, an `isPanel` flag, device
memberships with a role, twins. `backend_beta` imports it and owns the **screens**, the
**nameplate** data and the **nav category** the logger has no concept of.

The tree the FE receives is **exactly two container levels deep**, regardless of how deeply
the logger nests its groups:

```
category  (one of eight, fixed order, always emitted)
  ├── panel      -> Overview + Incoming/Outgoing/Spare -> member leaves
  └── loose leaf
```

| LoggerFast / beta | nav node |
|---|---|
| group, `isPanel=false` | **not a node.** Carries `nav_category` for its subtree and owns loose-leaf meters |
| group, `isPanel=true` | **distribution panel** at depth 1 — Overview + role groups |
| device in a panel group | **member leaf** under its role group |
| device in a plain group | **loose leaf** (flat single-meter node) |
| device in no group | not in the tree; counted in `ungrouped_mfm_count` |

### 1.1 The two placement axes

A panel is filed under a category by **one of two orthogonal fields**. They are never derived
from one another: `panel_kind` says what a row *is*, `nav_category` says where it is *filed*.

| category | placed by |
|---|---|
| HT Panels | `PanelConfig.panel_kind = 'ht_panel'` |
| AMF Panels | `PanelConfig.panel_kind = 'amf_panel'` |
| PCC Panels | `PanelConfig.panel_kind = 'pcc_panel'` |
| UPS Panels | `PanelConfig.panel_kind = 'ups_panel'` |
| APFC / Utility / Production / HVAC Panels | `Group.nav_category` / `MFM.nav_category` |

`panel_kind`'s default, `distribution_panel`, places nothing — such a row falls through to
`nav_category`, and then to its ancestors. Both fields are seeded from the row's name by the
importer and correctable in the Django admin; a hand-edited value is pinned
(`nav_category_source='manual'`) and never reseeded.

Rules:

1. **Fixed two levels.** Eight categories in a fixed order (`core/nav_categories.py`), each
   emitted **even when it has no children**, so the sidebar is stable.
2. **Every panel renders at depth 1** under its category, wherever it sits in the logger tree.
   A panel's child groups never render; a panel nested inside another panel becomes a
   *sibling* under its own category.
3. **Expansion is per node, not per category.** A `isPanel` group always gets
   `Overview` + its non-empty role groups; a loose meter is always a flat leaf. There is no
   per-category `hierarchical`/`flat` mode.
4. **Category resolution**: a group's own value wins, else the nearest ancestor that has one.
   A loose meter uses `MFM.nav_category`, else its plain group's resolved value. Inheritance
   is computed per request and **never written back**.
5. **A meter can appear under two panels** (e.g. MCC-1's outgoer is the Lighting DB's
   incomer). Both leaves carry the **same `mfm_id`** and **distinct node `id`s**. A meter any
   panel claims is **never also** emitted as a loose leaf, and a meter emits **at most one
   loose leaf tree-wide** however many plain groups it belongs to.
6. Only `active=true` groups and meters are emitted; within a category, panels come first,
   then loose leaves, each ordered by `sort_order, name, id`. Retired rows keep their ids and
   can return on a later import.
7. Slugs are unique tree-wide already (the importer dedupes them, reserving the eight category
   slugs before any group slug so a group cannot steal one); the nav does not re-dedupe.
8. **A retired ancestor still donates its `nav_category`**, so soft-retiring a section never
   re-buckets the panels beneath it.
9. A panel or loose meter that neither axis places is **not rendered**; it is counted in
   `uncategorised_panel_count` / `uncategorised_mfm_count` and listed in the import report's
   category census for the operator to assign.

## 2. Envelope

```json
{ "count": 8, "leaf_count": 8, "matched_mfm_count": 3, "ungrouped_mfm_count": 1,
  "uncategorised_panel_count": 0, "uncategorised_mfm_count": 0, "tree": [ … ] }
```

| field | meaning |
|---|---|
| `count` | number of root nodes — now always **8**, the fixed category spine |
| `leaf_count` | metered leaves only (member leaves + loose leaves) |
| `matched_mfm_count` | distinct meters in the tree that have BOTH a data source and a table name |
| `ungrouped_mfm_count` | active meters with no membership (imported, not in the nav) |
| `uncategorised_panel_count` | **new** — active panels neither axis places, so not rendered |
| `uncategorised_mfm_count` | **new** — loose meters neither axis places, so not rendered |

`ungrouped_mfm_count` is no longer the only reason a meter can be missing from the tree —
`uncategorised_mfm_count` is the other. Both are `0` in a fully-configured plant.

## 3. Node shapes

### 3.1 Category (one of the eight)

```json
{
  "id": "eq-pcc", "label": "PCC Panels", "slug": "pcc-panels",
  "kind": "section", "category": "pcc", "sort_order": 2,
  "pathOverride": "/electrical/pcc-panels",
  "model_slug": null,
  "children": [ … panels …, … loose leaves … ]
}
```

The eight, in emit order: `ht` `ht-panels`, `amf` `amf-panels`, `pcc` `pcc-panels`,
`apfc` `apfc-panels`, `ups` `ups-panels`, `utility` `utility-panels`,
`production` `production-panels`, `hvac` `hvac-panels`.

- `kind` stays `'section'` — a category is semantically what a section was (an unmetered
  container with its own route), so the FE's `kind` branch is unchanged. After this change
  `'section'` has exactly one producer instead of "any non-panel group at any depth".
- `id` is `eq-<category id>`; there is no `logger_id` (a category is not a group).
- `sort_order` is the index in the fixed list, so the FE can defensively re-sort.
- `pathOverride` is a **single segment** — there are no ancestors left to join.
- `children` may be `[]`; render the category anyway.
- Panels are listed **before** loose leaves.

### 3.2 Distribution panel (an `isPanel` group)

```json
{
  "id": "grp-grp_mcc1", "label": "MCC-1", "slug": "mcc-1", "key": "mcc-1",
  "kind": "panel", "distribution_panel": true,
  "mfm_id": 4, "mfm_name": "MCC-1",
  "bus_kv": 11.0, "model_slug": null,
  "logger_id": "grp_mcc1", "twins": ["mcc-2"],
  "category": "pcc", "parent_label": "Block A",
  "children": [ Overview, Incoming, Outgoing, Spare ]
}
```

- `mfm_id` = `Group.id`, **always < 1 000 000** — the id the aggregate screens are
  addressed by (`ws/mfm/<id>/…`, `/api/mfm/<id>/config/`).
- `key` = the panel slug (beta has no `Equipment.key`).
- `twins` = slugs of the sibling panels tied to this one (empty list when none).
- `category` = **new** — the category id this panel is filed under, so the FE can build a
  breadcrumb or a flat index without walking upward.
- `parent_label` = **new, optional** — the name of the nearest active ancestor group, omitted
  at a root. Flattening to two levels discards "which room is this panel in"; this carries it,
  so the FE nameplate need not fall back to guessing from the name.
- Child order is fixed: `Overview`, then the role groups present. **Never nested groups** — a
  panel's child groups do not render here.
- `Overview` is emitted for **every** panel, including one with no aggregate screen yet.

Overview child:

```json
{ "id": "grp-grp_mcc1-overview", "label": "Overview", "slug": "overview" }
```

Role group (`incoming` / `outgoing` / `spare`; **empty ones are omitted**):

```json
{ "id": "grp-grp_mcc1-outgoing", "label": "Outgoing", "slug": "outgoing",
  "group": true, "alwaysOpen": true, "children": [ …member leaves… ] }
```

### 3.3 Member leaf (one per membership, ordered by the logger's `devices[]` index)

```json
{
  "id": "grp-grp_mcc1-mfm-1000003", "label": "MFM-03", "slug": "mfm-03",
  "mfm_id": 1000003, "mfm_name": "MFM-03", "key": "mfm-03",
  "role": "outgoing", "logger_device_id": "dev_03",
  "mfm_row": null, "breaker_type": null,
  "nominal_kv": 0.41, "distribution_panel": false
}
```

- `mfm_id` = `1 000 000 + MFM.id` (`FEEDER_ID_OFFSET`) — always ≥ 1 000 000, so panel and
  meter ids never collide in the shared `ws/mfm/<id>/` namespace.
- `id` is **scoped to the panel**, so the same meter under two panels yields two nodes.
- `key` = the **meter slug** (in backend2 it was the mapped device's `Equipment.key`).
- `role` is new: `incoming | outgoing | spare`.
- `mfm_row` and `breaker_type` are always `null` — the logger has no sheet row and no
  breaker register.

### 3.4 Loose leaf (a meter attached straight to a plain group)

```json
{
  "id": "eq-mfm-1000007", "label": "MFM-09", "slug": "mfm-09", "key": "mfm-09",
  "kind": "panel", "distribution_panel": false,
  "bus_kv": 0.41, "model_slug": null,
  "mfm_id": 1000007, "mfm_name": "MFM-09", "mfm_ids": [1000007],
  "logger_device_id": "dev_09", "category": "hvac"
}
```

Same shape as today's "flat" panel node, so the FE's existing single-meter branch works
unchanged, plus the additive `category`. `distribution_panel:false` keeps the panel-only tabs
(energy-distribution, PQ) off it.

**At most one per meter tree-wide**, and never emitted for a meter that any panel claims.

### 3.5 Voltages

`bus_kv` (panel / loose leaf) and `nominal_kv` (member leaf) are the resolved **line-to-line**
nominal in **kV, 2 dp** — `null` when unset. 415 V renders as `0.41`, 11 kV as `11.0`.
Note `/api/mfm/<meter id>/config/` ships `voltage_nominal_v` **line-to-neutral** (÷√3, 1 dp)
because the per-phase meter screens plot L-N; the nav value is the L-L basis.

## 4. Worked example (GROUPS_AND_MFMS.md §8)

The logger tree — note that **none of these containers become nodes**; they only carry the
category downward:

```
Plant                                (no category)
├── Yard                             nav_category = hvac
│     MFM-09                         loose, no role
└── Block A                          nav_category = production
    ├── MCC-1   panel   panel_kind = pcc_panel, twins [mcc-2]
    ├── MCC-2   panel   (nothing set — inherits production from Block A)
    └── Lighting DB panel  nav_category = utility  (own value beats inherited)
```

The nav it produces:

```
HT Panels           eq-ht          /electrical/ht-panels          (empty)
AMF Panels          eq-amf         /electrical/amf-panels         (empty)
PCC Panels          eq-pcc         /electrical/pcc-panels
└── MCC-1     panel  twins: [mcc-2]  mfm_id 4   parent_label "Block A"
    ├── Overview
    ├── Incoming  → MFM-01                      mfm_id 1000001
    └── Outgoing  → MFM-02, MFM-03              mfm_id 1000002, 1000003
APFC Panels         eq-apfc        /electrical/apfc-panels        (empty)
UPS Panels          eq-ups         /electrical/ups-panels         (empty)
Utility Panels      eq-utility     /electrical/utility-panels
└── Lighting DB   panel                         mfm_id 6
    ├── Overview
    ├── Incoming  → MFM-03  ← SAME meter as MCC-1's outgoer:
    │                          mfm_id 1000003, id grp-grp_ldb-mfm-1000003
    └── Outgoing  → MFM-04
Production Panels   eq-production  /electrical/production-panels
└── MCC-2     panel  twins: [mcc-1]  mfm_id 5   ← inherited from Block A
    ├── Overview
    ├── Incoming  → MFM-05
    └── Outgoing  → MFM-06
HVAC Panels         eq-hvac        /electrical/hvac-panels
└── MFM-09    loose leaf  eq-mfm-1000007        ← inherited from Yard
```

Envelope: `count 8, leaf_count 8, matched_mfm_count 3, ungrouped_mfm_count 1,
uncategorised_panel_count 0, uncategorised_mfm_count 0`.

Note the ordering consequence: MCC-2 renders **after** Lighting DB even though it sorts before
it in the logger tree, because categories order the sidebar, not sibling order.

## 5. `GET /api/mfm/<id>/config/`

```json
{ "mfm_id": 1000001, "panel_id": "mfm-01", "mfm_type": "transformer",
  "config_table": "mfm_config", "config": { … }, "object": null }
```

- `id < 1 000 000` → the panel Group; `id ≥ 1 000 000` → the meter.
- `panel_id` is the **slug** of the subject.
- `config_table`: `panel_config` for a panel id, `mfm_config` for a meter id.
- `mfm_type` vocabulary: `distribution_panel | pcc_panel | ht_panel | amf_panel | ups_panel`
  (panels, from `PanelConfig.panel_kind` — `amf_panel` and `ups_panel` are **new**) and
  `lt_panel | transformer` (meters, from `MfmConfig.device_type`).
- `object` is always `null` — 3D resolution is out of scope in beta.
- Unknown / retired id → `404 {"detail": "not found"}`.

## 6. `GET /api/status/`

```json
{ "db": true, "logger_api_reachable": true, "groups": 6, "panels": 3, "mfms": 8,
  "bound_mfms": 4, "ungrouped_mfms": 1,
  "last_import_at": "2026-09-09T08:00:00+05:30", "version": "beta" }
```

`bound_mfms` = active meters with a data source **and** a table name.
`logger_api_reachable` probes `LOGGER_API_URL` `/health` then `/` with a 2 s timeout and
never raises.

## 7. Diff vs today's backend2 `/api/ems/` (:8889)

| area | backend2 (:8889) | backend_beta (:8891) |
|---|---|---|
| source | hardcoded `core/ems_tree.py` SECTIONS | **DB state** (`panel_kind` / `nav_category`), seeded from the name by the importer, corrected in the admin |
| depth | exactly 2 (7 fixed sections → panels) | exactly 2 (8 fixed categories → panels / loose leaves) |
| sections | 7, curated, shown only if listed | 8, fixed, **always emitted even when empty** |
| expansion | per-section `mode: hierarchical\|flat` | **no mode** — derived per node from `Group.is_panel` |
| envelope | `count, leaf_count, matched_mfm_count, tree` | **+ `ungrouped_mfm_count`, `uncategorised_panel_count`, `uncategorised_mfm_count`** |
| `leaf_count` | every childless node (Overview nodes included) | metered leaves only |
| `matched_mfm_count` | leaves carrying an `mfm_id` | distinct meters that are actually bound to a table |
| section id | `eq-<section>` | `eq-<category id>` (same scheme) |
| section fields | `id,label,slug,pathOverride,model_slug,children` | **+ `kind:'section'`, `category`, `sort_order`**; no `logger_id` |
| panel id | `eq-<section>-<pslug>` | `grp-<logger_id>` |
| panel fields | `key` = `Equipment.key`, `kind` = `Equipment.kind`, optional `parent_label` | `key` = slug, `kind` always `'panel'`, **+ `logger_id`, `twins[]`, `category`**, optional `parent_label` |
| member leaf id | `eq-mfm-<mfm_id>` (globally one node per meter) | `grp-<logger_id>-mfm-<mfm_id>` (**one node per membership**) |
| meter in two panels | impossible (one `reference` panel per meter) | **supported** — two leaves, same `mfm_id` |
| leaf `key` | mapped device's `Equipment.key`, or `null` | the meter slug |
| leaf fields | `mfm_row` = sheet row, `breaker_type` from `Breaker` | both always `null`; **+ `role`, `logger_device_id`, `distribution_panel:false`** |
| role groups | Incoming / Outgoing / Spare / **Bus Coupler** | Incoming / Outgoing / Spare (the logger's vocabulary) |
| `model_slug` | validated `kit_preview` catalog slug | always `null` (3D not wired in beta) |
| flat panels | `mfm_ids` node built from the section `mode` | only the loose-leaf case, same shape |
| `/api/mfm/<id>/config/` | `panel_id` = `Equipment.key`, `config_table` = `equipment_config`, `object` = resolved GLB | `panel_id` = slug, `config_table` = `panel_config`/`mfm_config`, `object` = `null` |
| `/api/status/` | register / data-source health probe | import counters + logger-API reachability |

### FE work implied

1. **Exactly two container levels and a known eight-category list.** Render all eight in the
   order given, **including the ones whose `children` is `[]`** — an empty category is a real
   state, not a bug.
2. Key nodes by `id`, not by `mfm_id` (`mfm_id` is no longer unique across the tree).
3. Read `kind:'section'` vs `kind:'panel'` + `distribution_panel` to choose the tab set.
4. Drop the Bus Coupler role group; render `role` from the leaf when a label is needed.
5. Treat `model_slug`, `mfm_row`, `breaker_type` and `object` as absent for now.
6. Route containers at `/electrical/<category slug>` (one segment) and panels at
   `/electrical/<category slug>/<panel slug>`. Deeper section paths no longer exist.
7. `mfm_type` gained `amf_panel` and `ups_panel` — handle them wherever `pcc_panel` /
   `ht_panel` are already special-cased.
