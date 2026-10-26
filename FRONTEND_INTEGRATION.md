# EMS Frontend Integration Guide

This is the frontend contract for the current EMS backend. The frontend must discover panel and
MFM IDs from `/api/ems/`; do not hardcode database IDs.

## 1. Origins

The backend serves HTTP and WebSockets on the same host and port. The systemd-managed service
uses port `18889` (an earlier `8891` instance may still be running for compatibility).

```ts
const HTTP_ORIGIN = import.meta.env.VITE_EMS_API_ORIGIN ?? "http://127.0.0.1:18889";
const WS_ORIGIN = HTTP_ORIGIN.replace(/^http/, "ws");
```

When the frontend is served over HTTPS, use an HTTPS backend and `wss://`; browsers block an
insecure `ws://` connection from a secure page.

## 2. Bootstrap requests

Load these once when the application starts:

```ts
const [ems, bms, assets, catalog] = await Promise.all([
  fetch(`${HTTP_ORIGIN}/api/ems/`).then(r => r.json()),
  fetch(`${HTTP_ORIGIN}/api/bms/`).then(r => r.json()),
  fetch(`${HTTP_ORIGIN}/api/assets/`).then(r => r.json()),
  fetch(`${HTTP_ORIGIN}/api/kit-preview/catalog/`).then(r => r.json()),
]);
```

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/ems/` | Authoritative EMS navigation and all usable frontend IDs |
| `GET` | `/api/bms/` | BMS shell tree; currently `{ "count": 0, "tree": [] }` |
| `GET` | `/api/assets/` | Asset shell tree; currently `{ "count": 0, "tree": [] }` |
| `GET` | `/api/kit-preview/catalog/` | Active 3D model catalogue |
| `GET` | `/api/mfm/<mfm_id>/config/` | Resolved panel or MFM nameplate and thresholds |
| `GET` | `/api/equipment/key/<key>/viewer/` | Resolved viewer model and settings |
| `GET` | `/api/status/` | Backend/import health |

No authentication is required for these read endpoints or the telemetry WebSockets.

## 3. Navigation and ID rules

`GET /api/ems/` returns:

```jsonc
{
  "count": 8,
  "leaf_count": 102,
  "matched_mfm_count": 102,
  "ungrouped_mfm_count": 0,
  "uncategorised_panel_count": 0,
  "uncategorised_mfm_count": 0,
  "tree": []
}
```

The eight entries in `tree` are category sections. Their `children` are distribution panels
and/or direct MFMs. A distribution panel then contains its own metered incomers, outgoers,
and spares. Render the returned `children` recursively; do not flatten the meter leaves into
the category or infer panel membership from names.

```text
HT Panels (category; no endpoint ID)
├── 33KV HT Panel (distribution panel; mfm_id = panel/Group ID)
│   ├── Overview (opens the parent panel)
│   ├── Incoming
│   │   └── I/C_33KV_MAIN_VCB (single MFM; its own mfm_id)
│   ├── Outgoing
│   │   └── ... (each single MFM has its own mfm_id)
│   └── Spare (only when this panel has spare MFMs)
└── Direct HT MFM (single MFM; only when one is filed directly under HT)
```

The live HT category currently has one panel, one incomer, three outgoers, and no direct
MFM or spare. Other categories can contain both panels and direct MFMs. Empty role groups
are omitted; `Overview` is always present on a panel.

### Distribution-panel node

```jsonc
{
  "id": "grp-2",
  "label": "33KV HT Panel",
  "key": "33-kv",
  "distribution_panel": true,
  "mfm_id": 2,
  "children": [
    { "slug": "overview", "label": "Overview" },
    {
      "slug": "incoming",
      "children": [{ "label": "I/C_33KV_MAIN_VCB", "mfm_id": 1000102, "distribution_panel": false }]
    },
    { "slug": "outgoing", "children": [/* three MFM leaves, each with its own mfm_id */] }
  ]
}
```

- A distribution panel uses its returned `mfm_id` directly. This is its **panel/Group ID**
  (currently below `1,000,000`), not an `Equipment.id`. There is no separate `panel_id` or
  `equipment_id` field on the tree node.
- Its incoming/outgoing/spare child leaves have their **own** offset MFM IDs; never open a
  single-MFM page using the parent panel ID.
- The Overview screen connects to the panel's `energy-distribution` socket; there is no
  distribution-panel `/overview/` socket.

### Standalone or feeder MFM node

```jsonc
{
  "label": "I/C_PCC_2_LAMINATOR_PDB",
  "key": "i-c-pcc-2-laminator-pdb",
  "distribution_panel": false,
  "mfm_id": 1000242
}
```

- A single MFM ID is `1,000,000 + internal MFM.id`.
- Always use the `mfm_id` returned by the tree. Never add or remove the offset in frontend code.
- Production currently contains 17 standalone MFM nodes and no distribution-panel nodes.

### Turning the tree into navigation

Use `id` as the UI tree key and `mfm_id` as the backend endpoint ID. A category is a container;
it has no `mfm_id`. The panel's `Overview` and Incoming/Outgoing/Spare nodes have no endpoint
IDs of their own: clicking Overview opens the **parent panel**; clicking a role group expands
it. A role-group leaf opens that leaf's **single MFM**. A direct category leaf opens the same
single-MFM page, without a parent panel.

```ts
type EmsNode = {
  id: string;
  label: string;
  slug?: string;
  kind?: string;
  distribution_panel?: boolean;
  mfm_id?: number;
  children?: EmsNode[];
};

