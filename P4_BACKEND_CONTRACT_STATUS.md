# P4 Backend Metadata and Contract Status

Verified against the live P4 backend on **2026-09-24**. Source-role mapping is still in
progress, so this document does not infer grid, DG, or solar identity from meter names.

Navigation and screen routing are available, but plant identity, aggregation semantics,
equipment topology, source roles, and several telemetry corrections remain incomplete. The
frontend must not treat any current panel ID as the whole-plant total.

## 1. Plant identity and totals

### What exists

- The deployment is understood to be P4 / Seetharampur, but that identity is not stored or
  exposed as authoritative backend metadata.
- Backend timezone: `Asia/Kolkata`.
- Panel `24` is UPS.
- Panel `2` is `33KV HT Panel`, but it is not declared as the plant aggregation root.
- There is no whole-plant totals endpoint or `aggregation_root` field.
- `/ws/mfm/<panel-id>/energy-distribution/` aggregates only that panel's memberships:
  - `incoming` becomes `widgets.sources`.
  - `outgoing` becomes `widgets.consumers`.
  - `spare` becomes `widgets.spares` and is excluded from reconciliation.
- Adding panel totals to their child meters or downstream panels would double count energy.

Until source mappings are complete, the backend cannot define whether plant consumption means
grid import, grid plus DG/solar, production consumption, or another boundary.

### What needs adding

- Authoritative site key, site name, timezone, and deployment identity.
- Explicit aggregation roots and calculation method.
- Included/excluded meters, panels, internal transfers, spares, and generation.
- A backend-owned plant-total API that does not require the frontend to infer a root ID.

## 2. Source roles

Source-role mapping is intentionally pending. Current membership roles only describe direction
inside a distribution panel:

- `incoming`
- `outgoing`
- `spare`

They do not mean grid, DG, solar, transformer, or another source type. Meter names must not be
used as the API contract. Explicit backend configuration and API fields are still required.

## 3. Existing hierarchy and relationships

`GET /api/ems/` currently returns:

- 8 categories.
- 9 distribution panels.
- 102 active MFMs.
- All 102 MFMs have a table, schema, data source, Equipment record, threshold record, and
  breaker metadata.
- Distribution-panel endpoint IDs are `Group.id`, below `1,000,000`.
- Meter endpoint IDs are `1,000,000 + MFM.id`.
- Equipment IDs are separate database identities and must not be used in MFM endpoints.

Current hierarchy:

```text
Category
├── Distribution panel: mfm_id = panel/Group ID
│   ├── Overview
│   ├── Incoming
│   │   └── MFM: mfm_id = offset meter ID
│   ├── Outgoing
│   │   └── MFM
│   └── Spare
└── Direct MFM
```

Production has 17 direct MFMs. Utility is represented by distribution panel `25`, with two
incomers and 23 outgoers.

Panel analytics also return local topology:

```json
{
  "topology": {
    "panel": {"id": 2, "key": "33-kv", "name": "33KV HT Panel"},
    "incoming": [1000102],
    "outgoing": [1000103, 1000104, 1000105],
    "spare": []
  }
}
```

### Missing relationship metadata

The following are not configured:

- Transformer primary-to-secondary meter relationships.
- Global upstream/downstream feed graph: `FeedEdge` currently has zero rows.
- `parent_series`: empty for all MFMs.
- Parent relationships between distribution panels: all nine panels currently have no parent
  group.
- Transformer classifications: all 102 MFMs currently resolve as `lt_panel`.
- Explicit production/utilities semantic groups beyond their navigation category or panel
  membership.
- A public topology endpoint spanning multiple panels.

Meter names suggest some relationships, but the backend must not turn those guesses into a
contract.

## 4. Supported capabilities

### HTTP APIs

| Endpoint | Status |
|---|---|
| `GET /api/ems/` | Available |
| `GET /api/mfm/<id>/config/` | Available |
| `GET /api/equipment/key/<key>/viewer/` | Available, but no models are currently bound |
| `GET /api/status/` | Available |
| `GET /api/bms/` | Intentionally empty |
| `GET /api/assets/` | Intentionally empty |
| `GET /api/kit-preview/catalog/` | Empty; zero viewer assets |
| Source/DG-run REST endpoints | Not implemented |

`/api/status/` currently reports 102/102 MFMs bound.

Schema bindings:

- `sch_EM6400_NG+`: 5 meters.
- `sch_EMMS7_96`: 59 meters.
- `sch_LG2599`: 38 meters.

BMS and Assets are application skeletons only: no models, migrations, consumers, or WebSocket
routes. The empty inventories are intentional for this version, but represent unfinished product
capability rather than an authentication or frontend problem.

### WebSocket page registry

Distribution panels support:

- `energy-distribution`
- `real-time-monitoring`
- `energy-power`
- `voltage-current`
- `power-quality-summary`

Single MFMs support:

- `overview`
- `real-time-monitoring`
- `energy-power`
- `energy-summary`
- `power-energy-analysis`
- `load-anomalies`
- `voltage-current`
- `voltage-history`
- `current-history`
- `power-quality-summary`
- `power-quality-history`

See `FRONTEND_INTEGRATION.md` for frontend routing and commands.

## 5. Payload and measurement contract

### Common panel frame

```json
{
  "type": "snapshot",
  "mfm_id": 2,
  "mfm_name": "33KV HT Panel",
  "panel_id": "33-kv",
  "mfm_type": "ht_panel",
  "page": "energy-distribution",
  "ts": "2026-09-24T10:34:16.347718+05:30",
  "range": "today",
  "sampling": null,
  "shift": "all",
  "start": "2026-09-23T18:30:00+00:00",
  "end": "2026-09-24T10:34:16.347718+05:30",
  "widgets": {}
}
```

Widget groups:

| Tab | `widgets` fields |
|---|---|
| Energy Distribution | `sources`, `consumers`, `spares`, `energy_flow`, `reconciliation`, `topology` |
| Real-Time Monitoring | `heatmap`, `power_balance`, `consumption_trend`, `bus_kpis`, `selection` |
| Energy & Power | `cumulative_energy`, `energy_trend`, `live_power_analysis`, `power_demand` |
| Voltage & Current | `event_summary`, `event_timeline`, `current_distribution`, `feeder_table`, `selection` |
| Harmonics & PQ | `issue_summary`, `timeline`, `feeder_table`, `signature`, `selection`, `ai_summary` |

### Units

Field suffixes identify the unit:

- `*_kw`, `*_kvar`, `*_kva`
- `*_kwh`, `*_kvarh`, `*_kvah`
- `*_v`, `*_a`, `*_hz`
- `*_pct`
- ISO-8601 timestamps with offsets

### Voltage basis

- Distribution-panel config returns L-L voltage.
- Single-MFM config returns L-N voltage.
- Current telemetry is not yet fully basis-safe: `voltage_avg` differs by logger schema.
- Explicit `unit`, `voltage_basis`, and `quality` metadata still need to be added.

### Scaling

- LoggerFast applies schema/register scaling.
- Backend additionally supports `energy_direction`, `energy_scale`, and `power_scale`.
- On P4, all 102 meters currently use `energy_direction="sum"`, `energy_scale=1`, and
  `power_scale=1`.

### Snapshot, tick, and filter behavior

- Initial response: `type: "snapshot"`.
- Live RTM routes subsequently send `type: "tick"`.
- Filter commands return complete replacement snapshots.
- Missing values may be `null` and must not be interpreted as zero.
- Live panel polling defaults to five seconds.
- Supported ranges include `today`, `yesterday`, `this_week`, `last_7d`, `this_month`,
  `last_month`, `last_30d`, `last_24h`, and `custom`.
- Custom ranges require start and end values.
- Supported shifts are `all`, `a`, `b`, and `c`.
- Available sampling options are returned by each filtered response.

### Status meanings

The real-time heatmap uses `low`, `normal`, `moderate`, `high`, and `critical`. Higher values are
worse for load, reactive ratio, voltage deviation, current loading, and unbalance. Power factor
uses descending severity, where a higher PF is better.

Voltage/current feeder rows use `normal`, `warning`, and `danger`. PQ feeder rows use `normal`,
`warning`, and `critical`. Missing or invalid telemetry must remain distinguishable from a valid
normal or zero value.

### Sanitized HT reconciliation sample

```json
{
  "supplied_kwh": 37216.0,
  "consumed_kwh": 37262.0,
  "gap_kwh": -46.0,
  "efficiency_pct": 100.12,
  "window_hours": 10.58,
  "utilization_pct": 78.28
}
```

### Sanitized PCC-1 reconciliation sample