// These callbacks are your frontend routes/page state, not backend endpoints.
function onEmsNodeClick(
  node: EmsNode,
  parentPanel: EmsNode | undefined,
  openPanel: (panelId: number, page: string) => void,
  openMfm: (mfmId: number) => void,
  toggle: (nodeId: string) => void,
) {
  if (node.distribution_panel === true && node.mfm_id !== undefined) {
    openPanel(node.mfm_id, "energy-distribution");
  } else if (node.slug === "overview" && parentPanel?.mfm_id !== undefined) {
    openPanel(parentPanel.mfm_id, "energy-distribution");
  } else if (node.distribution_panel === false && node.mfm_id !== undefined) {
    openMfm(node.mfm_id);
  } else {
    toggle(node.id); // category or Incoming/Outgoing/Spare group
  }
}
```

While rendering, pass the enclosing panel as `parentPanel` to its Overview and role-group
descendants. The panel opens the five aggregate screens in §7; either kind of MFM leaf opens
the eleven single-MFM screens in §8. For either page, request
`/api/mfm/<node.mfm_id>/config/` and connect to `/ws/mfm/<node.mfm_id>/<screen>/` using the
selected panel or leaf ID. Do not substitute an Equipment ID, the node's string `id`, or the
LoggerFast `logger_device_id`.

Current aggregate panels, shown only for testing/reference, are:

| Panel | Current `mfm_id` | Members |
|---|---:|---|
| 33KV HT Panel | `2` | 1 incoming, 3 outgoing |
| AMF-1 / AMF-2 / AMF-3 | `16` / `17` / `18` | 2 incoming, 2 outgoing each |
| PCC-1 | `19` | 2 incoming, 3 outgoing |
| PCC-2 | `20` | 2 incoming, 12 outgoing, 1 spare |
| PCC-3 | `21` | 2 incoming, 7 outgoing, 2 spares |
| UPS | `24` | 4 incoming, 9 outgoing |
| Utility Panel | `25` | 2 incoming, 23 outgoing |

These numbers can change if the metadata database is rebuilt. Runtime code must use `/api/ems/`.

## 4. Page bootstrap

For a selected navigation node:

```ts
const id = node.mfm_id;
const key = node.key;

const [config, viewer] = await Promise.all([
  fetch(`${HTTP_ORIGIN}/api/mfm/${id}/config/`).then(r => r.json()),
  fetch(`${HTTP_ORIGIN}/api/equipment/key/${encodeURIComponent(key)}/viewer/?default=panel`)
    .then(r => r.json()),
]);
```

The config response identifies the resolved type:

```jsonc
{
  "mfm_id": 24,
  "panel_id": "ups",
  "mfm_type": "ups_panel",
  "config_table": "panel_config",
  "config": {},
  "object": null
}
```

Important voltage convention:

- Distribution-panel config returns nominal voltage line-to-line.
- Single-MFM config returns per-phase nominal voltage line-to-neutral.

## 5. WebSocket helper

```ts
type SocketOptions = Record<string, string | number | undefined | null>;

export function openMfmSocket(
  mfmId: number,
  screen: string,
  query: SocketOptions,
  onFrame: (frame: any) => void,
) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const suffix = params.size ? `?${params}` : "";
  const socket = new WebSocket(`${WS_ORIGIN}/ws/mfm/${mfmId}/${screen}/${suffix}`);

  socket.onmessage = event => onFrame(JSON.parse(event.data));
  socket.onerror = error => console.error("EMS WebSocket error", error);
  return socket;
}
```

Keep one connection per visible widget/page unless the UI intentionally needs independent widget
lifecycles. Close sockets when the component unmounts. On an unexpected close, reconnect with
exponential backoff; do not reconnect after application close code `4404` until the route or ID is
corrected.

## 6. Common WebSocket frames

Panel aggregate snapshots use:

```jsonc
{
  "type": "snapshot",
  "mfm_id": 24,
  "mfm_name": "UPS",
  "panel_id": "ups",
  "mfm_type": "ups_panel",
  "page": "energy-distribution",
  "ts": "2026-09-21T13:18:13.036716+05:30",
  "widgets": {}
}
```

Live panel updates use `type: "tick"`. Filter-command responses are complete replacement snapshots,
so replace the page state rather than merging individual arrays.

Errors are data frames before closure or while the socket remains open:

```jsonc
{
  "type": "error",
  "code": 4404,
  "mfm_id": 24,
  "message": "id not served on this screen"
}
```

Other error codes are `invalid_filter` and `telemetry_error`. Do not treat missing metric fields or
JSON `null` as zero; a meter schema may genuinely not provide that field.

## 7. Distribution-panel WebSockets

These five routes accept only nodes with `distribution_panel: true`.

| Page | WebSocket | Main response keys |
|---|---|---|
| Overview / Energy Distribution | `/ws/mfm/<id>/energy-distribution/` | `widgets.sources`, `consumers`, `spares`, `energy_flow`, `reconciliation`, `topology` |
| Real-Time Monitoring | `/ws/mfm/<id>/real-time-monitoring/` | `heatmap`, `power_balance`, `consumption_trend`, `bus_kpis`, `selection` |
| Energy & Power | `/ws/mfm/<id>/energy-power/` | `cumulative_energy`, `energy_trend`, `live_power_analysis`, `power_demand` |
| Voltage & Current | `/ws/mfm/<id>/voltage-current/` | `event_summary`, `event_timeline`, `current_distribution`, `feeder_table`, `selection` |
| Harmonics & PQ | `/ws/mfm/<id>/power-quality-summary/` | `issue_summary`, `timeline`, `feeder_table`, `signature`, `selection`, `ai_summary` |

### Energy Distribution

```text
/ws/mfm/<panel-id>/energy-distribution/?range=today&shift=all
```

This endpoint does not accept `sampling`. It calculates energy-counter deltas over the selected
window. Spares are returned separately and are excluded from supplied/consumed reconciliation.

```ts
socket.send(JSON.stringify({
  range: "custom",
  start: "2026-09-01",
  end: "2026-09-21",
  shift: "all",
}));
```

### Panel Real-Time Monitoring

```text
/ws/mfm/<panel-id>/real-time-monitoring/
```

This endpoint has no calendar filter. It emits a full initial snapshot and full `tick` frames on the
configured polling interval. `window_seconds` is `900`.

```ts
socket.send(JSON.stringify({ select_feeder: feederMfmId }));
socket.send(JSON.stringify({ clear_feeder: true }));
```

Each heatmap row has raw metric fields and a `cells` object. A cell contains `value`, normalized
`basis`, and `status` (`low`, `normal`, `moderate`, `high`, or `critical`).

### Panel Energy & Power

```text
/ws/mfm/<panel-id>/energy-power/?range=today&sampling=hour&shift=all
```

The same filter fields may be sent as a JSON command. `energy_trend[].feeders` contains per-feeder
contribution values; aggregate energy totals count outgoing feeders, while demand includes the
returned feeder breakdown.

### Panel Voltage & Current

```text
/ws/mfm/<panel-id>/voltage-current/?range=today&sampling=hourly&shift=all
```

Commands:

```ts
socket.send(JSON.stringify({ shift: "a" }));
socket.send(JSON.stringify({ selected_period: { bucket: "12:00" } }));
socket.send(JSON.stringify({ selected_panel: { mfm_id: feederMfmId } }));
```

Use the timeline point's `bucket` value when possible. A displayed label such as `12:00` is also
accepted for a single-day timeline. Selecting a bucket changes the interval used by
`current_distribution` and `feeder_table`.

### Panel Harmonics & PQ

```text
/ws/mfm/<panel-id>/power-quality-summary/?range=today&sampling=hourly&shift=all
```

Commands:

```ts
socket.send(JSON.stringify({ select_feeder: feederMfmId }));
socket.send(JSON.stringify({ fleet_matrix: { focus: "i_thd" } }));
socket.send(JSON.stringify({
  timeline_filter: {
    range: "custom_range",
    sampling: "daily",
    shift: "all",
    start_date: "2026-09-01",
    end_date: "2026-09-21",
  },
}));
```

The timeline is historical. `feeder_table` and `signature` use the latest feeder readings; there is
no historical `selected_period` command on this endpoint.

## 8. Single-MFM WebSockets

The Overview Total card reads its latest logger averages from
`widgets.phase_readings.total.voltage_v` (V) and
`widgets.phase_readings.total.current_a` (A). The original register names
`voltage_avg` and `current_avg` are also supplied in that object, alongside the three total-power
fields. Preserve `null` as unavailable and numeric `0` as zero. These are logger
average registers at the latest sample, not averages across the selected day.
For the PCC-1 `INCOMER_FROM_AMF_1` meter, `voltage_avg` is L-L voltage.

Use these for panel feeder leaves and standalone Production MFMs. They accept IDs returned with
`distribution_panel: false`.

| Screen | WebSocket | Behavior |
|---|---|---|
| Overview | `/ws/mfm/<id>/overview/` | Full live equipment snapshot |
| Real-Time Monitoring | `/ws/mfm/<id>/real-time-monitoring/` | Initial queue plus incremental ticks |
| Energy & Power | `/ws/mfm/<id>/energy-power/` | Initial queue plus incremental ticks |
| Energy Summary | `/ws/mfm/<id>/energy-summary/` | Filtered period totals |
| Power/Energy Analysis | `/ws/mfm/<id>/power-energy-analysis/` | Filtered bucketed bars |
| Load Anomalies | `/ws/mfm/<id>/load-anomalies/` | Filtered actual/expected load |
| Voltage & Current | `/ws/mfm/<id>/voltage-current/` | Initial queue plus incremental ticks |
| Voltage History | `/ws/mfm/<id>/voltage-history/` | Filtered buckets and KPIs |
| Current History | `/ws/mfm/<id>/current-history/` | Filtered buckets and KPIs |
| Power Quality Summary | `/ws/mfm/<id>/power-quality-summary/` | Live PQ widgets |
| Power Quality History | `/ws/mfm/<id>/power-quality-history/` | Filtered PQ buckets and KPIs |

Live queue endpoints initially return:

```jsonc
{
  "type": "snapshot",
  "window_seconds": 900,
  "capacity": 12,
  "columns": [],
  "count": 12,
  "queue": [],
  "status": {}
}
```

Later live readings are incremental:

```jsonc
{
  "type": "tick",
  "enqueue": [{ "ts": "...", "active_power_total_kw": 120.5 }],
  "dequeue": 1,
  "queue_size": 12,
  "status": {}
}
```

Apply the update as:

```ts
queue = [...queue.slice(frame.dequeue), ...frame.enqueue].slice(-frame.queue_size);
```

Filtered single-MFM routes accept filter updates as ordinary JSON objects, using the same fields
described below. `energy-summary` is range-only and rejects `sampling` and `shift`; live queue and
live summary routes do not use calendar-filter commands. Use the filter metadata actually returned
by a route instead of assuming every control applies everywhere.

## 9. Time-filter contract

Supported fields:

| Field | Values |
|---|---|
| `range` | `today`, `yesterday`, `this_week`, `last_7d`, `this_month`, `last_month`, `last_30d`, `last_24h`, `custom` |
| Start | `start` or `start_date` |
| End | `end` or `end_date` |
| `sampling` | Depends on the selected range; use `available_samplings` from the response |
| `shift` | `all`, `a`, `b`, `c` |

Aliases accepted from the existing frontend are `custom_range`, `custom_date`, `daily`, `weekly`,
and `monthly`. The response always echoes canonical values such as `custom` and `day`.

Sampling tokens are exact: `hour` is one hour, legacy `hourly` is a three-hour bucket, `2hour` is
two hours, and `shift` is an eight-hour shift bucket.

Date-only custom ranges include the complete end date:

```text
start=2026-09-01&end=2026-09-03
→ [2026-09-01 00:00 IST, 2026-09-04 00:00 IST)
```

Use response-provided filter metadata to build selectors:

```jsonc
{
  "range": "today",
  "start": "...",
  "end": "...",
  "sampling": "hour",
  "shift": "all",
  "available_ranges": [],
  "available_samplings": [],
  "sampling_by_range": {},
  "available_shifts": ["all", "a", "b", "c"]
}
```

Do not send the old `days` value as the source of truth. It is ignored; `range`, dates, and
`sampling` control the query.

Shift times are IST:

- A: 06:00–14:00
- B: 14:00–22:00
- C: 22:00–06:00

## 10. Recommended routing decision

```ts
function screenSocket(node: any, page: string): string {
  if (node.distribution_panel) {
    const aggregate: Record<string, string> = {
      overview: "energy-distribution",
      "energy-distribution": "energy-distribution",
      "real-time-monitoring": "real-time-monitoring",
      "energy-power": "energy-power",
      "voltage-current": "voltage-current",
      "power-quality-summary": "power-quality-summary",
    };
    if (!aggregate[page]) throw new Error(`Unsupported panel page: ${page}`);
    return aggregate[page];
  }
  return page; // use one of the eleven single-MFM screen names
}
```

Never open a feeder-only history route with a distribution-panel ID. Never open
`energy-distribution` with a single-MFM ID. The backend returns error code `4404` for either type
mismatch.

## Threshold configuration update — 2026-09-29

See [THRESHOLD_IMPLEMENTATION.md](THRESHOLD_IMPLEMENTATION.md) for page-wise
threshold sources, admin paths and additive payload fields. URLs and navigation
IDs are unchanged. After admin edits, reconnect WebSockets. Panel RTM now uses
saved bands; single-MFM PQ can return `Watch`. Use the explicit voltage-basis
metadata rather than assuming all voltage averages are L-N.