```json
{
  "supplied_kwh": 0.0,
  "consumed_kwh": 0.0,
  "gap_kwh": 0.0,
  "efficiency_pct": null,
  "window_hours": 10.58,
  "utilization_pct": 0.0
}
```

Complete versioned HT/PCC JSON fixtures remain a backend deliverable. The existing frontend
guide documents the current field structure but is not yet a frozen, versioned schema.

## 6. Telemetry anomaly findings

### Voltage deviation above 14,000%

Invalid.

The EM6400 LoggerFast schema currently calculates:

```text
(voltage_avg - 240) / 240 × 100
```

That formula is incorrect for 33 kV meters. A live value of approximately `33,894 V` therefore
becomes approximately `14,417%`.

Two transformer-primary meters also contain invalid phase/average voltage values near zero,
producing `-100%` deviation and enormous voltage-unbalance values.

Required corrections:

- Use each meter's configured nominal voltage and correct L-L/L-N basis.
- Reject or suppress invalid phase values.
- Recalculate deviation and unbalance in the backend or LoggerFast schema.
- Add measurement-quality metadata.

### Power factor above 1

Invalid as a physical PF value.

Live HT values include `1.004`, `1.023`, and `1.033`. The backend currently forwards these raw
values without validation. Values outside `[-1, 1]` must be marked invalid or normalized under
an agreed rule.

### Tiny active power with multi-MVA apparent power

Invalid.

One observed example was:

```text
Active power:   0.156 kW
Apparent power: 3,071 kVA
Current:        54 A at approximately 33 kV
```

The current and apparent power are mutually plausible, while active power is not. The mismatch
is not a single uniform decade multiplier, so setting `power_scale` blindly is unsafe. The
EM6400 active-power register mapping, word order, or decoding must be checked in LoggerFast.

### Output energy above input

For HT, output exceeded input by approximately `46 kWh` out of `37,216 kWh`, or `0.12%`.
This small difference can result from asynchronous sample boundaries, meter accuracy, and counter
resolution. The raw deltas may be valid, but presenting this as `100.12% efficiency` is
misleading. The contract needs a reconciliation tolerance and an `inside_tolerance`-style state.

More importantly, every AMF, PCC, UPS, and Utility panel returned zero active-energy delta during
the audit despite nonzero live power and, in some cases, changing apparent-energy counters. Their
active-energy register/direction mappings are not production-ready.

## 7. Remaining backend work

| Work | Proposed owner | Target |
|---|---|---|
| Site identity and timezone API | Backend | Unscheduled |
| Authoritative plant aggregation boundary | Plant electrical owner + backend | Blocked on source/topology sign-off |
| Grid/DG/solar source roles | Plant electrical owner + backend | In progress |
| Transformer primary/secondary mapping and feed edges | Plant electrical owner + backend | Unscheduled |
| Capabilities endpoint/page-registry exposure | Backend | Unscheduled |
| Versioned HT/PCC response fixtures | Backend | Unscheduled |
| EM6400 voltage formula correction | LoggerFast/data owner + backend validation | High priority, unscheduled |
| EM6400 active-power decoding correction | LoggerFast/data owner | High priority, unscheduled |
| LV active-energy register/direction validation | LoggerFast/data owner + backend | High priority, unscheduled |
| Reconciliation tolerance and measurement-quality fields | Backend | After telemetry validation |
| BMS, Assets, Source, and DG-run modules | Product/backend | Pending scope decision |

Calendar dates cannot be stated honestly because none are assigned in the repository. Until the
high-priority telemetry items and aggregation mapping are signed off, the frontend may expose
navigation and raw meter pages but must not label any panel as `Plant Total` or treat HT/PCC
energy and quality analytics as validated.

## 8. Additive/versioned metadata recommendation

Plant-specific mappings should remain backend-owned and should be exposed through an additive or
versioned API so existing Seetharampur behavior is not broken. The future response should keep
identities namespaced and explicit, for example:

```json
{
  "site": {
    "key": "p4",
    "name": "P4",
    "timezone": "Asia/Kolkata"
  },
  "aggregates": [],
  "sources": [],
  "relationships": [],
  "capabilities": {},
  "mapping_complete": false
}
```

Until mappings are approved, incomplete arrays should remain empty and `mapping_complete` should
remain `false`; names must not be parsed to manufacture source types or topology.
